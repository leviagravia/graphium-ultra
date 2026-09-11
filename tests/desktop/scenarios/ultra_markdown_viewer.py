from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, load_gtk3, text_of, wait_until


def _viewer_windows(Gtk):
    return [
        window for window in Gtk.Window.list_toplevels()
        if window.get_title() == "Markdown Viewer" and window.get_visible()
    ]


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
        text = "# Live title\n\nBody with **strong** and [link](https://example.invalid).\n"
        window.core.editor.initialize_new_text(text, clean=True); drain(Gtk)
        assert text_of(window.text_view) == text
        assert not window.core.session.modified
        assert not window.core.editor.can_undo

        action = window.lookup_action("markdown-viewer")
        assert action is not None
        action.activate(None); drain(Gtk)
        viewers = _viewer_windows(Gtk)
        assert len(viewers) == 1
        viewer = viewers[0]
        assert viewer is window._markdown_viewer
        assert isinstance(viewer.text_view, Gtk.TextView)
        assert not viewer.text_view.get_editable()
        assert not viewer.text_view.get_cursor_visible()
        assert _viewer_text(viewer) == "Live title\n\nBody with strong and link."
        assert text_of(window.text_view) == text
        assert not window.core.session.modified
        assert not window.core.editor.can_undo

        # A real native edit changes only the document authority; the visible viewer
        # refreshes later from a fresh captured snapshot through the coalesced timer.
        end = window.buffer.get_end_iter()
        window.buffer.begin_user_action()
        try:
            window.buffer.insert(end, "\n- added")
        finally:
            window.buffer.end_user_action()
        assert window.core.session.modified
        assert wait_until(Gtk, lambda: _viewer_text(viewer).endswith("\n\n• added"))
        assert text_of(window.text_view).endswith("\n- added")

        old = viewer
        viewer.destroy(); drain(Gtk)
        assert window._markdown_viewer is None
        action.activate(None); drain(Gtk)
        viewers = _viewer_windows(Gtk)
        assert len(viewers) == 1
        assert viewers[0] is not old
        assert _viewer_text(viewers[0]).endswith("\n\n• added")

        print("ULTRA_U1_2_TRUE_GTK_NATIVE_MARKDOWN_VIEWER=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
