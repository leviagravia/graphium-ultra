from __future__ import annotations
import unittest
from unittest.mock import patch
from graphium.application.renderability import InteractiveRenderabilityError, MAX_INTERACTIVE_LINE_CHARS
from graphium.application.search import MAX_REPLACE_ALL_MATCHES, SearchController
from graphium.domain.edit_history import EditKind, ViewState
from graphium.domain.text_search import SearchScaleError
from graphium.domain.text_search import SearchInputError, SearchScaleError, find_all, find_next, find_previous, is_exact_match, validate_query, validate_replacement

class SearchControllerTests(unittest.TestCase):

    def test_config_is_small_and_single_line(self):
        c = SearchController()
        c.configure(query='Alpha', replacement='beta', match_case=True)
        self.assertEqual(c.query, 'Alpha')
        self.assertEqual(c.replacement, 'beta')
        self.assertTrue(c.match_case)

    def test_replace_all_freezes_original_matches_and_does_not_cascade(self):
        c = SearchController()
        c.configure(query='a', replacement='aa', match_case=True)
        plan = c.build_replace_all_plan(source_text='a a', source_state_id=7, before_view=ViewState(3, 3))
        self.assertEqual(plan.final_text, 'aa aa')
        self.assertEqual(plan.changed_count, 2)
        self.assertEqual([(o.kind, o.offset, o.text) for o in plan.operations], [(EditKind.DELETE, 2, 'a'), (EditKind.INSERT, 2, 'aa'), (EditKind.DELETE, 0, 'a'), (EditKind.INSERT, 0, 'aa')])

    def test_replace_all_casefold_preserves_exact_original_ranges(self):
        c = SearchController()
        c.configure(query='STRASSE', replacement='X', match_case=False)
        plan = c.build_replace_all_plan(source_text='Straße STRASSE', source_state_id=3, before_view=ViewState(14, 14))
        self.assertEqual(plan.final_text, 'X X')
        self.assertEqual(plan.changed_count, 2)
        self.assertEqual(plan.target_view, ViewState(3, 3))

    def test_replace_all_zero_effective_changes_is_noop(self):
        c = SearchController()
        c.configure(query='x', replacement='x', match_case=True)
        plan = c.build_replace_all_plan(source_text='x x', source_state_id=2, before_view=ViewState(1, 1))
        self.assertFalse(plan.changed)
        self.assertEqual(plan.operations, ())
        self.assertEqual(plan.final_text, 'x x')

    def test_replace_all_empty_replacement(self):
        c = SearchController()
        c.configure(query='xx', replacement='', match_case=True)
        plan = c.build_replace_all_plan(source_text='axx bxx', source_state_id=2, before_view=ViewState(7, 7))
        self.assertEqual(plan.final_text, 'a b')
        self.assertEqual(plan.changed_count, 2)
        self.assertTrue(all((o.kind is EditKind.DELETE for o in plan.operations)))

    def test_replace_all_preflights_pathological_final_line(self):
        source = 'x' * (MAX_INTERACTIVE_LINE_CHARS - 1)
        c = SearchController()
        c.configure(query='x', replacement='xx', match_case=True)
        with self.assertRaises(InteractiveRenderabilityError):
            c.build_replace_all_plan(source_text=source, source_state_id=1, before_view=ViewState())

    def test_replace_one_acquires_next_when_selection_is_not_match(self):
        c = SearchController()
        c.configure(query='one', replacement='X', match_case=True)
        plan = c.build_replace_one_plan(source_text='zero one two one', source_state_id=5, before_view=ViewState(0, 0), selection_start=0, selection_end=0)
        self.assertEqual(plan.final_text, 'zero X two one')
        self.assertEqual(plan.changed_count, 1)
        self.assertNotEqual(plan.target_view.insert_offset, plan.target_view.selection_bound_offset)

    def test_replace_one_exact_selection_is_fast_path(self):
        c = SearchController()
        c.configure(query='one', replacement='1', match_case=True)
        plan = c.build_replace_one_plan(source_text='one two', source_state_id=4, before_view=ViewState(3, 0), selection_start=0, selection_end=3)
        self.assertEqual(plan.final_text, '1 two')
        self.assertEqual(plan.operations[0].offset, 0)

    def test_replace_one_no_match_returns_none(self):
        c = SearchController()
        c.configure(query='z', replacement='x')
        self.assertIsNone(c.build_replace_one_plan(source_text='abc', source_state_id=1, before_view=ViewState(), selection_start=0, selection_end=0))

    def test_replace_all_match_count_is_bounded_before_plan_materialization(self):
        c = SearchController()
        c.configure(query='a', replacement='b', match_case=True)
        source = 'a ' * (MAX_REPLACE_ALL_MATCHES + 1)
        with self.assertRaises(SearchScaleError):
            c.build_replace_all_plan(source_text=source, source_state_id=1, before_view=ViewState())

    def test_replace_all_default_undo_payload_is_preflighted_before_final_text(self):
        c = SearchController()
        c.configure(query='a', replacement='BBBB', match_case=True)
        with patch('graphium.application.search.DEFAULT_MAX_HISTORY_PAYLOAD_CHARS', 4):
            with self.assertRaises(SearchScaleError):
                c.build_replace_all_plan(source_text='a\na', source_state_id=1, before_view=ViewState())

    def test_case_sensitive_identical_replace_all_is_zero_cost_noop_even_when_dense(self):
        c = SearchController()
        c.configure(query='a', replacement='a', match_case=True)
        source = 'a' * (MAX_REPLACE_ALL_MATCHES + 100)
        plan = c.build_replace_all_plan(source_text=source, source_state_id=1, before_view=ViewState(5, 5))
        self.assertFalse(plan.changed)
        self.assertEqual(plan.operations, ())
        self.assertEqual(plan.target_view, ViewState(5, 5))

class TextSearchTests(unittest.TestCase):

    def test_single_line_contract(self):
        self.assertEqual(validate_query('needle'), 'needle')
        self.assertEqual(validate_replacement(''), '')
        with self.assertRaises(SearchInputError):
            validate_query('')
        with self.assertRaises(SearchInputError):
            validate_query('a\nb')
        with self.assertRaises(SearchInputError):
            validate_replacement('a\rb')

    def test_case_sensitive_literal_non_overlapping(self):
        self.assertEqual([(m.start, m.end) for m in find_all('aaaa', 'aa', match_case=True)], [(0, 2), (2, 4)])
        self.assertEqual(find_all('Ab aB', 'ab', match_case=True), ())

    def test_unicode_casefold_maps_back_to_exact_source_offsets(self):
        matches = find_all('Straße STRASSE', 'strasse', match_case=False)
        self.assertEqual([(m.start, m.end) for m in matches], [(0, 6), (7, 14)])
        self.assertTrue(is_exact_match('Straße', 'STRASSE', 0, 6, match_case=False))

    def test_casefold_does_not_match_inside_one_source_character_expansion(self):
        self.assertEqual(find_all('ß', 's', match_case=False), ())
        self.assertEqual([(m.start, m.end) for m in find_all('ß', 'ss', match_case=False)], [(0, 1)])

    def test_unicode_multibyte_offsets_are_character_offsets(self):
        text = 'α🙂βeta βETA'
        matches = find_all(text, 'βeta', match_case=False)
        self.assertEqual([(m.start, m.end) for m in matches], [(2, 6), (7, 11)])

    def test_forward_and_backward_wrap_once(self):
        text = 'one two one'
        self.assertEqual(find_next(text, 'one', 4, match_case=True).match.start, 8)
        wrapped = find_next(text, 'one', len(text), match_case=True)
        self.assertTrue(wrapped.wrapped)
        self.assertEqual(wrapped.match.start, 0)
        self.assertEqual(find_previous(text, 'one', 8, match_case=True).match.start, 0)
        wrapped = find_previous(text, 'one', 0, match_case=True)
        self.assertTrue(wrapped.wrapped)
        self.assertEqual(wrapped.match.start, 8)

    def test_no_match_does_not_claim_wrap(self):
        result = find_next('abc', 'z', 0)
        self.assertIsNone(result.match)
        self.assertFalse(result.wrapped)

    def test_navigation_does_not_inherit_replace_all_nonoverlap_grid(self):
        m = find_next('aaaa', 'aa', 1, match_case=True).match
        self.assertEqual((m.start, m.end), (1, 3))

    def test_unicode_casefold_is_line_bounded_and_does_not_cross_newline(self):
        text = 'Straße\nSTRASSE\nneedle'
        first = find_next(text, 'strasse', 0, match_case=False)
        self.assertEqual((first.match.start, first.match.end), (0, 6))
        second = find_next(text, 'strasse', first.match.end, match_case=False)
        self.assertEqual((second.match.start, second.match.end), (7, 14))
        self.assertEqual(find_all('s\ns', 'ss', match_case=False), ())

    def test_find_all_match_cap_fails_closed_before_unbounded_materialization(self):
        with self.assertRaises(SearchScaleError):
            find_all('a a a', 'a', match_case=True, max_matches=2)
        self.assertEqual([(m.start, m.end) for m in find_all('a a', 'a', match_case=True, max_matches=2)], [(0, 1), (2, 3)])

    def test_exact_match_checks_only_selected_source_range(self):
        self.assertTrue(is_exact_match('Straße elsewhere', 'STRASSE', 0, 6, match_case=False))
        self.assertFalse(is_exact_match('ß', 's', 0, 1, match_case=False))
        self.assertFalse(is_exact_match('a\nb', 'ab', 0, 3, match_case=False))
