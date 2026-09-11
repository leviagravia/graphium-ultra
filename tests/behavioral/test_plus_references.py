import os
from pathlib import Path
import ast
import tempfile
import unittest

from graphium.infrastructure.guarded_file_writer import GuardedFileWriter
from graphium_plus.references import (
    MarkdownReferenceLibraryStore,
    ReferenceRecord,
    default_reference_library_path,
    parse_reference_library,
    reference_file_token,
    search_references,
    serialize_reference_library,
)


class PlusReferenceAuthorityTests(unittest.TestCase):
    def records(self):
        return (
            ReferenceRecord(
                key="ratzinger1968",
                type="book",
                authors=("Ratzinger, Joseph",),
                title="Introduction to Christianity",
                year="1968",
                publisher="Herder",
                extra_fields=(("Original Title", "Einführung in das Christentum"),),
            ),
            ReferenceRecord(
                key="smith2024",
                type="journal-article",
                authors=("Smith, Alice", "Jones, Bob"),
                title="Local Academic Writing",
                year="2024",
                container_title="Journal of Plain Text",
                doi="10.1000/example",
            ),
        )

    def test_human_readable_roundtrip_preserves_known_and_unknown_fields(self):
        encoded = serialize_reference_library(self.records())
        self.assertTrue(encoded.startswith("# Graphium Plus References v1\n"))
        self.assertIn("## ratzinger1968", encoded)
        self.assertIn("Original Title: Einführung in das Christentum", encoded)
        records, diagnostics = parse_reference_library(encoded)
        self.assertEqual(diagnostics, ())
        self.assertEqual(records, self.records())

    def test_duplicate_invalid_or_missing_title_is_blocking_diagnostic(self):
        text = """# Graphium Plus References v1

## duplicate
Title: First

## duplicate
Title: Second

## bad key
Title: Invalid

## no-title
Year: 2024
"""
        records, diagnostics = parse_reference_library(text)
        self.assertEqual(tuple(record.key for record in records), ("duplicate",))
        kinds = {item.kind for item in diagnostics}
        self.assertTrue({"duplicate-key", "invalid-record"}.issubset(kinds))
        self.assertTrue(all(item.blocking for item in diagnostics))

    def test_on_demand_search_is_deterministic_and_has_no_index_state(self):
        records = self.records()
        self.assertEqual(
            tuple(record.key for record in search_references(records, "ratz")),
            ("ratzinger1968",),
        )
        self.assertEqual(
            tuple(record.key for record in search_references(records, "alice 2024")),
            ("smith2024",),
        )
        self.assertEqual(search_references(records, "absent"), ())

    def test_default_path_is_one_graphium_plus_xdg_data_authority(self):
        path = default_reference_library_path(
            {"HOME": "/home/test", "XDG_DATA_HOME": "/data"}
        )
        self.assertEqual(path, Path("/data/graphium-plus/references.md"))

    def test_store_uses_conflict_token_and_core_guarded_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "references.md"
            store = MarkdownReferenceLibraryStore(GuardedFileWriter(), path)
            empty = store.load()
            self.assertFalse(empty.token.exists)
            saved = store.save(self.records(), empty.token)
            self.assertTrue(saved.saved, saved.message)
            self.assertEqual(saved.snapshot.records, self.records())
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

            before = saved.snapshot.token
            path.write_text(path.read_text(encoding="utf-8") + "\nExternal\n", encoding="utf-8")
            conflict = store.save((), before)
            self.assertEqual(conflict.status, "conflict")
            self.assertNotEqual(conflict.snapshot.token, before)

    def test_store_does_not_rewrite_on_load_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "references.md"
            raw = serialize_reference_library(self.records())
            path.write_text(raw, encoding="utf-8")
            before = path.read_bytes()
            snapshot = MarkdownReferenceLibraryStore(GuardedFileWriter(), path).load()
            self.assertEqual(snapshot.records, self.records())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(snapshot.token, reference_file_token(path))

            other = Path(tmp) / "other.md"
            other.write_text(raw, encoding="utf-8")
            path.unlink()
            os.symlink(other.name, path)
            unsafe = MarkdownReferenceLibraryStore(GuardedFileWriter(), path).load()
            self.assertFalse(unsafe.writable)
            self.assertEqual(unsafe.diagnostics[0].kind, "unsafe-file")

    def test_reference_module_has_no_database_indexer_or_second_physical_writer(self):
        source = Path(__file__).parents[2] / "graphium_plus" / "references.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn("ReferenceWriterPort", text)
        tree = ast.parse(text)
        self.assertFalse(any(isinstance(node, ast.Name) and node.id == "GuardedFileWriter" for node in ast.walk(tree)))
        for forbidden in ("sqlite3", "CREATE TABLE", "threading", "watchdog", "os.replace("):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
