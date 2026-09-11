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

    _Gdk, GLib, Gtk = load_gtk3()
    from graphium_ultra.adapters.gtk.application import GraphiumUltraApplication
    from graphium_ultra.adapters.gtk.markdown_viewer import MarkdownViewerSurface

    app = GraphiumUltraApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    source_buffer = window.buffer
    try:
        text = (
            "# Split Preview\n\n"
            "Needle body.\n\n"
            "| A | B |\n| --- | --- |\n| one | two |\n"
        )
        window.core.editor.initialize_new_text(text, clean=True); drain(Gtk)
        assert text_of(window.text_view) == text
        assert not window.core.session.modified
        assert not window.core.editor.can_undo

        split_action = window.lookup_action("source-preview-split")
        viewer_action = window.lookup_action("markdown-viewer")
        focus_action = window.lookup_action("focus-mode")
        assert split_action is not None and viewer_action is not None and focus_action is not None
        assert not split_action.get_state().get_boolean()
        assert window._markdown_split_surface is None

        split_action.change_state(GLib.Variant.new_boolean(True)); drain(Gtk)
        split = window._markdown_split_surface
        host = window._markdown_split_host
        assert isinstance(split, MarkdownViewerSurface)
        assert host is not None and host.get_visible()
        assert split_action.get_state().get_boolean()
        assert window.source_preview_paned.get_child1() is window.editor_box
        assert window.source_preview_paned.get_child2() is host
        assert source_buffer is window.buffer
        assert not split.text_view.get_editable()
        assert not split.text_view.get_cursor_visible()
        assert split._plan is not None
        assert "Needle body." in _viewer_text(split)
        assert len(split._embedded_widgets) == 1
        assert not window.core.session.modified
        assert not window.core.editor.can_undo
        assert window._markdown_viewer is None

        # Split search is the same U1.7 Viewer surface, not a second implementation.
        split._show_preview_search()
        split._search_entry.set_text("Needle"); drain(Gtk)
        assert len(split._search_hits) == 1
        split._close_preview_search(); drain(Gtk)

        # The existing separate Viewer may coexist. Both consume the same refresh plan.
        viewer_action.activate(None); drain(Gtk)
        viewer = window._markdown_viewer
        assert viewer is not None and viewer.get_visible()
        assert viewer.surface is not split
        assert viewer._plan is not None
        assert viewer._plan.source_sha256 == split._plan.source_sha256

        end = window.buffer.get_end_iter()
        window.buffer.begin_user_action()
        try:
            window.buffer.insert(end, "\nLive update")
        finally:
            window.buffer.end_user_action()
        assert wait_until(
            Gtk,
            lambda: "Live update" in _viewer_text(split) and "Live update" in _viewer_text(viewer),
        )
        assert window.core.session.modified
        assert window.core.editor.can_undo
        assert viewer._plan.source_sha256 == split._plan.source_sha256

        # Destroying the separate host must not retire the still-active split refresh owner.
        viewer.destroy(); drain(Gtk)
        assert window._markdown_viewer is None
        end = window.buffer.get_end_iter()
        window.buffer.begin_user_action()
        try:
            window.buffer.insert(end, "\nSplit survives")
        finally:
            window.buffer.end_user_action()
        assert wait_until(Gtk, lambda: "Split survives" in _viewer_text(split))

        # Focus temporarily hides the split while preserving the action state and surface identity.
        before_focus = _viewer_text(split)
        focus_action.activate(None); drain(Gtk)
        assert window._markdown_split_surface is split
        assert not host.get_visible()
        assert split_action.get_state().get_boolean()
        assert not split_action.get_enabled()
        assert window._markdown_viewer_focus_suspended

        end = window.buffer.get_end_iter()
        window.buffer.begin_user_action()
        try:
            window.buffer.insert(end, "\nFocus update")
        finally:
            window.buffer.end_user_action()
        drain_for(Gtk, seconds=0.24)
        assert _viewer_text(split) == before_focus
        assert window._markdown_viewer_focus_stale
        assert int(window._markdown_viewer_refresh_source_id) == 0

        focus_action.activate(None); drain(Gtk)
        assert split_action.get_enabled()
        assert split_action.get_state().get_boolean()
        assert host.get_visible()
        assert window._markdown_split_surface is split
        assert wait_until(Gtk, lambda: "Focus update" in _viewer_text(split))

        # User hide/show is session-only and reuses the already constructed preview surface.
        editor_text = text_of(window.text_view)
        editor_modified = window.core.session.modified
        editor_can_undo = window.core.editor.can_undo
        split_action.change_state(GLib.Variant.new_boolean(False)); drain(Gtk)
        assert not split_action.get_state().get_boolean()
        assert not host.get_visible()
        assert window._markdown_split_surface is split
        split_action.change_state(GLib.Variant.new_boolean(True)); drain(Gtk)
        assert split_action.get_state().get_boolean()
        assert host.get_visible()
        assert window._markdown_split_surface is split
        assert text_of(window.text_view) == editor_text
        assert window.core.session.modified == editor_modified
        assert window.core.editor.can_undo == editor_can_undo

        print("ULTRA_U1_8_TRUE_GTK_SOURCE_PREVIEW_SPLIT=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
