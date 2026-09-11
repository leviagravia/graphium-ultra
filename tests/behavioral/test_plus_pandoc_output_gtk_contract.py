from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[2]


class PlusPandocOutputGtkContractTests(unittest.TestCase):
    def test_file_menu_projects_one_parameterized_pandoc_action(self):
        source = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertIn('Gio.SimpleAction.new("pandoc-output", GLib.VariantType.new("s"))', source)
        self.assertIn('Gtk.MenuItem(label="Export with Pandoc")', source)
        self.assertIn('item.set_action_name("win.pandoc-output")', source)
        self.assertIn('item.set_action_target_value(GLib.Variant.new_string(descriptor.id))', source)
        self.assertNotIn('"pandoc-pdf"', source)

    def test_final_publication_revalidates_source_reference_and_target_before_one_core_commit(self):
        source = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_action_pandoc_output"
        )
        body = ast.get_source_segment(source, method) or ""
        self.assertIn("publish_pandoc_artifact(", body)
        self.assertIn("writer=self.core.writer", body)
        self.assertIn("target_observation=target", body)
        self.assertNotIn("self.core.writer.commit(", body)

        publication = (ROOT / "graphium_plus/pandoc_publication.py").read_text(encoding="utf-8")
        self.assertIn("pandoc_plan_matches_current_source", publication)
        self.assertIn("current_target = writer.observe_target(plan.destination)", publication)
        self.assertIn("if current_target != target_observation:", publication)
        self.assertEqual(publication.count("writer.commit("), 1)
        self.assertLess(publication.index("pandoc_plan_matches_current_source"), publication.index("writer.commit("))
        self.assertLess(publication.index("if current_target != target_observation:"), publication.index("writer.commit("))

    def test_gtk_adapter_does_not_own_process_thread_or_writer(self):
        source = (ROOT / "graphium_plus/adapters/gtk/pandoc_output.py").read_text(encoding="utf-8")
        for forbidden in (
            "subprocess", "threading", "GuardedFileWriter", "observe_target", ".commit(",
            "os.replace", "shell=True",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("GLib.timeout_add", source)
        self.assertIn("worker.cancel_and_join", source)
        self.assertIn("dialog.response(Gtk.ResponseType.ACCEPT)", source)
        self.assertIn("nonlocal source_id", source)
        self.assertGreaterEqual(source.count("source_id = 0"), 3)

    def test_process_worker_is_only_thread_owner_and_has_no_gtk_or_final_writer(self):
        source = (ROOT / "graphium_plus/pandoc_process.py").read_text(encoding="utf-8")
        self.assertIn("class PandocOutputWorker", source)
        self.assertIn("threading.Thread(", source)
        self.assertIn("runner.cancel_active()", source)
        for forbidden in ("import gi", "Gtk.", "GuardedFileWriter", "observe_target(", ".commit("):
            self.assertNotIn(forbidden, source)

    def test_save_chooser_reads_final_filename_after_accepted_response(self):
        source = (ROOT / "graphium_plus/adapters/gtk/pandoc_output.py").read_text(encoding="utf-8")
        self.assertIn("response = dialog.run()", source)
        self.assertIn("if response != Gtk.ResponseType.ACCEPT", source)
        self.assertIn("selected = dialog.get_filename()", source)
        self.assertNotRegex(source, r"assert\s+.*set_(?:filename|current_folder|current_name)")

    def test_close_path_cancels_and_joins_worker_before_core_close(self):
        source = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_on_delete_event"
        )
        body = ast.get_source_segment(source, method) or ""
        self.assertIn("worker.cancel_and_join(timeout_seconds=5.0)", body)
        self.assertIn("return super()._on_delete_event(*args)", body)
        self.assertLess(body.index("cancel_and_join"), body.index("super()._on_delete_event"))
        self.assertIn('self.connect("destroy", self._on_pandoc_destroy)', source)

    def test_desktop_scenario_uses_fake_pandoc_settled_save_and_exact_close_boundary(self):
        source = (ROOT / "tests/desktop/scenarios/plus_pandoc_output.py").read_text(encoding="utf-8")
        self.assertIn('pandoc 3.1.11', source)
        self.assertIn('title="Export with Pandoc — HTML"', source)
        self.assertIn('window.set_current_folder(str(path.parent))', source)
        self.assertIn('window.set_current_name(path.name)', source)
        self.assertIn('window._actions["pandoc-output"].activate(GLib.Variant.new_string("html"))', source)
        self.assertIn('source_text = "Draft unsaved [@alpha2020]."', source)
        self.assertIn('assert window.core.session.modified', source)
        self.assertIn('window.pandoc_worker.start_build(plan)', source)
        self.assertIn('window.close()', source)
        self.assertIn('os.kill(pid, 0)', source)
        self.assertNotIn('.emit("clicked")', source)
        self.assertNotRegex(source, r"assert\s+.*set_(?:filename|current_folder|current_name)")


if __name__ == "__main__":
    unittest.main()
