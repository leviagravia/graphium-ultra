from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, load_gtk3, wait_until, text_of


def _set_caret(window, offset: int) -> None:
    target = window.buffer.get_iter_at_offset(int(offset))
    window.buffer.place_cursor(target)


def _caret(window) -> int:
    return window.buffer.get_iter_at_mark(window.buffer.get_insert()).get_offset()


def _rows(panel):
    rows = []
    tree_iter = panel.store.get_iter_first()
    while tree_iter is not None:
        rows.append(tuple(panel.store[tree_iter]))
        tree_iter = panel.store.iter_next(tree_iter)
    return rows


def _selected_source_start(panel):
    model, tree_iter = panel.tree.get_selection().get_selected()
    return None if tree_iter is None else int(model[tree_iter][1])


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
        assert isinstance(window.workspace_paned, Gtk.Paned)
        assert isinstance(window.content_paned, Gtk.Paned)
        assert window.workspace_paned.get_child1() is window.workspace_panel.widget
        assert window.workspace_paned.get_child2() is window.content_paned
        assert window.content_paned.get_child1() is window.outliner_panel.widget
        assert window.content_paned.get_child2() is window.editor_companion_paned
        assert window.editor_companion_paned.get_child1() is window.editor_box
        assert window.outliner_panel.tree.get_activate_on_single_click()
        assert window.outliner_panel.tree.get_selection().get_mode() == Gtk.SelectionMode.SINGLE
        assert window.workspace_panel.widget.get_visible()
        assert window.outliner_panel.widget.get_visible()
        window.workspace_toggle.set_active(False); drain(Gtk)
        assert not window.workspace_panel.widget.get_visible()
        assert window.outliner_panel.widget.get_visible()
        window.workspace_toggle.set_active(True); drain(Gtk)
        assert window.workspace_panel.widget.get_visible()

        text = "preface\n# Alpha\nbody\n## Beta\nmore\nGamma\n---\nend\n"
        window.core.editor.initialize_new_text(text, clean=True)
        assert wait_until(Gtk, lambda: len(_rows(window.outliner_panel)) == 3)
        rows = _rows(window.outliner_panel)
        assert [row[0] for row in rows] == ["Alpha", "    Beta", "    Gamma"]
        assert not window.core.session.modified
        assert not window.core.editor.can_undo

        beta_source = text.index("## Beta")
        beta_content = text.index("Beta")
        _set_caret(window, text.index("more")); drain(Gtk)
        assert _selected_source_start(window.outliner_panel) == beta_source

        gamma_path = Gtk.TreePath.new_from_indices([2])
        gamma_content = text.index("Gamma")
        window.outliner_panel.tree.row_activated(
            gamma_path, window.outliner_panel.tree.get_column(0)
        ); drain(Gtk)
        assert _caret(window) == gamma_content
        assert not window.core.session.modified
        assert not window.core.editor.can_undo

        # Stale projection: edit first, then invoke navigation before debounce refresh.
        old_projection = window._outline_projection
        assert old_projection is not None
        old_target = beta_content
        _set_caret(window, len(text)); drain(Gtk)
        before = _caret(window)
        window.buffer.begin_user_action()
        try:
            window.buffer.insert(window.buffer.get_start_iter(), "# Zero\n")
        finally:
            window.buffer.end_user_action()
        assert window._outliner_dirty
        window._navigate_outliner(old_target); drain(Gtk)
        assert _caret(window) == before + len("# Zero\n")
        assert wait_until(Gtk, lambda: len(_rows(window.outliner_panel)) == 4)
        assert _rows(window.outliner_panel)[0][0] == "Zero"
        assert window.core.session.modified

        # Cursor tracking uses the refreshed projection without another text mutation.
        current = text_of(window.text_view)
        beta_new_source = current.index("## Beta")
        _set_caret(window, current.index("more")); drain(Gtk)
        assert _selected_source_start(window.outliner_panel) == beta_new_source

        print("PLUS_A2C_TRUE_GTK_AUTOMATED=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
