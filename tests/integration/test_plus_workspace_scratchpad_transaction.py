from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from graphium_plus.scratchpad import scratchpad_path
from graphium_plus.workspace.controller import WorkspaceController
from graphium_plus.workspace.gio import (
    WorkspaceDuplicateResult,
    WorkspaceGioAdapter,
)
from graphium_plus.workspace.model import WorkspaceError


class FakeError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class _FakeInfo:
    def __init__(self, path: str, *, can_trash: bool = True):
        observed = os.lstat(path)
        self._symlink = stat.S_ISLNK(observed.st_mode)
        self._directory = stat.S_ISDIR(observed.st_mode)
        self._can_trash = can_trash

    def get_is_symlink(self):
        return self._symlink

    def get_file_type(self):
        return FakeGio.FileType.DIRECTORY if self._directory else FakeGio.FileType.REGULAR

    def has_attribute(self, name):
        return name == "access::can-trash"

    def get_attribute_boolean(self, name):
        return self._can_trash if name == "access::can-trash" else False


class FakeFile:
    rename_fail_paths: set[str] = set()
    move_modes: dict[str, str] = {}
    trash_modes: dict[str, str] = {}
    queried: list[str] = []
    trashed: list[str] = []

    def __init__(self, path: str):
        self.path = os.path.abspath(path)

    @classmethod
    def new_for_path(cls, path: str):
        return cls(path)

    @classmethod
    def reset(cls):
        cls.rename_fail_paths = set()
        cls.move_modes = {}
        cls.trash_modes = {}
        cls.queried = []
        cls.trashed = []

    def get_path(self):
        return self.path

    def query_info(self, _attrs, _flags, _cancellable):
        self.__class__.queried.append(self.path)
        mode = self.__class__.trash_modes.get(self.path, "ok")
        return _FakeInfo(self.path, can_trash=(mode != "cannot-trash"))

    def set_display_name(self, display_name: str, _cancellable):
        if self.path in self.__class__.rename_fail_paths:
            raise FakeError("forced rename failure")
        target = os.path.join(os.path.dirname(self.path), display_name)
        if os.path.lexists(target):
            raise FakeError("target exists")
        os.rename(self.path, target)
        return FakeFile(target)

    def move(self, target, _flags, _cancellable, _progress, _data):
        mode = self.__class__.move_modes.get(self.path, "ok")
        if mode == "fail":
            raise FakeError("forced move failure")
        if os.path.lexists(target.path):
            raise FakeError("target exists")
        os.rename(self.path, target.path)
        if mode == "substitute-after-commit":
            hidden = target.path + ".moved-original"
            os.rename(target.path, hidden)
            Path(target.path).write_text("impostor", encoding="utf-8")
        return True

    def trash(self, _cancellable):
        self.__class__.trashed.append(self.path)
        mode = self.__class__.trash_modes.get(self.path, "ok")
        if mode == "fail":
            return False
        if mode == "error":
            raise FakeError("forced trash failure")
        # Simulate the namespace disappearance produced by system Trash.
        os.unlink(self.path)
        return True


class FakeGio:
    File = FakeFile

    class FileType:
        REGULAR = 1
        DIRECTORY = 2

    class FileQueryInfoFlags:
        NOFOLLOW_SYMLINKS = 1

    class FileCopyFlags:
        NO_FALLBACK_FOR_MOVE = 0x20


class FakeGLib:
    Error = FakeError


class WorkspaceScratchpadTransactionTests(unittest.TestCase):
    def setUp(self):
        FakeFile.reset()

    @staticmethod
    def _items(controller: WorkspaceController, root: Path):
        listing = controller.bind_root(str(root))
        return {item.name: item for item in listing.items}

    def test_planner_discovers_exact_companion_reserves_targets_and_rejects_managed_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "Destination"
            destination.mkdir()
            source = root / "draft.md"
            source.write_text("document", encoding="utf-8")
            companion = scratchpad_path(source)
            companion.write_text("notes", encoding="utf-8")

            controller = WorkspaceController()
            items = self._items(controller, root)
            rename = controller.plan_rename(items["draft.md"], "chapter.md")
            self.assertEqual(rename.companion.source_path, str(companion))
            self.assertEqual(rename.companion.target_path, str(root / "chapter.md.scratchpad.md"))
            self.assertIsNotNone(rename.companion.source_token)

            move = controller.plan_move(items["draft.md"], items["Destination"])
            self.assertEqual(move.companion.target_path, str(destination / "draft.md.scratchpad.md"))

            # Duplicate naming reserves the managed sidecar destination even when the
            # corresponding primary candidate is free.
            (root / "draft copy.md.scratchpad.md").write_text("foreign", encoding="utf-8")
            items = self._items(controller, root)
            duplicate = controller.plan_duplicate(items["draft.md"])
            self.assertEqual(duplicate.target_path, str(root / "draft copy 2.md"))
            self.assertEqual(duplicate.companion.target_path, str(root / "draft copy 2.md.scratchpad.md"))

            # A managed sidecar is never a first-class Workspace mutation target.
            items = self._items(controller, root)
            sidecar_item = items["draft.md.scratchpad.md"]
            for operation in (
                lambda: controller.plan_rename(sidecar_item, "renamed.md"),
                lambda: controller.plan_move(sidecar_item, items["Destination"]),
                lambda: controller.plan_duplicate(sidecar_item),
                lambda: controller.plan_trash(sidecar_item),
            ):
                with self.assertRaisesRegex(WorkspaceError, "managed with their document"):
                    operation()

    def test_planner_fails_closed_for_companion_type_ambiguity_and_target_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "Destination"
            destination.mkdir()
            source = root / "draft.md"
            source.write_text("document", encoding="utf-8")
            sidecar = scratchpad_path(source)
            sidecar.mkdir()
            controller = WorkspaceController()
            items = self._items(controller, root)
            with self.assertRaisesRegex(WorkspaceError, "Scratchpad companion changed type"):
                controller.plan_move(items["draft.md"], items["Destination"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "Destination"
            destination.mkdir()
            source = root / "draft.md"
            source.write_text("document", encoding="utf-8")
            (root / "chapter.md.scratchpad.md").write_text("foreign", encoding="utf-8")
            (destination / "draft.md.scratchpad.md").write_text("foreign", encoding="utf-8")
            controller = WorkspaceController()
            items = self._items(controller, root)
            with self.assertRaisesRegex(WorkspaceError, "Scratchpad destination already exists"):
                controller.plan_rename(items["draft.md"], "chapter.md")
            with self.assertRaisesRegex(WorkspaceError, "Scratchpad destination already exists"):
                controller.plan_move(items["draft.md"], items["Destination"])

    def test_rename_moves_companion_and_rolls_it_back_if_primary_does_not_commit(self):
        with patch("graphium_plus.workspace.gio.Gio", FakeGio), patch("graphium_plus.workspace.gio.GLib", FakeGLib):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "draft.md"
                source.write_text("document", encoding="utf-8")
                sidecar = scratchpad_path(source)
                sidecar.write_text("notes", encoding="utf-8")
                controller = WorkspaceController()
                plan = controller.plan_rename(self._items(controller, root)["draft.md"], "chapter.md")
                result = WorkspaceGioAdapter().rename(plan)
                self.assertTrue(result.success, result.message)
                self.assertTrue(result.committed)
                self.assertTrue(result.companion_committed)
                self.assertFalse(source.exists())
                self.assertFalse(sidecar.exists())
                self.assertEqual((root / "chapter.md").read_text(encoding="utf-8"), "document")
                self.assertEqual((root / "chapter.md.scratchpad.md").read_text(encoding="utf-8"), "notes")

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "draft.md"
                source.write_text("document", encoding="utf-8")
                sidecar = scratchpad_path(source)
                sidecar.write_text("notes", encoding="utf-8")
                controller = WorkspaceController()
                plan = controller.plan_rename(self._items(controller, root)["draft.md"], "chapter.md")
                FakeFile.rename_fail_paths = {str(source)}
                result = WorkspaceGioAdapter().rename(plan)
                self.assertFalse(result.success)
                self.assertFalse(result.committed)
                self.assertFalse(result.companion_committed)
                self.assertTrue(source.exists())
                self.assertTrue(sidecar.exists())
                self.assertFalse((root / "chapter.md").exists())
                self.assertFalse((root / "chapter.md.scratchpad.md").exists())

    def test_move_moves_companion_and_rolls_it_back_if_primary_does_not_commit(self):
        with patch("graphium_plus.workspace.gio.Gio", FakeGio), patch("graphium_plus.workspace.gio.GLib", FakeGLib):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                destination = root / "Destination"
                destination.mkdir()
                source = root / "draft.md"
                source.write_text("document", encoding="utf-8")
                sidecar = scratchpad_path(source)
                sidecar.write_text("notes", encoding="utf-8")
                controller = WorkspaceController()
                items = self._items(controller, root)
                plan = controller.plan_move(items["draft.md"], items["Destination"])
                result = WorkspaceGioAdapter().move(plan)
                self.assertTrue(result.success, result.message)
                self.assertTrue(result.committed)
                self.assertTrue(result.companion_committed)
                self.assertFalse(source.exists())
                self.assertFalse(sidecar.exists())
                self.assertEqual((destination / "draft.md").read_text(encoding="utf-8"), "document")
                self.assertEqual((destination / "draft.md.scratchpad.md").read_text(encoding="utf-8"), "notes")

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                destination = root / "Destination"
                destination.mkdir()
                source = root / "draft.md"
                source.write_text("document", encoding="utf-8")
                sidecar = scratchpad_path(source)
                sidecar.write_text("notes", encoding="utf-8")
                controller = WorkspaceController()
                items = self._items(controller, root)
                plan = controller.plan_move(items["draft.md"], items["Destination"])
                FakeFile.move_modes = {str(source): "fail"}
                result = WorkspaceGioAdapter().move(plan)
                self.assertFalse(result.success)
                self.assertFalse(result.committed)
                self.assertFalse(result.companion_committed)
                self.assertTrue(source.exists())
                self.assertTrue(sidecar.exists())
                self.assertFalse((destination / "draft.md").exists())
                self.assertFalse((destination / "draft.md.scratchpad.md").exists())

    def test_move_exposes_primary_committed_partial_without_claiming_safe_target_identity(self):
        with patch("graphium_plus.workspace.gio.Gio", FakeGio), patch("graphium_plus.workspace.gio.GLib", FakeGLib):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                destination = root / "Destination"
                destination.mkdir()
                source = root / "draft.md"
                source.write_text("document", encoding="utf-8")
                scratchpad_path(source).write_text("notes", encoding="utf-8")
                controller = WorkspaceController()
                items = self._items(controller, root)
                plan = controller.plan_move(items["draft.md"], items["Destination"])
                FakeFile.move_modes = {str(source): "substitute-after-commit"}
                result = WorkspaceGioAdapter().move(plan)
                self.assertFalse(result.success)
                self.assertTrue(result.committed)
                self.assertTrue(result.companion_committed)
                self.assertIsNone(result.object_token, "an impostor target must never acquire active-document reconciliation authority")
                self.assertFalse(source.exists())
                self.assertTrue((destination / "draft.md.scratchpad.md").exists())
                self.assertEqual((destination / "draft.md").read_text(encoding="utf-8"), "impostor")

    def test_duplicate_copies_saved_companion_bytes_and_rolls_back_first_output_on_second_failure(self):
        if not hasattr(os, "link"):
            self.skipTest("hard-link commit unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "draft.md"
            source.write_bytes(b"saved document\n")
            sidecar = scratchpad_path(source)
            sidecar.write_bytes(b"saved notes\n")
            controller = WorkspaceController()
            plan = controller.plan_duplicate(self._items(controller, root)["draft.md"])
            result = WorkspaceGioAdapter().duplicate(plan)
            self.assertTrue(result.success, result.message)
            self.assertTrue(result.companion_committed)
            self.assertEqual((root / "draft copy.md").read_bytes(), b"saved document\n")
            self.assertEqual((root / "draft copy.md.scratchpad.md").read_bytes(), b"saved notes\n")
            self.assertEqual(source.read_bytes(), b"saved document\n")
            self.assertEqual(sidecar.read_bytes(), b"saved notes\n")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "draft.md"
            source.write_bytes(b"saved document\n")
            sidecar = scratchpad_path(source)
            sidecar.write_bytes(b"saved notes\n")
            controller = WorkspaceController()
            plan = controller.plan_duplicate(self._items(controller, root)["draft.md"])
            adapter = WorkspaceGioAdapter()
            original = adapter._duplicate_single

            def fail_primary(candidate):
                if candidate.source_path == str(source):
                    return WorkspaceDuplicateResult(False, candidate.source_path, message="forced primary duplicate failure")
                return original(candidate)

            with patch.object(adapter, "_duplicate_single", side_effect=fail_primary):
                result = adapter.duplicate(plan)
            self.assertFalse(result.success)
            self.assertFalse(result.committed)
            self.assertFalse(result.companion_committed)
            self.assertFalse((root / "draft copy.md").exists())
            self.assertFalse((root / "draft copy.md.scratchpad.md").exists())
            self.assertEqual(source.read_bytes(), b"saved document\n")
            self.assertEqual(sidecar.read_bytes(), b"saved notes\n")

    def test_trash_preflights_both_then_reports_committed_partial_without_delete_fallback(self):
        with patch("graphium_plus.workspace.gio.Gio", FakeGio), patch("graphium_plus.workspace.gio.GLib", FakeGLib):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "draft.md"
                source.write_text("document", encoding="utf-8")
                sidecar = scratchpad_path(source)
                sidecar.write_text("notes", encoding="utf-8")
                controller = WorkspaceController()
                plan = controller.plan_trash(self._items(controller, root)["draft.md"])
                FakeFile.trash_modes = {str(sidecar): "cannot-trash"}
                result = WorkspaceGioAdapter().trash(plan)
                self.assertFalse(result.success)
                self.assertFalse(result.committed)
                self.assertEqual(FakeFile.trashed, [], "primary Trash must not start until both capabilities pass")
                self.assertTrue(source.exists())
                self.assertTrue(sidecar.exists())

            FakeFile.reset()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "draft.md"
                source.write_text("document", encoding="utf-8")
                sidecar = scratchpad_path(source)
                sidecar.write_text("notes", encoding="utf-8")
                controller = WorkspaceController()
                plan = controller.plan_trash(self._items(controller, root)["draft.md"])
                result = WorkspaceGioAdapter().trash(plan)
                self.assertTrue(result.success, result.message)
                self.assertTrue(result.committed)
                self.assertTrue(result.companion_committed)
                self.assertFalse(source.exists())
                self.assertFalse(sidecar.exists())

            FakeFile.reset()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "draft.md"
                source.write_text("document", encoding="utf-8")
                sidecar = scratchpad_path(source)
                sidecar.write_text("notes", encoding="utf-8")
                controller = WorkspaceController()
                plan = controller.plan_trash(self._items(controller, root)["draft.md"])
                FakeFile.trash_modes = {str(sidecar): "fail"}
                result = WorkspaceGioAdapter().trash(plan)
                self.assertFalse(result.success)
                self.assertTrue(result.committed)
                self.assertFalse(result.companion_committed)
                self.assertFalse(source.exists(), "committed primary Trash must be reported truthfully")
                self.assertTrue(sidecar.exists(), "failed companion Trash must never fall back to permanent deletion")
                self.assertIn("document was moved to Trash", result.message)

    def test_application_active_move_partial_reconciliation_requires_proven_target_object(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "graphium_plus" / "adapters" / "gtk" / "window.py"
        ).read_text(encoding="utf-8")
        self.assertIn('result.committed and getattr(result, "object_token", None) is not None', source)
        self.assertIn('current_token = self.workspace_gio._current_token(plan.target_path)', source)
        self.assertIn('if current_token == result.object_token:', source)
        self.assertIn('session.retarget_file_binding(post_move.file_state)', source)
        self.assertIn('self._notify_document_context_change(before_document_context)', source)


if __name__ == "__main__":
    unittest.main()
