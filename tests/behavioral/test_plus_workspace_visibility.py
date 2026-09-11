from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WINDOW = ROOT / "graphium_plus/adapters/gtk/window.py"
STATE = ROOT / "graphium_plus/workspace/state.py"


def segment(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.get_source_segment(source, node) or ""


class PlusWorkspaceVisibilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.window = WINDOW.read_text(encoding="utf-8")
        cls.state = STATE.read_text(encoding="utf-8")

    def test_single_stateful_action_owns_menu_toolbar_and_persistence(self):
        install = segment(self.window, "_install_workspace_visibility_control")
        toggle = segment(self.window, "_build_workspace_toggle")
        self.assertIn('"workspace-visible", None, GLib.Variant.new_boolean(self._workspace_preferred_visible)', install)
        self.assertIn('action.connect("change-state", self._change_workspace_visibility)', install)
        self.assertIn('Gtk.CheckMenuItem(label="Workspace")', install)
        self.assertIn('menu_item.set_action_name("win.workspace-visible")', install)
        self.assertIn('button.set_action_name("win.workspace-visible")', toggle)
        self.assertNotIn('connect("toggled"', toggle)
        self.assertNotIn("_on_workspace_visibility_toggled", self.window)
        self.assertNotIn("workspace_toggle.set_active", self.window)

    def test_projection_is_presentation_only_and_preserves_live_width(self):
        project = segment(self.window, "_project_workspace_visibility")
        change = segment(self.window, "_change_workspace_visibility")
        self.assertIn("panel.set_no_show_all(not visible)", project)
        self.assertIn("panel.show_all()", project)
        self.assertIn("panel.hide()", project)
        self.assertIn("self.workspace_paned.set_position(self._workspace_pane_width)", project)
        self.assertIn("self.workspace_paned.get_position()", change)
        self.assertIn("self._workspace_pane_width = position", change)
        self.assertLess(change.index("self._workspace_state_store.save(visible)"), change.index("action.set_state"))
        for forbidden in ("bind_root(", "refresh(", "load_directory(", "render_root(", "recent_workspaces.touch", "show_all() on"):
            self.assertNotIn(forbidden, project + change)

    def test_save_failure_fails_closed_and_locate_uses_same_action(self):
        change = segment(self.window, "_change_workspace_visibility")
        locate = segment(self.window, "_locate_active_document")
        self.assertIn('self._ui.show_warning("Workspace"', change)
        self.assertIn("return", change[change.index("except Exception as exc:"):change.index("if current and")])
        self.assertIn('action = self._actions["workspace-visible"]', locate)
        self.assertIn("action.change_state(GLib.Variant.new_boolean(True))", locate)
        self.assertNotIn("workspace_toggle", locate)

    def test_startup_state_is_plus_owned_visibility_only_and_fail_soft(self):
        init = segment(self.window, "__init__")
        store = next(node for node in ast.walk(ast.parse(self.state)) if isinstance(node, ast.ClassDef) and node.name == "WorkspacePanelStateStore")
        store_source = ast.get_source_segment(self.state, store) or ""
        self.assertIn('WorkspacePanelStateStore(self._xdg_paths.state / "workspace-panel.json")', init)
        self.assertIn("self._workspace_state_store.load()", init)
        self.assertIn("return True", store_source)
        self.assertIn('{"schema": 1, "visible": bool(visible)}', store_source)
        for forbidden in ("root", "width", "watcher", "sqlite", "thread"):
            self.assertNotIn(forbidden, store_source.lower())

    def test_true_gtk_boundary_is_irreducible_and_instrumentation_free(self):
        scenario_path = ROOT / "tests/desktop/scenarios/plus_workspace.py"
        scenario = scenario_path.read_text(encoding="utf-8")
        boundary = segment(scenario, "_p35_true_gtk_boundary")
        self.assertIn('workspace_action.activate(None)', boundary)
        self.assertIn('window.show_all()', boundary)
        self.assertIn('workspace_toggle.get_active()', boundary)
        self.assertIn('workspace_menu.get_active()', boundary)
        self.assertIn('workspace_paned.get_position()', boundary)
        for forbidden in (
            "._ui", "_workspace_state_store", "bind_root", "refresh(", "render_root",
            "recent_workspaces", "_file_manager_request", "_report_workspace_error", "lambda",
            "setattr(", "delattr(",
        ):
            self.assertNotIn(forbidden, boundary)
        tree = ast.parse(boundary)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                self.assertFalse(any(isinstance(target, ast.Attribute) for target in targets))
        main = segment(scenario, "main")
        self.assertIn('GRAPHIUM_P35_TRUE_GTK_ONLY', main)
        self.assertIn('_p35_true_gtk_boundary(window, _GLib, Gtk)', main)


if __name__ == "__main__":
    unittest.main()
