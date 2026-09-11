from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


class UltraS0StructuralStabilizationTests(unittest.TestCase):
    def test_plus_document_context_boundary_covers_all_path_changing_routes(self):
        source=(ROOT/"graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        tree=ast.parse(source)
        funcs={n.name: ast.get_source_segment(source,n) or "" for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)}
        for name in ("_action_new","_action_open","_action_open_recent","open_path","_action_save","_action_save_as"):
            self.assertIn("_notify_document_context_change", funcs[name], name)
        self.assertIn("_notify_document_context_change(before_document_context)", funcs["move_workspace_item"])
        self.assertIn("_rebind_companion_scratchpad", funcs["_on_document_context_changed"])

    def test_ultra_extends_context_boundary_for_viewer_without_lifecycle_override(self):
        source=(ROOT/"graphium_ultra/adapters/gtk/window.py").read_text(encoding="utf-8")
        tree=ast.parse(source)
        names={n.name for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)}
        for forbidden in ("_action_new","_action_open","_action_open_recent","open_path","_action_save","_action_save_as","move_workspace_item"):
            self.assertNotIn(forbidden,names)
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=="_on_document_context_changed")
        text=ast.get_source_segment(source,fn) or ""
        self.assertIn("super()._on_document_context_changed",text)
        self.assertIn("_schedule_markdown_viewer_refresh",text)

    def test_first_save_is_not_an_invalidation_hole(self):
        source=(ROOT/"graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertIn("def _action_save(self, *_args)",source)
        block=source[source.index("    def _action_save(self, *_args)"):source.index("    def _action_save_as", source.index("    def _action_save(self, *_args)"))]
        self.assertIn("before = self._document_context_path()",block)
        self.assertIn("super()._action_save(*_args)",block)
        self.assertIn("self._notify_document_context_change(before)",block)

    def test_focus_disables_companion_actions_and_persists_semantic_preference(self):
        source=(ROOT/"graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        tree=ast.parse(source)
        funcs={n.name: ast.get_source_segment(source,n) or "" for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)}
        for name in ("companion-clips","companion-scratchpad"):
            self.assertIn(name, funcs["_enter_focus_mode"])
            self.assertIn(name, funcs["_exit_focus_mode"])
        destroy=funcs["_on_companion_destroy"]
        self.assertIn("self._companion_preferred_visible",destroy)
        self.assertNotIn("widget.get_visible",destroy)

    def test_plus_source_has_no_ultra_symbols(self):
        all_plus="\n".join(p.read_text(encoding="utf-8") for p in (ROOT/"graphium_plus").rglob("*.py"))
        self.assertNotIn("graphium_ultra",all_plus)
        self.assertNotIn("MarkdownViewerWindow",all_plus)

    def test_shared_plus_ui_projects_current_product_identity(self):
        window=(ROOT/"graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        panel=(ROOT/"graphium_plus/adapters/gtk/workspace_panel.py").read_text(encoding="utf-8")
        self.assertIn("product_name=self._identity.product_name",window)
        self.assertIn('f"Open with {self._product_name}"',panel)
        self.assertIn("self._identity.product_name",window)


if __name__ == "__main__":
    unittest.main()
