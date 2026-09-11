from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, load_gtk3, text_of


def _menu_item(parent, Gtk, label):
    return next(
        item for item in parent.get_children()
        if isinstance(item, Gtk.MenuItem) and item.get_label() == label
    )


def _menu_tree(window, Gtk):
    menubar = next(
        child for child in window._root_box.get_children() if isinstance(child, Gtk.MenuBar)
    )
    commands = _menu_item(menubar, Gtk, "Commands")
    markdown = _menu_item(commands.get_submenu(), Gtk, "Markdown")
    return menubar, markdown.get_submenu()


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
    from graphium_plus.markdown_commands import MARKDOWN_COMMANDS, MARKDOWN_COMMAND_GROUPS

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    try:
        menubar, markdown_menu = _menu_tree(window, Gtk)
        labels = [item.get_label() for item in menubar.get_children()]
        assert "Commands" in labels
        assert labels.index("Commands") < labels.index("Help")
        groups = [
            item.get_label() for item in markdown_menu.get_children()
            if isinstance(item, Gtk.MenuItem)
        ]
        markdown_groups = [group for group in MARKDOWN_COMMAND_GROUPS if group != "Academic"]
        assert groups == markdown_groups

        menu_actions = []
        for group_name in markdown_groups:
            group_item = _menu_item(markdown_menu, Gtk, group_name)
            for item in group_item.get_submenu().get_children():
                if isinstance(item, Gtk.MenuItem):
                    menu_actions.append(item.get_action_name())
        expected_actions = [
            f"win.{spec.action}" for spec in MARKDOWN_COMMANDS if spec.group != "Academic"
        ]
        assert menu_actions == expected_actions
        for spec in MARKDOWN_COMMANDS:
            assert spec.action in window._actions

        document = _menu_item(menubar, Gtk, "Document")
        assert all(
            child.get_label() != "Academic"
            for child in document.get_submenu().get_children()
            if isinstance(child, Gtk.MenuItem)
        )

        # Representative real Gtk.TextBuffer edit through the shared action authority.
        window.core.editor.initialize_new_text("alpha", clean=True); drain(Gtk)
        _set_selection(window.buffer, 0, 5); drain(Gtk)
        window._actions["markdown-bold"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == "**alpha**"
        assert window.core.session.modified
        assert len(window.core.history.undo_stack) == 1
        window._actions["undo"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == "alpha"
        assert not window.core.session.modified
        window._actions["redo"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == "**alpha**"

        # Representative structural command on the actual GTK buffer.
        window.core.editor.initialize_new_text("Title\n---\nbody", clean=True); drain(Gtk)
        window.buffer.place_cursor(window.buffer.get_iter_at_offset(2)); drain(Gtk)
        window._actions["markdown-heading-3"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == "### Title\nbody"
        assert len(window.core.history.undo_stack) == 1

        print("PLUS_A2D_TRUE_GTK_AUTOMATED=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
