from __future__ import annotations
import unittest
from graphium.application.text_statistics import TextStatistics, count_text_statistics

class StatisticsTests(unittest.TestCase):

    def test_empty_and_line_semantics(self):
        self.assertEqual(count_text_statistics(''), TextStatistics(0, 0, 0))
        self.assertEqual(count_text_statistics('one'), TextStatistics(1, 1, 3))
        self.assertEqual(count_text_statistics('one\n'), TextStatistics(2, 1, 4))
        self.assertEqual(count_text_statistics('\n\n'), TextStatistics(3, 0, 2))

    def test_words_are_maximal_non_whitespace_runs(self):
        text = 'alpha,beta\tγδ\xa0epsilon\nfoo-bar'
        self.assertEqual(count_text_statistics(text).words, 4)

    def test_characters_are_unicode_codepoints_not_graphemes_or_bytes(self):
        text = 'é é 😀'
        stats = count_text_statistics(text)
        self.assertEqual(stats.characters, len(text))
        self.assertNotEqual(stats.characters, len(text.encode('utf-8')))

    def test_selection_contract_uses_same_function(self):
        text = 'one two\nthree'
        selection = text[4:7]
        self.assertEqual(count_text_statistics(selection), TextStatistics(1, 1, 3))
if __name__ == '__main__':
    unittest.main()
