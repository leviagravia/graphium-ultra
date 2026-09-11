from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest

from tests.release._common import ROOT
from graphium_plus.workspace.search import search_workspace


class PlusWorkspaceSearchGtkContractTests(unittest.TestCase):
    def test_window_installs_search_menu_action_and_reuses_open_lifecycle(self):
        source = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertIn('Gio.SimpleAction.new("find-in-workspace", None)', source)
        self.assertIn('item = Gtk.MenuItem(label="Find in Workspace…")', source)
        self.assertIn('item.set_action_name("win.find-in-workspace")', source)
        self.assertIn('search_workspace(', source)
        self.assertIn('active_buffer=self._workspace_search_active_buffer()', source)
        self.assertIn('revalidate_disk_result(report, result)', source)
        self.assertIn('if not self.open_path(result.path):', source)
        self.assertIn('self._project_view(ViewState(result.start, result.end))', source)
        self.assertNotIn('ThreadPoolExecutor', source)

    def test_dialog_is_projection_only_and_has_no_filesystem_or_search_semantics(self):
        path = ROOT / "graphium_plus/adapters/gtk/workspace_search_dialog.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertNotIn("os", imported)
        self.assertNotIn("pathlib", imported)
        self.assertNotIn("graphium.infrastructure.document_loader", imported)
        self.assertNotIn("graphium.domain.text_search", imported)
        self.assertIn("WorkspaceSearchReport", source)
        self.assertIn("WorkspaceSearchResult", source)
        self.assertIn('Gtk.TreeView(model=model)', source)

    def test_search_authority_has_no_index_database_watcher_thread_or_writer(self):
        source = (ROOT / "graphium_plus/workspace/search.py").read_text(encoding="utf-8")
        for forbidden in (
            "sqlite", "tantivy", "watchdog", "monitor_directory", "monitor_file",
            "threading", "subprocess", "GuardedFileWriter", "os.replace", "commit(",
        ):
            self.assertNotIn(forbidden, source.lower() if forbidden == "sqlite" else source)
        self.assertIn("from graphium.domain.text_search", source)
        self.assertIn("load_document", source)

    def test_fresh_nested_workspace_search_is_sandbox_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Workspace"
            (root / "A" / "B").mkdir(parents=True)
            (root / "A" / "B" / "paper.md").write_text("first\nacademic needle here\n", encoding="utf-8")
            report = search_workspace(str(root), "needle")
            self.assertEqual(len(report.results), 1)
            result = report.results[0]
            self.assertEqual(result.line, 2)
            self.assertTrue(result.relative_path.endswith("paper.md"))

    def test_desktop_scenario_uses_real_dialogs_unsaved_override_and_disk_open(self):
        scenario = (ROOT / "tests/desktop/scenarios/plus_workspace_search.py").read_text(encoding="utf-8")
        runner = (ROOT / "tests/desktop/run.py").read_text(encoding="utf-8")
        self.assertIn('title="Find in Workspace"', scenario)
        self.assertIn('title="Workspace Search Results"', scenario)
        self.assertIn('window.buffer.begin_user_action()', scenario)
        self.assertIn('assert window.core.session.modified', scenario)
        self.assertIn('assert window.core.session.logical_path == str(other_path)', scenario)
        self.assertIn('assert window.buffer.get_text(start, end, True) == "Nested needle"', scenario)
        self.assertNotIn('.emit("clicked")', scenario)
        self.assertIn("'plus_workspace_search'", runner)


if __name__ == "__main__":
    unittest.main()
