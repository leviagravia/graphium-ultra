from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, load_gtk3, text_of, wait_until


def _bool(action) -> bool:
    return bool(action.get_state().get_boolean())


def _set_selection(buffer, start: int, end: int) -> None:
    buffer.select_range(buffer.get_iter_at_offset(end), buffer.get_iter_at_offset(start))


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
        outline = window.lookup_action("outline-visible")
        toolbar = window.lookup_action("toolbar-visible")
        markdown_toolbar = window.lookup_action("markdown-toolbar-visible")
        workspace = window.lookup_action("workspace-visible")
        assert all(action is not None for action in (outline, toolbar, markdown_toolbar, workspace))
        assert all(_bool(action) for action in (outline, toolbar, markdown_toolbar, workspace))

        # Outline is a real optional surface and remains hidden across parent show_all().
        window.content_paned.set_position(267); drain(Gtk)
        outline.change_state(GLib.Variant.new_boolean(False)); drain(Gtk)
        assert not window.outliner_panel.widget.get_visible()
        assert window.outliner_panel.widget.get_no_show_all()
        window.show_all(); drain(Gtk)
        assert not window.outliner_panel.widget.get_visible()

        # Hidden Outline has no structural refresh work; it only becomes stale.
        window.core.editor.initialize_new_text("# Alpha\nbody\n", clean=True); drain(Gtk)
        assert window._outliner_dirty
        assert int(window._outliner_refresh_source_id) == 0
        outline.change_state(GLib.Variant.new_boolean(True)); drain(Gtk)
        assert wait_until(Gtk, lambda: not window._outliner_dirty)
        assert window.outliner_panel.widget.get_visible()
        assert window.content_paned.get_position() == 267

        # Focus Mode is a temporary hide: it must suspend Outline work without
        # changing the persisted preference, then refresh once when restored.
        focus = window.lookup_action("focus-mode")
        assert focus is not None
        focus.activate(None); drain(Gtk)
        assert not window.outliner_panel.widget.get_visible()
        assert int(window._outliner_refresh_source_id) == 0
        window.core.editor.initialize_new_text("# Focus\nbody\n", clean=True); drain(Gtk)
        assert window._outliner_dirty
        assert int(window._outliner_refresh_source_id) == 0
        focus.activate(None); drain(Gtk)
        assert window.outliner_panel.widget.get_visible()
        assert wait_until(Gtk, lambda: not window._outliner_dirty)
        assert int(window._outliner_refresh_source_id) == 0

        # Both higher-edition toolbars are independently optional and show_all-safe.
        toolbar.change_state(GLib.Variant.new_boolean(False)); drain(Gtk)
        markdown_toolbar.change_state(GLib.Variant.new_boolean(False)); drain(Gtk)
        assert not window.toolbar.get_visible()
        assert not window.markdown_toolbar.get_visible()
        window.show_all(); drain(Gtk)
        assert not window.toolbar.get_visible()
        assert not window.markdown_toolbar.get_visible()
        toolbar.change_state(GLib.Variant.new_boolean(True)); drain(Gtk)
        markdown_toolbar.change_state(GLib.Variant.new_boolean(True)); drain(Gtk)
        assert window.toolbar.get_visible()
        assert window.markdown_toolbar.get_visible()

        # The Markdown toolbar overflow proxy must have a real label and activate
        # the same existing Gio.Action; this is the mechanism behind GTK's arrow menu.
        buttons = [item for item in window.markdown_toolbar.get_children() if isinstance(item, Gtk.ToolButton)]
        assert buttons and all(button.get_label() for button in buttons)
        bold = next(button for button in buttons if button.get_action_name() == "win.markdown-bold")
        proxy = bold.retrieve_proxy_menu_item()
        assert isinstance(proxy, Gtk.MenuItem)
        assert proxy.get_label() == "Bold"
        window.core.editor.initialize_new_text("alpha", clean=True); drain(Gtk)
        _set_selection(window.buffer, 0, 5); drain(Gtk)
        proxy.activate(); drain(Gtk)
        assert text_of(window.text_view) == "**alpha**"
        window.lookup_action("undo").activate(None); drain(Gtk)
        assert text_of(window.text_view) == "alpha"
        assert not window.core.session.modified

        print("PLUS_SURFACE_MODULARITY_TRUE_GTK=PASS", flush=True)
        print("MARKDOWN_TOOLBAR_OVERFLOW_PROXY=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
