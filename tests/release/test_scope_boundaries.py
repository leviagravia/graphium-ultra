from __future__ import annotations
import ast,unittest
from tests.release._common import ROOT,imports
class ScopeBoundaryTests(unittest.TestCase):
    def test_forbidden_scope_expansion_absent(self):
        banned_imports=('requests','httpx','aiohttp','paramiko'); banned_attrs={('Gtk','Notebook'),('Gtk','Toolbar')}; bad=[]
        for p in (ROOT/'graphium').rglob('*.py'):
            rel=p.relative_to(ROOT).as_posix(); text=p.read_text(encoding='utf-8'); t=ast.parse(text,filename=rel)
            for x in imports(rel):
                if x.startswith(banned_imports) or x.startswith('gi.repository.GtkSource'): bad.append((rel,'import',x))
            for n in ast.walk(t):
                if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and (n.value.id,n.attr) in banned_attrs: bad.append((rel,'attr',f'{n.value.id}.{n.attr}'))
                if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'gtk-theme-name' in n.value.lower(): bad.append((rel,'theme-platform','gtk-theme-name'))
        self.assertEqual(bad,[])
        monitor_owners=[]
        for p in (ROOT/'graphium').rglob('*.py'):
            rel=p.relative_to(ROOT).as_posix(); text=p.read_text(encoding='utf-8')
            if '.monitor_file(' in text or '.monitor_directory(' in text:
                monitor_owners.append(rel)
        self.assertEqual(monitor_owners,['graphium/adapters/gtk/external_monitor.py'])
        monitor_text=(ROOT/'graphium/adapters/gtk/external_monitor.py').read_text(encoding='utf-8')
        self.assertNotIn('timeout_add_seconds',monitor_text)
        self.assertNotIn('while True:',monitor_text)
        self.assertIn('daemon=True',monitor_text)
        lifecycle = (ROOT / "graphium" / "application" / "file_lifecycle.py").read_text(encoding="utf-8")
        reload_body = lifecycle.split("def reload_document", 1)[1].split("def request_close", 1)[0]
        self.assertIn("confirm_modified_reload", reload_body)
        self.assertNotIn("_resolve_modified_before_replace", reload_body)
        self.assertNotIn("self.save(", reload_body)
        self.assertNotIn("save_as", reload_body)
        dialogs = (ROOT / "graphium" / "adapters" / "gtk" / "dialogs.py").read_text(encoding="utf-8")
        reload_dialog = dialogs.split("def confirm_modified_reload", 1)[1].split("def confirm_overwrite", 1)[0]
        self.assertIn('dialog.set_default_response(Gtk.ResponseType.CANCEL)', reload_dialog)
        self.assertIn('Discard Changes and Reload', reload_dialog)
        self.assertNotIn('add_button("Save"', reload_dialog)
    def test_external_spellcheck_is_one_optional_confined_subprocess_boundary(self):
        owners=[]
        for p in (ROOT/'graphium').rglob('*.py'):
            if 'subprocess.Popen' in p.read_text(encoding='utf-8'): owners.append(p.relative_to(ROOT).as_posix())
        self.assertEqual(sorted(owners),['graphium/adapters/gtk/application.py','graphium/infrastructure/hunspell_session.py'])
        text=(ROOT/'graphium/infrastructure/hunspell_session.py').read_text(encoding='utf-8')
        for token in ('"-a"','"-i"','"UTF-8"','"--check-apostrophe"','"-D"','"-d"','env["LC_ALL"] = "C"','MAX_DISCOVERY_OUTPUT_BYTES','MAX_AVAILABLE_DICTIONARIES','shell=False','b"^"'): self.assertIn(token,text)
        self.assertNotIn('document_path',text); self.assertNotIn('Gtk',text)
        self.assertNotIn('dictionary', (ROOT/'graphium/application/view_settings.py').read_text(encoding='utf-8').lower())
        self.assertNotIn('dictionary', (ROOT/'graphium/infrastructure/view_settings_store.py').read_text(encoding='utf-8').lower())
        app=(ROOT/'graphium/application/spellcheck.py').read_text(encoding='utf-8')
        self.assertIn('apply_prevalidated_programmatic_group',app); self.assertNotIn('subprocess',app); self.assertNotIn('pathlib',app); self.assertNotIn('import os',app); self.assertNotIn('Gtk',app)
        commands=(ROOT/'graphium/application/commands.py').read_text(encoding='utf-8'); self.assertIn('CHECK_SPELLING_COMMAND = CommandSpec("check-spelling", "Check Spelling…", "F2", "Document")',commands)

