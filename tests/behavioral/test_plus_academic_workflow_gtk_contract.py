from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[2]


class PlusAcademicWorkflowGtkContractTests(unittest.TestCase):
    def setUp(self):
        self.window_path = ROOT / "graphium_plus/adapters/gtk/window.py"
        self.window = self.window_path.read_text(encoding="utf-8")
        self.dialog_path = ROOT / "graphium_plus/adapters/gtk/reference_dialogs.py"
        self.dialogs = self.dialog_path.read_text(encoding="utf-8")

    def test_reference_store_reuses_core_writer_and_plus_xdg_data_path(self):
        self.assertIn("MarkdownReferenceLibraryStore(", self.window)
        self.assertIn('self.core.writer, self._xdg_paths.data / "references.md"', self.window)
        tree = ast.parse(self.dialogs)
        self.assertFalse(any(isinstance(node, ast.Name) and node.id == "GuardedFileWriter" for node in ast.walk(tree)))
        for forbidden in ("sqlite3", "threading", "subprocess", "watchdog", "monitor_file", "monitor_directory"):
            self.assertNotIn(forbidden, self.dialogs)

    def test_commands_surface_matches_accepted_academic_workflow_without_placeholders(self):
        for label in ('label="Markdown"', 'label="Notes & Citations"', 'label="References"', 'label="Review"'):
            self.assertIn(label, self.window)
        for action in (
            "insert-footnote", "insert-inline-note", "go-to-footnote",
            "quick-cite", "insert-citation", "reference-library",
            "find-reference", "check-footnotes", "check-citations",
        ):
            self.assertIn(f'"{action}"', self.window)
        self.assertNotIn('Gtk.MenuItem(label="Academic")', self.window)
        for deferred in (
            "Zotero", "RIS", "Citation Gap", "Generate Bibliography",
        ):
            self.assertNotIn(deferred, self.window)
        # A5 makes Pandoc real and File-owned; it must not become an Academic
        # top-level/Commands placeholder.
        self.assertIn('Gtk.MenuItem(label="Export with Pandoc")', self.window)
        self.assertNotIn('Gtk.MenuItem(label="Academic")', self.window)

    def test_citation_actions_use_a3_authorities_and_existing_editor_transaction(self):
        for marker in (
            "plan_insert_citation(",
            "CitationDocumentMap.from_text(source.text)",
            "citation_diagnostics(",
            "self.core.editor.apply_prevalidated_programmatic_group(",
            "run_reference_picker(",
        ):
            self.assertIn(marker, self.window)
        for forbidden in ("buffer.insert(", "buffer.delete(", "begin_user_action", "end_user_action"):
            self.assertNotIn(forbidden, self.window)

    def test_reference_dialog_is_specific_bounded_and_preserves_unshown_fields(self):
        self.assertIn("replace(\n            record,", self.dialogs)
        self.assertIn('entries["Key"].set_sensitive(False)', self.dialogs)
        self.assertIn("key=record.key", self.dialogs)
        self.assertIn("_confirm_delete(parent, record)", self.dialogs)
        self.assertIn("search_references(records, query, limit=500)", self.dialogs)
        self.assertIn("search_references(records, query, limit=100)", self.dialogs)
        self.assertIn("store.save(records, snapshot.token)", self.dialogs)
        self.assertIn("if dialog.run() != Gtk.ResponseType.OK", self.dialogs)
        for forbidden in ("class Plugin", "ServiceRegistry", "Background", "Gtk.Notebook", "Gtk.Stack"):
            self.assertNotIn(forbidden, self.dialogs)


    def test_desktop_scenario_establishes_reference_and_caret_preconditions_before_actions(self):
        scenario = (ROOT / "tests/desktop/scenarios/plus_academic_workflow.py").read_text(encoding="utf-8")
        for marker in (
            "assert window.reference_store._writer is window.core.writer",
            "assert saved.saved",
            "tree.get_selection().select_path(0)",
            '_set_caret(window.buffer, len("Claim."))',
            'assert (before_insert, before_bound) == (len("Claim."), len("Claim."))',
            'window._actions["quick-cite"].activate(None)',
            'expected = "Claim.[@alpha2020]"',
        ):
            self.assertIn(marker, scenario)
        self.assertNotIn('.emit("clicked")', scenario)
        # GLib.idle_add forwards positional user_data, not arbitrary callback
        # keyword arguments. Dialog responses must therefore be captured by a
        # zero-argument callback/lambda rather than supplied as idle_add kwargs.
        scenario_tree = ast.parse(scenario)
        idle_calls = [
            node for node in ast.walk(scenario_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "idle_add"
        ]
        self.assertEqual(len(idle_calls), 2)
        self.assertTrue(all(not call.keywords for call in idle_calls))
        self.assertTrue(all(len(call.args) == 1 and isinstance(call.args[0], ast.Lambda) for call in idle_calls))

    def test_user_guide_records_workflow_and_explicit_non_scope(self):
        guide = (ROOT / "docs/user/GRAPHIUM_PLUS_USER_GUIDE.txt").read_text(encoding="utf-8")
        for marker in (
            "28. REFERENCES AND CITATIONS — GRAPHIUM PLUS",
            "Commands -> Notes & Citations -> Quick Cite",
            "Commands -> Notes & Citations -> Insert Citation",
            "Commands -> References -> Reference Library",
            "Commands -> References -> Find Reference",
            "Commands -> Review -> Check Citations",
            "does not guess whether prose \"should\" have a citation",
        ):
            self.assertIn(marker, guide)
        self.assertNotIn("Commands -> Markdown -> Academic", guide)


if __name__ == "__main__":
    unittest.main()
