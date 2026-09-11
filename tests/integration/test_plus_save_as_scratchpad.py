from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from graphium.application.document_save_service import DocumentSaveService
from graphium.application.document_session import DocumentSession
from graphium.domain.history import TextHistory
from graphium.infrastructure.document_loader import load_document
from graphium.infrastructure.guarded_file_writer import GuardedFileWriter
from graphium_plus.companion_storage import CompanionAuthorityError
from graphium_plus.scratchpad import scratchpad_path
from graphium_plus.scratchpad_save_as import (
    commit_scratchpad_save_as,
    prepare_scratchpad_save_as,
    scratchpad_document_target_safe_to_bind,
    scratchpad_save_as_target_safe_to_bind,
)


class ScratchpadSaveAsTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.writer = GuardedFileWriter()

    def tearDown(self):
        self.tmp.cleanup()

    def _document(self, name: str = "old.md", data: bytes = b"document\n") -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_named_save_as_copies_exact_saved_scratchpad_bytes_and_preserves_source(self):
        source = self._document()
        target = self.root / "new.md"
        source_sidecar = scratchpad_path(source)
        source_sidecar.write_bytes(b"# Graphium Plus Scratchpad v1\n\n")

        plan = prepare_scratchpad_save_as(
            writer=self.writer,
            source_document_path=str(source),
            target_document_path=str(target),
            active_file_state=load_document(str(source)).file_state,
        )
        result = commit_scratchpad_save_as(writer=self.writer, plan=plan)

        target_sidecar = scratchpad_path(target)
        self.assertTrue(result.copied)
        self.assertEqual(target_sidecar.read_bytes(), source_sidecar.read_bytes())
        self.assertEqual(source_sidecar.read_bytes(), b"# Graphium Plus Scratchpad v1\n\n")
        self.assertFalse(target.exists(), "the sidecar coordinator must not write the primary document")

    def test_preexisting_target_scratchpad_blocks_before_primary_save(self):
        source = self._document()
        scratchpad_path(source).write_text("source notes", encoding="utf-8")
        target = self.root / "new.md"
        scratchpad_path(target).write_text("foreign notes", encoding="utf-8")

        with self.assertRaisesRegex(CompanionAuthorityError, "will not overwrite or inherit"):
            prepare_scratchpad_save_as(
                writer=self.writer,
                source_document_path=str(source),
                target_document_path=str(target),
                active_file_state=load_document(str(source)).file_state,
            )
        self.assertFalse(target.exists())
        self.assertEqual(scratchpad_path(target).read_text(encoding="utf-8"), "foreign notes")

    def test_source_change_after_preflight_fails_closed_and_does_not_create_target_sidecar(self):
        source = self._document()
        source_sidecar = scratchpad_path(source)
        source_sidecar.write_text("saved notes", encoding="utf-8")
        target = self.root / "new.md"
        plan = prepare_scratchpad_save_as(
            writer=self.writer,
            source_document_path=str(source),
            target_document_path=str(target),
            active_file_state=load_document(str(source)).file_state,
        )
        source_sidecar.write_text("changed externally", encoding="utf-8")

        with self.assertRaisesRegex(CompanionAuthorityError, "changed before Save As companion copy"):
            commit_scratchpad_save_as(writer=self.writer, plan=plan)
        self.assertFalse(scratchpad_path(target).exists())
        self.assertEqual(source_sidecar.read_text(encoding="utf-8"), "changed externally")

    def test_target_appearance_after_preflight_is_never_overwritten(self):
        source = self._document()
        source_sidecar = scratchpad_path(source)
        source_sidecar.write_text("saved notes", encoding="utf-8")
        target = self.root / "new.md"
        plan = prepare_scratchpad_save_as(
            writer=self.writer,
            source_document_path=str(source),
            target_document_path=str(target),
            active_file_state=load_document(str(source)).file_state,
        )
        target_sidecar = scratchpad_path(target)
        target_sidecar.write_text("competitor", encoding="utf-8")

        with self.assertRaises(CompanionAuthorityError):
            commit_scratchpad_save_as(writer=self.writer, plan=plan)
        self.assertEqual(target_sidecar.read_text(encoding="utf-8"), "competitor")
        self.assertEqual(source_sidecar.read_text(encoding="utf-8"), "saved notes")

    def test_untitled_first_save_has_no_source_copy_but_reserves_target_sidecar(self):
        target = self.root / "first.md"
        plan = prepare_scratchpad_save_as(
            writer=self.writer,
            source_document_path=None,
            target_document_path=str(target),
            active_file_state=None,
        )
        result = commit_scratchpad_save_as(writer=self.writer, plan=plan)
        self.assertFalse(result.copied)
        self.assertFalse(result.source_existed)
        self.assertFalse(scratchpad_path(target).exists())

        scratchpad_path(target).write_text("foreign", encoding="utf-8")
        with self.assertRaisesRegex(CompanionAuthorityError, "will not overwrite or inherit"):
            prepare_scratchpad_save_as(
                writer=self.writer,
                source_document_path=None,
                target_document_path=str(target),
                active_file_state=None,
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_alias_of_active_object_uses_core_ordinary_save_lane_without_companion_copy(self):
        source = self._document()
        alias = self.root / "alias.md"
        alias.symlink_to(source.name)
        scratchpad_path(alias).write_text("unrelated alias notes", encoding="utf-8")
        plan = prepare_scratchpad_save_as(
            writer=self.writer,
            source_document_path=str(source),
            target_document_path=str(alias),
            active_file_state=load_document(str(source)).file_state,
        )
        self.assertFalse(plan.binding_change_expected)
        self.assertIsNone(plan.target_observation)
        result = commit_scratchpad_save_as(writer=self.writer, plan=plan)
        self.assertFalse(result.copied)
        self.assertEqual(scratchpad_path(alias).read_text(encoding="utf-8"), "unrelated alias notes")


    def test_target_race_is_unsafe_to_bind_after_companion_commit_failure(self):
        source = self._document()
        source_sidecar = scratchpad_path(source)
        source_sidecar.write_text("saved notes", encoding="utf-8")
        target = self.root / "new.md"
        plan = prepare_scratchpad_save_as(
            writer=self.writer,
            source_document_path=str(source),
            target_document_path=str(target),
            active_file_state=load_document(str(source)).file_state,
        )
        target_sidecar = scratchpad_path(target)
        target_sidecar.write_text("competitor", encoding="utf-8")

        with self.assertRaises(CompanionAuthorityError):
            commit_scratchpad_save_as(writer=self.writer, plan=plan)
        self.assertFalse(
            scratchpad_save_as_target_safe_to_bind(writer=self.writer, plan=plan)
        )
        self.assertFalse(
            scratchpad_document_target_safe_to_bind(
                writer=self.writer, target_document_path=str(target)
            )
        )
        self.assertEqual(target_sidecar.read_text(encoding="utf-8"), "competitor")

    def test_absent_target_after_companion_failure_is_safe_for_empty_rebind(self):
        source = self._document()
        source_sidecar = scratchpad_path(source)
        source_sidecar.write_text("saved notes", encoding="utf-8")
        target = self.root / "new.md"
        plan = prepare_scratchpad_save_as(
            writer=self.writer,
            source_document_path=str(source),
            target_document_path=str(target),
            active_file_state=load_document(str(source)).file_state,
        )
        source_sidecar.write_text("changed externally", encoding="utf-8")

        with self.assertRaises(CompanionAuthorityError):
            commit_scratchpad_save_as(writer=self.writer, plan=plan)
        self.assertTrue(
            scratchpad_save_as_target_safe_to_bind(writer=self.writer, plan=plan)
        )
        self.assertFalse(scratchpad_path(target).exists())

    def test_primary_save_as_then_companion_copy_preserves_old_pair_and_binds_new_pair(self):
        source = self._document(data=b"document\n")
        source_sidecar = scratchpad_path(source)
        source_sidecar.write_bytes(b"# Graphium Plus Scratchpad v1\n\n")
        target = self.root / "new.md"

        history = TextHistory()
        session = DocumentSession()
        loaded = load_document(str(source))
        state = history.reset(loaded.text)
        session.establish_open(loaded, state)
        writer = GuardedFileWriter()
        plan = prepare_scratchpad_save_as(
            writer=writer,
            source_document_path=str(source),
            target_document_path=str(target),
            active_file_state=session.file_state,
        )
        primary = DocumentSaveService(session=session, writer=writer).save_as(
            writer.observe_target(str(target))
        )
        self.assertTrue(primary.committed)
        self.assertEqual(session.logical_path, str(target.resolve()))
        companion = commit_scratchpad_save_as(writer=writer, plan=plan)

        self.assertTrue(companion.copied)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), b"document\n")
        self.assertEqual(source_sidecar.read_bytes(), b"# Graphium Plus Scratchpad v1\n\n")
        self.assertEqual(target.read_bytes(), b"document\n")
        self.assertEqual(scratchpad_path(target).read_bytes(), source_sidecar.read_bytes())

    def test_symlink_or_nonregular_source_scratchpad_fails_closed(self):
        source = self._document()
        target = self.root / "new.md"
        sidecar = scratchpad_path(source)
        real = self.root / "real-notes"
        real.write_text("notes", encoding="utf-8")
        sidecar.symlink_to(real)
        with self.assertRaisesRegex(CompanionAuthorityError, "symbolic link"):
            prepare_scratchpad_save_as(
                writer=self.writer,
                source_document_path=str(source),
                target_document_path=str(target),
                active_file_state=load_document(str(source)).file_state,
            )


if __name__ == "__main__":
    unittest.main()
