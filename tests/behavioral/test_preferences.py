from __future__ import annotations
import unittest
from graphium.application.view_settings import (
    APPEARANCE_VALUES, DEFAULT_WINDOW_HEIGHT, DEFAULT_WINDOW_WIDTH,
    MAX_TAB_WIDTH, MIN_TAB_WIDTH, ViewSettings,
    logical_column_for_prefix, spaces_to_next_tab_stop,
)

class PreferenceValueModelTests(unittest.TestCase):
    def test_defaults_are_small_and_backward_compatible(self):
        s=ViewSettings()
        self.assertEqual((s.appearance,s.tab_width,s.insert_spaces),("system",8,False))
        self.assertEqual((s.window_width,s.window_height),(720,520))

    def test_tab_width_accepts_only_integer_1_to_32(self):
        self.assertEqual(ViewSettings(tab_width=MIN_TAB_WIDTH).tab_width,1)
        self.assertEqual(ViewSettings(tab_width=MAX_TAB_WIDTH).tab_width,32)
        for bad in (0,33,True,8.0):
            with self.assertRaises((TypeError,ValueError)):
                ViewSettings(tab_width=bad)  # type: ignore[arg-type]

    def test_appearance_is_exact_system_light_dark(self):
        self.assertEqual(APPEARANCE_VALUES,("system","light","dark"))
        for value in APPEARANCE_VALUES:
            self.assertEqual(ViewSettings(appearance=value).appearance,value)
        with self.assertRaises(ValueError): ViewSettings(appearance="custom")

    def test_geometry_is_bounded_normal_size_only(self):
        fields=set(ViewSettings.__dataclass_fields__)
        self.assertEqual((DEFAULT_WINDOW_WIDTH,DEFAULT_WINDOW_HEIGHT),(720,520))
        for forbidden in ("window_x","window_y","maximized","fullscreen","monitor"):
            self.assertNotIn(forbidden,fields)
        for changes in ({"window_width":10},{"window_height":10},{"window_width":100000},{"window_height":100000}):
            with self.assertRaises(ValueError): ViewSettings(**changes)

    def test_tab_math_respects_literal_tabs_and_next_stop(self):
        self.assertEqual(logical_column_for_prefix("abc",8),3)
        self.assertEqual(logical_column_for_prefix("a\tb",8),9)
        self.assertEqual(logical_column_for_prefix("12345678\t",8),16)
        self.assertEqual([spaces_to_next_tab_stop(x,8) for x in ("","a","1234567","12345678")],[8,7,1,8])
