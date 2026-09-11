from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, load_gtk3, text_of


def _tool_buttons(toolbar, Gtk):
    return [item for item in toolbar.get_children() if isinstance(item, Gtk.ToolButton)]


def _set_selection(buffer, start: int, end: int) -> None:
    buffer.select_range(buffer.get_iter_at_offset(end), buffer.get_iter_at_offset(start))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, _GLib, Gtk = load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication
    from graphium_plus.markdown_commands import MARKDOWN_COMMANDS

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    try:
        # Exact editor-local geometry: scroller first, Markdown toolbar below it.
        assert window.content_paned.get_child2() is window.editor_companion_paned
        assert window.editor_companion_paned.get_child1() is window.editor_box
        editor_children = window.editor_box.get_children()
        assert editor_children == [window._editor_scroller, window.markdown_toolbar]
        assert window.markdown_toolbar.get_parent() is window.editor_box

        expected = [
            f"win.{spec.action}" for spec in MARKDOWN_COMMANDS
            if spec.toolbar_markup is not None
        ]
        buttons = _tool_buttons(window.markdown_toolbar, Gtk)
        assert [button.get_action_name() for button in buttons] == expected
        tooltip_children = [button.get_child() for button in buttons]
        assert all(child is not None for child in tooltip_children)
        assert [child.get_tooltip_text() for child in tooltip_children] == [
            spec.label for spec in MARKDOWN_COMMANDS if spec.toolbar_markup is not None
        ]

        # The toolbar is only a Gtk.Actionable surface over the already-certified action.
        window.core.editor.initialize_new_text("alpha", clean=True); drain(Gtk)
        _set_selection(window.buffer, 0, 5); drain(Gtk)
        bold = next(button for button in buttons if button.get_action_name() == "win.markdown-bold")
        click_target = bold.get_child()
        assert isinstance(click_target, Gtk.Button)
        assert click_target.get_action_name() == "win.markdown-bold"
        # Semantic GTK activation of the actual actionable internal Gtk.Button.
        # This mirrors mature action-backed toolbar tests and avoids deprecated
        # pointer-warp/event-synthesis infrastructure on the real desktop.
        click_target.clicked(); drain(Gtk)
        assert text_of(window.text_view) == "**alpha**"
        assert window.core.session.modified
        assert len(window.core.history.undo_stack) == 1
        window._actions["undo"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == "alpha"
        assert not window.core.session.modified

        print("PLUS_A2E_TRUE_GTK_AUTOMATED=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
