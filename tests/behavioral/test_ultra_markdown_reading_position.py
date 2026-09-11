from __future__ import annotations

import unittest

from graphium_plus.markdown import build_markdown_document_map
from graphium_ultra.markdown_viewer import (
    MarkdownViewerReadingPosition,
    build_markdown_viewer_plan,
    capture_markdown_viewer_reading_position,
    resolve_markdown_viewer_reading_position,
)


def _plan(text: str):
    return build_markdown_viewer_plan(text, build_markdown_document_map(text))


class UltraMarkdownReadingPositionTests(unittest.TestCase):
    def test_unique_heading_preserves_fraction_inside_same_section(self):
        before = _plan("# Alpha\n\none two three four five six\n\n# Beta\n\nend\n")
        alpha = next(anchor for anchor in before.heading_anchors if anchor.identifier == "alpha")
        beta = next(anchor for anchor in before.heading_anchors if anchor.identifier == "beta")
        offset = alpha.offset + (beta.offset - alpha.offset) * 3 // 4
        position = capture_markdown_viewer_reading_position(before, offset)
        self.assertEqual(position.anchor_identifier, "alpha")
        self.assertAlmostEqual(position.section_fraction, 0.75, delta=0.08)

        after = _plan("intro\n\n# Alpha\n\none two three four five six seven eight nine ten\n\n# Beta\n\nend\n")
        resolved = resolve_markdown_viewer_reading_position(after, position)
        new_alpha = next(anchor for anchor in after.heading_anchors if anchor.identifier == "alpha")
        new_beta = next(anchor for anchor in after.heading_anchors if anchor.identifier == "beta")
        self.assertGreater(resolved, new_alpha.offset)
        self.assertLess(resolved, new_beta.offset)
        ratio = (resolved - new_alpha.offset) / (new_beta.offset - new_alpha.offset)
        self.assertAlmostEqual(ratio, position.section_fraction, delta=0.08)

    def test_missing_anchor_falls_back_to_global_fraction(self):
        before = _plan("# Keep\n\n" + "line\n" * 20)
        position = capture_markdown_viewer_reading_position(before, len(before.text) * 2 // 3)
        self.assertEqual(position.anchor_identifier, "keep")
        after = _plan("plain\n" * 40)
        resolved = resolve_markdown_viewer_reading_position(after, position)
        self.assertAlmostEqual(resolved / len(after.text), position.global_fraction, delta=0.03)

    def test_duplicate_explicit_heading_identifier_uses_global_fallback(self):
        plan = _plan("# One {#same}\n\nbody\n\n# Two {#same}\n\nmore\n")
        second = plan.heading_anchors[1]
        position = capture_markdown_viewer_reading_position(plan, second.offset + 2)
        self.assertIsNone(position.anchor_identifier)
        self.assertGreater(position.global_fraction, 0.4)

    def test_positions_are_clamped_and_empty_plan_is_safe(self):
        plan = _plan("plain text\n")
        low = capture_markdown_viewer_reading_position(plan, -100)
        high = capture_markdown_viewer_reading_position(plan, 10000)
        self.assertEqual(resolve_markdown_viewer_reading_position(plan, low), 0)
        self.assertEqual(resolve_markdown_viewer_reading_position(plan, high), len(plan.text))
        empty = _plan("")
        zero = capture_markdown_viewer_reading_position(empty, 0)
        self.assertEqual(resolve_markdown_viewer_reading_position(empty, zero), 0)

    def test_position_is_small_immutable_presentation_state_only(self):
        position = MarkdownViewerReadingPosition("section", 0.25, 0.5)
        self.assertEqual((position.anchor_identifier, position.section_fraction, position.global_fraction), ("section", 0.25, 0.5))
        with self.assertRaises(Exception):
            position.global_fraction = 0.2
        with self.assertRaises(ValueError):
            MarkdownViewerReadingPosition(None, -0.1, 0.5)


if __name__ == "__main__":
    unittest.main()
