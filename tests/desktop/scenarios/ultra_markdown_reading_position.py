from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, load_gtk3


GTK_3_24_41_SCROLL_ANIMATION_US = 200_000


def _plan(text: str):
    from graphium_plus.markdown import build_markdown_document_map
    from graphium_ultra.markdown_viewer import build_markdown_viewer_plan
    return build_markdown_viewer_plan(text, build_markdown_document_map(text))


def _translated_target_offset(viewer, plan, identifier: str, fraction: float) -> int:
    anchors = list(plan.heading_anchors)
    index = next(i for i, anchor in enumerate(anchors) if anchor.identifier == identifier)
    start = anchors[index].offset
    end = anchors[index + 1].offset if index + 1 < len(anchors) else len(plan.text)
    source_offset = round(start + (end - start) * fraction)
    return viewer._translated_offset(source_offset, viewer._replacement_ranges)


def _offset_visible(viewer, offset: int) -> bool:
    rect = viewer.text_view.get_iter_location(viewer.buffer.get_iter_at_offset(offset))
    visible = viewer.text_view.get_visible_rect()
    return rect.y < visible.y + visible.height and rect.y + max(1, rect.height) > visible.y


def _wait_for_animated_restore(Gtk, GLib, viewer, trigger) -> None:
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
    from graphium_ultra.markdown_viewer import resolve_markdown_viewer_reading_position

    parent = Gtk.Window()
    viewer = MarkdownViewerWindow(parent)
    base = (
        "# Intro\n\n" + "intro line\n" * 40 +
        "\n| A | B |\n| --- | --- |\n| one | two |\n\n" +
        "# Reading\n\n" + "reading line\n" * 55 +
        "\n# Tail\n\n" + "tail line\n" * 20
    )
    plan1 = _plan(base)
    viewer.render(plan1)
    viewer.show_all(); drain(Gtk)
    try:
        target1 = _translated_target_offset(viewer, plan1, "reading", 0.62)
        target_rect = viewer.text_view.get_iter_location(viewer.buffer.get_iter_at_offset(target1))
        viewer.scroller.get_vadjustment().set_value(float(target_rect.y))
        drain(Gtk)
        assert _offset_visible(viewer, target1)
        position1 = viewer._capture_reading_position()
        assert position1 is not None and position1.anchor_identifier == "reading"

        # Refresh after content growth above the semantic section: the same section
        # fraction, not the old scrollbar value, must remain visible.
        refreshed = "preface\n" * 35 + base.replace(
            "# Reading\n\n", "# Reading\n\n" + "new lead\n" * 3, 1
        )
        plan2 = _plan(refreshed)
        expected2 = None
        def refresh_trigger():
            nonlocal expected2
            viewer.render(plan2)
            plan_offset = resolve_markdown_viewer_reading_position(plan2, position1)
            expected2 = viewer._translated_offset(plan_offset, viewer._replacement_ranges)
        _wait_for_animated_restore(Gtk, GLib, viewer, refresh_trigger)
        assert expected2 is not None and _offset_visible(viewer, expected2)
        assert viewer._pending_reading_position is None

        # Focus-like hide/show with a stale refresh: capture before hiding, rebuild
        # while hidden, then restore only when the Viewer is shown again.
        position2 = viewer._capture_reading_position()
        assert position2 is not None and position2.anchor_identifier == "reading"
        viewer.remember_reading_position()
        viewer.hide(); drain(Gtk)
        plan3 = _plan("front\n" * 20 + refreshed)
        viewer.render(plan3); drain(Gtk)
        assert viewer._pending_reading_position is not None
        plan_offset3 = resolve_markdown_viewer_reading_position(plan3, position2)
        expected3 = viewer._translated_offset(plan_offset3, viewer._replacement_ranges)
        _wait_for_animated_restore(Gtk, GLib, viewer, viewer.show)
        assert _offset_visible(viewer, expected3)
        assert viewer._pending_reading_position is None

        print("ULTRA_U1_6_TRUE_GTK_READING_POSITION=PASS", flush=True)
        return 0
    finally:
        viewer.destroy(); parent.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
