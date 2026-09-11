from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class WorkspaceScratchpadTransactionContractTests(unittest.TestCase):
    def test_scratchpad_suffix_has_one_runtime_naming_authority(self):
        hits = []
        for path in (ROOT / "graphium_plus").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if ".scratchpad.md" in text:
                hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(hits, ["graphium_plus/scratchpad.py"])

    def test_gtk_workspace_callbacks_do_not_implement_a_second_sidecar_filesystem_path(self):
        source = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertNotIn("scratchpad_path(", source)
        self.assertNotIn("shutil.", source)
        for call in (
            "self.workspace_gio.rename(plan)",
            "self.workspace_gio.move(plan)",
            "self.workspace_gio.duplicate(plan)",
            "self.workspace_gio.trash(plan)",
        ):
            self.assertIn(call, source)

    def test_transaction_boundary_contains_no_permanent_delete_fallback_for_trash(self):
        source = (ROOT / "graphium_plus/workspace/gio.py").read_text(encoding="utf-8")
        trash_start = source.index("    def trash(self, plan: WorkspaceTrashPlan)")
        trash_end = source.index("    @staticmethod\n    def _write_all", trash_start)
        trash = source[trash_start:trash_end]
        self.assertNotIn("os.unlink", trash)
        self.assertNotIn(".delete(", trash)
        self.assertIn("companion_committed", trash)


if __name__ == "__main__":
    unittest.main()
