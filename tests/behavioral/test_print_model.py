from __future__ import annotations
from pathlib import Path
import unittest
from graphium.adapters.gtk.print_pagination import IncrementalVisualPage, IncrementalVisualPaginator, VisualLinePage, VisualLineSpan, logical_line_chunk_end, paginate_visual_line_heights
from graphium.application.print_model import PrintSnapshot, build_print_snapshot
ROOT = Path(__file__).resolve().parents[2]

class ContractArchitectureTests(unittest.TestCase):

    def test_visual_line_pagination_never_splits_a_measured_line(self):
        self.assertEqual(paginate_visual_line_heights([10, 10, 10, 10], usable_height=25), (VisualLinePage(0, 2), VisualLinePage(2, 4)))
        self.assertEqual(paginate_visual_line_heights([40, 5, 5], usable_height=25), (VisualLinePage(0, 1), VisualLinePage(1, 3)))
        with self.assertRaises(ValueError):
            paginate_visual_line_heights([10, 0], usable_height=25)

    def test_incremental_visual_paginator_preserves_chunk_spans_and_page_boundaries(self):
        paginator = IncrementalVisualPaginator(usable_height=25)
        paginator.add_chunk(0, [10, 10, 10])
        paginator.add_chunk(1, [5, 20, 5])
        self.assertEqual(paginator.finish(), (IncrementalVisualPage((VisualLineSpan(0, 0, 2),)), IncrementalVisualPage((VisualLineSpan(0, 2, 3), VisualLineSpan(1, 0, 1))), IncrementalVisualPage((VisualLineSpan(1, 1, 3),))))
        self.assertTrue(paginator.finished)
        with self.assertRaises(RuntimeError):
            paginator.add_chunk(2, [1])

    def test_logical_line_chunking_is_bounded_and_never_splits_source_lines(self):
        text = 'aa\nbbbb\ncc\n'
        first = logical_line_chunk_end(text, 0, target_chars=5, max_logical_lines=8)
        self.assertEqual(text[:first], 'aa\n')
        second = logical_line_chunk_end(text, first, target_chars=5, max_logical_lines=8)
        self.assertEqual(text[first:second], 'bbbb\n')
        self.assertEqual(logical_line_chunk_end('x' * 20, 0, target_chars=4, max_logical_lines=1), 20)
        with self.assertRaises(ValueError):
            logical_line_chunk_end(text, -1, target_chars=5, max_logical_lines=8)
if __name__ == '__main__':
    unittest.main()

class CurrentPrintModelTests(unittest.TestCase):

    def test_print_snapshot_uses_logical_basename_and_persistent_base_font(self):
        snap = build_print_snapshot(text='alpha', logical_path='/tmp/work/note.txt', base_font=('Monospace', 12.0))
        self.assertEqual(snap, PrintSnapshot('alpha', 'note.txt', 'Monospace', 12.0))
        untitled = build_print_snapshot(text='', logical_path=None, base_font=('Serif', 11.0))
        self.assertEqual(untitled.title, 'Untitled')
