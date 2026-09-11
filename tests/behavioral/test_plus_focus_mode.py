from __future__ import annotations

import ast
from pathlib import Path
import unittest

from graphium.application.commands import COMMANDS


ROOT = Path(__file__).resolve().parents[2]
WINDOW_PATH = ROOT / "graphium_plus/adapters/gtk/window.py"
APPLICATION_PATH = ROOT / "graphium_plus/adapters/gtk/application.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_segment(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    return ast.get_source_segment(source, node) or ""


class PlusFocusModeContractTests(unittest.TestCase):
    def test_focus_snapshot_is_transient_window_state_with_exact_fields(self):
        source = _source(WINDOW_PATH)
        tree = ast.parse(source)
        snapshot = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "_FocusSnapshot"
        )
        fields = [
            node.target.id
            for node in snapshot.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        ]
        self.assertEqual(
            fields,
            [
                "workspace_visible", "workspace_no_show_all", "workspace_position",
                "outliner_visible", "outliner_no_show_all", "outliner_position",
                "toolbar_visible", "toolbar_no_show_all",
                "markdown_toolbar_visible", "markdown_toolbar_no_show_all",
                "menubar_visible", "menubar_no_show_all",
                "status_bar_visible", "status_bar_no_show_all",
                "line_numbers_visible",
                "companion_constructed",
                "companion_visible",
                "companion_no_show_all",
                "companion_position",
            ],
        )
        self.assertIn("@dataclass(frozen=True)", source)
        self.assertIn("self._focus_snapshot: _FocusSnapshot | None = None", source)

    def test_focus_action_is_plus_owned_stateful_view_menu_projection(self):
        source = _source(WINDOW_PATH)
        install = _function_segment(source, "_install_focus_mode_control")
        self.assertIn('Gio.SimpleAction.new_stateful(', install)
        self.assertIn('"focus-mode", None, GLib.Variant.new_boolean(False)', install)
        self.assertIn('action.connect("activate", self._action_focus_mode)', install)
        self.assertIn('Gtk.CheckMenuItem(label="Focus Mode")', install)
        self.assertIn('item.set_action_name("win.focus-mode")', install)
        self.assertIn('item.get_label() == "View"', install)
        self.assertFalse(any(spec.action == "focus-mode" for spec in COMMANDS))

    def test_focus_accelerator_is_f9_and_core_f11_remains_independent(self):
        app_source = _source(APPLICATION_PATH)
        startup = _function_segment(app_source, "do_startup")
        self.assertIn("super().do_startup()", startup)
        self.assertIn('self.set_accels_for_action("win.focus-mode", ["F9"])', startup)
        runtime_hits = []
        for root_name in ("graphium", "graphium_plus"):
            for path in (ROOT / root_name).rglob("*.py"):
                if '"F9"' in _source(path) or "'F9'" in _source(path):
                    runtime_hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(runtime_hits, ["graphium_plus/adapters/gtk/application.py"])
        core_window = _source(ROOT / "graphium/adapters/gtk/window.py")
        self.assertIn('self.fullscreen()', core_window)
        self.assertIn('self.unfullscreen()', core_window)

    def test_capture_reads_actual_visibility_no_show_all_and_pane_positions(self):
        source = _source(WINDOW_PATH)
        capture = _function_segment(source, "_capture_focus_snapshot")
        for wanted in (
            "workspace.get_visible()", "workspace.get_no_show_all()",
            "self.workspace_paned.get_position()",
            "outliner.get_visible()", "outliner.get_no_show_all()",
            "self.content_paned.get_position()",
            "self.toolbar.get_visible()", "self.toolbar.get_no_show_all()",
            "self.markdown_toolbar.get_visible()",
            "self.markdown_toolbar.get_no_show_all()",
            "menubar.get_visible()", "menubar.get_no_show_all()",
            "self._status_bar.get_visible()", "self._status_bar.get_no_show_all()",
            "self.text_view.line_numbers_visible",
        ):
            self.assertIn(wanted, capture)
        self.assertNotIn("ViewSettings", capture)
        self.assertNotIn("core.view_settings", capture)

    def test_entry_hides_only_owned_chrome_without_using_persistent_actions(self):
        source = _source(WINDOW_PATH)
        enter = _function_segment(source, "_enter_focus_mode")
        self.assertIn("self.workspace_panel.widget", enter)
        self.assertIn("self.outliner_panel.widget", enter)
        self.assertIn("self.toolbar", enter)
        self.assertIn("self.markdown_toolbar", enter)
        self.assertIn("menubar", enter)
        self.assertIn("self._status_bar", enter)
        self.assertIn("self._set_focus_hidden(widget)", enter)
        self.assertIn("self.text_view.set_line_numbers_visible(False)", enter)
        self.assertIn('self._actions["focus-mode"]', enter)
        for action_name in (
            "workspace-visible", "toolbar-visible", "outline-visible", "markdown-toolbar-visible"
        ):
            self.assertIn(action_name, enter)
        self.assertIn("set_enabled(False)", enter)
        self.assertIn("self.text_view.grab_focus()", enter)
        for forbidden in (
            'self._actions["status-bar"]', 'self._actions["line-numbers"]',
            "workspace_toggle.set_active",
            "_search_bar", "_external_info_bar", "fullscreen(", "unfullscreen(",
            "maximize(", "unmaximize(", "ViewSettings", "view_settings.update",
        ):
            self.assertNotIn(forbidden, enter)

    def test_exit_restores_captured_values_not_defaults_or_show_all(self):
        source = _source(WINDOW_PATH)
        exit_focus = _function_segment(source, "_exit_focus_mode")
        for field in (
            "workspace_visible", "workspace_no_show_all", "workspace_position",
            "outliner_visible", "outliner_no_show_all", "outliner_position",
            "toolbar_visible", "toolbar_no_show_all",
            "markdown_toolbar_visible", "markdown_toolbar_no_show_all",
            "menubar_visible", "menubar_no_show_all",
            "status_bar_visible", "status_bar_no_show_all", "line_numbers_visible",
        ):
            self.assertIn(f"snapshot.{field}", exit_focus)
        self.assertIn("self.workspace_paned.set_position(snapshot.workspace_position)", exit_focus)
        self.assertIn("self.content_paned.set_position(snapshot.outliner_position)", exit_focus)
        self.assertIn("self._focus_snapshot = None", exit_focus)
        for action_name in (
            "workspace-visible", "toolbar-visible", "outline-visible", "markdown-toolbar-visible"
        ):
            self.assertIn(action_name, exit_focus)
        self.assertIn("set_enabled(True)", exit_focus)
        self.assertIn("self.text_view.grab_focus()", exit_focus)
        self.assertNotIn("show_all()", exit_focus)
        self.assertNotIn("_DEFAULT_WORKSPACE_PANE_WIDTH", exit_focus)
        self.assertNotIn("_DEFAULT_OUTLINER_PANE_WIDTH", exit_focus)

    def test_toggle_has_one_snapshot_and_fails_closed_before_entry_projection(self):
        source = _source(WINDOW_PATH)
        action = _function_segment(source, "_action_focus_mode")
        self.assertIn("snapshot = self._focus_snapshot", action)
        self.assertIn("if snapshot is not None:", action)
        self.assertIn("self._exit_focus_mode(snapshot)", action)
        self.assertIn("snapshot = self._capture_focus_snapshot()", action)
        self.assertIn('self._ui.show_warning("Focus Mode"', action)
        self.assertIn("self._enter_focus_mode(snapshot)", action)
        self.assertLess(action.index("_capture_focus_snapshot"), action.index("_enter_focus_mode"))

    def test_focus_route_has_no_document_lifecycle_persistence_or_window_state_authority(self):
        source = _source(WINDOW_PATH)
        route = "\n".join(
            _function_segment(source, name)
            for name in (
                "_capture_focus_snapshot", "_set_focus_hidden", "_restore_focus_widget",
                "_enter_focus_mode", "_exit_focus_mode", "_action_focus_mode",
            )
        )
        for forbidden in (
            "core.view_settings.update", "_persist_view_setting", "save(", "save_as",
            "open_document", "reload", "initialize_new_text", "apply_programmatic",
            "begin_user_action", "buffer.set_text", "fullscreen(", "unfullscreen(",
            "maximize(", "unmaximize(", "GLib.timeout_add", "GLib.idle_add",
        ):
            self.assertNotIn(forbidden, route)

    def test_view_settings_authority_has_no_focus_persistence(self):
        settings_source = _source(ROOT / "graphium/application/view_settings.py")
        self.assertNotIn("focus_mode", settings_source)
        self.assertNotIn("focus-mode", settings_source)


if __name__ == "__main__":
    unittest.main()
