from __future__ import annotations
import ast,unittest
from tests.release._common import ROOT

class SpellcheckProjectionTests(unittest.TestCase):
    def test_gtk_adapter_is_lazy_and_not_a_startup_or_composition_dependency(self):
        app=(ROOT/'graphium/adapters/gtk/application.py').read_text(encoding='utf-8')
        composition=(ROOT/'graphium/composition.py').read_text(encoding='utf-8')
        window=(ROOT/'graphium/adapters/gtk/window.py').read_text(encoding='utf-8')
        self.assertNotIn('spelling',app); self.assertNotIn('spelling',composition)
        tree=ast.parse(window); action=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_action_check_spelling')
        imports=[n for n in ast.walk(action) if isinstance(n,ast.ImportFrom)]
        self.assertEqual([(n.level,n.module,[a.name for a in n.names]) for n in imports],[(1,'spelling',['run_spell_check_dialog'])])
    def test_dialog_is_single_worker_main_context_projected_and_documented(self):
        text=(ROOT/'graphium/adapters/gtk/spelling.py').read_text(encoding='utf-8')
        for token in ('ThreadPoolExecutor(max_workers=1','GLib.idle_add','HunspellPipeSession','SpellCheckController','discover_hunspell_dictionaries','Gtk.ComboBoxText()','System default','dictionary_base=','_restart_for_dictionary','_discovery_cancel.set()','Replace','Ignore All','executor.shutdown(wait=True'):
            self.assertIn(token,text)
        self.assertNotIn('document_path',text); self.assertNotIn('Save',text); self.assertNotIn('ViewSettings',text); self.assertNotIn('gui language',text.lower())
        commands=(ROOT/'graphium/application/commands.py').read_text(encoding='utf-8')
        self.assertIn('CHECK_SPELLING_COMMAND,',commands)
        guide=(ROOT/'docs/user/GRAPHIUM_USER_GUIDE.txt').read_text(encoding='utf-8')
        keys=(ROOT/'docs/user/GRAPHIUM_KEYBOARD_SHORTCUTS.txt').read_text(encoding='utf-8')
        self.assertIn('Check Spelling… (F2)',guide); self.assertIn('F2              Check Spelling…',keys)
