from __future__ import annotations
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

class G16PreCandidateBlockerTests(unittest.TestCase):
    def test_line_number_gutter_uses_same_style_context_and_paints_background_first(self):
        text=(ROOT/'graphium/adapters/gtk/editor_view.py').read_text(encoding='utf-8')
        block=text[text.index('    def _draw_visible_line_numbers'):]
        self.assertIn('context = self.get_style_context()', block)
        self.assertIn('Gtk.render_background(context, cr,', block)
        self.assertLess(block.index('Gtk.render_background(context, cr,'), block.index('Gtk.render_layout(context, cr,'))
    def test_line_number_fix_adds_no_independent_palette_or_new_widget(self):
        text=(ROOT/'graphium/adapters/gtk/editor_view.py').read_text(encoding='utf-8')
        self.assertNotRegex(text, r'GUTTER_(?:BACKGROUND|FOREGROUND|LIGHT|DARK)')
        self.assertNotIn('Gtk.DrawingArea', text)
        self.assertNotIn('GtkSource', text)
    def test_hunspell_group_parser_is_explicitly_bounded(self):
        text=(ROOT/'graphium/infrastructure/hunspell_session.py').read_text(encoding='utf-8')
        self.assertRegex(text, r'MAX_RESPONSE_GROUP_LINES\s*=\s*[1-9][0-9]*')
        self.assertRegex(text, r'MAX_RESPONSE_GROUP_BYTES\s*=\s*[1-9][0-9]*')
        self.assertIn('_read_response_group', text)
    def test_hunspell_ui_does_not_mislabel_runtime_protocol_failure_as_not_installed(self):
        text=(ROOT/'graphium/adapters/gtk/spelling.py').read_text(encoding='utf-8')
        self.assertIn('HunspellProtocolError', text)
        self.assertIn('HunspellTimeoutError', text)
        deliver=text[text.index('    def _deliver('):text.index('    def _show_issue(')]
        self.assertNotIn('Verify that Hunspell and the selected dictionary are installed', deliver)
        absent=text[text.index('def run_spell_check_dialog'):]
        self.assertIn('Hunspell is not installed', absent)
    def test_no_new_spell_dependency_or_tokenizer_escape_hatch(self):
        infra=(ROOT/'graphium/infrastructure/hunspell_session.py').read_text(encoding='utf-8')
        domain=(ROOT/'graphium/domain/spellcheck.py').read_text(encoding='utf-8')
        for forbidden in ('libhunspell','gspell','libspelling','ctypes'):
            self.assertNotIn(forbidden, infra.lower())
        self.assertIn('_JOINERS = frozenset(("\'", "\\u2019", "-"))', domain)

if __name__ == '__main__':
    unittest.main()
