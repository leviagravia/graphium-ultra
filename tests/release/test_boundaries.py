from __future__ import annotations
import ast, unittest
from tests.release._common import ROOT,imports,parse
class BoundaryTests(unittest.TestCase):
    def test_cross_project_isolation(self):
        bad=[]
        for p in (ROOT/'graphium').rglob('*.py'):
            for x in imports(str(p.relative_to(ROOT))):
                if x=='calamus' or x.startswith('calamus.') or x.startswith('calamus_'): bad.append((p,x))
        self.assertEqual(bad,[])
    def test_gtk_boundary_and_layer_direction(self):
        bad=[]
        for p in (ROOT/'graphium').rglob('*.py'):
            rel=p.relative_to(ROOT).as_posix()
            if not rel.startswith('graphium/adapters/gtk/'):
                for x in imports(rel):
                    if x=='gi' or x.startswith('gi.'): bad.append((rel,x))
        for p in (ROOT/'graphium/domain').rglob('*.py'):
            for x in imports(str(p.relative_to(ROOT))):
                if x.startswith(('graphium.application','graphium.adapters','graphium.infrastructure','graphium.composition')): bad.append((p,x))
        for p in (ROOT/'graphium/application').rglob('*.py'):
            for x in imports(str(p.relative_to(ROOT))):
                if x.startswith('graphium.adapters'): bad.append((p,x))
        self.assertEqual(bad,[])
    def test_direct_gdk_imports_pin_gdk3_before_import(self):
        bad=[]
        for p in (ROOT/'graphium').rglob('*.py'):
            t=parse(str(p.relative_to(ROOT)))
            imports_gdk=[]
            for n in ast.walk(t):
                if isinstance(n,ast.ImportFrom) and n.module=='gi.repository' and any(a.name=='Gdk' for a in n.names): imports_gdk.append(n.lineno)
            for line in imports_gdk:
                ok=False
                for n in ast.walk(t):
                    if not isinstance(n,ast.Call) or n.lineno>=line: continue
                    if isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='gi' and n.func.attr=='require_version' and len(n.args)>=2 and all(isinstance(a,ast.Constant) for a in n.args[:2]) and n.args[0].value=='Gdk' and n.args[1].value=='3.0': ok=True
                if not ok: bad.append(p.relative_to(ROOT).as_posix())
        self.assertEqual(bad,[])

class TransformShortcutNamespaceTests(unittest.TestCase):
    def test_transform_shortcuts_match_frozen_g15_s4_namespace(self):
        from graphium.application.commands import FORBIDDEN_ACCELERATORS, accelerator_map
        amap = accelerator_map()
        self.assertEqual(amap.get("uppercase"), "<Ctrl>U")
        self.assertEqual(amap.get("lowercase"), "<Ctrl><Shift>L")
        self.assertNotEqual(amap.get("uppercase"), "<Ctrl><Shift>U")
        self.assertNotIn(amap.get("uppercase"), FORBIDDEN_ACCELERATORS)
        self.assertNotIn(amap.get("lowercase"), FORBIDDEN_ACCELERATORS)
        self.assertNotIn("<Ctrl><Alt>L", (amap.get("uppercase"), amap.get("lowercase")))

    def test_transform_shortcuts_are_projected_into_both_help_authorities(self):
        shortcuts = (ROOT / "docs/user/GRAPHIUM_KEYBOARD_SHORTCUTS.txt").read_text(encoding="utf-8")
        guide = (ROOT / "docs/user/GRAPHIUM_USER_GUIDE.txt").read_text(encoding="utf-8")
        for text in (shortcuts, guide):
            self.assertIn("Ctrl+U", text)
            self.assertIn("Ctrl+Shift+L", text)
        self.assertNotIn("Uppercase, Lowercase, Duplicate Line / Selection and Trim Trailing Spaces have no dedicated", shortcuts)
        self.assertNotIn("remaining transformation actions are menu-driven", guide)
