from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, load_gtk3


GTK_3_24_41_SCROLL_ANIMATION_US = 200_000


def _plan(text: str):
    from graphium_plus.markdown import build_markdown_document_map
    from graphium_ultra.markdown_viewer import build_markdown_viewer_plan
    return build_markdown_viewer_plan(text, build_markdown_document_map(text))


def _offset_visible(viewer, offset: int) -> bool:
    rect = viewer.text_view.get_iter_location(viewer.buffer.get_iter_at_offset(offset))
    visible = viewer.text_view.get_visible_rect()
    return rect.y < visible.y + visible.height and rect.y + max(1, rect.height) > visible.y


def _wait_for_scroll(Gtk, GLib, viewer, trigger) -> None:
    adjustment = viewer.scroller.get_vadjustment()
    clock = viewer.scroller.get_frame_clock()
    assert clock is not None
    animations = bool(Gtk.Settings.get_default().get_property("gtk-enable-animations"))
    loop = GLib.MainLoop()
    first_event = {"time": None}

    def on_adjustment(_adjustment):
        if first_event["time"] is None:
            first_event["time"] = int(clock.get_frame_time())

    def on_after_paint(_clock, *_args):
        first = first_event["time"]
        if first is None:
            return
        now = int(clock.get_frame_time())
        if not animations or now >= first + GTK_3_24_41_SCROLL_ANIMATION_US:
            loop.quit()

    adj_handler = adjustment.connect("value-changed", on_adjustment)
    frame_handler = clock.connect("after-paint", on_after_paint)
    updating = False
    try:
        clock.begin_updating(); updating = True
        trigger()
        loop.run()
    finally:
        adjustment.disconnect(adj_handler)
        clock.disconnect(frame_handler)
        if updating:
            clock.end_updating()
    assert first_event["time"] is not None
    drain(Gtk)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, GLib, Gtk = load_gtk3()
    from graphium_ultra.adapters.gtk.markdown_viewer import MarkdownViewerWindow

    parent = Gtk.Window()
    viewer = MarkdownViewerWindow(parent)
    text = (
        "# Preview Search\n\n" + "opening line\n" * 70 +
        "Body Needle here\n\n" + "middle line\n" * 65 +
        "| A | B |\n| --- | --- |\n| cell | Needle table |\n\n" +
        "tail line\n" * 20
    )
    plan1 = _plan(text)
    viewer.render(plan1)
    viewer.show_all(); drain(Gtk)
    try:
        before_chars = viewer.buffer.get_char_count()
        before_source = viewer._plan.source_sha256
        viewer._show_preview_search()
        viewer._search_entry.set_text("needle")
        drain(Gtk)
        assert len(viewer._search_hits) == 2
        assert viewer._search_current_index == -1

        _wait_for_scroll(Gtk, GLib, viewer, lambda: viewer._search_move(1))
        first = viewer._search_hits[viewer._search_current_index]
        first_segment = viewer._search_segments[first.segment_index]
        assert first_segment.body_start is not None
        first_offset = first_segment.body_start + first.start
        assert _offset_visible(viewer, first_offset)
        assert viewer._search_selected_label is None

        _wait_for_scroll(Gtk, GLib, viewer, lambda: viewer._search_move(1))
        second = viewer._search_hits[viewer._search_current_index]
        second_segment = viewer._search_segments[second.segment_index]
        assert second_segment.table_label is viewer._search_selected_label
        assert second_segment.table_anchor_offset is not None
        assert _offset_visible(viewer, second_segment.table_anchor_offset)
        assert "Needle" in second_segment.table_label.get_text()

        assert viewer.buffer.get_char_count() == before_chars
        assert viewer._plan.source_sha256 == before_source

        plan2 = _plan("preface\n" * 10 + text)
        viewer.render(plan2)
        drain(Gtk)
        assert viewer._search_entry.get_text() == "needle"
        assert len(viewer._search_hits) == 2
        assert viewer._search_current_index == -1
        assert viewer._search_selected_label is None
        viewer._cancel_reading_position_restore(clear_pending=True)

        viewer._search_match_case.set_active(True)
        viewer._search_entry.set_text("NEEDLE")
        drain(Gtk)
        assert not viewer._search_hits
        viewer._search_match_case.set_active(False)
        drain(Gtk)
        assert len(viewer._search_hits) == 2

        print("ULTRA_U1_7_TRUE_GTK_PREVIEW_SEARCH=PASS", flush=True)
        return 0
    finally:
        viewer.destroy(); parent.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
