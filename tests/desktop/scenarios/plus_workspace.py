from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

from tests.desktop.harness.runtime import drain, load_gtk3, text_of




class _DecisionUI:
    def __init__(self, base, decision):
        self._base = base
        self._decision = decision
        self.prompts = []

    def confirm_unsaved_changes(self, action_label):
        self.prompts.append(action_label)
        return self._decision

    def __getattr__(self, name):
        return getattr(self._base, name)


def _p35_true_gtk_boundary(window, GLib, Gtk) -> int:
    """Irreducible P3.5 GTK proof: drive real actions and observe real widgets only."""
    workspace_action = window.lookup_action("workspace-visible")
    assert workspace_action is not None and workspace_action.get_state().get_boolean()
    view_item = next(item for item in window._focus_menubar().get_children() if item.get_label() == "View")
    workspace_menu = next(item for item in view_item.get_submenu().get_children() if item.get_label() == "Workspace")
    assert window.workspace_toggle.get_active() and workspace_menu.get_active()
    assert window.workspace_panel.widget.get_visible()
    assert not window.workspace_panel.widget.get_no_show_all()

    window.present(); drain(Gtk)
    pane_position = int(window.workspace_paned.get_position())
    assert pane_position > 0

    for index in range(20):
        workspace_action.activate(None); drain(Gtk)
        visible = bool(workspace_action.get_state().get_boolean())
        assert visible == bool((index + 1) % 2 == 0)
        assert window.workspace_toggle.get_active() == visible
        assert workspace_menu.get_active() == visible
        assert window.workspace_panel.widget.get_visible() == visible
        assert window.workspace_panel.widget.get_no_show_all() == (not visible)
        if visible:
            assert int(window.workspace_paned.get_position()) == pane_position

    workspace_action.change_state(GLib.Variant.new_boolean(False)); drain(Gtk)
    window.show_all(); drain(Gtk)
    assert not window.workspace_panel.widget.get_visible()
    assert window.workspace_panel.widget.get_no_show_all()
    assert not window.workspace_toggle.get_active()
    assert not workspace_menu.get_active()

    workspace_action.change_state(GLib.Variant.new_boolean(True)); drain(Gtk)
    assert window.workspace_panel.widget.get_visible()
    assert not window.workspace_panel.widget.get_no_show_all()
    assert window.workspace_toggle.get_active()
    assert workspace_menu.get_active()
    assert int(window.workspace_paned.get_position()) == pane_position

    print("PLUS_P3_5_TRUE_GTK_WORKSPACE_PROJECTION=PASS", flush=True)
    print("PLUS_P3_5_TRUE_GTK_SHOW_ALL_GUARD=PASS", flush=True)
    print("PLUS_P3_5_TRUE_GTK_PANE_STABILITY=PASS", flush=True)
    return 0


def _append_user_text(window, text: str) -> None:
    window.buffer.begin_user_action()
    try:
        window.buffer.insert(window.buffer.get_end_iter(), text)
    finally:
        window.buffer.end_user_action()


def _target_present(Gdk, target_list, name: str) -> bool:
    if target_list is None:
        return False
    result = target_list.find(Gdk.atom_intern(name, False))
    return bool(result[0]) if isinstance(result, tuple) else bool(result)


def _root_items(panel):
    model = panel.store
    values = []
    tree_iter = model.get_iter_first()
    while tree_iter is not None:
        item = model[tree_iter][2]
        if item is not None:
            values.append(item)
        tree_iter = model.iter_next(tree_iter)
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    Gdk, _GLib, Gtk = load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication
    from graphium_plus.adapters.gtk.workspace_panel import DND_TARGET_WORKSPACE_ITEM
    from graphium.application.file_lifecycle import UnsavedDecision

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    if os.environ.get("GRAPHIUM_P35_TRUE_GTK_ONLY") == "1":
        try:
            return _p35_true_gtk_boundary(window, _GLib, Gtk)
        finally:
            window.destroy(); drain(Gtk); app.quit()
    try:
        assert isinstance(window.workspace_paned, Gtk.Paned)
        assert window.workspace_paned.get_child1() is window.workspace_panel.widget
        assert window.workspace_paned.get_child2() is window.content_paned
        assert window.content_paned.get_child1() is window.outliner_panel.widget
        assert window.content_paned.get_child2() is window.editor_companion_paned
        assert window.editor_companion_paned.get_child1() is window.editor_box
        assert window.text_view.get_parent() is window._editor_scroller
        assert window.workspace.root is None
        assert window.workspace_panel.tree.get_enable_search()
        assert window.workspace_panel.tree.get_search_column() == 1
        assert window.workspace_panel.tree.get_selection().get_mode() == Gtk.SelectionMode.SINGLE
        assert not window.workspace_panel.tree.get_reorderable()
        assert _target_present(
            Gdk, window.workspace_panel.tree.drag_source_get_target_list(), DND_TARGET_WORKSPACE_ITEM
        )
        assert _target_present(
            Gdk, window.workspace_panel.tree.drag_dest_get_target_list(), DND_TARGET_WORKSPACE_ITEM
        )
        assert _target_present(
            Gdk, window.workspace_panel.root_label.drag_dest_get_target_list(), DND_TARGET_WORKSPACE_ITEM
        )
        assert window.workspace_toggle.get_active()
        assert window.workspace_panel.widget.get_visible()
        pane_position = window.workspace_paned.get_position()
        assert pane_position > 0
        assert pane_position < window.get_allocated_width() - pane_position

        window.workspace_toggle.set_active(False); drain(Gtk)
        assert not window.workspace_panel.widget.get_visible()
        window.workspace_toggle.set_active(True); drain(Gtk)
        assert window.workspace_panel.widget.get_visible()

        with tempfile.TemporaryDirectory(prefix="graphium-plus-workspace-") as td:
            root = Path(td)
            folder = root / "Chapter"; folder.mkdir()
            (folder / "deep.md").write_text("deep text", encoding="utf-8")
            (root / "draft.md").write_text("draft text", encoding="utf-8")
            (root / "image.bin").write_bytes(b"x")
            (root / ".hidden.md").write_text("hidden", encoding="utf-8")

            window._open_workspace_root(str(root)); drain(Gtk)
            root_items = _root_items(window.workspace_panel)
            assert [item.name for item in root_items] == ["Chapter", "draft.md", "image.bin"]
            assert all(item.name != "deep.md" for item in root_items)

            folder_path = window.workspace_panel.path_for_relative("Chapter")
            assert folder_path is not None
            draft_path = window.workspace_panel.path_for_relative("draft.md")
            assert draft_path is not None
            # Realized GTK geometry must classify a directory row as an INTO target and a
            # regular-file row as invalid; inter-row positions remain rejected by the panel.
            folder_area = window.workspace_panel.tree.get_cell_area(folder_path, None)
            draft_area = window.workspace_panel.tree.get_cell_area(draft_path, None)
            folder_drop = window.workspace_panel._drop_directory_at(
                max(1, folder_area.x + max(1, folder_area.width // 2)),
                folder_area.y + max(1, folder_area.height // 2),
            )
            file_drop = window.workspace_panel._drop_directory_at(
                max(1, draft_area.x + max(1, draft_area.width // 2)),
                draft_area.y + max(1, draft_area.height // 2),
            )
            assert folder_drop is not None and folder_drop.path == str(folder)
            assert file_drop is None
            window.workspace_panel.tree.expand_row(folder_path, False); drain(Gtk)
            assert window.workspace_panel.tree.row_expanded(folder_path)
            deep_tree_path = window.workspace_panel.path_for_relative("Chapter/deep.md")
            assert deep_tree_path is not None
            assert window.workspace_panel.select_relative_path("draft.md")

            window._refresh_workspace(); drain(Gtk)
            refreshed_path = window.workspace_panel.path_for_relative("Chapter")
            assert refreshed_path is not None
            assert window.workspace_panel.tree.row_expanded(refreshed_path)
            assert window.workspace_panel.path_for_relative("Chapter/deep.md") is not None
            selected = window.workspace_panel.selected_item()
            assert selected is not None and selected.relative_path == "draft.md"

            menu_labels = [
                child.get_label() for child in window.workspace_panel.context_menu.get_children()
                if isinstance(child, Gtk.MenuItem) and not isinstance(child, Gtk.SeparatorMenuItem)
            ]
            assert menu_labels == [
                "Open with Graphium Plus", "Reveal in File Manager",
                "New Text File", "New Folder", "Rename", "Duplicate", "Move to Trash",
            ]
            assert window.workspace_panel.context_open_in_graphium.get_sensitive()
            window.workspace_panel.context_open_in_graphium.activate(); drain(Gtk)
            assert text_of(window.text_view) == "draft text"
            assert window.core.session.logical_path == str(root / "draft.md")

            doc_path = window.workspace_panel.path_for_relative("draft.md")
            assert doc_path is not None
            reveal_calls = []
            window._file_manager_request = lambda method, path: reveal_calls.append((method, path))
            assert window.workspace_panel.select_relative_path("draft.md")
            window._reveal_workspace()
            assert reveal_calls[-1] == ("ShowItems", str(root / "draft.md"))
            window.workspace_panel.tree.get_selection().unselect_all()
            window._reveal_workspace()
            assert reveal_calls[-1] == ("ShowFolders", str(root))
            window.workspace_panel.tree.emit(
                "row-activated", doc_path, window.workspace_panel.tree.get_column(0)
            )
            drain(Gtk)
            assert text_of(window.text_view) == "draft text"
            assert window.core.session.logical_path == str(root / "draft.md")
            assert window._locate_active_document()
            drain(Gtk)
            selected = window.workspace_panel.selected_item()
            assert selected is not None and selected.path == str(root / "draft.md")

            deep = folder / "deep.md"
            assert window.open_path(str(deep)); drain(Gtk)
            window.workspace_panel.tree.collapse_all(); drain(Gtk)
            assert window._locate_active_document(); drain(Gtk)
            chapter_path = window.workspace_panel.path_for_relative("Chapter")
            assert chapter_path is not None and window.workspace_panel.tree.row_expanded(chapter_path)
            selected = window.workspace_panel.selected_item()
            assert selected is not None and selected.path == str(deep)

            assert window.open_path(str(root / "draft.md")); drain(Gtk)
            original_ui = window.core.lifecycle.ui
            _append_user_text(window, " modified"); drain(Gtk)
            assert window.core.session.modified
            cancel_ui = _DecisionUI(original_ui, UnsavedDecision.CANCEL)
            window.core.lifecycle.ui = cancel_ui
            cancelled = root / "cancelled.md"
            assert not window.create_workspace_text_file("cancelled", ".md", parent_path=str(root))
            drain(Gtk)
            assert not cancelled.exists()
            assert text_of(window.text_view) == "draft text modified"
            assert window.core.session.modified
            assert cancel_ui.prompts == ["open a new Workspace text file"]

            discard_ui = _DecisionUI(original_ui, UnsavedDecision.DISCARD)
            window.core.lifecycle.ui = discard_ui
            created = root / "created.md"
            assert window.create_workspace_text_file("created", ".md", parent_path=str(root))
            drain(Gtk)
            assert created.read_bytes() == b""
            assert window.core.session.logical_path == str(created)
            assert text_of(window.text_view) == ""
            assert not window.core.session.modified
            assert discard_ui.prompts == ["open a new Workspace text file"]
            selected = window.workspace_panel.selected_item()
            assert selected is not None and selected.path == str(created)

            expected_workspace_errors = []
            original_report_workspace_error = window._report_workspace_error
            window._report_workspace_error = expected_workspace_errors.append
            try:
                assert not window.create_workspace_text_file("created.md", ".txt", parent_path=str(root))
                drain(Gtk)
            finally:
                window._report_workspace_error = original_report_workspace_error
            assert len(expected_workspace_errors) == 1
            assert "File exists" in expected_workspace_errors[0]
            assert created.read_bytes() == b""
            assert window.core.session.logical_path == str(created)

            new_folder = root / "Notes"
            assert window.create_workspace_folder("Notes", parent_path=str(root))
            drain(Gtk)
            assert new_folder.is_dir() and not new_folder.is_symlink()
            selected = window.workspace_panel.selected_item()
            assert selected is not None and selected.path == str(new_folder)

            # Production move boundary: preserve an unrelated expanded branch, expand the
            # destination, and select the moved target after one real native GIO move.
            move_destination = root / "MoveDest"; move_destination.mkdir()
            move_source = root / "move-source.md"; move_source.write_text("move source", encoding="utf-8")
            window._refresh_workspace(); drain(Gtk)
            chapter_path = window.workspace_panel.path_for_relative("Chapter")
            assert chapter_path is not None
            window.workspace_panel.tree.expand_row(chapter_path, False); drain(Gtk)
            current_items = _root_items(window.workspace_panel)
            source_item = next(item for item in current_items if item.name == "move-source.md")
            destination_item = next(item for item in current_items if item.name == "MoveDest")
            assert window.move_workspace_item(source_item, destination_item); drain(Gtk)
            moved = move_destination / "move-source.md"
            assert moved.read_text(encoding="utf-8") == "move source" and not move_source.exists()
            chapter_path = window.workspace_panel.path_for_relative("Chapter")
            destination_path = window.workspace_panel.path_for_relative("MoveDest")
            assert chapter_path is not None and window.workspace_panel.tree.row_expanded(chapter_path)
            assert destination_path is not None and window.workspace_panel.tree.row_expanded(destination_path)
            selected = window.workspace_panel.selected_item()
            assert selected is not None and selected.path == str(moved)

            # Direct active-file move is namespace-only: no Save/Discard, no buffer rewrite,
            # dirty identity and native Undo remain live across the retarget.
            active = root / "active-move.md"; active.write_text("active text", encoding="utf-8")
            active_destination = root / "ActiveDest"; active_destination.mkdir()
            assert window.open_path(str(active)); drain(Gtk)
            _append_user_text(window, " modified"); drain(Gtk)
            before_text = text_of(window.text_view)
            before_ids = (window.core.session.current_editor_state_id, window.core.session.saved_editor_state_id)
            assert window.core.session.modified and window.core.editor.can_undo
            window._refresh_workspace(); drain(Gtk)
            current_items = _root_items(window.workspace_panel)
            active_item = next(item for item in current_items if item.name == "active-move.md")
            active_destination_item = next(item for item in current_items if item.name == "ActiveDest")
            assert window.move_workspace_item(active_item, active_destination_item); drain(Gtk)
            active_moved = active_destination / "active-move.md"
            assert not active.exists() and active_moved.exists()
            assert window.core.session.logical_path == str(active_moved)
            assert text_of(window.text_view) == before_text
            assert window.core.session.modified
            assert (window.core.session.current_editor_state_id, window.core.session.saved_editor_state_id) == before_ids
            assert window.core.editor.can_undo
            window._action_undo(); drain(Gtk)
            assert text_of(window.text_view) == "active text"
            assert not window.core.session.modified
            window.core.lifecycle.ui = original_ui
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
