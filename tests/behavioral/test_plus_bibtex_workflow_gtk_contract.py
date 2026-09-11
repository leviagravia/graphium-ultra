from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[2]


class PlusBibWorkflowGtkContractTests(unittest.TestCase):
    def setUp(self):
        self.window_path = ROOT / "graphium_plus/adapters/gtk/window.py"
        self.window = self.window_path.read_text(encoding="utf-8")
        self.dialog_path = ROOT / "graphium_plus/adapters/gtk/bibtex_dialogs.py"
        self.dialogs = self.dialog_path.read_text(encoding="utf-8")

    def test_references_menu_exposes_only_real_a4_actions(self):
        for label, action in (
            ("Import BibTeX/BibLaTeX…", "import-bibliography"),
            ("Export BibTeX…", "export-bibtex"),
            ("Export BibLaTeX…", "export-biblatex"),
        ):
            self.assertIn(f'("{label}", "{action}")', self.window)
            self.assertIn(f'("{action}", self._action_', self.window)
        for deferred in ("RIS", "Zotero", "CSL", "Generate Bibliography"):
            self.assertNotIn(deferred, self.window)
        # A5 Pandoc output is File-owned and separate from A4 bibliography dialogs.
        self.assertNotIn("Pandoc", self.dialogs)
        self.assertIn('Gtk.MenuItem(label="Export with Pandoc")', self.window)

    def test_import_workflow_uses_pure_a4_authority_and_canonical_store(self):
        for marker in (
            "import_bibliography(text)",
            "analyze_bibliography_import(snapshot.records, parsed.records)",
            "plan_bibliography_import(snapshot.records, parsed.records, conflict_policy=policy)",
            "store.save(plan.records, snapshot.token)",
            '"keep-existing"',
            '"replace-existing"',
            "No field-by-field or automatic merge is performed.",
        ):
            self.assertIn(marker, self.dialogs)
        self.assertNotIn("ReferenceRecord(", self.dialogs)
        self.assertNotIn("serialize_reference_library", self.dialogs)

    def test_import_file_read_delegates_to_gtk_free_bounded_input_adapter(self):
        self.assertIn("from graphium_plus.bibtex_io import read_bibliography_text", self.dialogs)
        self.assertIn("text = read_bibliography_text(path)", self.dialogs)
        self.assertNotIn("os.open(", self.dialogs)
        io_source = (ROOT / "graphium_plus/bibtex_io.py").read_text(encoding="utf-8")
        for marker in (
            "MAX_BIB_IMPORT_BYTES = 16 * 1024 * 1024",
            "target.lstat()", "stat.S_ISLNK", "stat.S_ISREG",
            'getattr(os, "O_NOFOLLOW", 0)', "os.fstat(fd)",
            'decode("utf-8")', "path changed before Graphium Plus opened it",
            "changed while Graphium Plus was reading it",
        ):
            self.assertIn(marker, io_source)


    def test_export_reuses_injected_core_writer_and_never_mutates_reference_authority(self):
        for marker in (
            "export_bibliography(snapshot.records, flavor=flavor)",
            "observation = writer.observe_target(path)",
            'writer.commit(observation, result.text.encode("utf-8"))',
            "set_do_overwrite_confirmation(True)",
        ):
            self.assertIn(marker, self.dialogs)
        tree = ast.parse(self.dialogs)
        forbidden_names = {"GuardedFileWriter", "DocumentSession", "NativeEditorController"}
        self.assertFalse(
            any(isinstance(node, ast.Name) and node.id in forbidden_names for node in ast.walk(tree))
        )

    def test_adapter_has_no_database_network_background_or_subprocess_authority(self):
        tree = ast.parse(self.dialogs)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        roots = {name.split(".")[0] for name in imports}
        self.assertFalse(roots & {"sqlite3", "subprocess", "socket", "urllib", "requests", "httpx", "threading"})
        for forbidden in ("monitor_file", "monitor_directory", "watchdog", "Gtk.Notebook", "Gtk.Stack"):
            self.assertNotIn(forbidden, self.dialogs)

    def test_desktop_scenario_must_preflight_exact_import_and_export_authorities(self):
        scenario = (ROOT / "tests/desktop/scenarios/plus_bibliography_interop.py").read_text(encoding="utf-8")
        for marker in (
            "assert window.reference_store._writer is window.core.writer",
            'assert "import-bibliography" in window._actions',
            'assert "export-bibtex" in window._actions',
            'assert "export-biblatex" in window._actions',
            'title="Import BibTeX/BibLaTeX"',
            'title="Review Bibliography Import"',
            'title="Export BibLaTeX"',
            "assert imported.key == \"beta2024\"",
            "exported_text = export_path.read_text(encoding=\"utf-8\")",
        ):
            self.assertIn(marker, scenario)
        self.assertNotIn('.emit("clicked")', scenario)

    def test_desktop_filechooser_oracle_is_settled_and_never_asserts_setter_returns(self):
        scenario_path = ROOT / "tests/desktop/scenarios/plus_bibliography_interop.py"
        scenario = scenario_path.read_text(encoding="utf-8")
        tree = ast.parse(scenario)

        # A Gtk.FileChooser setter return is not application-domain completion.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            calls = [child for child in ast.walk(node.test) if isinstance(child, ast.Call)]
            for call in calls:
                func = call.func
                if isinstance(func, ast.Attribute):
                    self.assertNotIn(func.attr, {"set_filename", "set_current_folder", "select_filename"})

        for marker in (
            "settled = action(window)",
            "if settled is False:",
            "dialog.get_current_folder()",
            "dialog.select_filename(str(path))",
            "dialog.get_filename()",
            "dialog selection did not settle before timeout",
        ):
            self.assertIn(marker, scenario)

    def test_filechooser_actions_wait_for_delayed_folder_and_selection_state(self):
        import importlib.util

        scenario_path = ROOT / "tests/desktop/scenarios/plus_bibliography_interop.py"
        spec = importlib.util.spec_from_file_location("a4b_desktop_scenario", scenario_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        class FakeChooser:
            def __init__(self):
                self.folder = None
                self.selected = None
                self.pending_folder = None
                self.pending_selection = None
                self.current_name = None
                self.responses = []

            def set_current_folder(self, value):
                self.pending_folder = value
                return False  # Deliberately non-authoritative.

            def get_current_folder(self):
                return self.folder

            def select_filename(self, value):
                self.pending_selection = value
                return False  # Deliberately non-authoritative.

            def get_filename(self):
                if self.selected is not None:
                    return self.selected
                if self.folder is not None and self.current_name is not None:
                    return str(Path(self.folder) / self.current_name)
                return None

            def set_current_name(self, value):
                self.current_name = value

            def response(self, value):
                self.responses.append(value)

            def settle_folder(self):
                self.folder = self.pending_folder

            def settle_selection(self):
                self.selected = self.pending_selection

        with self.subTest("open chooser"):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "input.bib"
                path.write_text("@book{x,title={X}}", encoding="utf-8")
                chooser = FakeChooser()
                action = module._choose_open_file(path, 7)
                self.assertFalse(action(chooser))
                self.assertEqual(chooser.responses, [])
                chooser.settle_folder()
                self.assertFalse(action(chooser))
                self.assertEqual(chooser.responses, [])
                chooser.settle_selection()
                self.assertTrue(action(chooser))
                self.assertEqual(chooser.responses, [7])

        with self.subTest("save chooser"):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "out.bib"
                chooser = FakeChooser()
                action = module._choose_save_file(path, 9)
                self.assertFalse(action(chooser))
                self.assertEqual(chooser.responses, [])
                chooser.settle_folder()
                self.assertFalse(action(chooser))
                self.assertEqual(chooser.responses, [])
                self.assertTrue(action(chooser))
                self.assertEqual(chooser.responses, [9])


if __name__ == "__main__":
    unittest.main()
