from __future__ import annotations
import unittest
from graphium.application.renderability import MAX_INTERACTIVE_LINE_CHARS, InteractiveRenderabilityError, ensure_insert_renderable, ensure_interactive_text_renderable, ensure_join_renderable

class RenderabilityTests(unittest.TestCase):

    def test_exact_limit_is_admitted(self):
        ensure_interactive_text_renderable('a' * MAX_INTERACTIVE_LINE_CHARS)

    def test_limit_plus_one_is_rejected_without_transform(self):
        text = 'a' * (MAX_INTERACTIVE_LINE_CHARS + 1)
        with self.assertRaises(InteractiveRenderabilityError) as cm:
            ensure_interactive_text_renderable(text)
        self.assertEqual(cm.exception.line_number, 1)
        self.assertEqual(cm.exception.observed_chars, MAX_INTERACTIVE_LINE_CHARS + 1)
        self.assertEqual(text, 'a' * (MAX_INTERACTIVE_LINE_CHARS + 1))

    def test_large_multiline_document_is_admitted(self):
        text = ('0123456789abcdef\n' * 70000)[:1000000]
        self.assertGreaterEqual(len(text), 900000)
        ensure_interactive_text_renderable(text)

    def test_later_huge_line_reports_exact_line(self):
        text = 'short\n' + 'x' * (MAX_INTERACTIVE_LINE_CHARS + 1) + '\nend'
        with self.assertRaises(InteractiveRenderabilityError) as cm:
            ensure_interactive_text_renderable(text)
        self.assertEqual(cm.exception.line_number, 2)

    def test_insert_without_newline_is_rejected_if_result_exceeds_budget(self):
        with self.assertRaises(InteractiveRenderabilityError):
            ensure_insert_renderable(prefix_chars=MAX_INTERACTIVE_LINE_CHARS - 2, suffix_chars=1, inserted_text='zz')

    def test_multiline_insert_checks_first_middle_and_last_fragments(self):
        ensure_insert_renderable(prefix_chars=10, suffix_chars=10, inserted_text='a\nb\nc')
        with self.assertRaises(InteractiveRenderabilityError):
            ensure_insert_renderable(prefix_chars=0, suffix_chars=0, inserted_text='ok\n' + 'z' * (MAX_INTERACTIVE_LINE_CHARS + 1) + '\nok')

    def test_delete_join_is_rejected_if_joined_line_exceeds_budget(self):
        with self.assertRaises(InteractiveRenderabilityError):
            ensure_join_renderable(prefix_chars=12000, suffix_chars=8001)

    def test_delete_join_at_exact_limit_is_admitted(self):
        ensure_join_renderable(prefix_chars=12000, suffix_chars=8000)
if __name__ == '__main__':
    unittest.main()

class CurrentRenderabilityTests(unittest.TestCase):

    def test_renderability_preflight_is_content_neutral(self):
        text = 'x' * (MAX_INTERACTIVE_LINE_CHARS + 1)
        before = text
        with self.assertRaises(InteractiveRenderabilityError):
            ensure_interactive_text_renderable(text)
        self.assertEqual(text, before)
