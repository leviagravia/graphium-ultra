from __future__ import annotations

import unittest

from graphium.domain.text_search import SearchInputError, SearchScaleError
from graphium_ultra.markdown_viewer import find_markdown_viewer_search_hits


class UltraMarkdownPreviewSearchTests(unittest.TestCase):
    def test_matches_are_ordered_across_visual_segments_without_crossing_boundaries(self):
        hits = find_markdown_viewer_search_hits(
            ("alpha Needle", "Needle table", "tail needle"), "needle"
        )
        self.assertEqual(
            [(hit.segment_index, hit.start, hit.end) for hit in hits],
            [(0, 6, 12), (1, 0, 6), (2, 5, 11)],
        )
        self.assertEqual(find_markdown_viewer_search_hits(("abc", "def"), "cde"), ())

    def test_unicode_casefold_semantics_reuse_graphium_search_authority(self):
        hits = find_markdown_viewer_search_hits(("Straße", "STRASSE"), "strasse")
        self.assertEqual([(hit.segment_index, hit.start, hit.end) for hit in hits], [(0, 0, 6), (1, 0, 7)])
        exact = find_markdown_viewer_search_hits(("Straße", "STRASSE"), "Straße", match_case=True)
        self.assertEqual([(hit.segment_index, hit.start, hit.end) for hit in exact], [(0, 0, 6)])

    def test_query_remains_single_line_literal_and_bounded(self):
        with self.assertRaises(SearchInputError):
            find_markdown_viewer_search_hits(("one",), "one\ntwo")
        with self.assertRaises(SearchScaleError):
            find_markdown_viewer_search_hits(("x x", "x"), "x", max_matches=2)
        with self.assertRaises(ValueError):
            find_markdown_viewer_search_hits(("x",), "x", max_matches=0)

    def test_search_hits_are_immutable_coordinates_only(self):
        hit = find_markdown_viewer_search_hits(("find me",), "me")[0]
        self.assertEqual((hit.segment_index, hit.start, hit.end), (0, 5, 7))
        with self.assertRaises(Exception):
            hit.start = 0


if __name__ == "__main__":
    unittest.main()
