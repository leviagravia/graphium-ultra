from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, load_gtk3, text_of


def _menu_labels(window, Gtk):
    menubar = next(
        child for child in window._root_box.get_children() if isinstance(child, Gtk.MenuBar)
    )
    commands = next(item for item in menubar.get_children() if item.get_label() == "Commands")
    notes = next(
        item for item in commands.get_submenu().get_children()
        if isinstance(item, Gtk.MenuItem) and item.get_label() == "Notes & Citations"
    )
    return [
        child.get_label() for child in notes.get_submenu().get_children()
        if isinstance(child, Gtk.MenuItem)
    ]


def _set_caret(buffer, offset: int) -> None:
    it = buffer.get_iter_at_offset(offset)
    buffer.place_cursor(it)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, _GLib, Gtk = load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    try:
        assert _menu_labels(window, Gtk)[:3] == [
            "Insert Footnote",
            "Insert Inline Note",
            "Go to Footnote / Reference",
        ]
        assert "Quick Cite…" in _menu_labels(window, Gtk)
        assert "Insert Citation…" in _menu_labels(window, Gtk)
        for name in ("insert-footnote", "insert-inline-note", "go-to-footnote", "check-footnotes"):
            assert name in window._actions

        # Real Gtk.TextBuffer + existing Graphium programmatic-edit authority.
        window.core.editor.initialize_new_text("Claim.", clean=True); drain(Gtk)
        _set_caret(window.buffer, len("Claim.")); drain(Gtk)
        pre_insert = window.buffer.get_iter_at_mark(window.buffer.get_insert()).get_offset()
        pre_bound = window.buffer.get_iter_at_mark(window.buffer.get_selection_bound()).get_offset()
        assert (pre_insert, pre_bound) == (len("Claim."), len("Claim."))
        assert text_of(window.text_view) == "Claim."
        assert not window.core.session.modified
        initial_state = window.core.history.current_state_id
        window._actions["insert-footnote"].activate(None); drain(Gtk)
        expected = "Claim.[^1]\n\n[^1]: "
        assert text_of(window.text_view) == expected
        assert window.core.session.modified
        assert window.core.history.current_state_id > initial_state
        assert len(window.core.history.undo_stack) == 1
        cursor = window.buffer.get_iter_at_mark(window.buffer.get_insert()).get_offset()
        assert cursor == len(expected)

        window._actions["undo"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == "Claim."
        assert not window.core.session.modified
        window._actions["redo"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == expected

        # Inline note must be one Undo unit and preserve literal source syntax.
        window.core.editor.initialize_new_text("alpha beta", clean=True); drain(Gtk)
        start = window.buffer.get_iter_at_offset(0)
        end = window.buffer.get_iter_at_offset(5)
        window.buffer.select_range(end, start)
        window._actions["insert-inline-note"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == "^[alpha] beta"
        assert len(window.core.history.undo_stack) == 1
        insert = window.buffer.get_iter_at_mark(window.buffer.get_insert()).get_offset()
        bound = window.buffer.get_iter_at_mark(window.buffer.get_selection_bound()).get_offset()
        assert (insert, bound) == (7, 2)
        window._actions["undo"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == "alpha beta"
        assert not window.core.session.modified

        # Navigation is projection-only: no text/history/dirty mutation.
        nav_text = "A[^n].\n\n[^n]: body\n"
        window.core.editor.initialize_new_text(nav_text, clean=True); drain(Gtk)
        before_state = window.core.history.current_state_id
        before_undo = len(window.core.history.undo_stack)
        _set_caret(window.buffer, nav_text.index("[^n]") + 1)
        window._actions["go-to-footnote"].activate(None); drain(Gtk)
        cursor = window.buffer.get_iter_at_mark(window.buffer.get_insert()).get_offset()
        assert cursor == nav_text.index("body")
        assert text_of(window.text_view) == nav_text
        assert window.core.history.current_state_id == before_state
        assert len(window.core.history.undo_stack) == before_undo
        assert not window.core.session.modified

        _set_caret(window.buffer, nav_text.index("body"))
        window._actions["go-to-footnote"].activate(None); drain(Gtk)
        cursor = window.buffer.get_iter_at_mark(window.buffer.get_insert()).get_offset()
        assert cursor == nav_text.index("[^n]")
        assert text_of(window.text_view) == nav_text
        assert not window.core.session.modified

        print("PLUS_A2_TRUE_GTK_AUTOMATED=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
