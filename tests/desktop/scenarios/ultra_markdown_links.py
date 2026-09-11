from __future__ import annotations

import argparse
import sys
from tests.desktop.harness.runtime import drain, load_gtk3, text_of


def _viewer_windows(Gtk):
    return [w for w in Gtk.Window.list_toplevels() if w.get_title() == "Markdown Viewer" and w.get_visible()]


def _table_labels(grid):
    return [child.get_child() for child in grid.get_children() if child.get_child() is not None]


def _assert_target_visible(viewer, target: str) -> None:
    from graphium_ultra.markdown_viewer import resolve_markdown_viewer_link
    action = resolve_markdown_viewer_link(viewer._plan, target)
    assert action is not None and action.offset is not None
    offset = viewer._translated_offset(action.offset, viewer._replacement_ranges)
    rect = viewer.text_view.get_iter_location(viewer.buffer.get_iter_at_offset(offset))
    visible = viewer.text_view.get_visible_rect()
    assert rect.y < visible.y + visible.height
    assert rect.y + max(1, rect.height) > visible.y


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)
    Gdk, _GLib, Gtk = load_gtk3()
    from graphium_ultra.adapters.gtk.application import GraphiumUltraApplication

    app = GraphiumUltraApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    try:
        filler = "\n".join(f"line {index}" for index in range(180))
        source = (
            "# Top\n\n"
            "[jump](#target) [web](https://example.invalid) [file](file:///tmp/no)\n\n"
            "| Item | Link |\n"
            "| --- | --- |\n"
            "| A | [table jump](#target) |\n\n"
            f"{filler}\n\n"
            "# Target\n"
            "done\n"
        )
        window.core.editor.initialize_new_text(source, clean=True); drain(Gtk)
        assert text_of(window.text_view) == source
        assert not window.core.session.modified
        assert not window.core.editor.can_undo

        action = window.lookup_action("markdown-viewer")
        assert action is not None
        action.activate(None); drain(Gtk)
        viewers = _viewer_windows(Gtk)
        assert len(viewers) == 1
        viewer = viewers[0]
        assert viewer is window._markdown_viewer
        assert not viewer.text_view.get_editable()
        targets = [target for _start, _end, target in viewer._active_links]
        assert "#target" in targets
        assert "https://example.invalid" in targets
        assert "file:///tmp/no" not in targets

        start = next(start for start, _end, target in viewer._active_links if target == "#target")
        rect = viewer.text_view.get_iter_location(viewer.buffer.get_iter_at_offset(start))
        window_x, window_y = viewer.text_view.buffer_to_window_coords(
            Gtk.TextWindowType.TEXT, rect.x + max(1, rect.width // 2), rect.y + max(1, rect.height // 2)
        )
        text_window = viewer.text_view.get_window(Gtk.TextWindowType.TEXT)
        assert text_window is not None
        for event_type in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE):
            assert Gdk.test_simulate_button(
                text_window, window_x, window_y, Gdk.BUTTON_PRIMARY, Gdk.ModifierType(0), event_type
            )
        drain(Gtk)
        _assert_target_visible(viewer, "#target")

        assert len(viewer._embedded_widgets) == 1
        labels = _table_labels(viewer._embedded_widgets[0])
        link_label = next(label for label in labels if label.get_text() == "table jump")
        viewer.scroller.get_vadjustment().set_value(0); drain(Gtk)
        assert link_label.emit("activate-link", "#target")
        drain(Gtk)
        _assert_target_visible(viewer, "#target")
        assert text_of(window.text_view) == source
        assert not window.core.session.modified
        assert not window.core.editor.can_undo
        viewer.destroy(); drain(Gtk)
        assert window._markdown_viewer is None
        print("ULTRA_U1_5_TRUE_GTK_ACTIVE_LINKS=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
