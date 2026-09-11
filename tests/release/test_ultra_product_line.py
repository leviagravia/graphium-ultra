from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from graphium.paths import resolve_xdg_paths
from graphium_plus.product import PLUS_PRODUCT_IDENTITY
from graphium_ultra.product import ULTRA_PRODUCT_IDENTITY
from tests.release._common import ROOT, imports


class UltraProductLineTests(unittest.TestCase):
    def test_ultra_identity_and_xdg_are_distinct_from_plus(self):
        self.assertEqual(ULTRA_PRODUCT_IDENTITY.product_name, "Graphium Ultra")
        self.assertEqual(ULTRA_PRODUCT_IDENTITY.version, "0.0.1")
        self.assertEqual(ULTRA_PRODUCT_IDENTITY.repository_url, "https://github.com/leviagravia/graphium-ultra")
        self.assertEqual(ULTRA_PRODUCT_IDENTITY.executable_name, "graphium-ultra")
        self.assertEqual(ULTRA_PRODUCT_IDENTITY.desktop_application_id, "io.github.leviagravia.GraphiumUltra")
        self.assertEqual(ULTRA_PRODUCT_IDENTITY.xdg_namespace, "graphium-ultra")
        self.assertNotEqual(ULTRA_PRODUCT_IDENTITY.xdg_namespace, PLUS_PRODUCT_IDENTITY.xdg_namespace)
        plus = resolve_xdg_paths({"HOME": "/tmp/home"}, namespace=PLUS_PRODUCT_IDENTITY.xdg_namespace)
        ultra = resolve_xdg_paths({"HOME": "/tmp/home"}, namespace=ULTRA_PRODUCT_IDENTITY.xdg_namespace)
        self.assertNotEqual(plus, ultra)

    def test_layering_is_core_then_plus_then_ultra_only(self):
        for base in (ROOT / "graphium", ROOT / "graphium_plus"):
            bad=[]
            for path in base.rglob("*.py"):
                rel=path.relative_to(ROOT).as_posix()
                for imported in imports(rel):
                    if imported == "graphium_ultra" or imported.startswith("graphium_ultra."):
                        bad.append((rel, imported))
            self.assertEqual(bad, [])
        ultra_app=(ROOT/"graphium_ultra/adapters/gtk/application.py").read_text(encoding="utf-8")
        ultra_win=(ROOT/"graphium_ultra/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertIn("GraphiumPlusApplication", ultra_app)
        self.assertIn("GraphiumPlusWindow", ultra_win)

    def test_viewer_ownership_is_ultra_only_and_shared_markdown_map_stays_plus(self):
        for rel in (
            "graphium_ultra/markdown_viewer.py",
            "graphium_ultra/markdown_viewer_images.py",
            "graphium_ultra/adapters/gtk/markdown_viewer.py",
        ):
            self.assertTrue((ROOT/rel).is_file(), rel)
        plus_window=(ROOT/"graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        ultra_window=(ROOT/"graphium_ultra/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertNotIn('"markdown-viewer"', plus_window)
        self.assertIn('Gio.SimpleAction.new("markdown-viewer", None)', ultra_window)
        self.assertIn("from graphium_plus.markdown import build_markdown_document_map", ultra_window)
        self.assertFalse((ROOT/"graphium_ultra/markdown.py").exists())

    def test_ultra_guide_is_separate_and_plus_guide_has_no_ultra_section(self):
        plus=(ROOT/"docs/user/GRAPHIUM_PLUS_USER_GUIDE.txt").read_text(encoding="utf-8")
        ultra=(ROOT/"docs/user/GRAPHIUM_ULTRA_USER_GUIDE.txt").read_text(encoding="utf-8")
        self.assertNotIn("NATIVE MARKDOWN VIEWER — GRAPHIUM ULTRA", plus)
        self.assertIn("33. NATIVE MARKDOWN VIEWER — GRAPHIUM ULTRA", ultra)
        self.assertIn("Graphium Ultra is the cumulative edition built on Graphium Plus", ultra)
        self.assertIn("This guide describes Graphium Ultra 0.0.1.", ultra)

    def test_public_readme_and_logo_are_ultra_specific(self):
        readme=(ROOT/"README.md").read_text(encoding="utf-8")
        self.assertIn('<h1 align="center">Graphium Ultra</h1>', readme)
        self.assertIn('Release line: 0.0.1', readme)
        self.assertIn('assets/graphium-ultra.svg', readme)
        self.assertIn('git clone https://github.com/leviagravia/graphium-ultra.git', readme)
        self.assertIn('./bin/graphium-ultra', readme)
        self.assertIn('./bin/graphium-ultra-install', readme)
        self.assertNotIn('alt="Graphium Plus logo"', readme)
        logo=ROOT/"assets/graphium-ultra.svg"
        canonical=ROOT/"graphium_ultra/data/icons/hicolor/scalable/apps/io.github.leviagravia.GraphiumUltra.svg"
        self.assertEqual(logo.read_bytes(), canonical.read_bytes())

    def test_working_copy_launcher_is_private_xdg_and_bytecode_clean(self):
        launcher=(ROOT/"RUN_WORKING_COPY.sh").read_text(encoding="utf-8")
        for marker in (
            'XDG_CONFIG_HOME="$PRIVATE/config"',
            'XDG_DATA_HOME="$PRIVATE/data"',
            'XDG_CACHE_HOME="$PRIVATE/cache"',
            'XDG_STATE_HOME="$PRIVATE/state"',
            'PYTHONDONTWRITEBYTECODE=1',
            'exec "$ROOT/bin/graphium-ultra"',
        ):
            self.assertIn(marker, launcher)
        self.assertTrue((ROOT/"RUN_WORKING_COPY.sh").stat().st_mode & stat.S_IXUSR)

    def test_ultra_launcher_and_staged_installer_are_self_contained(self):
        launcher=ROOT/"bin/graphium-ultra"
        installer=ROOT/"bin/graphium-ultra-install"
        self.assertTrue(launcher.stat().st_mode & stat.S_IXUSR)
        self.assertTrue(installer.stat().st_mode & stat.S_IXUSR)
        self.assertIn("GraphiumUltraApplication", launcher.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            stage=Path(td)/"stage"; stage.mkdir()
            subprocess.run(
                [sys.executable, str(installer), "--prefix", "/usr", "--destdir", str(stage)],
                check=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE":"1"},
            )
            prefix=stage/"usr"
            private=prefix/"lib/graphium-ultra"
            self.assertTrue((prefix/"bin/graphium-ultra").is_symlink())
            for package in ("graphium","graphium_plus","graphium_ultra"):
                self.assertTrue((private/package).is_dir())
            desktop=(prefix/"share/applications/io.github.leviagravia.GraphiumUltra.desktop").read_text(encoding="utf-8")
            self.assertIn("Name=Graphium Ultra", desktop)
            self.assertIn("Exec=graphium-ultra %F", desktop)
            self.assertIn("Icon=io.github.leviagravia.GraphiumUltra", desktop)


if __name__ == "__main__":
    unittest.main()
