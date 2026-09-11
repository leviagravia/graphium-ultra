from __future__ import annotations
import ast, unittest
from tests.release._common import ROOT,parse,call_name
class AuthorityTopologyTests(unittest.TestCase):
    def test_single_physical_writer(self):
        writers=[]; risky=[]
        allowed={'graphium/infrastructure/guarded_file_writer.py','graphium/infrastructure/view_settings_store.py','graphium/infrastructure/recent_files_store.py','graphium/adapters/gtk/printing.py','graphium/infrastructure/recovery_store.py'}
        for p in (ROOT/'graphium').rglob('*.py'):
            rel=p.relative_to(ROOT).as_posix(); t=parse(rel)
            for n in ast.walk(t):
                if isinstance(n,ast.ClassDef) and n.name.endswith('Writer'): writers.append((rel,n.name))
                if isinstance(n,ast.Call):
                    name=call_name(n.func)
                    if (name in {'os.replace','os.link','os.rename','os.fsync'} or name.endswith('.write_bytes')) and rel not in allowed: risky.append((rel,name,n.lineno))
        self.assertEqual(writers,[('graphium/infrastructure/guarded_file_writer.py','GuardedFileWriter')])
        self.assertEqual(risky,[])
    def test_composition_has_single_core_authorities(self):
        t=parse('graphium/composition.py'); f=next(n for n in t.body if isinstance(n,ast.FunctionDef) and n.name=='build_core')
        names=['DocumentSession','DeltaHistory','GuardedFileWriter','SearchController','ViewSettingsController','RecentFilesController','DocumentCopyService','DocumentPropertiesController','FileLifecycleController']
        counts={x:0 for x in names}
        for n in ast.walk(f):
            if isinstance(n,ast.Call):
                name=call_name(n.func).split('.')[-1]
                if name in counts: counts[name]+=1
        self.assertEqual(counts,{x:1 for x in names})
