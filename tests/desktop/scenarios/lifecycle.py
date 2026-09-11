from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from tests.desktop.harness.runtime import drain, load_gtk3


def _buffer_text(window):
    start, end = window.buffer.get_bounds()
    return window.buffer.get_text(start, end, True)


def _append_user_text(window, text: str) -> None:
    window.buffer.begin_user_action()
    try:
        window.buffer.insert(window.buffer.get_end_iter(), text)
    finally:
        window.buffer.end_user_action()


def _arm_reload_dialog(GLib, Gtk, *, response):
    state = {"seen": False, "valid": False}

    def responder():
        for top in Gtk.Window.list_toplevels():
            if not isinstance(top, Gtk.MessageDialog) or not top.get_visible():
                continue
            if top.get_title() != "Reload from Disk":
                continue
            labels = set()
            area = top.get_action_area()
            for child in area.get_children():
                if isinstance(child, Gtk.Button):
                    labels.add(child.get_label())
            expected = {"Cancel", "Discard Changes and Reload"}
            state["seen"] = True
            state["valid"] = labels == expected
            top.response(response)
            return False
        return True

    source_id = GLib.timeout_add(1, responder)
    return state, source_id


def _remove_source(GLib, source_id):
    try:
        GLib.source_remove(source_id)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--manual", action="store_true")
    ns = ap.parse_args()
    import sys
    sys.path.insert(0, ns.repo)

    Gdk, GLib, Gtk = load_gtk3()
    from gi.repository import Gio
    if Gtk.get_major_version() != 3:
        return 1

    from graphium.adapters.gtk.application import GraphiumApplication
    app = GraphiumApplication()
    if not (app.get_flags() & Gio.ApplicationFlags.NON_UNIQUE):
        return 1
    if not app.register(None):
        return 1
    app.activate(); drain(Gtk)
    w = app.window
    if w is None or not isinstance(w, Gtk.ApplicationWindow):
        return 1
    if len([x for x in Gtk.Window.list_toplevels() if isinstance(x, Gtk.ApplicationWindow)]) < 1:
        return 1

    reload_action = w.lookup_action("reload")
    if reload_action is None or reload_action.get_enabled():
        return 1

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Clean Reload: no dialog, fresh disk bytes accepted, baseline Saved.
        path = root / "reload-clean.txt"
        path.write_text("first", encoding="utf-8")
        if not w.open_path(str(path)):
            return 1
        drain(Gtk)
        if not reload_action.get_enabled() or _buffer_text(w) != "first":
            return 1
        path.write_text("second", encoding="utf-8")
        reload_action.activate(None); drain(Gtk)
        if _buffer_text(w) != "second" or w.core.session.modified:
            return 1
        if w.core.session.logical_path != str(path):
            return 1

        # Modified + Discard and Reload: no writer call is possible from the dedicated
        # decision; the current disk object becomes the fresh accepted baseline.
        discard_path = root / "reload-discard.txt"
        discard_path.write_text("old", encoding="utf-8")
        if not w.open_path(str(discard_path)):
            return 1
        _append_user_text(w, " mine"); drain(Gtk)
        if not w.core.session.modified or _buffer_text(w) != "old mine":
            return 1
        replacement = root / "replacement.tmp"
        replacement.write_text("new disk object", encoding="utf-8")
        old_object = w.core.session.file_state.binding.object_id
        replacement.replace(discard_path)
        state, source_id = _arm_reload_dialog(GLib, Gtk, response=Gtk.ResponseType.REJECT)
        reload_action.activate(None); drain(Gtk)
        _remove_source(GLib, source_id)
        if not state["seen"] or not state["valid"]:
            return 1
        if _buffer_text(w) != "new disk object" or w.core.session.modified:
            return 1
        if w.core.session.logical_path != str(discard_path):
            return 1
        if w.core.session.file_state.binding.object_id == old_object:
            return 1
        if w.core.editor.can_undo:
            return 1
        if discard_path.read_text(encoding="utf-8") != "new disk object":
            return 1

        # Modified + Cancel: state is machine-owned before trigger. Responder is armed
        # before F5/action enters Gtk.Dialog.run(). No terminal/user handoff is involved.
        cancel_path = root / "reload-cancel.txt"
        cancel_path.write_text("base", encoding="utf-8")
        if not w.open_path(str(cancel_path)):
            return 1
        _append_user_text(w, " mine"); drain(Gtk)
        if not w.core.session.modified or _buffer_text(w) != "base mine":
            return 1
        cancel_path.write_text("external", encoding="utf-8")
        before_session = w.core.session.snapshot()
        before_history = w.core.history.checkpoint()
        state, source_id = _arm_reload_dialog(GLib, Gtk, response=Gtk.ResponseType.CANCEL)
        reload_action.activate(None); drain(Gtk)
        _remove_source(GLib, source_id)
        if not state["seen"] or not state["valid"]:
            return 1
        if _buffer_text(w) != "base mine" or not w.core.session.modified:
            return 1
        if w.core.session.snapshot() != before_session:
            return 1
        if w.core.history.checkpoint() != before_history:
            return 1
        if cancel_path.read_text(encoding="utf-8") != "external":
            return 1

        # No unexpected dialog may remain after the automated nested-loop responses.
        if any(isinstance(x, Gtk.Dialog) and x.get_visible() for x in Gtk.Window.list_toplevels() if x is not w):
            return 1

    w.destroy(); drain(Gtk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
