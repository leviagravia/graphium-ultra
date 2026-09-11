from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, drain_for, load_gtk3, text_of, wait_until


def _viewer_text(viewer) -> str:
    buffer = viewer.buffer
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, _GLib, Gtk = load_gtk3()
    from graphium_ultra.adapters.gtk.application import GraphiumUltraApplication

    app = GraphiumUltraApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    try:
        text = "# Focus viewer\n\nBody.\n"
        window.core.editor.initialize_new_text(text, clean=True); drain(Gtk)
        assert text_of(window.text_view) == text
        assert not window.core.session.modified

        viewer_action = window.lookup_action("markdown-viewer")
        focus_action = window.lookup_action("focus-mode")
        assert viewer_action is not None and focus_action is not None
        assert viewer_action.get_enabled()

        # Open the real Ultra viewer before Focus. Focus must hide it without
        # destroying it and suspend the coalesced refresh boundary.
        viewer_action.activate(None); drain(Gtk)
        viewer = window._markdown_viewer
        assert viewer is not None and viewer.get_visible()
        before = _viewer_text(viewer)
        assert before == "Focus viewer\n\nBody."

        focus_action.activate(None); drain(Gtk)
        assert window._markdown_viewer is viewer
        assert not viewer.get_visible()
        assert not viewer_action.get_enabled()
        assert window._markdown_viewer_focus_suspended

        # A real native edit during Focus makes the hidden viewer stale but
        # must not arm a GLib refresh timer or update the hidden projection.
        end = window.buffer.get_end_iter()
        window.buffer.begin_user_action()
        try:
            window.buffer.insert(end, "\n- added")
        finally:
            window.buffer.end_user_action()
        drain_for(Gtk, seconds=0.24)
        assert window.core.session.modified
        assert window.core.editor.can_undo
        assert window._markdown_viewer is viewer
        assert not viewer.get_visible()
        assert _viewer_text(viewer) == before
        assert window._markdown_viewer_focus_stale
        assert int(window._markdown_viewer_refresh_source_id) == 0

        # Leaving Focus restores only the pre-existing visible viewer and
        # refreshes it from the current editor authority before showing it.
        focus_action.activate(None); drain(Gtk)
        assert viewer_action.get_enabled()
        assert not window._markdown_viewer_focus_suspended
        assert window._markdown_viewer is viewer
        assert viewer.get_visible()
        assert wait_until(Gtk, lambda: _viewer_text(viewer).endswith("\n\n• added"))
        assert int(window._markdown_viewer_refresh_source_id) == 0
        assert text_of(window.text_view).endswith("\n- added")

        # If no Viewer exists before Focus, Focus must not create one on exit;
        # activating the disabled action while Focus is active is also inert.
        viewer.destroy(); drain(Gtk)
        assert window._markdown_viewer is None
        focus_action.activate(None); drain(Gtk)
        assert not viewer_action.get_enabled()
        assert window._markdown_viewer is None
        viewer_action.activate(None); drain(Gtk)
        assert window._markdown_viewer is None
        focus_action.activate(None); drain(Gtk)
        assert viewer_action.get_enabled()
        assert window._markdown_viewer is None

        print("ULTRA_R1C_TRUE_GTK_FOCUS_VIEWER_OVERRIDE=PASS", flush=True)
        print("ULTRA_R1C_TRUE_GTK_VIEWER_REFRESH_SUSPEND_RESUME=PASS", flush=True)
        print("ULTRA_R1C_TRUE_GTK_NO_VIEWER_CONSTRUCTION_DURING_FOCUS=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
