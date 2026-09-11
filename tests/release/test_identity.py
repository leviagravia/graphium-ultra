from __future__ import annotations
import re, unittest
from tests.release._common import ROOT
class ProductIdentityTests(unittest.TestCase):
    def test_stable_product_identity_has_no_work_item_lineage(self):
        ns={}; exec((ROOT/'graphium/product.py').read_text(encoding='utf-8'),ns)
        self.assertEqual(ns['PRODUCT_NAME'],'Graphium')
        self.assertEqual(ns['PACKAGE_NAME'],'graphium')
        self.assertEqual(ns['EXECUTABLE_NAME'],'graphium')
        self.assertEqual(ns['DESKTOP_APPLICATION_ID'],'io.github.leviagravia.Graphium')
        self.assertRegex(ns['VERSION'],r'^\d+\.\d+\.\d+$')
        self.assertNotIn('WORK_ITEM',ns); self.assertNotIn('WORK_ITEM_DESCRIPTION',ns)
        self.assertNotRegex(ns['VERSION'].lower(),r'g\d+')
