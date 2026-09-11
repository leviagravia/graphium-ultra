from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from graphium_plus.workspace.search import WorkspaceSearchBufferOverride, search_workspace


class PlusWorkspaceR1D3ReservedSidecarSearchTests(unittest.TestCase):
    def test_find_in_workspace_excludes_managed_sidecars_without_hiding_normal_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "doc.md").write_text("needle document\n", encoding="utf-8")
            (root / "doc.md.scratchpad.md").write_text("needle private companion\n", encoding="utf-8")
            (root / "ordinary.md").write_text("needle ordinary\n", encoding="utf-8")

            report = search_workspace(str(root), "needle")
            self.assertEqual(
                [result.relative_path for result in report.results],
                ["doc.md", "ordinary.md"],
            )
            self.assertEqual(report.files_considered, 2)

    def test_active_buffer_override_cannot_reintroduce_reserved_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sidecar = root / "doc.md.scratchpad.md"
            sidecar.write_text("disk marker\n", encoding="utf-8")

            report = search_workspace(
                str(root),
                "unsaved-marker",
                active_buffer=WorkspaceSearchBufferOverride(
                    path=str(sidecar),
                    text="unsaved-marker\n",
                    state_id=9,
                ),
            )
            self.assertEqual(report.results, ())
            self.assertEqual(report.files_considered, 0)


if __name__ == "__main__":
    unittest.main()
