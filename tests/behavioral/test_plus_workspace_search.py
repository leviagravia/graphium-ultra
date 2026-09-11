from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from graphium_plus.workspace.search import (
    WorkspaceSearchBufferOverride,
    revalidate_disk_result,
    search_workspace,
)


class PlusWorkspaceSearchTests(unittest.TestCase):
    def test_recursive_search_is_hidden_symlink_free_unicode_aware_and_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "Notes"
            nested.mkdir()
            (root / "Alpha.md").write_text("One\nStraße topic\n", encoding="utf-8")
            (nested / "beta.txt").write_text("prefix STRASSE suffix\nsecond\n", encoding="utf-8")
            (root / ".hidden.md").write_text("STRASSE", encoding="utf-8")
            hidden_dir = root / ".hidden-dir"
            hidden_dir.mkdir()
            (hidden_dir / "inside.md").write_text("STRASSE", encoding="utf-8")
            if hasattr(os, "symlink"):
                link = root / "linked.md"
                link.symlink_to(nested / "beta.txt")

            report = search_workspace(str(root), "strasse")
            self.assertEqual([item.relative_path for item in report.results], ["Alpha.md", os.path.join("Notes", "beta.txt")])
            self.assertEqual([item.line for item in report.results], [2, 1])
            self.assertEqual(report.results[0].context, "Straße topic")
            self.assertEqual(report.results[1].context, "prefix STRASSE suffix")
            self.assertEqual(report.results[0].end - report.results[0].start, len("Straße"))
            self.assertFalse(report.truncated)
            self.assertEqual(report.files_considered, 2)

    def test_active_buffer_override_is_authoritative_and_state_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "draft.md"
            path.write_text("disk only\n", encoding="utf-8")
            report = search_workspace(
                str(root),
                "unsaved",
                active_buffer=WorkspaceSearchBufferOverride(
                    path=str(path), text="disk only\nUNSAVED thought\n", state_id=17
                ),
            )
            self.assertEqual(len(report.results), 1)
            result = report.results[0]
            self.assertEqual(result.source_kind, "active-buffer")
            self.assertEqual(result.source_state_id, 17)
            self.assertEqual(result.line, 2)
            self.assertEqual(result.context, "UNSAVED thought")
            self.assertFalse(revalidate_disk_result(report, result).current)

    def test_disk_result_revalidation_refuses_content_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "doc.md"
            path.write_text("alpha needle omega\n", encoding="utf-8")
            report = search_workspace(str(root), "needle")
            self.assertEqual(len(report.results), 1)
            result = report.results[0]
            current = revalidate_disk_result(report, result)
            self.assertTrue(current.current)
            self.assertEqual(current.text, "alpha needle omega\n")
            path.write_text("alpha changed omega\n", encoding="utf-8")
            stale = revalidate_disk_result(report, result)
            self.assertFalse(stale.current)
            self.assertIn("changed", stale.reason.lower())

    def test_case_sensitive_search_and_non_utf8_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("Needle needle\n", encoding="utf-8")
            (root / "bad.txt").write_bytes(b"\xff\xfe\xff")
            report = search_workspace(str(root), "Needle", match_case=True)
            self.assertEqual(len(report.results), 1)
            self.assertEqual(report.results[0].start, 0)
            self.assertTrue(any(item.relative_path == "bad.txt" for item in report.diagnostics))

    def test_search_uses_only_md_txt_regular_visible_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "doc.md").write_text("needle", encoding="utf-8")
            (root / "doc.TXT").write_text("needle", encoding="utf-8")
            (root / "image.bin").write_text("needle", encoding="utf-8")
            folder = root / "Folder"
            folder.mkdir()
            (folder / "deep.md").write_text("needle", encoding="utf-8")
            report = search_workspace(str(root), "needle")
            self.assertEqual(
                [item.relative_path for item in report.results],
                ["doc.md", "doc.TXT", os.path.join("Folder", "deep.md")],
            )


if __name__ == "__main__":
    unittest.main()
