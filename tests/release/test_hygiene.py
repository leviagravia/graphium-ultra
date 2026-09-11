from __future__ import annotations
import unittest
from tests.release._common import ROOT
EXPECTED={'GRAPHIUM_PRODUCT_ARCHITECTURE_CONTRACT.md','GRAPHIUM_ROADMAP.md','GRAPHIUM_MEMORIA_OPERATIVA.txt'}
class HygieneTests(unittest.TestCase):
    def test_canonical_document_cap(self):
        actual={p.name for p in (ROOT/'docs/canonical').iterdir() if p.is_file()}; self.assertEqual(actual,EXPECTED)
    def test_python_sources_compile_in_memory(self):
        for p in ROOT.rglob('*.py'):
            if '.git' in p.parts: continue
            compile(p.read_text(encoding='utf-8'),str(p),'exec')
    def test_repository_is_utf8_and_bytecode_clean(self):
        bad=[]
        for p in ROOT.rglob('*'):
            if '.git' in p.parts: continue
            if p.is_dir() and p.name=='__pycache__': bad.append(str(p.relative_to(ROOT)))
            elif p.is_file() and p.suffix in {'.pyc','.pyo'}: bad.append(str(p.relative_to(ROOT)))
            elif p.is_file():
                try: p.read_text(encoding='utf-8')
                except UnicodeDecodeError: bad.append(str(p.relative_to(ROOT))+':nonutf8')
        self.assertEqual(bad,[])
