from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

from tests.desktop.harness.runtime import drain, load_gtk3, text_of


def _widgets(root, Gtk, widget_type):
    found = []
    if isinstance(root, widget_type):
        found.append(root)
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            found.extend(_widgets(child, Gtk, widget_type))
    return found


def _palette_dialog(Gtk):
    matches = [
        window for window in Gtk.Window.list_toplevels()
        if isinstance(window, Gtk.Dialog) and window.get_title() == "Command Palette" and window.get_visible()
    ]
    assert len(matches) == 1, f"expected one visible Command Palette dialog, got {len(matches)}"
    return matches[0]


def _visible_palette_dialogs(Gtk):
    return [
        window for window in Gtk.Window.list_toplevels()
        if isinstance(window, Gtk.Dialog) and window.get_title() == "Command Palette" and window.get_visible()
    ]


def _selected_index(tree):
    model, tree_iter = tree.get_selection().get_selected()
    assert model is not None and tree_iter is not None
    indices = model.get_path(tree_iter).get_indices()
    assert indices
    return int(indices[0])


def _bool_state(action):
    state = action.get_state()
    assert state is not None
    return bool(state.get_boolean())


def _assert_accelerator_binding(app, Gtk, action_name: str, expected: str) -> str:
    """Compare accelerator semantics, never GTK's serialized spelling."""
    actual = tuple(str(value) for value in (app.get_accels_for_action(action_name) or ()))
    assert len(actual) == 1, f"{action_name}: expected one accelerator, got {actual!r}"
    expected_key, expected_mods = Gtk.accelerator_parse(expected)
    actual_key, actual_mods = Gtk.accelerator_parse(actual[0])
    assert expected_key != 0 and actual_key != 0
    assert (actual_key, actual_mods) == (expected_key, expected_mods), (
        f"{action_name}: accelerator semantics differ: expected={expected!r} "
        f"actual={actual[0]!r}"
    )
    reverse = tuple(str(value) for value in (app.get_actions_for_accel(actual[0]) or ()))
    assert reverse == (action_name,), (
        f"{action_name}: reverse accelerator authority mismatch for {actual[0]!r}: {reverse!r}"
    )
    return actual[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, _GLib, Gtk = load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication
    from graphium_plus.adapters.gtk.command_palette import collect_command_palette_entries

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    window.present(); drain(Gtk)

    try:
        # Live GtkApplication accelerator authority. We certify the binding table itself,
        # then exercise the bound Gio.Actions semantically instead of synthesizing key events.
        focus_accel = _assert_accelerator_binding(app, Gtk, "win.focus-mode", "F9")
        palette_accel = _assert_accelerator_binding(app, Gtk, "win.command-palette", "<Ctrl>K")
        print(f"P3_4_ACCELERATOR_RUNTIME_F9={focus_accel}", flush=True)
        print(f"P3_4_ACCELERATOR_RUNTIME_CTRL_K={palette_accel}", flush=True)
        focus_action = window.lookup_action("focus-mode")
        palette_action = window.lookup_action("command-palette")
        workspace_action = window.lookup_action("workspace-visible")
        assert focus_action is not None
        assert palette_action is not None
        assert workspace_action is not None
        workspace_entries = [entry for entry in collect_command_palette_entries(window, window._focus_menubar()) if entry.action_name == "win.workspace-visible"]
        assert len(workspace_entries) == 1
        assert (workspace_entries[0].label, workspace_entries[0].breadcrumb, workspace_entries[0].target, workspace_entries[0].shortcut) == ("Workspace", "View", None, "")
        assert window.lookup_action("companion-clips") is not None
        assert window.lookup_action("companion-scratchpad") is not None

        with tempfile.TemporaryDirectory(prefix="graphium-p3-4-") as tmp:
            doc = Path(tmp) / "p34.md"
            doc.write_text("alpha", encoding="utf-8")
            assert window.open_path(str(doc)); drain(Gtk)
            assert text_of(window.text_view) == "alpha"
            assert not window.core.session.modified

            # P3.2 real GTK construction: lazy Companion, two clients, document-local scratch binding.
            assert window._companion_panel is None
            window._actions["companion-clips"].activate(None); drain(Gtk)
            panel = window._companion_panel
            assert panel is not None
            assert panel.widget.get_visible()
            assert panel.active_client() == "clips"
            assert window.editor_companion_paned.get_child2() is panel.widget
            state_path = window._companion_state_store.path
            assert state_path.exists()
            window._actions["companion-scratchpad"].activate(None); drain(Gtk)
            assert panel.widget.get_visible()
            assert panel.active_client() == "scratchpad"
            assert panel._scratch_bound_path == Path(str(doc) + ".scratchpad.md")
            window._actions["companion-clips"].activate(None); drain(Gtk)
            assert panel.active_client() == "clips"
            persisted_before_focus = state_path.read_bytes()

            # Freeze non-default pre-Focus chrome/geometry so exact restoration is observable.
            window.workspace_paned.set_position(233)
            window.content_paned.set_position(271)
            total = int(window.editor_companion_paned.get_allocation().width or 900)
            window.editor_companion_paned.set_position(max(1, total - 205))
            workspace_action.change_state(_GLib.Variant.new_boolean(False)); drain(Gtk)
            workspace_state_path = window._workspace_state_store.path
            workspace_state_before_focus = workspace_state_path.read_bytes()
            toolbar_action = window.lookup_action("toolbar-visible")
            assert toolbar_action is not None and _bool_state(toolbar_action)
            toolbar_action.change_state(_GLib.Variant.new_boolean(False)); drain(Gtk)
            assert not window.toolbar.get_visible()
            assert not window.workspace_panel.widget.get_visible()
            pre_positions = (
                window.workspace_paned.get_position(),
                window.content_paned.get_position(),
                window.editor_companion_paned.get_position(),
            )
            pre_visibility = (
                window.workspace_panel.widget.get_visible(),
                window.outliner_panel.widget.get_visible(),
                window.toolbar.get_visible(),
                window.markdown_toolbar.get_visible(),
                window._focus_menubar().get_visible(),
                window._status_bar.get_visible(),
                window.text_view.line_numbers_visible,
            )
            pre_companion_visible = panel.widget.get_visible()
            pre_companion_no_show_all = panel.widget.get_no_show_all()
            pre_text = text_of(window.text_view)
            pre_modified = window.core.session.modified
            pre_undo = len(window.core.history.undo_stack)

            # P3.1: live Gio.Action bound to F9 enters Focus Mode without persistent-state writes.
            window.text_view.grab_focus(); window.present(); drain(Gtk)
            focus_action.activate(None); drain(Gtk)
            assert _bool_state(focus_action)
            assert window._focus_snapshot is not None
            assert not workspace_action.get_enabled()
            assert not toolbar_action.get_enabled()
            assert not window.lookup_action("outline-visible").get_enabled()
            assert not window.lookup_action("markdown-toolbar-visible").get_enabled()
            assert not _bool_state(workspace_action)
            assert not any(entry.action_name == "win.workspace-visible" for entry in collect_command_palette_entries(window, window._focus_menubar()))
            assert workspace_state_path.read_bytes() == workspace_state_before_focus
            assert not window.workspace_panel.widget.get_visible()
            assert not window.outliner_panel.widget.get_visible()
            assert not window.toolbar.get_visible()
            assert not window.markdown_toolbar.get_visible()
            assert not window._focus_menubar().get_visible()
            assert not window._status_bar.get_visible()
            assert not window.text_view.line_numbers_visible
            assert not panel.widget.get_visible()
            assert state_path.read_bytes() == persisted_before_focus
            assert text_of(window.text_view) == pre_text
            assert window.core.session.modified == pre_modified
            assert len(window.core.history.undo_stack) == pre_undo

            # P3.3: live command-palette Gio.Action bound to Ctrl+K opens inside Focus Mode.
            # Cancellation is exercised through Gtk.Window.close(), the semantic close route
            # which emits delete-event and therefore reaches the palette's cancel/focus restore.
            palette_action.activate(None); drain(Gtk)
            dialog = _palette_dialog(Gtk)
            entries = _widgets(dialog, Gtk, Gtk.Entry)
            trees = _widgets(dialog, Gtk, Gtk.TreeView)
            assert len(entries) == 1 and len(trees) == 1
            query, tree = entries[0], trees[0]
            assert dialog.get_focus() is query
            query.set_text("focus mode"); drain(Gtk)
            model = tree.get_model()
            assert len(model) == 1
            assert str(model[0][0]) == "Focus Mode"
            assert str(model[0][2]) == "F9"
            dialog.close(); drain(Gtk)
            assert not _visible_palette_dialogs(Gtk)
            assert window.get_focus() is window.text_view
            assert _bool_state(focus_action)
            assert state_path.read_bytes() == persisted_before_focus

            # Re-open through the same live action. Real GtkTreeSelection and row-activation
            # exercise the endpoint used by Enter without synthesizing a physical key event.
            palette_action.activate(None); drain(Gtk)
            dialog = _palette_dialog(Gtk)
            query = _widgets(dialog, Gtk, Gtk.Entry)[0]
            tree = _widgets(dialog, Gtk, Gtk.TreeView)[0]
            query.set_text("tab width"); drain(Gtk)
            tab_rows = tuple(
                (str(row[0]), str(row[1]))
                for row in tree.get_model()
            )
            expected_tab_rows = tuple(
                (label, "Edit > Tab Width")
                for label in ("2", "3", "4", "8", "Other…")
            )
            print(f"P3_4_PALETTE_TAB_WIDTH_ROWS={tab_rows!r}", flush=True)
            assert tab_rows == expected_tab_rows
            assert _selected_index(tree) == 0
            query.set_text("tab width 2"); drain(Gtk)
            assert len(tree.get_model()) == 1
            assert str(tree.get_model()[0][0]) == "2"
            assert str(tree.get_model()[0][1]) == "Edit > Tab Width"
            tab_action = window.lookup_action("tab-width")
            assert tab_action is not None
            before_tab = tab_action.get_state().get_string()
            path = Gtk.TreePath.new_from_string("0")
            tree.row_activated(path, tree.get_column(0)); drain(Gtk)
            assert not _visible_palette_dialogs(Gtk)
            assert tab_action.get_state().get_string() == "2"
            assert before_tab != "2" or window.text_view.tab_width == 2
            assert window.text_view.tab_width == 2
            assert _bool_state(focus_action)
            assert window._focus_snapshot is not None

            # Live F9-bound action exits and restores the exact pre-Focus projection, not defaults.
            window.text_view.grab_focus(); drain(Gtk)
            focus_action.activate(None); drain(Gtk)
            assert not _bool_state(focus_action)
            assert window._focus_snapshot is None
            assert workspace_action.get_enabled()
            assert not _bool_state(workspace_action)
            assert workspace_state_path.read_bytes() == workspace_state_before_focus
            assert (
                window.workspace_panel.widget.get_visible(),
                window.outliner_panel.widget.get_visible(),
                window.toolbar.get_visible(),
                window.markdown_toolbar.get_visible(),
                window._focus_menubar().get_visible(),
                window._status_bar.get_visible(),
                window.text_view.line_numbers_visible,
            ) == pre_visibility
            assert panel.widget.get_visible() == pre_companion_visible
            assert panel.widget.get_no_show_all() == pre_companion_no_show_all
            assert (
                window.workspace_paned.get_position(),
                window.content_paned.get_position(),
                window.editor_companion_paned.get_position(),
            ) == pre_positions
            assert state_path.read_bytes() == persisted_before_focus
            assert text_of(window.text_view) == pre_text
            assert window.core.session.modified == pre_modified
            assert len(window.core.history.undo_stack) == pre_undo

            print("PLUS_P3_1_TRUE_GTK_FOCUS_MODE_R2=PASS", flush=True)
            print("PLUS_P3_1_ACCELERATOR_AUTHORITY_F9=PASS", flush=True)
            print("PLUS_P3_2_TRUE_GTK_COMPANION_INTEGRATION=PASS", flush=True)
            print("PLUS_P3_3_TRUE_GTK_COMMAND_PALETTE=PASS", flush=True)
            print("PLUS_P3_3_ACCELERATOR_AUTHORITY_CTRL_K=PASS", flush=True)
            print("PLUS_P3_3_SEMANTIC_CANCEL_FOCUS_RESTORE=PASS", flush=True)
            print("PLUS_P3_3_GIO_ACTION_TARGET_DISPATCH=PASS", flush=True)
            print("PLUS_P3_FOCUS_COMPANION_STATE_NEUTRALITY=PASS", flush=True)
            print("PLUS_P3_5_FOCUS_WORKSPACE_OVERRIDE=PASS", flush=True)
            print("PLUS_P3_5_PALETTE_WORKSPACE_FILTER=PASS", flush=True)
            return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
