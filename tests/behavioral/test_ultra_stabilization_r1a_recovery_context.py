from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
PLUS_WINDOW = ROOT / "graphium_plus/adapters/gtk/window.py"
CORE_WINDOW = ROOT / "graphium/adapters/gtk/window.py"
ULTRA_WINDOW = ROOT / "graphium_ultra/adapters/gtk/window.py"


def _functions(path: Path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return source, {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }


class R1ARecoveryContextTests(unittest.TestCase):
    def test_core_remains_the_only_recovery_execution_authority(self):
        core_source, core = _functions(CORE_WINDOW)
        plus_source, plus = _functions(PLUS_WINDOW)
        self.assertIn("self._startup_recovery.run(explicit_path)", core["offer_startup_recovery"])
        self.assertNotIn("_startup_recovery.run", plus["offer_startup_recovery"])
        self.assertIn("super().offer_startup_recovery(explicit_path)", plus["offer_startup_recovery"])
        self.assertEqual(core_source.count("def offer_startup_recovery("), 1)
        self.assertEqual(plus_source.count("def offer_startup_recovery("), 1)

    def test_plus_notifies_context_only_after_actual_recovery(self):
        _source, plus = _functions(PLUS_WINDOW)
        body = plus["offer_startup_recovery"]
        self.assertIn("before = self._document_context_path()", body)
        self.assertIn("result = super().offer_startup_recovery(explicit_path)", body)
        self.assertIn("if result.recovered:", body)
        self.assertIn("self._notify_document_context_change(before)", body)
        self.assertIn("return result", body)
        self.assertLess(body.index("super().offer_startup_recovery"), body.index("_notify_document_context_change"))
        self.assertNotIn("_rebind_companion_scratchpad()", body)
        self.assertNotIn("_schedule_markdown_viewer_refresh()", body)

    def test_ultra_inherits_recovery_route_and_only_extends_context_projection(self):
        source, ultra = _functions(ULTRA_WINDOW)
        self.assertNotIn("offer_startup_recovery", ultra)
        context = ultra["_on_document_context_changed"]
        self.assertIn("super()._on_document_context_changed", context)
        self.assertIn("_schedule_markdown_viewer_refresh", context)
        self.assertNotIn("_startup_recovery", source)

    def test_recovery_override_has_no_new_document_or_persistence_authority(self):
        _source, plus = _functions(PLUS_WINDOW)
        body = plus["offer_startup_recovery"]
        for forbidden in (
            "set_text(", "initialize_new_text", "save(", "open_document(",
            "ScratchpadStore", "MarkdownViewerWindow", "GLib.", "Gtk.",
        ):
            self.assertNotIn(forbidden, body)


if __name__ == "__main__":
    unittest.main()
