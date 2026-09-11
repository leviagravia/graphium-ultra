from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from graphium.application.view_settings import ViewSettings, ViewSettingsController
from graphium.application.view_status import project_compact_status
from graphium.domain.document_identity import BomKind, LineEnding
from graphium.domain.document_serialization import DocumentSerializationProfile
from graphium.infrastructure.view_settings_store import JsonViewSettingsStore
class MemoryStore:
    def __init__(self, value=None, *, fail_save=False):
        self.value = value or ViewSettings()
        self.fail_save = fail_save
        self.saves = 0
    def load(self):
        return self.value
    def save(self, settings):
        self.saves += 1
        if self.fail_save:
            raise OSError('synthetic config failure')
        self.value = settings
class ViewSettingsTests(unittest.TestCase):
    def test_defaults_are_small_and_preserve_uncluttered_view(self):
        got = ViewSettings()
        self.assertFalse(got.word_wrap)
        self.assertFalse(got.line_numbers)
        self.assertTrue(got.status_bar)
        self.assertEqual(got.font_family, 'Monospace')
        self.assertEqual(got.font_size_points, 11.0)
    def test_validation_rejects_bad_font_without_hidden_coercion(self):
        with self.assertRaises(ValueError):
            ViewSettings(font_family='')
        with self.assertRaises(ValueError):
            ViewSettings(font_size_points=2)
        with self.assertRaises(ValueError):
            ViewSettings(font_size_points=100)
    def test_controller_publishes_setting_only_after_persistence_succeeds(self):
        store = MemoryStore(fail_save=True)
        controller = ViewSettingsController(store)
        before = controller.current
        with self.assertRaises(OSError):
            controller.update(word_wrap=True)
        self.assertEqual(controller.current, before)
        self.assertEqual(store.saves, 1)
    def test_json_store_roundtrip_is_atomic_and_has_no_temp_residue(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'graphium' / 'view.json'
            store = JsonViewSettingsStore(path)
            expected = ViewSettings(word_wrap=True, line_numbers=True, status_bar=False, font_family='DejaVu Sans Mono', font_size_points=13.5)
            store.save(expected)
            self.assertEqual(store.load(), expected)
            self.assertEqual(oct(path.stat().st_mode & 511), '0o600')
            self.assertFalse(list(path.parent.glob('.view-settings-*.tmp')))
            payload = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(set(payload), {'word_wrap', 'line_numbers', 'status_bar', 'font_family', 'font_size_points', 'appearance', 'tab_width', 'insert_spaces', 'window_width', 'window_height'})
    def test_missing_or_corrupt_config_falls_back_without_creating_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'view.json'
            store = JsonViewSettingsStore(path)
            self.assertEqual(store.load(), ViewSettings())
            self.assertFalse(path.exists())
            path.write_text('not-json', encoding='utf-8')
            self.assertEqual(store.load(), ViewSettings())
class CompactStatusTests(unittest.TestCase):
    def test_new_document_projects_utf8_lf_saved(self):
        got = project_compact_status(line=1, column=1, representation_profile=DocumentSerializationProfile('utf-8', BomKind.NONE, LineEnding.LF), modified=False)
        self.assertEqual(got.position_text, 'Ln 1, Col 1')
        self.assertEqual(got.document_text, 'UTF-8 · LF · Saved')
    def test_loaded_representation_and_modified_relation_are_projected(self):
        got = project_compact_status(line=12, column=7, representation_profile=DocumentSerializationProfile('utf-16-le', BomKind.UTF16_LE, LineEnding.CRLF), modified=True)
        self.assertEqual(got.position_text, 'Ln 12, Col 7')
        self.assertEqual(got.document_text, 'UTF-16 LE BOM · CRLF · Modified')
    def test_utf8_bom_and_mixed_eol_are_observation_not_conversion(self):
        got = project_compact_status(line=2, column=3, representation_profile=DocumentSerializationProfile('utf-8', BomKind.UTF8, LineEnding.LF, True), modified=False)
        self.assertEqual(got.document_text, 'UTF-8 BOM · Mixed EOL (LF) · Saved')
    def test_pending_conversion_profile_is_projected_before_save(self):
        got = project_compact_status(line=4, column=2, representation_profile=DocumentSerializationProfile('utf-32-be', BomKind.UTF32_BE, LineEnding.CR), modified=True)
        self.assertEqual(got.document_text, 'UTF-32 BE BOM · CR · Modified')
    def test_position_is_strictly_one_based(self):
        with self.assertRaises(ValueError):
            project_compact_status(line=0, column=1, representation_profile=DocumentSerializationProfile('utf-8', BomKind.NONE, LineEnding.LF), modified=False)
if __name__ == '__main__':
    unittest.main()
class CurrentViewSettingsTests(unittest.TestCase):
    def test_zoom_and_fullscreen_are_not_persistent_view_settings(self):
        fields = set(ViewSettings.__dataclass_fields__)
        self.assertNotIn('zoom_percent', fields)
        self.assertNotIn('fullscreen', fields)
class PreferencePersistenceTests(unittest.TestCase):
    def test_legacy_five_key_payload_loads_new_defaults_without_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'view.json'
            old={'word_wrap':True,'line_numbers':True,'status_bar':False,'font_family':'Monospace','font_size_points':12.0}
            path.write_text(json.dumps(old),encoding='utf-8'); before=path.read_bytes()
            got=JsonViewSettingsStore(path).load()
            self.assertTrue(got.word_wrap); self.assertTrue(got.line_numbers); self.assertFalse(got.status_bar)
            self.assertEqual((got.appearance,got.tab_width,got.insert_spaces,got.window_width,got.window_height),('system',8,False,720,520))
            self.assertEqual(path.read_bytes(),before)
    def test_invalid_owned_preference_falls_back_to_complete_defaults(self):
        cases=({'tab_width':'8'},{'appearance':'blue'},{'window_width':1,'window_height':520})
        with tempfile.TemporaryDirectory() as td:
            for i,payload in enumerate(cases):
                path=Path(td)/f'view-{i}.json'; path.write_text(json.dumps(payload),encoding='utf-8')
                self.assertEqual(JsonViewSettingsStore(path).load(),ViewSettings())
    def test_unknown_keys_do_not_gain_authority(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'view.json'; path.write_text(json.dumps({'tab_width':4,'mystery':'ignored'}),encoding='utf-8')
            got=JsonViewSettingsStore(path).load(); self.assertEqual(got.tab_width,4); self.assertFalse(hasattr(got,'mystery'))
    def test_preference_update_is_one_atomic_snapshot_and_failure_does_not_publish(self):
        store=MemoryStore(); controller=ViewSettingsController(store)
        got=controller.update(tab_width=4,insert_spaces=True)
        self.assertEqual(store.saves,1); self.assertEqual((got.tab_width,got.insert_spaces),(4,True))
        failing=MemoryStore(value=got,fail_save=True); controller=ViewSettingsController(failing); before=controller.current
        with self.assertRaises(OSError): controller.update(tab_width=6,insert_spaces=False)
        self.assertEqual(controller.current,before); self.assertEqual(failing.saves,1)
