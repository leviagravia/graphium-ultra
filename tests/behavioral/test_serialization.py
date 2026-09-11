from __future__ import annotations
import codecs
from pathlib import Path
import tempfile
import unittest
from graphium.domain.document_identity import BomKind, LineEnding
from graphium.domain.document_serialization import DocumentSerializationError, DocumentSerializationProfile, MixedLineEndingConfirmationRequired, profile_for_document, serialize_document
from graphium.infrastructure.document_loader import load_document

class DocumentSerializationTests(unittest.TestCase):

    def test_utf16le_bom_and_crlf_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'doc.txt'
            path.write_bytes(codecs.BOM_UTF16_LE + 'A\r\nB\r\n'.encode('utf-16-le'))
            state = load_document(str(path)).file_state
            self.assertEqual(state.load.encoding, 'utf-16-le')
            self.assertEqual(state.load.bom, BomKind.UTF16_LE)
            self.assertEqual(state.load.eol.dominant, LineEnding.CRLF)
            out = serialize_document('X\nY\n', profile_for_document(state))
            self.assertEqual(out.data, codecs.BOM_UTF16_LE + 'X\r\nY\r\n'.encode('utf-16-le'))

    def test_strict_encoding_failure_never_substitutes_characters(self):
        legacy = DocumentSerializationProfile('ascii', BomKind.NONE, LineEnding.LF)
        with self.assertRaises(DocumentSerializationError):
            serialize_document('città', legacy)

    def test_mixed_eol_requires_explicit_confirmation_then_normalizes_dominant(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'mixed.txt'
            path.write_bytes(b'A\r\nB\r\nC\n')
            loaded = load_document(str(path))
            profile = profile_for_document(loaded.file_state)
            self.assertTrue(profile.mixed_source)
            with self.assertRaises(MixedLineEndingConfirmationRequired):
                serialize_document(loaded.text, profile)
            out = serialize_document(loaded.text, profile, allow_mixed_eol_normalization=True)
            self.assertEqual(out.data, b'A\r\nB\r\nC\r\n')

    def test_new_document_defaults_utf8_no_bom_lf(self):
        out = serialize_document('A\n', profile_for_document(None))
        self.assertEqual(out.data, b'A\n')
        self.assertEqual(out.profile.encoding, 'utf-8')
        self.assertEqual(out.profile.bom, BomKind.NONE)
        self.assertEqual(out.profile.line_ending, LineEnding.LF)

    def test_utf8_bom_lf_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'doc.txt'
            path.write_bytes(codecs.BOM_UTF8 + b'A\nB\n')
            loaded = load_document(str(path))
            out = serialize_document('X\nY\n', profile_for_document(loaded.file_state))
            self.assertEqual(out.data, codecs.BOM_UTF8 + b'X\nY\n')
            self.assertEqual(out.profile.bom, BomKind.UTF8)

    def test_utf16be_and_utf32_bom_families_serialize_losslessly(self):
        cases = ((codecs.BOM_UTF16_BE, 'utf-16-be', BomKind.UTF16_BE), (codecs.BOM_UTF32_LE, 'utf-32-le', BomKind.UTF32_LE), (codecs.BOM_UTF32_BE, 'utf-32-be', BomKind.UTF32_BE))
        with tempfile.TemporaryDirectory() as td:
            for index, (bom, codec, kind) in enumerate(cases):
                with self.subTest(codec=codec):
                    path = Path(td) / f'doc-{index}.txt'
                    path.write_bytes(bom + 'A\n'.encode(codec))
                    loaded = load_document(str(path))
                    self.assertEqual(loaded.file_state.load.bom, kind)
                    out = serialize_document('Ω\n', profile_for_document(loaded.file_state))
                    self.assertEqual(out.data, bom + 'Ω\n'.encode(codec))

    def test_cr_line_endings_are_preserved_at_byte_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'doc.txt'
            path.write_bytes(b'A\rB\r')
            loaded = load_document(str(path))
            self.assertEqual(loaded.file_state.load.eol.dominant, LineEnding.CR)
            out = serialize_document('X\nY\n', profile_for_document(loaded.file_state))
            self.assertEqual(out.data, b'X\rY\r')

    def test_no_historical_separator_defaults_to_lf_for_future_newlines(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'one-line.txt'
            path.write_bytes(b'one line')
            loaded = load_document(str(path))
            self.assertEqual(loaded.file_state.load.eol.dominant, LineEnding.NONE)
            profile = profile_for_document(loaded.file_state)
            self.assertEqual(profile.line_ending, LineEnding.LF)
            self.assertEqual(serialize_document('one\ntwo', profile).data, b'one\ntwo')
if __name__ == '__main__':
    unittest.main()
