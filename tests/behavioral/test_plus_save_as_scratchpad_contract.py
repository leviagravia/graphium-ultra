from __future__ import annotations

import ast
from pathlib import Path
import unittest


class PlusSaveAsScratchpadContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]
        cls.core_window = (cls.root / "graphium/adapters/gtk/window.py").read_text(encoding="utf-8")
        cls.plus_window = (cls.root / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        cls.service = (cls.root / "graphium_plus/scratchpad_save_as.py").read_text(encoding="utf-8")

    @staticmethod
    def _function(source: str, name: str) -> str:
        tree = ast.parse(source)
        node = next(item for item in ast.walk(tree) if isinstance(item, ast.FunctionDef) and item.name == name)
        return ast.get_source_segment(source, node) or ""

    def test_core_exposes_one_save_as_execution_helper_without_plus_dependency(self):
        helper = self._function(self.core_window, "_perform_save_as")
        action = self._function(self.core_window, "_action_save_as")
        self.assertIn("self.core.lifecycle.save_as(path)", helper)
        self.assertIn("self._perform_save_as()", action)
        self.assertNotIn("graphium_plus", self.core_window)

    def test_plus_save_as_preflights_companion_before_primary_then_copies_after_success(self):
        body = self._function(self.plus_window, "_action_save_as")
        self.assertIn("self._ui.choose_save_path(before)", body)
        self.assertIn("self._prepare_scratchpad_save_as_plan(before, target_path)", body)
        self.assertIn("self._perform_save_as(target_path)", body)
        self.assertIn("self._commit_scratchpad_save_as_plan(plan)", body)
        self.assertIn("self._notify_document_context_change(before)", body)
        self.assertLess(body.index("_prepare_scratchpad_save_as_plan"), body.index("_perform_save_as"))
        self.assertLess(body.index("_perform_save_as"), body.index("_commit_scratchpad_save_as_plan"))
        self.assertLess(body.rindex("_commit_scratchpad_save_as_plan"), body.index("_notify_document_context_change"))

    def test_first_save_from_untitled_routes_same_plus_save_as_guard(self):
        body = self._function(self.plus_window, "_action_save")
        self.assertIn("if before is None", body)
        self.assertIn("self._action_save_as(*_args)", body)
        self.assertIn("super()._action_save(*_args)", body)

    def test_gtk_callback_contains_no_physical_companion_file_implementation(self):
        for name in ("_action_save_as", "_prepare_scratchpad_save_as_plan", "_commit_scratchpad_save_as_plan"):
            body = self._function(self.plus_window, name)
            for forbidden in ("os.open(", "os.link(", "os.unlink(", "shutil.", "Gio.File.new_for_path"):
                self.assertNotIn(forbidden, body, (name, forbidden))
        self.assertIn("writer=self.core.writer", self._function(self.plus_window, "_prepare_scratchpad_save_as_plan"))
        self.assertIn("writer=self.core.writer", self._function(self.plus_window, "_commit_scratchpad_save_as_plan"))

    def test_service_reuses_r1d1_strong_token_and_injected_core_writer_port(self):
        self.assertIn("workspace_path_token", self.service)
        self.assertIn("CheckedTextAuthority", self.service)
        self.assertIn("class CompanionWriterPort(Protocol)", self.service)
        self.assertIn("writer.observe_target", self.service)
        self.assertIn("writer.commit", self.service)
        for forbidden in (
            "GuardedFileWriter", "shutil.copy", "Gio.File",
            "open(plan.target_path", "Path.write_bytes",
        ):
            self.assertNotIn(forbidden, self.service)

    def test_save_as_companion_failure_is_explicit_and_preserves_original_claim(self):
        body = self._function(self.plus_window, "_commit_scratchpad_save_as_plan")
        self.assertIn("Save As completed without Scratchpad copy", body)
        self.assertIn("original Scratchpad was left untouched", body)


    def test_post_commit_companion_failure_quarantines_unverified_target_before_rebind(self):
        commit = self._function(self.plus_window, "_commit_scratchpad_save_as_plan")
        action = self._function(self.plus_window, "_action_save_as")
        reason = self._function(self.plus_window, "_companion_scratchpad_unavailable_reason")
        panel = (self.root / "graphium_plus/adapters/gtk/companion_panel.py").read_text(encoding="utf-8")
        self.assertIn("scratchpad_save_as_target_safe_to_bind", commit)
        self.assertIn("_quarantine_scratchpad_binding", commit)
        self.assertIn("_quarantine_unverified_scratchpad_target", action)
        self.assertLess(action.rindex("_quarantine_unverified_scratchpad_target"), action.index("_notify_document_context_change"))
        self.assertIn("_scratchpad_binding_quarantine", reason)
        self.assertIn("scratchpad_unavailable_reason", panel)
        self.assertIn("if blocked_reason", panel)
        self.assertIn("self._scratch_store=None", panel)

    def test_quarantine_is_transient_presentation_state_not_new_persistent_store(self):
        self.assertIn("self._scratchpad_binding_quarantine: dict[str, str] = {}", self.plus_window)
        self.assertNotIn("scratchpad-quarantine", self.plus_window)
        self.assertNotIn("scratchpad-conflict", self.plus_window)
        self.assertNotIn("json", self._function(self.plus_window, "_quarantine_scratchpad_binding"))

    def test_user_guides_document_guarded_copy_not_move(self):
        for relative in (
            "docs/user/GRAPHIUM_PLUS_USER_GUIDE.txt",
            "docs/user/GRAPHIUM_ULTRA_USER_GUIDE.txt",
        ):
            text = (self.root / relative).read_text(encoding="utf-8")
            normalized = " ".join(text.split())
            self.assertIn("Save As preserves the original Scratchpad", normalized)
            self.assertIn("copies its saved sidecar bytes", normalized)
            self.assertIn("never overwrites an existing destination Scratchpad", normalized)
            self.assertIn("will not automatically adopt an unverified destination Scratchpad", normalized)


if __name__ == "__main__":
    unittest.main()
