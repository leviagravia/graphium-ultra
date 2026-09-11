from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from graphium_ultra.markdown_viewer_images import (
    MAX_IMAGE_FILE_BYTES,
    MarkdownViewerImageStatus,
    resolve_markdown_viewer_image,
)


class UltraMarkdownViewerImageResolutionTests(unittest.TestCase):
    def test_relative_target_resolves_from_document_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "figures" / "one.png"
            image.parent.mkdir()
            image.write_bytes(b"png")
            result = resolve_markdown_viewer_image(
                "figures/one.png", document_path=str(root / "chapter.md")
            )
            self.assertTrue(result.ready)
            self.assertEqual(result.path, str(image))

    def test_percent_encoded_path_and_query_fragment_use_local_path_component(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "figure one.png"
            image.write_bytes(b"png")
            result = resolve_markdown_viewer_image(
                "figure%20one.png?cache=1#view", document_path=str(root / "chapter.md")
            )
            self.assertTrue(result.ready)
            self.assertEqual(result.path, str(image))

    def test_absolute_local_target_can_resolve_without_bound_document(self):
        with TemporaryDirectory() as tmp:
            image = Path(tmp) / "absolute.jpg"
            image.write_bytes(b"jpg")
            result = resolve_markdown_viewer_image(str(image), document_path=None)
            self.assertTrue(result.ready)
            self.assertEqual(result.path, str(image))

    def test_relative_target_without_bound_document_stays_inert(self):
        result = resolve_markdown_viewer_image("figure.png", document_path=None)
        self.assertEqual(result.status, MarkdownViewerImageStatus.UNBOUND_RELATIVE)

    def test_remote_and_uri_targets_are_refused_without_io(self):
        for target in (
            "https://example.invalid/a.png",
            "http://example.invalid/a.png",
            "file:///tmp/a.png",
            "data:image/png;base64,AAAA",
            "//example.invalid/a.png",
        ):
            with self.subTest(target=target):
                result = resolve_markdown_viewer_image(target, document_path="/tmp/doc.md")
                self.assertEqual(result.status, MarkdownViewerImageStatus.REMOTE_OR_URI)
                self.assertIsNone(result.path)

    def test_missing_directory_and_oversized_files_fall_back(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = str(root / "doc.md")
            missing = resolve_markdown_viewer_image("missing.png", document_path=document)
            self.assertEqual(missing.status, MarkdownViewerImageStatus.MISSING)
            folder = root / "folder.png"
            folder.mkdir()
            not_regular = resolve_markdown_viewer_image("folder.png", document_path=document)
            self.assertEqual(not_regular.status, MarkdownViewerImageStatus.NOT_REGULAR)
            huge = root / "huge.png"
            with huge.open("wb") as handle:
                handle.truncate(MAX_IMAGE_FILE_BYTES + 1)
            too_large = resolve_markdown_viewer_image("huge.png", document_path=document)
            self.assertEqual(too_large.status, MarkdownViewerImageStatus.TOO_LARGE)


if __name__ == "__main__":
    unittest.main()
