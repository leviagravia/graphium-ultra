from __future__ import annotations
import importlib, stat, sys, tempfile, types, unittest
from pathlib import Path

class PrintSettingsIntegrationTests(unittest.TestCase):

    def test_page_setup_store_is_atomic_0600_and_fail_soft_without_real_gtk(self):
        old = {k: sys.modules.get(k) for k in ('gi', 'gi.repository', 'graphium.adapters.gtk.printing')}
        try:
            gi = types.ModuleType('gi')
            gi.require_version = lambda *a: None
            repo = types.ModuleType('gi.repository')

            class PageSetup:

                def __init__(self, value='default'):
                    self.value = value

                @classmethod
                def new_from_file(cls, path):
                    return cls(Path(path).read_text(encoding='utf-8'))

                def to_file(self, path):
                    Path(path).write_text(self.value, encoding='utf-8')
            repo.Gtk = types.SimpleNamespace(PageSetup=PageSetup)
            repo.Pango = types.SimpleNamespace()
            repo.PangoCairo = types.SimpleNamespace()
            gi.repository = repo
            sys.modules['gi'] = gi
            sys.modules['gi.repository'] = repo
            sys.modules.pop('graphium.adapters.gtk.printing', None)
            mod = importlib.import_module('graphium.adapters.gtk.printing')
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / 'page-setup.ini'
                store = mod._PageSetupStore(p)
                self.assertEqual(store.load().value, 'default')
                store.save(PageSetup('saved'))
                self.assertEqual(p.read_text(encoding='utf-8'), 'saved')
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 384)
                self.assertEqual(store.load().value, 'saved')
                p.unlink()
                p.mkdir()
                self.assertEqual(store.load().value, 'default')
        finally:
            sys.modules.pop('graphium.adapters.gtk.printing', None)
            for k, v in old.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
