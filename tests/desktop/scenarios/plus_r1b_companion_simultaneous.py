from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tests.desktop.harness.runtime import drain, load_gtk3


def _bool(action) -> bool:
    return bool(action.get_state().get_boolean())


def _click(widget, Gtk) -> None:
    widget.emit("clicked")
    drain(Gtk)


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
    try:
        companion_visible = window.lookup_action("companion-visible")
        clips = window.lookup_action("companion-clips")
        scratch = window.lookup_action("companion-scratchpad")
        workspace = window.lookup_action("workspace-visible")
        outline = window.lookup_action("outline-visible")
        focus = window.lookup_action("focus-mode")
        assert all(action is not None for action in (
            companion_visible, clips, scratch, workspace, outline, focus
        ))

        # Fresh isolated state defaults Companion closed.  Opening either client
        # reveals one composite Companion in which Clips and Scratchpad remain
        # simultaneously visible.  Client actions are focus routes only.
        assert not _bool(companion_visible)
        assert window._companion_panel is None
        clips.activate(None); drain(Gtk)
        panel = window._companion_panel
        assert panel is not None
        assert _bool(companion_visible)
        assert panel.widget.get_visible()
        assert isinstance(panel.clients_paned, Gtk.Paned)
        assert panel.clients_paned.get_orientation() == Gtk.Orientation.VERTICAL
        assert panel.clients_paned.get_child1() is panel.clips_frame
        assert panel.clients_paned.get_child2() is panel.scratch_frame
        assert panel.clips_frame.get_visible()
        assert panel.scratch_frame.get_visible()
        assert panel.clips.widget.get_visible()
        assert panel.scratch.widget.get_visible()
        assert panel.active_client() == "clips"

        # Focusing Scratchpad must not replace or hide Clips.
        scratch.activate(None); drain(Gtk)
        assert panel.active_client() == "scratchpad"
        assert panel.clips_frame.get_visible()
        assert panel.scratch_frame.get_visible()
        assert panel.clips.widget.get_visible()
        assert panel.scratch.widget.get_visible()

        # Focusing Clips again must keep both clients visible.
        clips.activate(None); drain(Gtk)
        assert panel.active_client() == "clips"
        assert panel.clips_frame.get_visible()
        assert panel.scratch_frame.get_visible()

        # The Companion X changes the same action state and persists it.
        _click(panel.close_button, Gtk)
        assert not _bool(companion_visible)
        assert not panel.widget.get_visible()
        state = json.loads(window._companion_state_store.path.read_text(encoding="utf-8"))
        assert state.get("visible") is False

        companion_visible.change_state(GLib.Variant.new_boolean(True)); drain(Gtk)
        assert _bool(companion_visible)
        assert panel.widget.get_visible()
        assert panel.clips_frame.get_visible() and panel.scratch_frame.get_visible()

        # Workspace and Outline X buttons are projections of their existing stateful actions.
        assert _bool(workspace) and window.workspace_panel.widget.get_visible()
        _click(window.workspace_panel.close_button, Gtk)
        assert not _bool(workspace)
        assert not window.workspace_panel.widget.get_visible()
        window.show_all(); drain(Gtk)
        assert not window.workspace_panel.widget.get_visible()
        workspace.change_state(GLib.Variant.new_boolean(True)); drain(Gtk)
        assert _bool(workspace) and window.workspace_panel.widget.get_visible()

        assert _bool(outline) and window.outliner_panel.widget.get_visible()
        _click(window.outliner_panel.close_button, Gtk)
        assert not _bool(outline)
        assert not window.outliner_panel.widget.get_visible()
        window.show_all(); drain(Gtk)
        assert not window.outliner_panel.widget.get_visible()
        outline.change_state(GLib.Variant.new_boolean(True)); drain(Gtk)
        assert _bool(outline) and window.outliner_panel.widget.get_visible()

        # Focus is a temporary override. It disables the new visibility action without
        # overwriting the semantic preference, then restores the visible panel.
        focus.activate(None); drain(Gtk)
        assert not companion_visible.get_enabled()
        assert _bool(companion_visible)
        assert not panel.widget.get_visible()
        focus.activate(None); drain(Gtk)
        assert companion_visible.get_enabled()
        assert _bool(companion_visible)
        assert panel.widget.get_visible()

        print("PLUS_R1B_COMPANION_SIMULTANEOUS_CLIENTS=PASS", flush=True)
        print("PLUS_R1B_PANEL_CLOSE_ACTION_AUTHORITY=PASS", flush=True)
        print("PLUS_R1B_COMPANION_VISIBILITY_AUTHORITY=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
