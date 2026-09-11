from __future__ import annotations
import ast,os,pathlib,stat,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests.desktop.harness import comparators
from tests.release._common import ROOT
class EntrypointTests(unittest.TestCase):
    def test_entrypoints_are_repo_relative_and_executable(self):
        for rel in ('bin/graphium','bin/graphium-selftest','bin/graphium-install'):
            p=ROOT/rel; self.assertTrue(p.is_file()); self.assertTrue(p.stat().st_mode & stat.S_IXUSR)
            txt=p.read_text(encoding='utf-8'); self.assertNotIn('/home/',txt)
            t=ast.parse(txt,filename=rel); self.assertIn('__file__',[n.id for n in ast.walk(t) if isinstance(n,ast.Name)])
    def test_minimal_staged_install_is_exact_and_importable(self):
        with tempfile.TemporaryDirectory() as td:
            stage=Path(td)/'package root'; stage.mkdir(); subprocess.run([sys.executable,str(ROOT/'bin/graphium-install'),'--prefix','/usr','--destdir',str(stage)],check=True)
            prefix=stage/'usr'; private=prefix/'lib/graphium'; public=prefix/'bin/graphium'
            expected={f'usr/lib/graphium/graphium/{p.relative_to(ROOT/"graphium").as_posix()}' for p in (ROOT/'graphium').rglob('*') if p.is_file()}
            expected|={f'usr/lib/graphium/docs/user/{p.name}' for p in (ROOT/'docs/user').iterdir() if p.is_file()}
            expected|={'usr/lib/graphium/bin/graphium','usr/lib/graphium/LICENSE','usr/bin/graphium','usr/share/applications/io.github.leviagravia.Graphium.desktop'}
            expected|={f'usr/share/icons/hicolor/{p.relative_to(ROOT/"data/icons/hicolor").as_posix()}' for p in (ROOT/'data/icons/hicolor').rglob('*') if p.is_file()}
            actual={p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file() or p.is_symlink()}
            self.assertEqual(actual,expected); self.assertTrue(public.is_symlink()); self.assertEqual(public.readlink(),Path('../lib/graphium/bin/graphium'))
            desktop=(prefix/'share/applications/io.github.leviagravia.Graphium.desktop').read_text(encoding='utf-8')
            for marker in ('Type=Application','Name=Graphium','GenericName=Text Editor','Exec=graphium %F','Icon=io.github.leviagravia.Graphium','Terminal=false','MimeType=text/plain;','Categories=GTK;Utility;TextEditor;','StartupNotify=true'): self.assertIn(marker,desktop)
            probe='from pathlib import Path; import sys; p=Path(sys.argv[1]).resolve(); sys.path.insert(0,str(p.parents[1])); import graphium.product as x; assert x.DESKTOP_APPLICATION_ID=="io.github.leviagravia.Graphium"; assert (p.parents[1]/"docs/user/GRAPHIUM_USER_GUIDE.txt").is_file()'
            subprocess.run([sys.executable,'-I','-c',probe,str(public)],check=True)
            self.assertFalse(any((private/name).exists() for name in ('tests','evidence','docs/canonical','bin/graphium-selftest','bin/graphium-install')))
            home=Path(td)/'home with space'; home.mkdir(); subprocess.run([sys.executable,str(ROOT/'bin/graphium-install')],check=True,env={**os.environ,'HOME':str(home)}); self.assertTrue((home/'.local/bin/graphium').is_symlink())
    def test_common_comparator_protocol(self):
        self.assertEqual(comparators.APPS,('graphium','leafpad','l3afpad','mousepad','featherpad')); self.assertEqual(tuple(comparators.SIZES.values()),(0,5120,1048576,10485760))
        with patch.object(comparators.shutil,'which',side_effect=lambda x:'/usr/bin/'+x):
            self.assertEqual(comparators.command_for('graphium',None,ROOT),[str(ROOT/'bin/graphium')]); self.assertEqual(comparators.command_for('mousepad','/f',ROOT),['/usr/bin/mousepad','--disable-server','/f']); self.assertEqual(comparators.command_for('featherpad','/f',ROOT),['/usr/bin/featherpad','--standalone','/f'])
        data=comparators.workload_bytes('5KiB'); self.assertEqual(len(data),5120); self.assertTrue(data.startswith(b'GRAPHIUM::5KiB::BEGIN\n')); self.assertTrue(data.endswith(b'\nGRAPHIUM::5KiB::END\n'))
        with comparators.isolated_env(prefix='comparator-test-') as (root,env): self.assertTrue(all(env[k].startswith(str(root)) for k in ('HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME')))
        src=(ROOT/'tests/desktop/harness/comparators.py').read_text(); self.assertNotIn('Atspi',src); self.assertNotIn('EditableText',src); self.assertNotIn('generate_keyboard_event',src); self.assertNotIn('get_process_id',src)
