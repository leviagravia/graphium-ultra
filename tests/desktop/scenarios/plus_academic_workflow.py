from __future__ import annotations

import argparse
from pathlib import Path
import sys

from tests.desktop.harness.runtime import drain, load_gtk3, text_of


def _menu_item(parent, Gtk, label):
    return next(
        item for item in parent.get_children()
        if isinstance(item, Gtk.MenuItem) and item.get_label() == label
    )


def _find_widget(root, Gtk, widget_type):
    if isinstance(root, widget_type):
        return root
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            found = _find_widget(child, Gtk, widget_type)
            if found is not None:
                return found
    return None


def _respond_to_dialog(Gtk, *, title: str, response, select_first: bool = False):
    for window in Gtk.Window.list_toplevels():
        if not isinstance(window, Gtk.Dialog) or window.get_title() != title:
            continue
        if select_first:
            tree = _find_widget(window, Gtk, Gtk.TreeView)
            assert tree is not None
            assert len(tree.get_model()) >= 1
            tree.get_selection().select_path(0)
            model, tree_iter = tree.get_selection().get_selected()
            assert model is not None and tree_iter is not None
        window.response(response)
        return False
    raise AssertionError(f"dialog not found: {title}")


def _set_caret(buffer, offset: int) -> None:
    buffer.place_cursor(buffer.get_iter_at_offset(offset))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, GLib, Gtk = load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication
    from graphium_plus.references import ReferenceRecord

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    try:
        menubar = next(
            child for child in window._root_box.get_children() if isinstance(child, Gtk.MenuBar)
        )
        commands = _menu_item(menubar, Gtk, "Commands")
        workflow = [
            item.get_label() for item in commands.get_submenu().get_children()
            if isinstance(item, Gtk.MenuItem)
        ]
        assert workflow == ["Markdown", "Notes & Citations", "References", "Review"]
        for name in (
            "reference-library", "find-reference", "quick-cite",
            "insert-citation", "check-citations",
        ):
            assert name in window._actions

        assert window.reference_store._writer is window.core.writer
        assert window.reference_store.path.name == "references.md"
        assert window.reference_store.path.parent.name == "graphium-plus"

        empty = window.reference_store.load()
        assert not empty.token.exists
        saved = window.reference_store.save(
            (
                ReferenceRecord(
                    key="alpha2020", title="Alpha Study",
                    authors=("Alpha, A.",), year="2020", doi="10.1000/alpha",
                ),
            ),
            empty.token,
        )
        assert saved.saved
        assert saved.snapshot.records[0].key == "alpha2020"

        # Real GTK Reference Library dialog opens and Cancel is non-mutating.
        token_before = saved.snapshot.token
        GLib.idle_add(
            lambda: _respond_to_dialog(
                Gtk, title="Reference Library", response=Gtk.ResponseType.CANCEL
            )
        )
        window._actions["reference-library"].activate(None); drain(Gtk)
        assert window.reference_store.load().token == token_before

        # Real picker + real Gio action + real Gtk.TextBuffer transaction.
        window.core.editor.initialize_new_text("Claim.", clean=True); drain(Gtk)
        _set_caret(window.buffer, len("Claim.")); drain(Gtk)
        before_insert = window.buffer.get_iter_at_mark(window.buffer.get_insert()).get_offset()
        before_bound = window.buffer.get_iter_at_mark(window.buffer.get_selection_bound()).get_offset()
        assert (before_insert, before_bound) == (len("Claim."), len("Claim."))
        assert not window.core.session.modified

        GLib.idle_add(
            lambda: _respond_to_dialog(
                Gtk, title="Quick Cite", response=Gtk.ResponseType.OK, select_first=True
            )
        )
        window._actions["quick-cite"].activate(None); drain(Gtk)
        expected = "Claim.[@alpha2020]"
        assert text_of(window.text_view) == expected
        assert window.core.session.modified
        assert len(window.core.history.undo_stack) == 1

        window._actions["undo"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == "Claim."
        assert not window.core.session.modified
        window._actions["redo"].activate(None); drain(Gtk)
        assert text_of(window.text_view) == expected

        print("PLUS_A3B_TRUE_GTK_AUTOMATED=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
