from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
ULTRA_WINDOW = ROOT / "graphium_ultra/adapters/gtk/window.py"
PLUS_WINDOW = ROOT / "graphium_plus/adapters/gtk/window.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, node) or ""


class UltraR1CFocusViewerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ultra = _source(ULTRA_WINDOW)
        self.plus = _source(PLUS_WINDOW)

    def test_plus_remains_unaware_of_ultra_viewer(self):
        self.assertNotIn("graphium_ultra", self.plus)
        self.assertNotIn("markdown-viewer", self.plus)
        self.assertNotIn("MarkdownViewerWindow", self.plus)

    def test_ultra_owns_transient_focus_state_only(self):
        init = _function(self.ultra, "__init__")
        for wanted in (
            "self._markdown_viewer_focus_suspended = False",
            "self._markdown_viewer_focus_was_visible = False",
            "self._markdown_viewer_focus_stale = False",
        ):
            self.assertIn(wanted, init)
        for forbidden in ("Store(", ".save(", "json", "XDG", "state /", "config /"):
            self.assertNotIn(forbidden, init)

    def test_enter_focus_wraps_plus_then_hides_and_disables_only_ultra_owned_surface(self):
        enter = _function(self.ultra, "_enter_focus_mode")
        self.assertIn("super()._enter_focus_mode(snapshot)", enter)
        self.assertIn("viewer.get_visible()", enter)
        self.assertIn("self._markdown_viewer_focus_suspended = True", enter)
        self.assertIn("self._markdown_viewer_focus_was_visible = viewer_was_visible", enter)
        self.assertIn("GLib.source_remove(source_id)", enter)
        self.assertIn("viewer.hide()", enter)
        self.assertIn('self._actions["markdown-viewer"].set_enabled(False)', enter)
        for forbidden in ("_ensure_markdown_viewer", "show_all", "present(", "save(", "open_path"):
            self.assertNotIn(forbidden, enter)

    def test_refresh_is_coalesced_into_stale_flag_while_focus_is_active(self):
        schedule = _function(self.ultra, "_schedule_markdown_viewer_refresh")
        self.assertIn("if self._markdown_viewer_focus_suspended:", schedule)
        self.assertIn("self._markdown_viewer_focus_stale = True", schedule)
        self.assertLess(
            schedule.index("if self._markdown_viewer_focus_suspended:"),
            schedule.index("GLib.timeout_add"),
        )

    def test_viewer_action_cannot_construct_a_window_while_focus_is_active(self):
        action = _function(self.ultra, "_action_markdown_viewer")
        self.assertIn("if self._markdown_viewer_focus_suspended:", action)
        self.assertLess(
            action.index("if self._markdown_viewer_focus_suspended:"),
            action.index("self._ensure_markdown_viewer()"),
        )

    def test_exit_focus_restores_only_previously_visible_viewer_and_refreshes_if_stale(self):
        exit_focus = _function(self.ultra, "_exit_focus_mode")
        self.assertIn("super()._exit_focus_mode(snapshot)", exit_focus)
        self.assertIn("self._markdown_viewer_focus_suspended = False", exit_focus)
        self.assertIn('self._actions["markdown-viewer"].set_enabled(True)', exit_focus)
        self.assertIn("if self._markdown_viewer_focus_was_visible and viewer is not None:", exit_focus)
        self.assertIn("if self._markdown_viewer_focus_stale:", exit_focus)
        self.assertIn("self._refresh_markdown_viewer_now()", exit_focus)
        self.assertIn("viewer.show()", exit_focus)
        self.assertIn("self.text_view.grab_focus()", exit_focus)
        self.assertNotIn("_ensure_markdown_viewer", exit_focus)
        self.assertNotIn("viewer.present", exit_focus)

    def test_focus_route_has_no_persistence_document_mutation_or_new_timer(self):
        route = "\n".join(
            _function(self.ultra, name)
            for name in ("_enter_focus_mode", "_exit_focus_mode")
        )
        for forbidden in (
            "GLib.timeout_add", "initialize_new_text", "begin_user_action",
            "buffer.insert", "buffer.set_text", "save(", "save_as", "open_path",
            "ViewSettings", "SurfaceStateStore", "workspace", "scratchpad",
        ):
            self.assertNotIn(forbidden, route)

    def test_true_gtk_boundary_uses_real_actions_without_modal_or_instrumentation_seams(self):
        scenario = _source(ROOT / "tests/desktop/scenarios/ultra_r1c_focus_viewer.py")
        runner = _source(ROOT / "tests/desktop/run.py")
        self.assertIn('window.lookup_action("markdown-viewer")', scenario)
        self.assertIn('window.lookup_action("focus-mode")', scenario)
        self.assertIn("window.buffer.begin_user_action()", scenario)
        self.assertIn("viewer.get_visible()", scenario)
        self.assertIn("ultra_r1c_focus_viewer", runner)
        for forbidden in ("open_path(", "show_warning", "Gtk.Dialog", "_ui.", "setattr(", "monkeypatch"):
            self.assertNotIn(forbidden, scenario)


if __name__ == "__main__":
    unittest.main()
