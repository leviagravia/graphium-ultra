"""Graphium Plus window: Core editor, compact toolbar and bounded Workspace."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from graphium.adapters.gtk.window import GraphiumWindow
from graphium.adapters.gtk.dialogs import show_text_document
from graphium.application.commands import COMMANDS
from graphium_plus.product import PLUS_PRODUCT_IDENTITY
from graphium_plus.markdown import build_markdown_document_map
from graphium_plus.outliner import OutlineProjection, build_outline_projection
from graphium_plus.markdown_commands import (
    ACADEMIC_MARKDOWN_ACTIONS,
    MARKDOWN_COMMANDS,
    MARKDOWN_COMMAND_GROUPS,
    MarkdownCommandError,
    plan_markdown_command,
)
from graphium.domain.edit_history import EditKind, ReplayOperation, ViewState
from graphium.domain.text_search import is_exact_match
from graphium_plus.citations import CitationDocumentMap, citation_diagnostics, plan_insert_citation
from graphium_plus.references import MarkdownReferenceLibraryStore
from graphium_plus.academic_notes import (
    ScholarlyNoteError,
    ScholarlyNotesMap,
    diagnostics_text,
    plan_insert_footnote,
    plan_insert_inline_note,
    plan_note_navigation,
)
from graphium_plus.workspace.controller import WorkspaceController
from graphium_plus.workspace.model import WorkspaceItem
from graphium_plus.workspace.operations import (
    plan_new_folder,
    plan_new_text_file,
    remap_workspace_relative_path,
    rewrite_workspace_relative_paths,
)
from graphium_plus.workspace.gio import WorkspaceGioAdapter
from graphium_plus.workspace.state import RecentWorkspaces, WorkspacePanelStateStore
from graphium_plus.surface_state import SurfaceVisibilityStore
from graphium_plus.scratchpad_save_as import (
    ScratchpadSaveAsPlan,
    commit_scratchpad_save_as,
    prepare_scratchpad_save_as,
    scratchpad_document_target_safe_to_bind,
    scratchpad_save_as_target_safe_to_bind,
)
from graphium_plus.workspace.search import (
    WorkspaceSearchBufferOverride,
    revalidate_disk_result,
    search_workspace,
)
from .workspace_panel import WorkspacePanel
from .workspace_search_dialog import request_workspace_search, choose_workspace_search_result
from .outliner_panel import OutlinerPanel
from .companion_panel import CompanionHost, CompanionPanel, CompanionPanelStateStore
from .command_palette import show_command_palette
from .reference_dialogs import run_reference_library, run_reference_picker, show_reference_details
from .bibtex_dialogs import run_bibliography_export, run_bibliography_import
from .pandoc_output import build_pandoc_artifact, choose_pandoc_destination, detect_pandoc
from .review_dialog import run_academic_review, show_reference_source_target
from graphium_plus.pandoc import (
    pandoc_format,
    pandoc_formats,
    prepare_pandoc_export_plan,
)
from graphium_plus.pandoc_process import PandocArtifactBuilder, PandocOutputWorker, PandocProcessRunner
from graphium_plus.pandoc_publication import publish_pandoc_artifact
from graphium_plus.review import build_academic_review


_TOOLBAR_LAYOUT = (
    ("new", "document-new"),
    ("open", "document-open"),
    ("save", "document-save"),
    None,
    ("undo", "edit-undo"),
    ("redo", "edit-redo"),
    None,
    ("cut", "edit-cut"),
    ("copy", "edit-copy"),
    ("paste", "edit-paste"),
    None,
    ("reload", "view-refresh"),
    ("find", "edit-find"),
    ("replace", "edit-find-replace"),
)
_COMMANDS = {spec.action: spec for spec in COMMANDS}
_DEFAULT_WORKSPACE_PANE_WIDTH = 260
_DEFAULT_OUTLINER_PANE_WIDTH = 220
_OUTLINER_REFRESH_DELAY_MS = 120


@dataclass(frozen=True)
class _FocusSnapshot:
    workspace_visible: bool
    workspace_no_show_all: bool
    workspace_position: int
    outliner_visible: bool
    outliner_no_show_all: bool
    outliner_position: int
    toolbar_visible: bool
    toolbar_no_show_all: bool
    markdown_toolbar_visible: bool
    markdown_toolbar_no_show_all: bool
    menubar_visible: bool
    menubar_no_show_all: bool
    status_bar_visible: bool
    status_bar_no_show_all: bool
    line_numbers_visible: bool
    companion_constructed: bool
    companion_visible: bool
    companion_no_show_all: bool
    companion_position: int


class GraphiumPlusWindow(GraphiumWindow):
    def __init__(
        self, application: Gtk.Application, *, identity=PLUS_PRODUCT_IDENTITY
    ) -> None:
        super().__init__(application, identity=identity)
        self._toolbar_state_store = SurfaceVisibilityStore(
            self._xdg_paths.state / "toolbar-visible.json"
        )
        self._toolbar_preferred_visible = self._toolbar_state_store.load()
        self.toolbar = self._build_toolbar()
        self._root_box.pack_start(self.toolbar, False, False, 0)
        self._root_box.reorder_child(self.toolbar, 1)
        self._install_toolbar_visibility_control()

        self.workspace = WorkspaceController()
        self.workspace_gio = WorkspaceGioAdapter()
        self.recent_workspaces = RecentWorkspaces(self._xdg_paths.state / "recent-workspaces.json")
        self._workspace_state_store = WorkspacePanelStateStore(self._xdg_paths.state / "workspace-panel.json")
        self._workspace_preferred_visible = self._workspace_state_store.load()
        self._workspace_pane_width = _DEFAULT_WORKSPACE_PANE_WIDTH
        self.workspace_panel = WorkspacePanel(
            on_open_folder=self._choose_workspace_root,
            on_recent_requested=self.recent_workspaces.paths,
            on_recent_selected=self._open_workspace_root,
            on_refresh=self._refresh_workspace,
            on_reveal=self._reveal_workspace,
            on_locate_active=self._locate_active_document,
            on_new_text_file=self._new_workspace_text_file,
            on_new_folder=self._new_workspace_folder,
            on_rename=self._rename_workspace_item,
            on_duplicate=self._duplicate_workspace_item,
            on_trash=self._trash_workspace_item,
            on_move_requested=self.move_workspace_item,
            on_open_in_graphium=self._open_workspace_text_item,
            on_activate=self._activate_workspace_item,
            on_expand=self.workspace.load_directory,
            report_error=self._report_workspace_error,
            on_close=self._request_workspace_close,
            product_name=self._identity.product_name,
        )
        self._install_workspace_search_controls()
        self._outline_state_store = SurfaceVisibilityStore(
            self._xdg_paths.state / "outline-visible.json"
        )
        self._outline_preferred_visible = self._outline_state_store.load()
        self._outliner_pane_width = _DEFAULT_OUTLINER_PANE_WIDTH
        self.outliner_panel = OutlinerPanel(
            on_activate=self._navigate_outliner, on_close=self._request_outline_close
        )
        self._outline_projection: OutlineProjection | None = None
        self._outliner_dirty = True
        self._outliner_refresh_source_id = 0

        self.reference_store = MarkdownReferenceLibraryStore(
            self.core.writer, self._xdg_paths.data / "references.md"
        )
        self.pandoc_runner = PandocProcessRunner()
        self.pandoc_worker = PandocOutputWorker(PandocArtifactBuilder(self.pandoc_runner))
        self._install_markdown_controls()
        self._install_pandoc_output_controls()
        self._markdown_toolbar_state_store = SurfaceVisibilityStore(
            self._xdg_paths.state / "markdown-toolbar-visible.json"
        )
        self._markdown_toolbar_preferred_visible = self._markdown_toolbar_state_store.load()
        self.markdown_toolbar = self._build_markdown_toolbar()
        self.editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._root_box.remove(self._editor_scroller)
        self.editor_box.pack_start(self._editor_scroller, True, True, 0)
        self.editor_box.pack_end(self.markdown_toolbar, False, False, 0)
        self._install_markdown_toolbar_visibility_control()

        self._companion_state_store = CompanionPanelStateStore(
            self._xdg_paths.state / "companion-panel.json"
        )
        self._companion_preferred_visible, self._companion_width = self._companion_state_store.load()
        self._companion_panel: CompanionPanel | None = None
        self._scratchpad_binding_quarantine: dict[str, str] = {}
        self.editor_companion_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.editor_companion_paned.pack1(self.editor_box, resize=True, shrink=False)

        self.content_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.content_paned.pack1(self.outliner_panel.widget, resize=False, shrink=False)
        self.content_paned.pack2(self.editor_companion_paned, resize=True, shrink=False)
        self.content_paned.set_position(self._outliner_pane_width)

        self.workspace_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.workspace_paned.pack1(self.workspace_panel.widget, resize=False, shrink=False)
        self.workspace_paned.pack2(self.content_paned, resize=True, shrink=False)
        self.workspace_paned.set_position(self._workspace_pane_width)
        self._root_box.pack_start(self.workspace_paned, True, True, 0)
        self._install_workspace_visibility_control()
        self._install_outline_visibility_control()
        self.workspace_toggle = self._build_workspace_toggle()
        self.toolbar.insert(Gtk.SeparatorToolItem(), -1)
        self.toolbar.insert(self.workspace_toggle, -1)

        self.buffer.connect("changed", self._on_outliner_buffer_changed)
        self.buffer.connect("notify::cursor-position", self._on_outliner_cursor_changed)
        self.connect("destroy", self._on_outliner_destroy)
        self.connect("destroy", self._on_pandoc_destroy)
        self._focus_snapshot: _FocusSnapshot | None = None
        self._install_focus_mode_control()
        self._install_companion_controls()
        self.connect("destroy", self._on_companion_destroy)
        if self._companion_preferred_visible:
            self._project_companion_visibility(True, client="clips")
        if self._outline_preferred_visible:
            self._refresh_outliner_now()

    def _outline_effectively_visible(self) -> bool:
        return bool(self._outline_preferred_visible and self.outliner_panel.widget.get_visible())

    def _cancel_outliner_refresh(self) -> None:
        source_id = int(self._outliner_refresh_source_id)
        self._outliner_refresh_source_id = 0
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass

    def _on_outliner_buffer_changed(self, _buffer) -> None:
        self._outliner_dirty = True
        if self._outline_effectively_visible():
            self._schedule_outliner_refresh()
        self._on_document_text_changed()

    def _on_document_text_changed(self) -> None:
        """Edition extension hook for projections derived from live document text."""
        return None

    def _schedule_outliner_refresh(self) -> None:
        if not self._outline_effectively_visible():
            return
        source_id = int(self._outliner_refresh_source_id)
        if source_id:
            GLib.source_remove(source_id)
        self._outliner_refresh_source_id = GLib.timeout_add(
            _OUTLINER_REFRESH_DELAY_MS, self._refresh_outliner_now
        )

    def _refresh_outliner_now(self) -> bool:
        self._outliner_refresh_source_id = 0
        if not self._outline_effectively_visible():
            return False
        snapshot = self.core.editor.capture_programmatic_source()
        document_map = build_markdown_document_map(snapshot.text)
        projection = build_outline_projection(snapshot.text, document_map)
        self._outline_projection = projection
        self._outliner_dirty = False
        self.outliner_panel.render(projection)
        self._sync_outliner_cursor()
        return False

    def _on_outliner_cursor_changed(self, _buffer, _pspec) -> None:
        if self._outline_effectively_visible() and not self._outliner_dirty:
            self._sync_outliner_cursor()

    def _sync_outliner_cursor(self) -> None:
        projection = self._outline_projection
        if projection is None:
            self.outliner_panel.select_source_start(None)
            return
        insert = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        entry = projection.entry_at_or_before(insert)
        self.outliner_panel.select_source_start(
            None if entry is None else entry.source_start
        )

    def _navigate_outliner(self, content_start: int) -> None:
        projection = self._outline_projection
        if projection is None:
            return
        snapshot = self.core.editor.capture_programmatic_source()
        if not projection.matches_text(snapshot.text):
            self._outliner_dirty = True
            self._schedule_outliner_refresh()
            return
        target = max(0, min(int(content_start), len(snapshot.text)))
        self._project_view(ViewState(target, target))
        self.text_view.scroll_to_mark(self.buffer.get_insert(), 0.08, False, 0.0, 0.0)
        self.text_view.grab_focus()
        self._sync_outliner_cursor()

    def _on_outliner_destroy(self, *_args) -> None:
        source_id = int(self._outliner_refresh_source_id)
        self._outliner_refresh_source_id = 0
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass

    def _install_markdown_controls(self) -> None:
        callbacks = {
            "insert-footnote": self._action_insert_footnote,
            "insert-inline-note": self._action_insert_inline_note,
            "go-to-footnote": self._action_go_to_footnote,
            "check-footnotes": self._action_check_footnotes,
        }
        for spec in MARKDOWN_COMMANDS:
            action = Gio.SimpleAction.new(spec.action, None)
            if spec.action in ACADEMIC_MARKDOWN_ACTIONS:
                action.connect("activate", callbacks[spec.action])
            else:
                action.connect("activate", self._action_markdown_edit)
            self.add_action(action)
            self._actions[spec.action] = action

        palette_action = Gio.SimpleAction.new("command-palette", None)
        palette_action.connect("activate", self._action_command_palette)
        self.add_action(palette_action)
        self._actions["command-palette"] = palette_action

        for action_name, callback in (
            ("reference-library", self._action_reference_library),
            ("find-reference", self._action_find_reference),
            ("quick-cite", self._action_quick_cite),
            ("insert-citation", self._action_insert_citation),
            ("check-citations", self._action_check_citations),
            ("academic-review", self._action_academic_review),
            ("import-bibliography", self._action_import_bibliography),
            ("export-bibtex", self._action_export_bibtex),
            ("export-biblatex", self._action_export_biblatex),
        ):
            action = Gio.SimpleAction.new(action_name, None)
            action.connect("activate", callback)
            self.add_action(action)
            self._actions[action_name] = action

        menubar = next(
            (child for child in self._root_box.get_children() if isinstance(child, Gtk.MenuBar)),
            None,
        )
        if menubar is None:
            raise RuntimeError("Graphium Plus menu bar is unavailable")

        commands_item = Gtk.MenuItem(label="Commands")
        commands_menu = Gtk.Menu()

        palette_item = Gtk.MenuItem(label="Command Palette…")
        palette_item.set_action_name("win.command-palette")
        commands_menu.append(palette_item)
        commands_menu.append(Gtk.SeparatorMenuItem())

        markdown_item = Gtk.MenuItem(label="Markdown")
        markdown_menu = Gtk.Menu()
        for group in MARKDOWN_COMMAND_GROUPS:
            if group == "Academic":
                continue
            group_item = Gtk.MenuItem(label=group)
            group_menu = Gtk.Menu()
            for spec in MARKDOWN_COMMANDS:
                if spec.group != group:
                    continue
                item = Gtk.MenuItem(label=spec.label)
                item.set_action_name(f"win.{spec.action}")
                group_menu.append(item)
            group_item.set_submenu(group_menu)
            markdown_menu.append(group_item)
        markdown_item.set_submenu(markdown_menu)
        commands_menu.append(markdown_item)

        notes_item = Gtk.MenuItem(label="Notes & Citations")
        notes_menu = Gtk.Menu()
        for label, action_name in (
            ("Insert Footnote", "insert-footnote"),
            ("Insert Inline Note", "insert-inline-note"),
            ("Go to Footnote / Reference", "go-to-footnote"),
            ("Quick Cite…", "quick-cite"),
            ("Insert Citation…", "insert-citation"),
        ):
            item = Gtk.MenuItem(label=label)
            item.set_action_name(f"win.{action_name}")
            notes_menu.append(item)
        notes_item.set_submenu(notes_menu)
        commands_menu.append(notes_item)

        references_item = Gtk.MenuItem(label="References")
        references_menu = Gtk.Menu()
        for label, action_name in (
            ("Reference Library…", "reference-library"),
            ("Find Reference…", "find-reference"),
            ("Import BibTeX/BibLaTeX…", "import-bibliography"),
            ("Export BibTeX…", "export-bibtex"),
            ("Export BibLaTeX…", "export-biblatex"),
        ):
            item = Gtk.MenuItem(label=label)
            item.set_action_name(f"win.{action_name}")
            references_menu.append(item)
        references_item.set_submenu(references_menu)
        commands_menu.append(references_item)

        review_item = Gtk.MenuItem(label="Review")
        review_menu = Gtk.Menu()
        for label, action_name in (
            ("Check Footnotes", "check-footnotes"),
            ("Check Citations", "check-citations"),
            ("Academic Review…", "academic-review"),
        ):
            item = Gtk.MenuItem(label=label)
            item.set_action_name(f"win.{action_name}")
            review_menu.append(item)
        review_item.set_submenu(review_menu)
        commands_menu.append(review_item)

        commands_item.set_submenu(commands_menu)
        children = list(menubar.get_children())
        help_index = next(
            (index for index, item in enumerate(children) if item.get_label() == "Help"),
            len(children),
        )
        menubar.insert(commands_item, help_index)
        commands_item.show_all()

    def _action_command_palette(self, *_args) -> None:
        show_command_palette(
            self,
            self._focus_menubar(),
            lambda message: self._ui.show_warning("Command Palette", message),
        )

    def _build_markdown_toolbar(self) -> Gtk.Toolbar:
        toolbar = Gtk.Toolbar()
        toolbar.set_style(Gtk.ToolbarStyle.ICONS)
        toolbar.set_icon_size(Gtk.IconSize.SMALL_TOOLBAR)
        toolbar.set_show_arrow(True)
        toolbar.get_style_context().add_class("inline-toolbar")

        previous_group = None
        for spec in MARKDOWN_COMMANDS:
            if spec.toolbar_markup is None:
                continue
            if previous_group is not None and spec.group != previous_group:
                toolbar.insert(Gtk.SeparatorToolItem(), -1)
            glyph = Gtk.Label()
            glyph.set_markup(spec.toolbar_markup)
            button = Gtk.ToolButton()
            button.set_label(spec.label)
            button.set_icon_widget(glyph)
            button.set_tooltip_text(spec.label)
            button.set_action_name(f"win.{spec.action}")
            toolbar.insert(button, -1)
            glyph.show()
            previous_group = spec.group
        toolbar.show_all()
        return toolbar

    def _action_markdown_edit(self, action: Gio.SimpleAction, _parameter) -> None:
        try:
            snapshot, before_view = self._academic_source()
            plan = plan_markdown_command(
                action.get_name(),
                source_text=snapshot.text,
                source_state_id=snapshot.state_id,
                before_view=before_view,
            )
            if not plan.changed:
                return
            self.core.editor.apply_prevalidated_programmatic_group(
                operations=plan.operations,
                expected_source_state_id=plan.source_state_id,
                final_text=plan.final_text,
                before_view=plan.before_view,
                target_view=plan.target_view,
            )
            self._refresh_projection()
            self.text_view.scroll_to_mark(self.buffer.get_insert(), 0.08, False, 0.0, 0.0)
            self.text_view.grab_focus()
        except MarkdownCommandError as exc:
            self._ui.show_warning("Markdown command was not applied", str(exc))
        except Exception as exc:
            self._ui.show_warning("Markdown command failed", str(exc))

    def _academic_source(self):
        snapshot = self.core.editor.capture_programmatic_source()
        return snapshot, ViewState(snapshot.insert_offset, snapshot.selection_bound_offset)

    def _apply_academic_edit(self, planner) -> None:
        try:
            snapshot, before_view = self._academic_source()
            plan = planner(
                source_text=snapshot.text,
                source_state_id=snapshot.state_id,
                before_view=before_view,
            )
            self.core.editor.apply_prevalidated_programmatic_group(
                operations=plan.operations,
                expected_source_state_id=plan.source_state_id,
                final_text=plan.final_text,
                before_view=plan.before_view,
                target_view=plan.target_view,
            )
            self._refresh_projection()
            self.text_view.scroll_to_mark(self.buffer.get_insert(), 0.08, False, 0.0, 0.0)
            self.text_view.grab_focus()
        except Exception as exc:
            self._ui.show_warning("Academic note edit was not applied", str(exc))

    def _action_insert_footnote(self, *_args) -> None:
        self._apply_academic_edit(plan_insert_footnote)

    def _action_insert_inline_note(self, *_args) -> None:
        self._apply_academic_edit(plan_insert_inline_note)

    def _action_go_to_footnote(self, *_args) -> None:
        try:
            snapshot, before_view = self._academic_source()
            plan = plan_note_navigation(source_text=snapshot.text, before_view=before_view)
            if plan.target_view is None:
                self._ui.show_warning(
                    "Footnote navigation",
                    "Place the caret on a footnote reference or definition that has a counterpart.",
                )
                return
            current = self.core.editor.capture_programmatic_source()
            if not plan.matches_text(current.text):
                raise ScholarlyNoteError("footnote navigation became stale before projection")
            self._project_view(plan.target_view)
            self.text_view.scroll_to_mark(self.buffer.get_insert(), 0.08, False, 0.0, 0.0)
            self.text_view.grab_focus()
        except Exception as exc:
            self._ui.show_warning("Footnote navigation", str(exc))

    def _action_check_footnotes(self, *_args) -> None:
        try:
            snapshot, _before_view = self._academic_source()
            report = diagnostics_text(ScholarlyNotesMap.from_text(snapshot.text))
        except Exception as exc:
            self._ui.show_warning("Check Footnotes", str(exc))
            return
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text="Footnote Check",
        )
        dialog.format_secondary_text(report)
        try:
            dialog.run()
        finally:
            dialog.destroy()

    def _reference_snapshot(self):
        snapshot = self.reference_store.load()
        if snapshot.diagnostics:
            self._ui.show_warning(
                "Reference Library",
                "\n".join(item.message for item in snapshot.diagnostics),
            )
            return None
        return snapshot

    def _action_reference_library(self, *_args) -> None:
        try:
            run_reference_library(self, self.reference_store)
        except Exception as exc:
            self._ui.show_warning("Reference Library", str(exc))

    def _action_find_reference(self, *_args) -> None:
        snapshot = self._reference_snapshot()
        if snapshot is None:
            return
        if not snapshot.records:
            self._ui.show_warning("Find Reference", "The Reference Library is empty.")
            return
        chosen = run_reference_picker(
            self, snapshot.records, title="Find Reference", accept_label="View"
        )
        if chosen is not None:
            show_reference_details(self, chosen[0])

    def _apply_citation_from_picker(self, *, with_locator: bool) -> None:
        snapshot = self._reference_snapshot()
        if snapshot is None:
            return
        if not snapshot.records:
            self._ui.show_warning("Insert Citation", "The Reference Library is empty.")
            return
        chosen = run_reference_picker(
            self,
            snapshot.records,
            title="Insert Citation" if with_locator else "Quick Cite",
            accept_label="Insert",
            with_locator=with_locator,
        )
        if chosen is None:
            return
        record, locator = chosen
        try:
            source, before_view = self._academic_source()
            plan = plan_insert_citation(
                source_text=source.text,
                source_state_id=source.state_id,
                before_view=before_view,
                key=record.key,
                locator=locator,
                records=snapshot.records,
            )
            self.core.editor.apply_prevalidated_programmatic_group(
                operations=plan.operations,
                expected_source_state_id=plan.source_state_id,
                final_text=plan.final_text,
                before_view=plan.before_view,
                target_view=plan.target_view,
            )
            self._refresh_projection()
            self.text_view.scroll_to_mark(self.buffer.get_insert(), 0.08, False, 0.0, 0.0)
            self.text_view.grab_focus()
        except Exception as exc:
            self._ui.show_warning("Insert Citation", str(exc))

    def _action_quick_cite(self, *_args) -> None:
        self._apply_citation_from_picker(with_locator=False)

    def _action_insert_citation(self, *_args) -> None:
        self._apply_citation_from_picker(with_locator=True)

    def _action_check_citations(self, *_args) -> None:
        snapshot = self._reference_snapshot()
        if snapshot is None:
            return
        try:
            source, _before_view = self._academic_source()
            diagnostics = citation_diagnostics(
                CitationDocumentMap.from_text(source.text), snapshot.records
            )
        except Exception as exc:
            self._ui.show_warning("Check Citations", str(exc))
            return
        report = (
            "No citation problems found."
            if not diagnostics
            else "\n".join(f"• {item.message}" for item in diagnostics)
        )
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text="Citation Check",
        )
        dialog.format_secondary_text(report)
        try:
            dialog.run()
        finally:
            dialog.destroy()

    def _action_academic_review(self, *_args) -> None:
        try:
            source = self.core.editor.capture_programmatic_source()
            references = self.reference_store.load()
            report = build_academic_review(source.text, references)
            selected = run_academic_review(self, report)
            if selected is None:
                return

            current_source = self.core.editor.capture_programmatic_source()
            current_references = self.reference_store.load()
            if not report.matches(current_source.text, current_references.token):
                self._ui.show_warning(
                    "Academic Review",
                    "The review became stale because the document or Reference Library changed.",
                )
                return

            target = selected.target
            if target.scope == "document":
                if target.offset is None:
                    raise RuntimeError("document review target has no source offset")
                start = max(0, min(target.offset, len(current_source.text)))
                end = max(start, min(start + target.length, len(current_source.text)))
                self._project_view(ViewState(end, start))
                self.text_view.grab_focus()
                return

            if target.line is None:
                raise RuntimeError("reference review target has no source line")
            if not show_reference_source_target(
                self,
                path=self.reference_store.path,
                line=target.line,
                expected_token=current_references.token,
            ):
                self._ui.show_warning(
                    "Academic Review",
                    "The Reference Library changed before its diagnostic target could be shown.",
                )
        except Exception as exc:
            self._ui.show_warning("Academic Review", str(exc))


    def _action_import_bibliography(self, *_args) -> None:
        run_bibliography_import(self, self.reference_store)

    def _action_export_bibtex(self, *_args) -> None:
        run_bibliography_export(
            self, self.reference_store, self.core.writer, flavor="bibtex"
        )

    def _action_export_biblatex(self, *_args) -> None:
        run_bibliography_export(
            self, self.reference_store, self.core.writer, flavor="biblatex"
        )

    def _install_pandoc_output_controls(self) -> None:
        action = Gio.SimpleAction.new("pandoc-output", GLib.VariantType.new("s"))
        action.connect("activate", self._action_pandoc_output)
        self.add_action(action)
        self._actions["pandoc-output"] = action

        menubar = next(
            (child for child in self._root_box.get_children() if isinstance(child, Gtk.MenuBar)),
            None,
        )
        if menubar is None:
            raise RuntimeError("Graphium Plus File menu is unavailable")
        file_item = next(
            (item for item in menubar.get_children() if item.get_label() == "File"),
            None,
        )
        if file_item is None or file_item.get_submenu() is None:
            raise RuntimeError("Graphium Plus File submenu is unavailable")
        output_menu = Gtk.Menu()
        for descriptor in pandoc_formats():
            item = Gtk.MenuItem(label=descriptor.label)
            item.set_action_name("win.pandoc-output")
            item.set_action_target_value(GLib.Variant.new_string(descriptor.id))
            output_menu.append(item)
        output_item = Gtk.MenuItem(label="Export with Pandoc")
        output_item.set_submenu(output_menu)
        file_item.get_submenu().append(output_item)
        output_item.show_all()

    def _pandoc_document_directory(self) -> str | None:
        logical = self.core.session.logical_path
        if not logical:
            return None
        return str(Path(logical).expanduser().absolute().parent)

    def _pandoc_suggested_stem(self) -> str:
        logical = self.core.session.logical_path
        if not logical:
            return "Untitled"
        return Path(logical).stem or "Untitled"

    def _action_pandoc_output(self, _action: Gio.SimpleAction, parameter) -> None:
        if parameter is None:
            self._ui.show_warning("Pandoc Output", "Choose a Pandoc output format.")
            return
        try:
            format_id = parameter.get_string()
            descriptor = pandoc_format(format_id)
            identity = detect_pandoc(self, self.pandoc_worker)
            if identity is None:
                return
            destination = choose_pandoc_destination(
                self,
                descriptor,
                suggested_stem=self._pandoc_suggested_stem(),
            )
            if not destination:
                return
            target = self.core.writer.observe_target(destination)
            if getattr(target, "existing", None) is not None and not self._ui.confirm_overwrite(destination):
                return

            source = self.core.editor.capture_programmatic_source()
            references = self.reference_store.load()
            blocking = tuple(item for item in references.diagnostics if item.blocking)
            if blocking:
                raise ValueError(blocking[0].message)
            plan = prepare_pandoc_export_plan(
                identity=identity,
                format_id=descriptor.id,
                destination=destination,
                document_text=source.text,
                source_state_id=source.state_id,
                document_directory=self._pandoc_document_directory(),
                reference_records=references.records,
                reference_token=references.token,
            )
            artifact = build_pandoc_artifact(self, self.pandoc_worker, plan)
            if artifact is None:
                return
            if not artifact.succeeded:
                raise RuntimeError(artifact.message or "Pandoc did not produce an output artifact.")

            current_source = self.core.editor.capture_programmatic_source()
            current_references = self.reference_store.load()
            blocking = tuple(item for item in current_references.diagnostics if item.blocking)
            if blocking:
                raise RuntimeError(blocking[0].message)
            publication = publish_pandoc_artifact(
                plan=plan,
                artifact=artifact,
                current_text=current_source.text,
                current_state_id=current_source.state_id,
                current_reference_token=current_references.token,
                writer=self.core.writer,
                target_observation=target,
            )
            result = publication.write_result
            if result.warnings:
                self._ui.show_warning(
                    "Pandoc Output Saved with Warnings",
                    "\n".join(result.warnings),
                )
        except Exception as exc:
            self._ui.show_warning("Pandoc Output", str(exc))
        finally:
            self.text_view.grab_focus()

    def _on_delete_event(self, *args) -> bool:
        worker = getattr(self, "pandoc_worker", None)
        if worker is not None and worker.active:
            try:
                stopped = worker.cancel_and_join(timeout_seconds=5.0)
            except Exception as exc:
                self._ui.show_warning("Pandoc Output", f"Pandoc could not be stopped: {exc}")
                return True
            if not stopped:
                self._ui.show_warning(
                    "Pandoc Output",
                    f"Pandoc could not be stopped safely; {self._identity.product_name} will remain open.",
                )
                return True
        return super()._on_delete_event(*args)

    def _on_pandoc_destroy(self, *_args) -> None:
        try:
            self.pandoc_worker.cancel_and_join(timeout_seconds=5.0)
        except Exception:
            pass

    def _focus_menubar(self) -> Gtk.MenuBar:
        menubar = next(
            (child for child in self._root_box.get_children() if isinstance(child, Gtk.MenuBar)),
            None,
        )
        if menubar is None:
            raise RuntimeError("Graphium Plus menu bar is unavailable")
        return menubar

    def _install_focus_mode_control(self) -> None:
        action = Gio.SimpleAction.new_stateful(
            "focus-mode", None, GLib.Variant.new_boolean(False)
        )
        action.connect("activate", self._action_focus_mode)
        self.add_action(action)
        self._actions["focus-mode"] = action

        menubar = self._focus_menubar()
        view_item = next(
            (item for item in menubar.get_children() if item.get_label() == "View"),
            None,
        )
        view_menu = None if view_item is None else view_item.get_submenu()
        if view_menu is None:
            raise RuntimeError("Graphium Plus View menu is unavailable")
        item = Gtk.CheckMenuItem(label="Focus Mode")
        item.set_action_name("win.focus-mode")
        view_menu.append(item)
        item.show()

    def _capture_focus_snapshot(self) -> _FocusSnapshot:
        menubar = self._focus_menubar()
        workspace = self.workspace_panel.widget
        outliner = self.outliner_panel.widget
        return _FocusSnapshot(
            workspace_visible=workspace.get_visible(),
            workspace_no_show_all=workspace.get_no_show_all(),
            workspace_position=self.workspace_paned.get_position(),
            outliner_visible=outliner.get_visible(),
            outliner_no_show_all=outliner.get_no_show_all(),
            outliner_position=self.content_paned.get_position(),
            toolbar_visible=self.toolbar.get_visible(),
            toolbar_no_show_all=self.toolbar.get_no_show_all(),
            markdown_toolbar_visible=self.markdown_toolbar.get_visible(),
            markdown_toolbar_no_show_all=self.markdown_toolbar.get_no_show_all(),
            menubar_visible=menubar.get_visible(),
            menubar_no_show_all=menubar.get_no_show_all(),
            status_bar_visible=self._status_bar.get_visible(),
            status_bar_no_show_all=self._status_bar.get_no_show_all(),
            line_numbers_visible=self.text_view.line_numbers_visible,
            companion_constructed=self._companion_panel is not None,
            companion_visible=bool(self._companion_panel and self._companion_panel.widget.get_visible()),
            companion_no_show_all=bool(self._companion_panel and self._companion_panel.widget.get_no_show_all()),
            companion_position=self.editor_companion_paned.get_position(),
        )

    @staticmethod
    def _set_focus_hidden(widget: Gtk.Widget) -> None:
        widget.set_no_show_all(True)
        widget.hide()

    @staticmethod
    def _restore_focus_widget(widget: Gtk.Widget, *, visible: bool, no_show_all: bool) -> None:
        widget.set_no_show_all(no_show_all)
        widget.set_visible(visible)

    def _enter_focus_mode(self, snapshot: _FocusSnapshot) -> None:
        menubar = self._focus_menubar()
        self._cancel_outliner_refresh()
        for widget in (
            self.workspace_panel.widget,
            self.outliner_panel.widget,
            self.toolbar,
            self.markdown_toolbar,
            menubar,
            self._status_bar,
        ):
            self._set_focus_hidden(widget)
        if self._companion_panel is not None:
            self._set_focus_hidden(self._companion_panel.widget)
        self.text_view.set_line_numbers_visible(False)
        self._focus_snapshot = snapshot
        for name in (
            "workspace-visible", "toolbar-visible", "outline-visible", "markdown-toolbar-visible",
            "companion-visible", "companion-clips", "companion-scratchpad",
        ):
            self._actions[name].set_enabled(False)
        self._set_boolean_action(self._actions["focus-mode"], True)
        self.text_view.grab_focus()

    def _exit_focus_mode(self, snapshot: _FocusSnapshot) -> None:
        menubar = self._focus_menubar()
        self._restore_focus_widget(
            self.workspace_panel.widget,
            visible=snapshot.workspace_visible,
            no_show_all=snapshot.workspace_no_show_all,
        )
        self._restore_focus_widget(
            self.outliner_panel.widget,
            visible=snapshot.outliner_visible,
            no_show_all=snapshot.outliner_no_show_all,
        )
        self._restore_focus_widget(
            self.toolbar,
            visible=snapshot.toolbar_visible,
            no_show_all=snapshot.toolbar_no_show_all,
        )
        self._restore_focus_widget(
            self.markdown_toolbar,
            visible=snapshot.markdown_toolbar_visible,
            no_show_all=snapshot.markdown_toolbar_no_show_all,
        )
        self._restore_focus_widget(
            menubar,
            visible=snapshot.menubar_visible,
            no_show_all=snapshot.menubar_no_show_all,
        )
        self._restore_focus_widget(
            self._status_bar,
            visible=snapshot.status_bar_visible,
            no_show_all=snapshot.status_bar_no_show_all,
        )
        self.text_view.set_line_numbers_visible(snapshot.line_numbers_visible)
        if snapshot.companion_constructed and self._companion_panel is not None:
            self._restore_focus_widget(
                self._companion_panel.widget,
                visible=snapshot.companion_visible,
                no_show_all=snapshot.companion_no_show_all,
            )
        self.workspace_paned.set_position(snapshot.workspace_position)
        self.content_paned.set_position(snapshot.outliner_position)
        self.editor_companion_paned.set_position(snapshot.companion_position)
        self._focus_snapshot = None
        if snapshot.outliner_visible and self._outliner_dirty:
            self._schedule_outliner_refresh()
        for name in (
            "workspace-visible", "toolbar-visible", "outline-visible", "markdown-toolbar-visible",
            "companion-visible", "companion-clips", "companion-scratchpad",
        ):
            self._actions[name].set_enabled(True)
        self._set_boolean_action(self._actions["focus-mode"], False)
        self.text_view.grab_focus()

    def _action_focus_mode(self, _action: Gio.SimpleAction, _parameter) -> None:
        snapshot = self._focus_snapshot
        if snapshot is not None:
            self._exit_focus_mode(snapshot)
            return
        try:
            snapshot = self._capture_focus_snapshot()
        except Exception as exc:
            self._ui.show_warning("Focus Mode", f"Focus Mode could not start: {exc}")
            self.text_view.grab_focus()
            return
        self._enter_focus_mode(snapshot)

    def _install_companion_controls(self) -> None:
        visible_action = Gio.SimpleAction.new_stateful(
            "companion-visible",
            None,
            GLib.Variant.new_boolean(self._companion_preferred_visible),
        )
        visible_action.connect("change-state", self._change_companion_visibility)
        self.add_action(visible_action)
        self._actions["companion-visible"] = visible_action

        for name, client in (("companion-clips", "clips"), ("companion-scratchpad", "scratchpad")):
            action = Gio.SimpleAction.new(name, None)
            action.connect(
                "activate",
                lambda _action, _parameter, target=client: self._action_companion(target),
            )
            self.add_action(action)
            self._actions[name] = action

        menubar = self._focus_menubar()
        view_item = next(
            (item for item in menubar.get_children() if item.get_label() == "View"), None
        )
        view_menu = None if view_item is None else view_item.get_submenu()
        if view_menu is None:
            raise RuntimeError("Graphium Plus View menu is unavailable")

        visible_item = Gtk.CheckMenuItem(label="Companion Panel")
        visible_item.set_action_name("win.companion-visible")
        view_menu.append(visible_item)
        visible_item.show()

        client_parent = Gtk.MenuItem(label="Companion Focus")
        client_menu = Gtk.Menu()
        for label, action_name in (
            ("Clips", "win.companion-clips"),
            ("Scratchpad", "win.companion-scratchpad"),
        ):
            item = Gtk.MenuItem(label=label)
            item.set_action_name(action_name)
            client_menu.append(item)
            item.show()
        client_parent.set_submenu(client_menu)
        view_menu.append(client_parent)
        client_parent.show()

    def _companion_document_path(self) -> str | None:
        state = self.core.session.file_state
        return None if state is None else state.binding.logical_path

    def _companion_scratchpad_unavailable_reason(self) -> str | None:
        path = self._companion_document_path()
        if path is None:
            return None
        return self._scratchpad_binding_quarantine.get(os.path.abspath(path))

    def _quarantine_scratchpad_binding(self, document_path: str, reason: str) -> None:
        self._scratchpad_binding_quarantine[os.path.abspath(document_path)] = str(reason)

    def _clear_scratchpad_binding_quarantine(self, document_path: str | None) -> None:
        if document_path:
            self._scratchpad_binding_quarantine.pop(os.path.abspath(document_path), None)

    def _quarantine_unverified_scratchpad_target(
        self, document_path: str, reason: str
    ) -> bool:
        safe = scratchpad_document_target_safe_to_bind(
            writer=self.core.writer, target_document_path=document_path
        )
        if not safe:
            self._quarantine_scratchpad_binding(document_path, reason)
        return not safe

    def _companion_selected_text(self) -> str:
        bounds = self.buffer.get_selection_bounds()
        return "" if not bounds else self.buffer.get_text(bounds[0], bounds[1], True)

    def _companion_insert_text(self, text: str) -> None:
        if not text:
            return
        source = self.core.editor.capture_programmatic_source()
        lo = min(source.insert_offset, source.selection_bound_offset)
        hi = max(source.insert_offset, source.selection_bound_offset)
        operations: list[ReplayOperation] = []
        if hi > lo:
            operations.append(ReplayOperation(EditKind.DELETE, lo, source.text[lo:hi]))
        operations.append(ReplayOperation(EditKind.INSERT, lo, text))
        self.core.editor.apply_prevalidated_programmatic_group(
            operations=tuple(operations),
            expected_source_state_id=source.state_id,
            final_text=source.text[:lo] + text + source.text[hi:],
            before_view=ViewState(source.insert_offset, source.selection_bound_offset),
            target_view=ViewState(lo + len(text), lo + len(text)),
        )
        self._refresh_projection()
        self.text_view.grab_focus()

    def _companion_copy_text(self, text: str) -> None:
        clipboard = Gtk.Clipboard.get_for_display(self.get_display(), Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()

    def _report_companion_error(self, message: str) -> None:
        self._ui.show_warning("Companion Panel", str(message))

    def _ensure_companion_panel(self) -> CompanionPanel:
        if self._companion_panel is None:
            host = CompanionHost(
                selected_text=self._companion_selected_text, insert_text=self._companion_insert_text,
                document_path=self._companion_document_path,
                scratchpad_unavailable_reason=self._companion_scratchpad_unavailable_reason,
                copy_text=self._companion_copy_text,
                focus_editor=self.text_view.grab_focus, report_error=self._report_companion_error,
            )
            panel = CompanionPanel(
                self, self.core.writer, self._xdg_paths.data, host, self._request_companion_close
            )
            panel.widget.set_no_show_all(True)
            panel.widget.hide()
            self.editor_companion_paned.pack2(panel.widget, resize=False, shrink=True)
            self._companion_panel = panel
        return self._companion_panel

    def _companion_current_width(self) -> int:
        total = int(getattr(self.editor_companion_paned.get_allocation(), "width", 0) or 0)
        position = int(self.editor_companion_paned.get_position() or 0)
        return max(184, total - position) if total > position > 0 else max(184, int(self._companion_width))

    def _project_companion_visibility(self, visible: bool, *, client: str | None = None) -> None:
        if not visible:
            if self._companion_panel is not None:
                self._companion_panel.widget.set_no_show_all(True)
                self._companion_panel.widget.hide()
            self.text_view.grab_focus()
            return

        panel = self._ensure_companion_panel()
        if client is not None:
            panel.focus_client(client)
        total = int(getattr(self.editor_companion_paned.get_allocation(), "width", 0) or 900)
        maximum = max(184, total - 360)
        cap = max(184, int(total * 0.22))
        width = max(184, min(int(self._companion_width), maximum, cap))
        panel.widget.set_no_show_all(False)
        panel.widget.show_all()
        self.editor_companion_paned.set_position(max(1, total - width))
        self._companion_width = width

    def _change_companion_visibility(
        self, action: Gio.SimpleAction, requested: GLib.Variant
    ) -> None:
        visible = bool(requested.get_boolean())
        current = bool(action.get_state().get_boolean())
        if visible == current:
            if visible:
                self._project_companion_visibility(True)
            return
        if current and self._companion_panel is not None and self._companion_panel.widget.get_visible():
            self._companion_width = self._companion_current_width()
        try:
            self._companion_state_store.save(visible, self._companion_width)
        except Exception as exc:
            self._ui.show_warning(
                "Companion Panel", f"Companion Panel visibility could not be saved: {exc}"
            )
            return
        self._companion_preferred_visible = visible
        action.set_state(GLib.Variant.new_boolean(visible))
        self._project_companion_visibility(visible)

    def _action_companion(self, client: str) -> None:
        panel = self._ensure_companion_panel()
        action = self._actions["companion-visible"]
        if not bool(action.get_state().get_boolean()):
            action.change_state(GLib.Variant.new_boolean(True))
        else:
            self._project_companion_visibility(True)
        panel.focus_client(client)

    def _request_companion_close(self) -> None:
        self._actions["companion-visible"].change_state(GLib.Variant.new_boolean(False))

    def _on_companion_destroy(self, *_args) -> None:
        try:
            self._companion_state_store.save(
                bool(self._companion_preferred_visible),
                self._companion_current_width(),
            )
        except Exception:
            pass

    def _rebind_companion_scratchpad(self) -> None:
        if self._companion_panel is not None:
            self._companion_panel.rebind_scratchpad()

    def _request_workspace_close(self) -> None:
        self._actions["workspace-visible"].change_state(GLib.Variant.new_boolean(False))

    def _request_outline_close(self) -> None:
        self._actions["outline-visible"].change_state(GLib.Variant.new_boolean(False))

    def _install_workspace_visibility_control(self) -> None:
        action = Gio.SimpleAction.new_stateful(
            "workspace-visible", None, GLib.Variant.new_boolean(self._workspace_preferred_visible)
        )
        action.connect("change-state", self._change_workspace_visibility)
        self.add_action(action)
        self._actions["workspace-visible"] = action
        view_item = next(
            (item for item in self._focus_menubar().get_children() if item.get_label() == "View"), None
        )
        view_menu = None if view_item is None else view_item.get_submenu()
        if view_menu is None:
            raise RuntimeError("Graphium Plus View menu is unavailable")
        menu_item = Gtk.CheckMenuItem(label="Workspace")
        menu_item.set_action_name("win.workspace-visible")
        view_menu.append(menu_item)
        menu_item.show()
        self._project_workspace_visibility(self._workspace_preferred_visible)

    def _project_workspace_visibility(self, visible: bool) -> None:
        panel = self.workspace_panel.widget
        panel.set_no_show_all(not visible)
        if visible:
            panel.show_all()
            self.workspace_paned.set_position(self._workspace_pane_width)
        else:
            panel.hide()

    def _change_workspace_visibility(self, action: Gio.SimpleAction, requested: GLib.Variant) -> None:
        visible = bool(requested.get_boolean())
        current = bool(action.get_state().get_boolean())
        if visible == current:
            return
        try:
            self._workspace_state_store.save(visible)
        except Exception as exc:
            self._ui.show_warning("Workspace", f"Workspace visibility could not be saved: {exc}")
            return
        if current and self.workspace_panel.widget.get_visible():
            position = int(self.workspace_paned.get_position())
            if position > 0:
                self._workspace_pane_width = position
        self._workspace_preferred_visible = visible
        action.set_state(GLib.Variant.new_boolean(visible))
        self._project_workspace_visibility(visible)

    def _build_workspace_toggle(self) -> Gtk.ToggleToolButton:
        button = Gtk.ToggleToolButton()
        button.set_label("Workspace")
        button.set_icon_name("folder-symbolic")
        button.set_tooltip_text("Show or hide Workspace")
        button.set_action_name("win.workspace-visible")
        return button

    @staticmethod
    def _project_optional_surface(widget: Gtk.Widget, visible: bool) -> None:
        widget.set_no_show_all(not visible)
        if visible:
            widget.show_all()
        else:
            widget.hide()

    def _install_toolbar_visibility_control(self) -> None:
        action = Gio.SimpleAction.new_stateful(
            "toolbar-visible", None, GLib.Variant.new_boolean(self._toolbar_preferred_visible)
        )
        action.connect("change-state", self._change_toolbar_visibility)
        self.add_action(action)
        self._actions["toolbar-visible"] = action

        view_item = next(
            (item for item in self._focus_menubar().get_children() if item.get_label() == "View"),
            None,
        )
        view_menu = None if view_item is None else view_item.get_submenu()
        if view_menu is None:
            raise RuntimeError("Graphium Plus View submenu is unavailable")
        menu_item = Gtk.CheckMenuItem(label="Toolbar")
        menu_item.set_action_name("win.toolbar-visible")
        view_menu.append(menu_item)
        menu_item.show()
        self._project_optional_surface(self.toolbar, self._toolbar_preferred_visible)

    def _change_toolbar_visibility(self, action: Gio.SimpleAction, requested: GLib.Variant) -> None:
        visible = bool(requested.get_boolean())
        current = bool(action.get_state().get_boolean())
        if visible == current:
            return
        try:
            self._toolbar_state_store.save(visible)
        except Exception as exc:
            self._ui.show_warning("Toolbar", f"Toolbar visibility could not be saved: {exc}")
            return
        self._toolbar_preferred_visible = visible
        action.set_state(GLib.Variant.new_boolean(visible))
        self._project_optional_surface(self.toolbar, visible)

    def _install_markdown_toolbar_visibility_control(self) -> None:
        action = Gio.SimpleAction.new_stateful(
            "markdown-toolbar-visible",
            None,
            GLib.Variant.new_boolean(self._markdown_toolbar_preferred_visible),
        )
        action.connect("change-state", self._change_markdown_toolbar_visibility)
        self.add_action(action)
        self._actions["markdown-toolbar-visible"] = action

        view_item = next(
            (item for item in self._focus_menubar().get_children() if item.get_label() == "View"),
            None,
        )
        view_menu = None if view_item is None else view_item.get_submenu()
        if view_menu is None:
            raise RuntimeError("Graphium Plus View submenu is unavailable")
        menu_item = Gtk.CheckMenuItem(label="Markdown Toolbar")
        menu_item.set_action_name("win.markdown-toolbar-visible")
        view_menu.append(menu_item)
        menu_item.show()
        self._project_optional_surface(
            self.markdown_toolbar, self._markdown_toolbar_preferred_visible
        )

    def _change_markdown_toolbar_visibility(
        self, action: Gio.SimpleAction, requested: GLib.Variant
    ) -> None:
        visible = bool(requested.get_boolean())
        current = bool(action.get_state().get_boolean())
        if visible == current:
            return
        try:
            self._markdown_toolbar_state_store.save(visible)
        except Exception as exc:
            self._ui.show_warning(
                "Markdown Toolbar", f"Markdown Toolbar visibility could not be saved: {exc}"
            )
            return
        self._markdown_toolbar_preferred_visible = visible
        action.set_state(GLib.Variant.new_boolean(visible))
        self._project_optional_surface(self.markdown_toolbar, visible)

    def _install_outline_visibility_control(self) -> None:
        action = Gio.SimpleAction.new_stateful(
            "outline-visible", None, GLib.Variant.new_boolean(self._outline_preferred_visible)
        )
        action.connect("change-state", self._change_outline_visibility)
        self.add_action(action)
        self._actions["outline-visible"] = action

        view_item = next(
            (item for item in self._focus_menubar().get_children() if item.get_label() == "View"),
            None,
        )
        view_menu = None if view_item is None else view_item.get_submenu()
        if view_menu is None:
            raise RuntimeError("Graphium Plus View submenu is unavailable")
        menu_item = Gtk.CheckMenuItem(label="Outline")
        menu_item.set_action_name("win.outline-visible")
        view_menu.append(menu_item)
        menu_item.show()
        self._project_outline_visibility(self._outline_preferred_visible)

    def _project_outline_visibility(self, visible: bool) -> None:
        panel = self.outliner_panel.widget
        panel.set_no_show_all(not visible)
        if visible:
            panel.show_all()
            self.content_paned.set_position(self._outliner_pane_width)
        else:
            self._cancel_outliner_refresh()
            panel.hide()

    def _change_outline_visibility(self, action: Gio.SimpleAction, requested: GLib.Variant) -> None:
        visible = bool(requested.get_boolean())
        current = bool(action.get_state().get_boolean())
        if visible == current:
            return
        try:
            self._outline_state_store.save(visible)
        except Exception as exc:
            self._ui.show_warning("Outline", f"Outline visibility could not be saved: {exc}")
            return
        if current and self.outliner_panel.widget.get_visible():
            position = int(self.content_paned.get_position())
            if position > 0:
                self._outliner_pane_width = position
        self._outline_preferred_visible = visible
        action.set_state(GLib.Variant.new_boolean(visible))
        self._project_outline_visibility(visible)
        if visible and self._outliner_dirty:
            self._schedule_outliner_refresh()

    @staticmethod
    def _build_toolbar() -> Gtk.Toolbar:
        toolbar = Gtk.Toolbar()
        toolbar.set_style(Gtk.ToolbarStyle.ICONS)
        toolbar.set_icon_size(Gtk.IconSize.SMALL_TOOLBAR)
        for item in _TOOLBAR_LAYOUT:
            if item is None:
                toolbar.insert(Gtk.SeparatorToolItem(), -1)
                continue
            action, icon_name = item
            spec = _COMMANDS[action]
            button = Gtk.ToolButton()
            button.set_label(spec.label)
            button.set_icon_name(icon_name)
            button.set_tooltip_text(spec.label)
            button.set_action_name(f"win.{action}")
            toolbar.insert(button, -1)
        return toolbar

    def _install_workspace_search_controls(self) -> None:
        action = Gio.SimpleAction.new("find-in-workspace", None)
        action.connect("activate", self._action_find_in_workspace)
        self.add_action(action)
        self._actions["find-in-workspace"] = action

        menubar = next(
            (child for child in self._root_box.get_children() if isinstance(child, Gtk.MenuBar)),
            None,
        )
        if menubar is None:
            raise RuntimeError("Graphium Plus menu bar is unavailable")
        search_item = next(
            (item for item in menubar.get_children() if item.get_label() == "Search"),
            None,
        )
        search_menu = None if search_item is None else search_item.get_submenu()
        if search_menu is None:
            raise RuntimeError("Graphium Search menu is unavailable")
        search_menu.append(Gtk.SeparatorMenuItem())
        item = Gtk.MenuItem(label="Find in Workspace…")
        item.set_action_name("win.find-in-workspace")
        search_menu.append(item)
        item.show()

    def _workspace_search_active_buffer(self):
        root = self.workspace.root
        logical_path = self.core.session.logical_path
        if root is None or not logical_path:
            return None
        try:
            self.workspace.relative_path_for_document(logical_path)
        except Exception:
            return None
        snapshot = self.core.editor.capture_programmatic_source()
        return WorkspaceSearchBufferOverride(
            path=logical_path,
            text=snapshot.text,
            state_id=snapshot.state_id,
        )

    def _action_find_in_workspace(self, *_args) -> None:
        root = self.workspace.root
        if root is None:
            self._ui.show_warning("Find in Workspace", "Open a Workspace folder first.")
            return
        request = request_workspace_search(self)
        if request is None:
            return
        try:
            report = search_workspace(
                root,
                request.query,
                match_case=request.match_case,
                active_buffer=self._workspace_search_active_buffer(),
            )
        except Exception as exc:
            self._ui.show_warning("Find in Workspace", str(exc))
            return
        if not report.results:
            message = "No matches were found in the current Workspace."
            if report.diagnostics:
                message += f" {len(report.diagnostics)} files or folders were skipped or bounded."
            self._ui.show_warning("Find in Workspace", message)
            return
        selected = choose_workspace_search_result(self, report)
        if selected is not None:
            self._navigate_workspace_search_result(report, selected)

    def _navigate_workspace_search_result(self, report, result) -> None:
        if self.workspace.root != report.root or not report.contains(result):
            self._ui.show_warning("Find in Workspace", "The Workspace search result is stale; run the search again.")
            return

        if result.source_kind == "active-buffer":
            logical_path = self.core.session.logical_path
            snapshot = self.core.editor.capture_programmatic_source()
            current_path = os.path.abspath(logical_path) if logical_path else None
            if (
                current_path != result.path
                or snapshot.state_id != result.source_state_id
                or hashlib.sha256(snapshot.text.encode("utf-8")).hexdigest() != result.text_sha256
                or not is_exact_match(
                    snapshot.text, report.query, result.start, result.end,
                    match_case=report.match_case,
                )
            ):
                self._ui.show_warning("Find in Workspace", "The active document changed after the search; run the search again.")
                return
            self._project_view(ViewState(result.start, result.end))
            self.text_view.scroll_to_mark(self.buffer.get_insert(), 0.08, False, 0.0, 0.0)
            self.text_view.grab_focus()
            return

        revalidation = revalidate_disk_result(report, result)
        if not revalidation.current:
            self._ui.show_warning("Find in Workspace", revalidation.reason or "The search result is stale; run the search again.")
            return
        if not self.open_path(result.path):
            return
        snapshot = self.core.editor.capture_programmatic_source()
        if (
            hashlib.sha256(snapshot.text.encode("utf-8")).hexdigest() != result.text_sha256
            or not is_exact_match(
                snapshot.text, report.query, result.start, result.end,
                match_case=report.match_case,
            )
        ):
            self._ui.show_warning("Find in Workspace", "The file changed while it was being opened; the old match was not selected.")
            return
        self._project_view(ViewState(result.start, result.end))
        self.text_view.scroll_to_mark(self.buffer.get_insert(), 0.08, False, 0.0, 0.0)
        self.text_view.grab_focus()

    def _choose_workspace_root(self) -> None:
        dialog = Gtk.FileChooserNative.new(
            "Open Workspace Folder",
            self,
            Gtk.FileChooserAction.SELECT_FOLDER,
            "Open",
            "Cancel",
        )
        try:
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                path = dialog.get_filename()
                if path:
                    self._open_workspace_root(path)
        finally:
            dialog.destroy()

    def _open_workspace_root(self, path: str) -> None:
        try:
            listing = self.workspace.bind_root(path)
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return
        self.workspace_panel.render_root(listing)
        try:
            self.recent_workspaces.touch(listing.root)
        except Exception as exc:
            self._report_workspace_error(f"Workspace opened, but Recent Workspaces could not be saved: {exc}")

    def _refresh_workspace(self) -> None:
        expanded = self.workspace_panel.expanded_paths()
        selected = self.workspace_panel.selected_item()
        selected_relative = selected.relative_path if selected is not None else None
        try:
            listing = self.workspace.refresh()
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return
        self.workspace_panel.render_root(listing, restore_expanded=expanded)
        if selected_relative:
            self.workspace_panel.select_relative_path(selected_relative)

    def _locate_active_document(self) -> bool:
        try:
            relative = self.workspace.relative_path_for_document(
                self._active_document_physical_path()
            )
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return False
        action = self._actions["workspace-visible"]
        if not bool(action.get_state().get_boolean()):
            action.change_state(GLib.Variant.new_boolean(True))
            if not bool(action.get_state().get_boolean()):
                return
        if self.workspace_panel.locate_relative_path(relative):
            return True
        self._report_workspace_error("The active document is not currently visible in Workspace.")
        return False

    def _creation_destination(self) -> str | None:
        try:
            return self.workspace.creation_parent(self.workspace_panel.selected_item())
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return None

    def _prompt_workspace_creation(
        self,
        *,
        title: str,
        action_label: str,
        destination: str,
        name_label: str,
        placeholder: str = "",
        choose_text_format: bool = False,
    ) -> tuple[str, str | None] | None:
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, action_label, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        location = Gtk.Label(label=f"Create inside: {destination}")
        location.set_xalign(0.0)
        box.pack_start(location, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        entry = Gtk.Entry()
        entry.set_placeholder_text(placeholder)
        entry.set_activates_default(True)
        row.pack_start(Gtk.Label(label=name_label), False, False, 0)
        row.pack_start(entry, True, True, 0)
        box.pack_start(row, False, False, 0)
        formats = None
        if choose_text_format:
            formats = Gtk.ComboBoxText()
            formats.append(".txt", "Plain text (.txt)")
            formats.append(".md", "Markdown (.md)")
            formats.set_active_id(".txt")
            format_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            format_row.pack_start(Gtk.Label(label="Format:"), False, False, 0)
            format_row.pack_start(formats, False, False, 0)
            box.pack_start(format_row, False, False, 0)
        ok = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        ok.set_sensitive(False)
        entry.connect("changed", lambda widget: ok.set_sensitive(bool(widget.get_text().strip())))
        dialog.show_all()
        entry.grab_focus()
        try:
            if dialog.run() != Gtk.ResponseType.OK:
                return None
            suffix = formats.get_active_id() if formats is not None else None
            return entry.get_text(), suffix
        finally:
            dialog.destroy()

    def _prompt_new_text_file(self, destination: str) -> tuple[str, str] | None:
        result = self._prompt_workspace_creation(
            title="New Text File",
            action_label="Create and Open",
            destination=destination,
            name_label="File name:",
            placeholder="Chapter_1",
            choose_text_format=True,
        )
        return None if result is None else (result[0], result[1] or ".txt")

    def _prompt_new_folder(self, destination: str) -> str | None:
        result = self._prompt_workspace_creation(
            title="New Folder",
            action_label="Create Folder",
            destination=destination,
            name_label="Folder name:",
        )
        return None if result is None else result[0]

    def _refresh_workspace_after_mutation(self, target_path: str, parent_path: str) -> None:
        expanded = self.workspace_panel.expanded_paths()
        try:
            listing = self.workspace.refresh()
        except Exception as exc:
            self._report_workspace_error(f"The item was created, but Workspace could not refresh: {exc}")
            return
        self.workspace_panel.render_root(listing, restore_expanded=expanded)
        relative = os.path.relpath(target_path, listing.root)
        parent_relative = os.path.relpath(parent_path, listing.root)
        if parent_relative == ".":
            parent_relative = ""
        self.workspace_panel.select_relative_path(relative, parent_relative=parent_relative)

    def _refresh_workspace_after_removal(self, parent_path: str) -> None:
        expanded = self.workspace_panel.expanded_paths()
        try:
            listing = self.workspace.refresh()
        except Exception as exc:
            self._report_workspace_error(f"The item was moved to Trash, but Workspace could not refresh: {exc}")
            return
        self.workspace_panel.render_root(listing, restore_expanded=expanded)
        parent_relative = os.path.relpath(parent_path, listing.root)
        if parent_relative == ".":
            self.workspace_panel.tree.get_selection().unselect_all()
            return
        grandparent = os.path.dirname(parent_relative)
        if grandparent == ".":
            grandparent = ""
        self.workspace_panel.select_relative_path(parent_relative, parent_relative=grandparent)

    def _new_workspace_text_file(self) -> None:
        parent = self._creation_destination()
        if parent is None:
            return
        request = self._prompt_new_text_file(parent)
        if request is not None:
            self.create_workspace_text_file(request[0], request[1], parent_path=parent)

    def _resume_after_aborted_workspace_open(self) -> None:
        self._schedule_external_monitor_bind()
        self._refresh_projection()
        self.text_view.grab_focus()

    def create_workspace_text_file(self, name: str, suffix: str = ".txt", *, parent_path: str | None = None) -> bool:
        parent = self._creation_destination() if parent_path is None else parent_path
        if parent is None:
            return False
        try:
            plan = plan_new_text_file(self.workspace.root, parent, name, suffix=suffix)
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return False

        self._suspend_external_monitor()
        permit = self.core.lifecycle.prepare_document_replacement("open a new Workspace text file")
        if permit is None:
            self._resume_after_aborted_workspace_open()
            return False
        try:
            result = self.workspace_gio.create(plan)
        except Exception as exc:
            self._report_workspace_error(f"The text file could not be created: {exc}")
            self._resume_after_aborted_workspace_open()
            return False
        if not result.success:
            if result.committed:
                self._refresh_workspace_after_mutation(result.path, plan.parent_path)
            self._report_workspace_error(result.message or "The text file could not be created.")
            self._resume_after_aborted_workspace_open()
            return False

        self._refresh_workspace_after_mutation(result.path, plan.parent_path)
        opened = self.open_path(result.path, replacement_permit=permit)
        if not opened:
            self._report_workspace_error(f"The text file was created, but it could not be opened in {self._identity.product_name}.")
        return opened

    def _new_workspace_folder(self) -> None:
        parent = self._creation_destination()
        if parent is None:
            return
        name = self._prompt_new_folder(parent)
        if name is not None:
            self.create_workspace_folder(name, parent_path=parent)

    def create_workspace_folder(self, name: str, *, parent_path: str | None = None) -> bool:
        parent = self._creation_destination() if parent_path is None else parent_path
        if parent is None:
            return False
        try:
            plan = plan_new_folder(self.workspace.root, parent, name)
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return False
        try:
            result = self.workspace_gio.create(plan)
        except Exception as exc:
            self._report_workspace_error(f"The folder could not be created: {exc}")
            return False
        if not result.success:
            if result.committed:
                self._refresh_workspace_after_mutation(result.path, plan.parent_path)
            self._report_workspace_error(result.message or "The folder could not be created.")
            return False
        self._refresh_workspace_after_mutation(result.path, plan.parent_path)
        return True

    def _refresh_workspace_after_move(
        self, plan, expanded: tuple[str, ...], selected_relative: str | None
    ) -> None:
        source_relative = os.path.relpath(plan.source_path, plan.root)
        target_relative = os.path.relpath(plan.target_path, plan.root)
        observed_moved = not os.path.lexists(plan.source_path) and os.path.lexists(plan.target_path)
        restore = expanded
        restored_selection = selected_relative
        if observed_moved:
            restore = rewrite_workspace_relative_paths(expanded, source_relative, target_relative)
            if selected_relative:
                restored_selection = remap_workspace_relative_path(
                    selected_relative, source_relative, target_relative
                )
            destination_relative = os.path.relpath(plan.destination_path, plan.root)
            if destination_relative != ".":
                restore = tuple(dict.fromkeys((*restore, destination_relative)))
        try:
            listing = self.workspace.refresh()
        except Exception as exc:
            self._report_workspace_error(f"Workspace move occurred or was attempted, but refresh failed: {exc}")
            return
        self.workspace_panel.render_root(listing, restore_expanded=restore)
        if observed_moved:
            parent_relative = os.path.relpath(plan.destination_path, listing.root)
            if parent_relative == ".":
                parent_relative = ""
            self.workspace_panel.select_relative_path(target_relative, parent_relative=parent_relative)
        elif restored_selection:
            self.workspace_panel.select_relative_path(restored_selection)

    def move_workspace_item(self, source: WorkspaceItem, destination: WorkspaceItem | None) -> bool:
        expanded = self.workspace_panel.expanded_paths()
        selected = self.workspace_panel.selected_item()
        selected_relative = selected.relative_path if selected is not None else None
        try:
            plan = self.workspace.plan_move(
                source, destination, active_document_path=self._active_document_physical_path()
            )
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return False

        session = self.core.session
        before_document_context = self._document_context_path()
        accepted_state = session.file_state
        active_physical = self._active_document_physical_path()
        direct_active = bool(active_physical and os.path.abspath(active_physical) == plan.source_path)
        if direct_active:
            if accepted_state is None:
                self._report_workspace_error("The active document has no accepted disk identity for this move.")
                return False
            binding = accepted_state.binding
            logical = os.path.abspath(binding.logical_path)
            canonical = os.path.abspath(binding.canonical_path) if binding.canonical_path else None
            if logical != plan.source_path or canonical != plan.source_path or os.path.islink(binding.logical_path):
                self._report_workspace_error(
                    "The active document reaches this file through an alias or symbolic link; the move was rejected."
                )
                return False
            self._suspend_external_monitor()
            try:
                self.core.writer.observe_target(plan.source_path, expected_file_state=accepted_state)
            except Exception as exc:
                self._schedule_external_monitor_bind()
                self._report_workspace_error(f"The active document changed before the move could commit: {exc}")
                return False

        try:
            result = self.workspace_gio.move(plan)
        except Exception as exc:
            if direct_active:
                self._schedule_external_monitor_bind()
            self._report_workspace_error(f"The selected item could not be moved: {exc}")
            return False

        if not result.success:
            if result.committed or getattr(result, "companion_committed", False):
                self._refresh_workspace_after_move(plan, expanded, selected_relative)
            if direct_active and result.committed and getattr(result, "object_token", None) is not None:
                current_token = self.workspace_gio._current_token(plan.target_path)
                if current_token == result.object_token:
                    try:
                        post_move = self.core.lifecycle.loader(plan.target_path)
                        session.retarget_file_binding(post_move.file_state)
                        self._clear_external_file_alert()
                        self._refresh_projection()
                        self._schedule_external_monitor_bind()
                        self._notify_document_context_change(before_document_context)
                    except Exception as exc:
                        self._schedule_external_monitor_bind()
                        self._report_workspace_error(
                            "The filesystem move committed, but the active document binding could not be "
                            f"reconciled safely: {exc}. Use Save As if recovery is required."
                        )
                        return False
                else:
                    self._schedule_external_monitor_bind()
            elif direct_active:
                self._schedule_external_monitor_bind()
            self._report_workspace_error(result.message or "The selected item could not be moved.")
            return False

        if direct_active:
            try:
                post_move = self.core.lifecycle.loader(plan.target_path)
                session.retarget_file_binding(post_move.file_state)
            except Exception as exc:
                self._refresh_workspace_after_move(plan, expanded, selected_relative)
                self._refresh_projection()
                self._schedule_external_monitor_bind()
                self._report_workspace_error(
                    "The filesystem move succeeded, but the active document binding could not be "
                    f"reconciled safely: {exc}. Use Save As if recovery is required."
                )
                return False
            self._clear_external_file_alert()
            self._refresh_projection()
            self._schedule_external_monitor_bind()

        self._refresh_workspace_after_move(plan, expanded, selected_relative)
        if direct_active:
            self._notify_document_context_change(before_document_context)
        return True

    def _prompt_workspace_rename(self, item: WorkspaceItem) -> str | None:
        dialog = Gtk.Dialog(title="Rename Workspace Item", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Rename", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        entry = Gtk.Entry()
        entry.set_text(item.name)
        entry.select_region(0, -1)
        entry.set_activates_default(True)
        row.pack_start(Gtk.Label(label="New name:"), False, False, 0)
        row.pack_start(entry, True, True, 0)
        box.pack_start(row, False, False, 0)
        ok = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        entry.connect("changed", lambda widget: ok.set_sensitive(bool(widget.get_text().strip())))
        dialog.show_all()
        entry.grab_focus()
        try:
            return entry.get_text() if dialog.run() == Gtk.ResponseType.OK else None
        finally:
            dialog.destroy()

    def _active_document_physical_path(self) -> str | None:
        state = self.core.session.file_state
        if state is not None and state.binding.canonical_path:
            return state.binding.canonical_path
        return self.core.session.logical_path

    def _rename_workspace_item(self) -> None:
        selected = self.workspace_panel.selected_item()
        if selected is None:
            self._report_workspace_error("Select one Workspace file or folder to rename.")
            return
        name = self._prompt_workspace_rename(selected)
        if name is not None:
            self.rename_workspace_item(name)

    def rename_workspace_item(self, name: str) -> bool:
        selected = self.workspace_panel.selected_item()
        try:
            plan = self.workspace.plan_rename(
                selected, name, active_document_path=self._active_document_physical_path()
            )
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return False
        try:
            result = self.workspace_gio.rename(plan)
        except Exception as exc:
            self._report_workspace_error(f"The selected item could not be renamed: {exc}")
            return False
        if not result.success:
            if result.committed or getattr(result, "companion_committed", False):
                self._refresh_workspace_after_mutation(result.path, plan.parent_path)
            self._report_workspace_error(result.message or "The selected item could not be renamed.")
            return False
        self._refresh_workspace_after_mutation(result.path, plan.parent_path)
        return True

    def _duplicate_workspace_item(self) -> None:
        self.duplicate_workspace_item()

    def duplicate_workspace_item(self) -> bool:
        selected = self.workspace_panel.selected_item()
        try:
            plan = self.workspace.plan_duplicate(selected)
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return False
        try:
            result = self.workspace_gio.duplicate(plan)
        except Exception as exc:
            self._report_workspace_error(f"The selected file could not be duplicated: {exc}")
            return False
        if not result.success:
            if result.committed or getattr(result, "companion_committed", False):
                self._refresh_workspace_after_mutation(result.path, plan.parent_path)
            self._report_workspace_error(result.message or "The selected file could not be duplicated.")
            return False
        self._refresh_workspace_after_mutation(result.path, plan.parent_path)
        if result.message:
            self.workspace_panel.status.set_text(result.message)
        return True

    def _confirm_workspace_trash(self, plan) -> bool:
        kind = "folder" if plan.source_is_directory else "file"
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Move “{plan.source_name}” to Trash?",
        )
        dialog.format_secondary_text(
            f"This {kind} will be moved to the system Trash. "
            f"{self._identity.product_name} will not permanently delete it as a fallback."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Move to Trash", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        try:
            return dialog.run() == Gtk.ResponseType.ACCEPT
        finally:
            dialog.destroy()

    def _trash_workspace_item(self) -> None:
        selected = self.workspace_panel.selected_item()
        try:
            plan = self.workspace.plan_trash(
                selected, active_document_path=self._active_document_physical_path()
            )
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return
        if not self._confirm_workspace_trash(plan):
            return
        try:
            result = self.workspace_gio.trash(plan)
        except Exception as exc:
            self._report_workspace_error(f"The selected item could not be moved to Trash: {exc}")
            return
        if result.accepted:
            self._refresh_workspace_after_removal(plan.parent_path)
        if not result.success:
            self._report_workspace_error(result.message or "The selected item could not be moved to Trash.")

    def _open_workspace_text_item(self, item: WorkspaceItem) -> None:
        """Open one current Workspace text item through Graphium's canonical lifecycle."""
        try:
            activation = self.workspace.activation_for(item)
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return
        if activation.kind in ("blocked", "missing"):
            self._report_workspace_error(activation.message)
            return
        if activation.kind != "internal":
            self._report_workspace_error(f"Only .txt and .md Workspace files open in {self._identity.product_name}.")
            return
        self.open_path(activation.path)

    def _activate_workspace_item(self, item: WorkspaceItem) -> None:
        try:
            activation = self.workspace.activation_for(item)
        except Exception as exc:
            self._report_workspace_error(str(exc))
            return
        if activation.kind in ("blocked", "missing"):
            self._report_workspace_error(activation.message)
            return
        if activation.kind == "internal":
            self.open_path(activation.path)
            return
        if activation.kind == "external":
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(activation.path).get_uri(), None)
            except Exception as exc:
                self._report_workspace_error(f"Could not open the selected file: {exc}")

    @staticmethod
    def _file_manager_request(method: str, target_path: str) -> None:
        uri = Gio.File.new_for_path(target_path).get_uri()
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        connection.call_sync(
            "org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1",
            method,
            GLib.Variant("(ass)", ([uri], "")),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

    def _reveal_workspace(self) -> None:
        selected = self.workspace_panel.selected_item()
        target = selected.path if selected is not None else self.workspace.root
        if not target:
            self._report_workspace_error("No Workspace folder is selected.")
            return
        method = "ShowItems" if selected is not None else "ShowFolders"
        try:
            self._file_manager_request(method, target)
            return
        except Exception:
            pass

        # FileManager1 is optional. Fall back to opening the containing directory,
        # without pretending that the selected item can still be highlighted.
        directory = (
            target
            if selected is None or (os.path.isdir(target) and not os.path.islink(target))
            else os.path.dirname(target)
        )
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(directory).get_uri(), None)
        except Exception as exc:
            self._report_workspace_error(f"Could not reveal the Workspace location: {exc}")


    def offer_startup_recovery(self, explicit_path: str | None = None):
        """Extend Core recovery with the single Plus/Ultra document-context invalidation boundary."""
        before = self._document_context_path()
        result = super().offer_startup_recovery(explicit_path)
        if result.recovered:
            self._notify_document_context_change(before)
        return result

    def _document_context_path(self) -> str | None:
        """Logical path authority for projections whose meaning depends on document location."""
        return self.core.session.logical_path

    def _on_document_context_changed(
        self, before_path: str | None, after_path: str | None
    ) -> None:
        """One edition-safe invalidation boundary for path-dependent projections."""
        if before_path == after_path:
            return
        self._rebind_companion_scratchpad()

    def _notify_document_context_change(self, before_path: str | None) -> None:
        self._on_document_context_changed(before_path, self._document_context_path())

    def _action_new(self, *_args) -> None:
        before = self._document_context_path()
        super()._action_new(*_args)
        self._notify_document_context_change(before)

    def _action_open(self, *_args) -> None:
        before = self._document_context_path()
        super()._action_open(*_args)
        self._notify_document_context_change(before)

    def _action_open_recent(self, _action, parameter) -> None:
        before = self._document_context_path()
        super()._action_open_recent(_action, parameter)
        self._notify_document_context_change(before)

    def open_path(self, path: str, *, replacement_permit=None) -> bool:
        before = self._document_context_path()
        completed = super().open_path(path, replacement_permit=replacement_permit)
        if completed:
            self._notify_document_context_change(before)
        return completed

    def _action_save(self, *_args) -> None:
        before = self._document_context_path()
        if before is None:
            self._action_save_as(*_args)
            return
        super()._action_save(*_args)
        self._notify_document_context_change(before)

    def _prepare_scratchpad_save_as_plan(
        self, before_path: str | None, target_path: str
    ) -> ScratchpadSaveAsPlan:
        return prepare_scratchpad_save_as(
            writer=self.core.writer,
            source_document_path=before_path,
            target_document_path=target_path,
            active_file_state=self.core.session.file_state,
        )

    def _commit_scratchpad_save_as_plan(self, plan: ScratchpadSaveAsPlan) -> bool:
        try:
            commit_scratchpad_save_as(writer=self.core.writer, plan=plan)
        except Exception as exc:
            target_safe = scratchpad_save_as_target_safe_to_bind(
                writer=self.core.writer, plan=plan
            )
            if not target_safe:
                self._quarantine_scratchpad_binding(
                    plan.target_document_path,
                    "Scratchpad is unavailable for this document because its Save As "
                    "companion could not be committed safely and the destination sidecar "
                    "is no longer provably absent. Resolve the sidecar conflict before "
                    "using Scratchpad for this document.",
                )
            self._ui.show_warning(
                "Save As completed without Scratchpad copy",
                "The document was saved at its new location, but its saved Scratchpad "
                "could not be copied safely. The original Scratchpad was left untouched. "
                "Graphium will not adopt an unverified destination Scratchpad.\n\n"
                f"{exc}",
            )
            return False
        self._clear_scratchpad_binding_quarantine(plan.target_document_path)
        return True

    def _action_save_as(self, *_args) -> None:
        before = self._document_context_path()
        target_path = self._ui.choose_save_path(before)
        if not target_path:
            return
        try:
            plan = self._prepare_scratchpad_save_as_plan(before, target_path)
        except Exception as exc:
            self._ui.show_warning("Save As blocked by Scratchpad", str(exc))
            return

        result = self._perform_save_as(target_path)
        after = self._document_context_path()
        if result.completed and after != before:
            if (
                not plan.binding_change_expected
                or os.path.abspath(after or "") != plan.target_document_path
            ):
                try:
                    plan = self._prepare_scratchpad_save_as_plan(before, after or target_path)
                except Exception as exc:
                    actual_target = after or target_path
                    self._quarantine_unverified_scratchpad_target(
                        actual_target,
                        "Scratchpad is unavailable for this document because its Save As "
                        "companion could not be prepared safely after the document commit "
                        "and the destination sidecar is no longer provably absent. Resolve "
                        "the sidecar conflict before using Scratchpad for this document.",
                    )
                    self._ui.show_warning(
                        "Save As completed without Scratchpad copy",
                        "The document was saved at its new location, but the Scratchpad "
                        "copy could not be prepared safely. The original Scratchpad was left untouched. "
                        "Graphium will not adopt an unverified destination Scratchpad.\n\n"
                        f"{exc}",
                    )
                else:
                    self._commit_scratchpad_save_as_plan(plan)
            else:
                self._commit_scratchpad_save_as_plan(plan)
        self._notify_document_context_change(before)

    def _action_user_guide(self, *_args) -> None:
        show_text_document(
            self,
            title="Graphium Plus User Guide",
            path=self._help_path("GRAPHIUM_PLUS_USER_GUIDE.txt"),
        )

    def _action_keyboard_shortcuts(self, *_args) -> None:
        show_text_document(
            self,
            title="Graphium Plus Keyboard Shortcuts",
            path=self._help_path("GRAPHIUM_PLUS_KEYBOARD_SHORTCUTS.txt"),
        )

    def _report_workspace_error(self, message: str) -> None:
        self._ui.show_warning("Workspace", message)
