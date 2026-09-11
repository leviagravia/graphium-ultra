"""Thin GTK projection for Graphium's explicit, on-demand spell-check session."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from graphium.application.spellcheck import (
    SpellCheckController,
    SpellCheckRequest,
    SpellCheckStaleError,
    SpellIssue,
)
from graphium.infrastructure.hunspell_session import (
    HunspellDictionary,
    HunspellError,
    HunspellPipeSession,
    HunspellProcessError,
    HunspellProtocolError,
    HunspellTimeoutError,
    discover_hunspell_dictionaries,
    resolve_hunspell_executable,
)


class GtkSpellCheckDialog:
    """One modal dialog, one controller, one worker, at most one Hunspell child."""

    def __init__(
        self,
        parent: Gtk.Window,
        *,
        editor,
        executable: str,
        on_changed: Callable[[], None] | None = None,
        session_factory=HunspellPipeSession,
        dictionary_discoverer=discover_hunspell_dictionaries,
    ) -> None:
        self._editor = editor
        self._controller = SpellCheckController(editor)
        self._executable = executable
        self._session_factory = session_factory
        self._dictionary_discoverer = dictionary_discoverer
        self._session: HunspellPipeSession | None = None
        self._on_changed = on_changed
        self._executor: ThreadPoolExecutor | None = None
        self._discovery_cancel = threading.Event()
        self._closed = False
        self._ticket = 0
        self._dictionary_ready = False
        self._dictionary_bases: list[str | None] = [None]
        self._selected_dictionary: str | None = None

        dialog = Gtk.Dialog(title="Spelling", transient_for=parent, modal=True)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(440, -1)
        self._dialog = dialog
        area = dialog.get_content_area()
        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        grid.set_border_width(12)
        area.pack_start(grid, True, True, 0)

        self._dictionary = Gtk.ComboBoxText()
        self._dictionary.append_text("System default")
        self._dictionary.set_active(0)
        self._dictionary.set_sensitive(False)
        self._dictionary.connect("changed", self._on_dictionary_changed)
        self._word = Gtk.Label(label="")
        self._word.set_xalign(0.0)
        self._replacement = Gtk.ComboBoxText.new_with_entry()
        self._replacement.set_hexpand(True)
        child = self._replacement.get_child()
        if isinstance(child, Gtk.Entry):
            child.set_activates_default(False)
            child.set_width_chars(28)
        self._status = Gtk.Label(label="")
        self._status.set_xalign(0.0)
        self._status.set_line_wrap(True)

        grid.attach(Gtk.Label(label="Dictionary:"), 0, 0, 1, 1)
        grid.attach(self._dictionary, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Unknown word:"), 0, 1, 1, 1)
        grid.attach(self._word, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Replacement:"), 0, 2, 1, 1)
        grid.attach(self._replacement, 1, 2, 1, 1)
        grid.attach(self._status, 0, 3, 2, 1)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._replace = Gtk.Button(label="Replace")
        self._ignore = Gtk.Button(label="Ignore")
        self._ignore_all = Gtk.Button(label="Ignore All")
        for button in (self._replace, self._ignore, self._ignore_all):
            buttons.pack_start(button, False, False, 0)
        grid.attach(buttons, 0, 4, 2, 1)
        self._replace.connect("clicked", self._on_replace)
        self._ignore.connect("clicked", self._on_ignore)
        self._ignore_all.connect("clicked", self._on_ignore_all)
        self._set_issue_controls(False)

    def run(self) -> None:
        self._dialog.show_all()
        self._begin_dictionary_discovery()
        try:
            self._dialog.run()
        finally:
            self.close()
            self._dialog.destroy()

    def _set_issue_controls(self, enabled: bool) -> None:
        for widget in (self._replacement, self._replace, self._ignore, self._ignore_all):
            widget.set_sensitive(bool(enabled))

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="graphium-spell")
        return self._executor

    def _begin_dictionary_discovery(self) -> None:
        if self._closed:
            return
        self._set_issue_controls(False)
        self._status.set_text("Finding dictionaries…")
        self._ticket += 1
        ticket = self._ticket
        future = self._ensure_executor().submit(
            self._dictionary_discoverer,
            self._executable,
            cancel_event=self._discovery_cancel,
        )
        future.add_done_callback(lambda done, t=ticket: GLib.idle_add(self._deliver_dictionary_discovery, t, done))

    def _deliver_dictionary_discovery(self, ticket: int, future: Future) -> bool:
        if self._closed or ticket != self._ticket:
            return False
        try:
            dictionaries = tuple(future.result())
        except HunspellError:
            dictionaries = ()
        self._dictionary_bases = [None]
        self._dictionary.remove_all()
        self._dictionary.append_text("System default")
        for dictionary in dictionaries:
            if not isinstance(dictionary, HunspellDictionary):
                continue
            self._dictionary.append_text(dictionary.display_name)
            self._dictionary_bases.append(dictionary.base_path)
        self._dictionary.set_active(0)
        self._dictionary_ready = True
        self._dictionary.set_sensitive(True)
        self._selected_dictionary = None
        self._session = self._session_factory(self._executable, dictionary_base=None)
        self._advance()
        return False

    def _advance(self) -> None:
        if self._closed or self._session is None:
            return
        try:
            request = self._controller.next_request()
        except SpellCheckStaleError as exc:
            self._fatal("Spell check stopped", str(exc))
            return
        if request is None:
            self._word.set_text("")
            self._replacement.remove_all()
            self._set_issue_controls(False)
            self._status.set_text("Spell check complete.")
            return
        self._word.set_text(request.span.text)
        self._replacement.remove_all()
        self._set_issue_controls(False)
        self._status.set_text("Checking…")
        self._ticket += 1
        ticket = self._ticket
        future = self._ensure_executor().submit(self._session.check, request.span.text)
        future.add_done_callback(
            lambda done, t=ticket, r=request: GLib.idle_add(self._deliver, t, r, done)
        )

    def _deliver(self, ticket: int, request: SpellCheckRequest, future: Future) -> bool:
        if self._closed or ticket != self._ticket:
            return False
        try:
            result = future.result()
            issue = self._controller.accept_result(request, result)
        except SpellCheckStaleError as exc:
            self._fatal("Spell check stopped", str(exc))
            return False
        except HunspellProtocolError as exc:
            self._fatal(
                "Spell check stopped",
                "Hunspell returned an unexpected response. Spell check stopped to avoid using unreliable results.\n\n"
                + str(exc),
            )
            return False
        except HunspellTimeoutError as exc:
            self._fatal(
                "Spell check stopped",
                "Hunspell did not respond in time. Spell check stopped.\n\n" + str(exc),
            )
            return False
        except HunspellProcessError as exc:
            self._fatal(
                "Spell check stopped",
                "Hunspell stopped or the selected dictionary became unavailable during spell checking.\n\n"
                + str(exc),
            )
            return False
        except HunspellError as exc:
            self._fatal("Spell check stopped", "Hunspell could not continue.\n\n" + str(exc))
            return False
        except Exception as exc:
            self._fatal("Spell check stopped", str(exc))
            return False
        if issue is None:
            self._advance()
        else:
            self._show_issue(issue)
        return False

    def _show_issue(self, issue: SpellIssue) -> None:
        self._word.set_text(issue.span.text)
        self._replacement.remove_all()
        for suggestion in issue.suggestions:
            self._replacement.append_text(suggestion)
        entry = self._replacement.get_child()
        if isinstance(entry, Gtk.Entry):
            entry.set_text(issue.suggestions[0] if issue.suggestions else issue.span.text)
            entry.select_region(0, -1)
        self._status.set_text(
            "Choose a suggestion or type a replacement."
            if issue.suggestions else "No suggestions. Type a replacement or ignore this word."
        )
        self._set_issue_controls(True)
        if isinstance(entry, Gtk.Entry):
            entry.grab_focus()

    def _on_dictionary_changed(self, combo) -> None:
        if self._closed or not self._dictionary_ready:
            return
        index = combo.get_active()
        if index < 0 or index >= len(self._dictionary_bases):
            return
        selected = self._dictionary_bases[index]
        if selected == self._selected_dictionary:
            return
        self._restart_for_dictionary(selected)

    def _restart_for_dictionary(self, dictionary_base: str | None) -> None:
        self._ticket += 1
        snapshot = self._editor.capture_programmatic_source()
        if snapshot.state_id != self._controller.source_state_id:
            self._fatal("Spell check stopped", "document changed; run spell check again")
            return
        old_session, self._session = self._session, None
        if old_session is not None:
            old_session.cancel()
        self._controller.close()
        self._controller = SpellCheckController(self._editor)
        self._selected_dictionary = dictionary_base
        self._session = self._session_factory(self._executable, dictionary_base=dictionary_base)
        self._word.set_text("")
        self._replacement.remove_all()
        self._set_issue_controls(False)
        self._status.set_text("Checking…")
        self._advance()

    def _on_ignore(self, _button) -> None:
        try:
            self._controller.ignore()
        except SpellCheckStaleError as exc:
            self._fatal("Spell check stopped", str(exc))
            return
        self._advance()

    def _on_ignore_all(self, _button) -> None:
        try:
            self._controller.ignore_all()
        except SpellCheckStaleError as exc:
            self._fatal("Spell check stopped", str(exc))
            return
        self._advance()

    def _on_replace(self, _button) -> None:
        entry = self._replacement.get_child()
        replacement = entry.get_text() if isinstance(entry, Gtk.Entry) else ""
        try:
            plan = self._controller.replace(replacement)
        except SpellCheckStaleError as exc:
            self._fatal("Spell check stopped", str(exc))
            return
        except Exception as exc:
            self._fatal("Replacement was not applied", str(exc))
            return
        if plan.changed and self._on_changed is not None:
            self._on_changed()
        self._advance()

    def _fatal(self, title: str, message: str) -> None:
        self._set_issue_controls(False)
        self._status.set_text(message)
        alert = Gtk.MessageDialog(
            transient_for=self._dialog,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        alert.format_secondary_text(message)
        try:
            alert.run()
        finally:
            alert.destroy()
        self._dialog.response(Gtk.ResponseType.CLOSE)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._ticket += 1
        self._discovery_cancel.set()
        self._controller.close()
        session, self._session = self._session, None
        if session is not None:
            session.cancel()
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


def run_spell_check_dialog(
    parent: Gtk.Window,
    *,
    editor,
    on_changed: Callable[[], None] | None = None,
    resolver=resolve_hunspell_executable,
) -> bool:
    """Run spell check only on explicit invocation; return False when capability is absent."""
    executable = resolver()
    if not executable:
        alert = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text="Spell check unavailable",
        )
        alert.format_secondary_text(
            "Hunspell is not installed. Install Hunspell and a dictionary for your language, then run Check Spelling again."
        )
        try:
            alert.run()
        finally:
            alert.destroy()
        return False
    GtkSpellCheckDialog(parent, editor=editor, executable=executable, on_changed=on_changed).run()
    return True
