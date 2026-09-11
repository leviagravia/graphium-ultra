from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from graphium_plus.bibtex_io import MAX_BIB_IMPORT_BYTES, read_bibliography_text


class PlusBibInputTests(unittest.TestCase):
    def test_regular_utf8_file_reads_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "refs.bib"
            text = "@book{k,title={Città}}\n"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(read_bibliography_text(path), text)

    def test_symlink_and_directory_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "refs.bib"
            target.write_text("@book{k,title={T}}", encoding="utf-8")
            link = root / "link.bib"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "regular local file"):
                read_bibliography_text(link)
            with self.assertRaisesRegex(ValueError, "regular local file"):
                read_bibliography_text(root)

    def test_size_bound_and_invalid_utf8_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            large = root / "large.bib"
            large.write_bytes(b"x" * 17)
            with self.assertRaisesRegex(ValueError, "exceeds"):
                read_bibliography_text(large, max_bytes=16)
            invalid = root / "invalid.bib"
            invalid.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                read_bibliography_text(invalid)

    def test_default_bound_is_explicit_and_finite(self):
        self.assertEqual(MAX_BIB_IMPORT_BYTES, 16 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
