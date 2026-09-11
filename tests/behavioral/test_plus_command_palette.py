from __future__ import annotations

import ast
from pathlib import Path
import unittest

from graphium.application.commands import COMMANDS
from graphium_plus.command_palette import PaletteEntry, filter_palette_entries, normalize_palette_text


ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "graphium_plus/command_palette.py"
GTK_PATH = ROOT / "graphium_plus/adapters/gtk/command_palette.py"
WINDOW_PATH = ROOT / "graphium_plus/adapters/gtk/window.py"
APPLICATION_PATH = ROOT / "graphium_plus/adapters/gtk/application.py"


def entry(label: str, breadcrumb: str, order: int, *, action: str | None = None) -> PaletteEntry:
    return PaletteEntry(label, breadcrumb, action or f"win.test-{order}", None, "", order)


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def function_segment(text: str, name: str) -> str:
    tree = ast.parse(text)
    node = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(text, node) or ""


class CommandPaletteModelTests(unittest.TestCase):
    def test_normalization_casefolds_and_collapses_whitespace(self):
        self.assertEqual(normalize_palette_text("  STRAẞE\n  Save  "), "strasse save")

    def test_empty_query_preserves_menu_order(self):
        items = (entry("Zulu", "Edit", 9), entry("Alpha", "File", 2), entry("Beta", "View", 5))
        self.assertEqual([item.order for item in filter_palette_entries(items, "")], [2, 5, 9])

    def test_exact_label_beats_prefix_then_substring(self):
        items = (
            entry("Save As", "File", 0),
            entry("Save", "File", 1),
            entry("Quick Save Helper", "Commands", 2),
        )
        self.assertEqual(
            [item.label for item in filter_palette_entries(items, "save")],
            ["Save", "Save As", "Quick Save Helper"],
        )

    def test_breadcrumb_participates_in_matching(self):
        items = (entry("Bold", "Commands > Markdown > Inline", 0), entry("Open", "File", 1))
        self.assertEqual(filter_palette_entries(items, "markdown bold"), (items[0],))

    def test_each_token_must_match_as_ordered_subsequence(self):
        items = (entry("Command Palette", "Commands", 0), entry("Companion Panel", "View", 1))
        self.assertEqual(filter_palette_entries(items, "cmd plt"), (items[0],))
        self.assertEqual(filter_palette_entries(items, "cmd xyz"), ())

    def test_word_boundary_quality_beats_general_subsequence(self):
        items = (
            entry("Insert Citation", "Commands > Notes", 0),
            entry("Music", "View", 1),
        )
        result = filter_palette_entries(items, "i c")
        self.assertEqual(result[0].label, "Insert Citation")

    def test_gap_penalty_then_shorter_label_then_menu_order_are_deterministic(self):
        items = (
            entry("Alpha Beta", "Commands", 5),
            entry("Alpine Beta", "Commands", 1),
            entry("Alpha Beta Extended", "Commands", 0),
        )
        result = filter_palette_entries(items, "ap bt")
        self.assertEqual(result[0].label, "Alpha Beta")

    def test_whitespace_only_query_is_same_as_empty_query(self):
        items = (entry("Second", "Edit", 4), entry("First", "File", 1))
        self.assertEqual(
            filter_palette_entries(items, "  \n\t "),
            filter_palette_entries(items, ""),
        )

    def test_action_implementation_name_is_not_search_corpus(self):
        item = PaletteEntry("Save", "File", "win.secret-internal-token", None, "Ctrl+S", 0)
        self.assertEqual(filter_palette_entries((item,), "secret"), ())

    def test_equal_quality_uses_original_menu_order(self):
        items = (entry("Alpha One", "Commands", 8), entry("Alpha Two", "Commands", 3))
        self.assertEqual(
            [item.order for item in filter_palette_entries(items, "alpha")],
            [3, 8],
        )

    def test_filter_is_pure_and_does_not_reorder_input_tuple(self):
        items = (entry("Zulu", "View", 7), entry("Alpha", "File", 1))
        before = tuple(items)
        filter_palette_entries(items, "a")
        self.assertEqual(items, before)



class CommandPaletteSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = source(MODEL_PATH)
        cls.gtk = source(GTK_PATH)
        cls.window = source(WINDOW_PATH)
        cls.application = source(APPLICATION_PATH)

    def test_exact_two_new_runtime_modules_are_small_authority_projection(self):
        self.assertIn("class PaletteEntry", self.model)
        self.assertIn("def filter_palette_entries", self.model)
        self.assertIn("def collect_command_palette_entries", self.gtk)
        self.assertNotIn("sqlite", (self.model + self.gtk).lower())
        self.assertNotIn("thread", (self.model + self.gtk).lower())
        self.assertNotIn("watcher", (self.model + self.gtk).lower())

    def test_command_collector_uses_core_catalog_plus_live_gtk_and_revalidates_actions(self):
        collect = function_segment(self.gtk, "collect_command_palette_entries")
        self.assertIn("COMMANDS", collect)
        self.assertIn("TOP_LEVEL_MENUS", collect)
        self.assertIn("core_action_names", collect)
        self.assertIn("append_core_menu", collect)
        self.assertIn("walk_plus", collect)
        self.assertIn("_MAX_LEAVES", collect)
        self.assertIn("_MAX_DEPTH", collect)
        self.assertIn('spec.action == "open-recent"', collect)
        self.assertIn('action_name == _SELF_ACTION', collect)
        self.assertIn("window.lookup_action", collect)
        self.assertIn("action.get_enabled()", collect)
        self.assertIn("spec.choices", collect)
        self.assertIn('breadcrumb = f"{menu_name} > {spec.label}"', collect)
        self.assertIn("target=choice_value", collect)
        self.assertIn("action_name in core_action_names", collect)

    def test_core_tab_width_choices_come_from_single_core_command_authority(self):
        tab_width = next(spec for spec in COMMANDS if spec.action == "tab-width")
        self.assertEqual(tab_width.menu, "Edit")
        self.assertEqual(tab_width.label, "Tab Width")
        self.assertEqual(
            tab_width.choices,
            (("2", "2"), ("3", "3"), ("4", "4"), ("8", "8"), ("Other…", "other")),
        )

    def test_plus_live_menu_cycle_guard_is_path_local_not_global_wrapper_identity(self):
        collect = function_segment(self.gtk, "collect_command_palette_entries")
        self.assertNotIn("seen:", collect)
        self.assertNotIn("seen.add", collect)
        self.assertNotIn("id(container)", collect)
        self.assertNotIn("repeated/cyclic", collect)
        self.assertIn("cyclic widget", collect)
        self.assertIn("ancestors: tuple[Gtk.Container, ...]", collect)
        self.assertIn("any(container is ancestor for ancestor in ancestors)", collect)
        self.assertIn("next_ancestors = ancestors + (container,)", collect)
        self.assertIn("walk_plus(submenu, path, depth + 1, next_ancestors)", collect)
        self.assertIn("walk_plus(submenu, (label,), 1, ())", collect)

    def test_projection_accepts_only_none_or_existing_string_target(self):
        target = function_segment(self.gtk, "_project_target")
        self.assertIn("action.get_parameter_type()", target)
        self.assertIn("_parameter_is_string(action)", target)
        self.assertIn('target.get_type_string() != "s"', target)
        self.assertIn("target.get_string()", target)

    def test_accelerator_hint_is_read_from_live_application_binding(self):
        hint = function_segment(self.gtk, "_shortcut_hint")
        self.assertIn("application.get_accels_for_action", hint)
        self.assertIn("Gio.Action.print_detailed_name", hint)
        self.assertNotIn("ACCELERATORS", self.gtk)

    def test_workspace_visibility_is_projected_from_live_view_menu_without_palette_registry(self):
        self.assertIn('Gtk.CheckMenuItem(label="Workspace")', self.window)
        self.assertIn('menu_item.set_action_name("win.workspace-visible")', self.window)
        collect = function_segment(self.gtk, "collect_command_palette_entries")
        self.assertIn("action.get_enabled()", collect)
        self.assertNotIn("workspace-visible", self.gtk)

    def test_activation_reresolves_enabled_action_closes_then_activates_gio(self):
        resolve = function_segment(self.gtk, "_resolve_live_action")
        activate = function_segment(self.gtk, "_activate_selected")
        self.assertIn("window.lookup_action", resolve)
        self.assertIn("action.get_enabled()", resolve)
        self.assertIn("_resolve_live_action(self.window, entry)", activate)
        self.assertLess(activate.index("self._close(restore_focus=False)"), activate.index("action.activate(target)"))
        self.assertNotIn("_action_", activate.replace("_activate_selected", ""))

    def test_palette_keyboard_contract_and_no_persistence(self):
        for key in ("Gdk.KEY_Down", "Gdk.KEY_Up", "Gdk.KEY_Return", "Gdk.KEY_Escape"):
            self.assertIn(key, self.gtk)
        self.assertIn('Gtk.Label(label="No matching commands")', self.gtk)
        self.assertIn("self.previous_focus = window.get_focus()", self.gtk)
        self.assertIn("focus.grab_focus()", self.gtk)
        self.assertNotIn("companion-panel.json", self.gtk)
        self.assertNotIn("view_settings", self.gtk)

    def test_commands_menu_owns_palette_before_groups_with_separator(self):
        install = function_segment(self.window, "_install_markdown_controls")
        self.assertIn('Gio.SimpleAction.new("command-palette", None)', install)
        self.assertIn('palette_item = Gtk.MenuItem(label="Command Palette…")', install)
        self.assertIn('palette_item.set_action_name("win.command-palette")', install)
        self.assertIn("commands_menu.append(Gtk.SeparatorMenuItem())", install)
        self.assertLess(install.index("palette_item ="), install.index("markdown_item ="))

    def test_window_route_only_opens_projection_and_does_not_mutate_document_or_focus_snapshot(self):
        handler = function_segment(self.window, "_action_command_palette")
        self.assertIn("show_command_palette(", handler)
        self.assertIn("self._focus_menubar()", handler)
        for forbidden in ("buffer.", "core.editor", "core.session", "_focus_snapshot =", "_companion_state_store"):
            self.assertNotIn(forbidden, handler)

    def test_ctrl_k_is_single_plus_accelerator_and_print_namespace_is_unchanged(self):
        startup = function_segment(self.application, "do_startup")
        self.assertIn('self.set_accels_for_action("win.command-palette", ["<Ctrl>K"])', startup)
        self.assertIn('self.set_accels_for_action("win.focus-mode", ["F9"])', startup)
        core = source(ROOT / "graphium/application/commands.py")
        self.assertIn("<Ctrl>P", core)
        self.assertIn("<Ctrl><Shift>P", core)
        runtime_hits = []
        for package in ("graphium", "graphium_plus"):
            for path in (ROOT / package).rglob("*.py"):
                text = source(path)
                if '"<Ctrl>K"' in text or "'<Ctrl>K'" in text:
                    runtime_hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(runtime_hits, ["graphium_plus/adapters/gtk/application.py"])


    def test_p3_4_integrated_oracle_uses_semantic_actions_not_synthetic_key_injection(self):
        scenario = source(ROOT / "tests/desktop/scenarios/plus_p3_integrated.py")
        self.assertNotIn("Gtk.test_widget_send_key", scenario)
        self.assertNotIn("Gdk.test_simulate_key", scenario)
        self.assertIn("focus_action.activate(None)", scenario)
        self.assertIn("palette_action.activate(None)", scenario)
        self.assertIn("dialog.close()", scenario)
        self.assertIn("tree.row_activated(", scenario)
        self.assertIn("def _assert_accelerator_binding", scenario)
        self.assertIn("Gtk.accelerator_parse(expected)", scenario)
        self.assertIn("Gtk.accelerator_parse(actual[0])", scenario)
        self.assertIn("app.get_actions_for_accel(actual[0])", scenario)
        self.assertNotIn('get_accels_for_action("win.command-palette")) == ("<Ctrl>K",)', scenario)
        self.assertIn("P3_4_PALETTE_TAB_WIDTH_ROWS", scenario)
        self.assertIn('(label, "Edit > Tab Width")', scenario)
        self.assertIn('("2", "3", "4", "8", "Other…")', scenario)
        self.assertNotIn("len(tree.get_model()) >= 4", scenario)

    def test_palette_model_is_immutable_projection_without_callback_or_index_authority(self):
        tree = ast.parse(self.model)
        palette = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PaletteEntry")
        fields = [
            node.target.id for node in palette.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        ]
        self.assertEqual(fields, ["label", "breadcrumb", "action_name", "target", "shortcut", "order"])
        self.assertIn("@dataclass(frozen=True)", self.model)
        for forbidden in ("callback", "filesystem", "Path(", "rglob(", "history", "favorite"):
            self.assertNotIn(forbidden, self.model)

    def test_gtk_palette_has_no_filesystem_scan_timer_worker_or_signal_invention(self):
        for forbidden in (
            "os.walk", ".rglob(", "GLib.timeout_add", "threading", "Thread(",
            "emit(\"activate\"", "emit('activate'", "Open Recent"):
            if forbidden == "Open Recent":
                continue
            self.assertNotIn(forbidden, self.gtk)
        self.assertIn('label == "Open Recent"', self.gtk)

    def test_palette_is_command_only_and_user_docs_do_not_claim_quick_open(self):
        guide = source(ROOT / "docs/user/GRAPHIUM_PLUS_USER_GUIDE.txt")
        self.assertIn("The palette is command-only.", guide)
        self.assertIn("It does not search Workspace filenames", guide)
        self.assertNotIn("Workspace filename results", self.gtk)



if __name__ == "__main__":
    unittest.main()
