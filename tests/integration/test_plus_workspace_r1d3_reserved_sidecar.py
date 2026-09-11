from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from graphium_plus.scratchpad import scratchpad_path
from graphium_plus.workspace.controller import WorkspaceController
from graphium_plus.workspace.model import WorkspaceError
from graphium_plus.workspace.operations import plan_new_text_file


class PlusWorkspaceR1D3ReservedSidecarTests(unittest.TestCase):
    def test_sidecar_remains_visible_but_is_classified_managed_and_not_activatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = root / "draft.md"
            document.write_text("body\n", encoding="utf-8")
            sidecar = scratchpad_path(document)
            assert sidecar is not None
            sidecar.write_text("# Graphium Plus Scratchpad v1\n", encoding="utf-8")

            controller = WorkspaceController()
            listing = controller.bind_root(str(root))
            items = {item.name: item for item in listing.items}

            self.assertIn("draft.md.scratchpad.md", items)
            self.assertFalse(items["draft.md"].managed_companion)
            self.assertTrue(items["draft.md"].text_document)
            self.assertTrue(items["draft.md.scratchpad.md"].managed_companion)
            self.assertFalse(items["draft.md.scratchpad.md"].text_document)

            activation = controller.activation_for(items["draft.md.scratchpad.md"])
            self.assertEqual(activation.kind, "blocked")
            self.assertIn("managed with their document", activation.message)

    def test_locate_and_new_file_refuse_reserved_sidecar_as_workspace_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = root / "draft.md"
            document.write_text("body\n", encoding="utf-8")
            sidecar = scratchpad_path(document)
            assert sidecar is not None
            sidecar.write_text("notes\n", encoding="utf-8")

            controller = WorkspaceController()
            controller.bind_root(str(root))
            with self.assertRaisesRegex(WorkspaceError, "not Workspace documents"):
                controller.relative_path_for_document(str(sidecar))

            with self.assertRaisesRegex(WorkspaceError, "reserved for managed Scratchpad companions"):
                plan_new_text_file(str(root), str(root), "draft.md.scratchpad.md", suffix=".md")


if __name__ == "__main__":
    unittest.main()
