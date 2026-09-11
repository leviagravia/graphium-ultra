from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from tests.desktop.harness.runtime import drain, load_gtk3, text_of


def _menu_item(parent, Gtk, label):
    return next(
        item for item in parent.get_children()
        if isinstance(item, Gtk.MenuItem) and item.get_label() == label
    )


def _widgets(root, Gtk, widget_type):
    found = []
    if isinstance(root, widget_type):
        found.append(root)
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            found.extend(_widgets(child, Gtk, widget_type))
    return found


def _schedule(GLib, Gtk, failures, *, title: str, action, timeout: float = 3.0):
    deadline = time.monotonic() + timeout

    def poll():
        for window in Gtk.Window.list_toplevels():
            if not isinstance(window, Gtk.Dialog) or window.get_title() != title:
                continue
            try:
                action(window)
            except Exception as exc:
                failures.append(f"{title}: {type(exc).__name__}: {exc}")
                try:
                    window.response(Gtk.ResponseType.CANCEL)
                except Exception:
                    pass
            return False
        if time.monotonic() < deadline:
            return True
        failures.append(f"dialog not found before timeout: {title}")
        for window in Gtk.Window.list_toplevels():
            if isinstance(window, Gtk.Dialog):
                try:
                    window.response(Gtk.ResponseType.CANCEL)
                except Exception:
                    pass
        return False

    GLib.timeout_add(10, poll)


def _select_review(Gtk, *, category: str, message_contains: str):
    def apply(dialog):
        trees = _widgets(dialog, Gtk, Gtk.TreeView)
        assert len(trees) == 1
        tree = trees[0]
        model = tree.get_model()
        index = next(
            i for i, row in enumerate(model)
            if str(row[1]) == category and message_contains in str(row[3])
        )
        tree.get_selection().select_path(index)
        selected_model, tree_iter = tree.get_selection().get_selected()
        assert selected_model is not None and tree_iter is not None
        dialog.response(Gtk.ResponseType.OK)
    return apply


def _verify_reference_line(Gtk, expected_reference_line: int, expected_text: str):
    def apply(dialog):
        views = _widgets(dialog, Gtk, Gtk.TextView)
        assert len(views) == 1
        buffer = views[0].get_buffer()
        bounds = buffer.get_selection_bounds()
        assert len(bounds) == 2
        start, end = bounds
        selected_line = start.get_line() + 1
        assert selected_line == expected_reference_line
        assert buffer.get_text(start, end, True) == expected_text
        dialog.response(Gtk.ResponseType.CLOSE)
    return apply


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, GLib, Gtk = load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    failures: list[str] = []
    try:
        menubar = next(
            child for child in window._root_box.get_children() if isinstance(child, Gtk.MenuBar)
        )
        commands = _menu_item(menubar, Gtk, "Commands")
        review = _menu_item(commands.get_submenu(), Gtk, "Review")
        labels = [item.get_label() for item in review.get_submenu().get_children()]
        assert labels == ["Check Footnotes", "Check Citations", "Academic Review…"]
        assert "academic-review" in window._actions

        # Document diagnostic: exact selection projection, document/history neutral.
        window.core.editor.initialize_new_text("Alpha  beta.\n", clean=True); drain(Gtk)
        before_history = window.core.history.current_state_id
        assert not window.core.session.modified
        _schedule(
            GLib, Gtk, failures,
            title="Academic Review",
            action=_select_review(
                Gtk,
                category="Writing Hygiene",
                message_contains="Multiple consecutive spaces",
            ),
        )
        window._actions["academic-review"].activate(None); drain(Gtk)
        assert not failures, failures
        assert text_of(window.text_view) == "Alpha  beta.\n"
        assert not window.core.session.modified
        assert window.core.history.current_state_id == before_history
        bounds = window.buffer.get_selection_bounds()
        assert len(bounds) == 2
        start, end = bounds
        selected_text = window.buffer.get_text(start, end, True)
        assert selected_text == "  "

        # Reference diagnostic: raw canonical source projection at exact line.
        raw = (
            "# Graphium Plus References v1\n\n"
            "## dup\n"
            "Type: article\n"
            "Title: First\n\n"
            "## dup\n"
            "Type: article\n"
            "Title: Second\n"
        )
        # Intentionally malformed raw fixture: the test owns its filesystem
        # preconditions. Do not route malformed bytes through the canonical
        # store serializer, and do not misuse the Core writer as a directory
        # creation authority.
        reference_path = window.reference_store.path
        reference_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            reference_path.parent.chmod(0o700)
        except OSError:
            pass
        reference_path.write_text(raw, encoding="utf-8")
        try:
            reference_path.chmod(0o600)
        except OSError:
            pass
        ref_snapshot = window.reference_store.load()
        assert ref_snapshot.diagnostics
        expected_reference_line = ref_snapshot.diagnostics[0].line
        expected_reference_text = "## dup"

        window.core.editor.initialize_new_text("Clean.\n", clean=True); drain(Gtk)
        before_history = window.core.history.current_state_id
        _schedule(
            GLib, Gtk, failures,
            title="Academic Review",
            action=_select_review(Gtk, category="References", message_contains="Duplicate reference key"),
        )
        _schedule(
            GLib, Gtk, failures,
            title="Reference Library Source",
            action=_verify_reference_line(Gtk, expected_reference_line, expected_reference_text),
        )
        window._actions["academic-review"].activate(None); drain(Gtk)
        assert not failures, failures
        assert text_of(window.text_view) == "Clean.\n"
        assert not window.core.session.modified
        assert window.core.history.current_state_id == before_history

        print("PLUS_A6B_TRUE_GTK_AUTOMATED=PASS", flush=True)
        print("PLUS_A6B_DOCUMENT_TARGET_NAVIGATION=PASS", flush=True)
        print("PLUS_A6B_REFERENCE_TARGET_NAVIGATION=PASS", flush=True)
        print("PLUS_A6B_DOCUMENT_HISTORY_NEUTRALITY=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
