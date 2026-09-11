"""Graphium Ultra window: Graphium Plus plus native Markdown preview surfaces."""
from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from graphium.adapters.gtk.dialogs import show_text_document
from graphium_plus.adapters.gtk.window import GraphiumPlusWindow
from graphium_plus.markdown import build_markdown_document_map
from graphium_ultra.markdown_viewer import build_markdown_viewer_plan
from graphium_ultra.product import ULTRA_PRODUCT_IDENTITY
from .markdown_viewer import MarkdownViewerSurface, MarkdownViewerWindow


_MARKDOWN_VIEWER_REFRESH_DELAY_MS = 140
_DEFAULT_SPLIT_PREVIEW_WIDTH = 460
_MIN_SPLIT_SOURCE_WIDTH = 300
_MIN_SPLIT_PREVIEW_WIDTH = 300


class GraphiumUltraWindow(GraphiumPlusWindow):
    """Cumulative Ultra window; Plus remains unaware of Ultra-only presentation."""

    def __init__(self, application: Gtk.Application) -> None:
        super().__init__(application, identity=ULTRA_PRODUCT_IDENTITY)
        self._markdown_viewer: MarkdownViewerWindow | None = None
        self._markdown_split_surface: MarkdownViewerSurface | None = None
        self._markdown_split_host: Gtk.Box | None = None
        self._source_preview_position = 0
        self._markdown_viewer_refresh_source_id = 0
        self._markdown_viewer_focus_suspended = False
        self._markdown_viewer_focus_was_visible = False
        self._markdown_split_focus_was_visible = False
        self._markdown_viewer_focus_stale = False

        self.source_preview_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.source_preview_paned.set_hexpand(True)
        self.source_preview_paned.set_vexpand(True)
        self.editor_companion_paned.remove(self.editor_box)
        self.source_preview_paned.pack1(self.editor_box, resize=True, shrink=False)
        self.editor_companion_paned.pack1(
            self.source_preview_paned, resize=True, shrink=False
        )

        self._install_markdown_viewer_control()
        self.connect("destroy", self._on_markdown_viewer_parent_destroy)

    def _install_markdown_viewer_control(self) -> None:
        action = Gio.SimpleAction.new("markdown-viewer", None)
        action.connect("activate", self._action_markdown_viewer)
        self.add_action(action)
        self._actions["markdown-viewer"] = action

        split_action = Gio.SimpleAction.new_stateful(
            "source-preview-split", None, GLib.Variant.new_boolean(False)
        )
        split_action.connect("change-state", self._change_source_preview_split)
        self.add_action(split_action)
        self._actions["source-preview-split"] = split_action

        view_item = next(
            (item for item in self._focus_menubar().get_children() if item.get_label() == "View"),
            None,
        )
        view_menu = None if view_item is None else view_item.get_submenu()
        if view_menu is None:
            raise RuntimeError("Graphium Ultra View menu is unavailable")
        item = Gtk.MenuItem(label="Markdown Viewer")
        item.set_action_name("win.markdown-viewer")
        view_menu.append(item)
        item.show()
        split_item = Gtk.CheckMenuItem(label="Source | Preview")
        split_item.set_action_name("win.source-preview-split")
        view_menu.append(split_item)
        split_item.show()

    def _split_action_active(self) -> bool:
        state = self._actions["source-preview-split"].get_state()
        return bool(state is not None and state.get_boolean())

    def _ensure_markdown_viewer(self) -> MarkdownViewerWindow:
        if self._markdown_viewer is None:
            viewer = MarkdownViewerWindow(self)
            viewer.connect("destroy", self._on_markdown_viewer_destroy)
            self._markdown_viewer = viewer
        return self._markdown_viewer

    def _ensure_markdown_split_preview(self) -> MarkdownViewerSurface:
        if self._markdown_split_surface is not None:
            return self._markdown_split_surface

        host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        host.set_hexpand(True)
        host.set_vexpand(True)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.set_border_width(4)
        title = Gtk.Label(label="Preview")
        title.set_xalign(0.0)
        close = Gtk.Button(label="×")
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_tooltip_text("Close Source | Preview")
        close.connect("clicked", self._close_source_preview_split)
        header.pack_start(title, True, True, 4)
        header.pack_end(close, False, False, 0)
        host.pack_start(header, False, False, 0)

        surface = MarkdownViewerSurface(is_effectively_visible=host.get_visible)
        host.pack_start(surface, True, True, 0)
        host.set_no_show_all(True)
        host.hide()
        self.source_preview_paned.pack2(host, resize=True, shrink=True)
        self._markdown_split_host = host
        self._markdown_split_surface = surface
        return surface

    def _close_source_preview_split(self, *_args) -> None:
        self._actions["source-preview-split"].change_state(
            GLib.Variant.new_boolean(False)
        )

    def _default_source_preview_position(self) -> int:
        total = int(self.source_preview_paned.get_allocated_width() or 0)
        if total < (_MIN_SPLIT_SOURCE_WIDTH + _MIN_SPLIT_PREVIEW_WIDTH):
            total = max(
                720,
                int(getattr(self.editor_companion_paned.get_allocation(), "width", 0) or 0),
            )
        preview = max(
            _MIN_SPLIT_PREVIEW_WIDTH,
            min(_DEFAULT_SPLIT_PREVIEW_WIDTH, total // 2),
        )
        return max(_MIN_SPLIT_SOURCE_WIDTH, total - preview)

    def _project_source_preview_split(self, visible: bool, *, remember: bool = True) -> None:
        host = self._markdown_split_host
        surface = self._markdown_split_surface
        if visible:
            surface = self._ensure_markdown_split_preview()
            host = self._markdown_split_host
            assert host is not None
            host.set_no_show_all(False)
            host.show_all()
            total = int(self.source_preview_paned.get_allocated_width() or 0)
            position = int(self._source_preview_position)
            if position <= 0 or (total > 0 and position >= total):
                position = self._default_source_preview_position()
            self.source_preview_paned.set_position(position)
            surface._on_viewer_show()
            return
        if host is None or surface is None:
            return
        if host.get_visible() and remember:
            surface.remember_reading_position()
            position = int(self.source_preview_paned.get_position() or 0)
            if position > 0:
                self._source_preview_position = position
        host.set_no_show_all(True)
        host.hide()

    def _change_source_preview_split(
        self, action: Gio.SimpleAction, requested: GLib.Variant
    ) -> None:
        visible = bool(requested.get_boolean())
        current = bool(action.get_state().get_boolean())
        if visible == current or self._markdown_viewer_focus_suspended:
            return
        if visible:
            self._ensure_markdown_split_preview()
            action.set_state(GLib.Variant.new_boolean(True))
            self._refresh_markdown_viewer_now()
            self._project_source_preview_split(True)
        else:
            self._project_source_preview_split(False)
            action.set_state(GLib.Variant.new_boolean(False))
        self.text_view.grab_focus()

    def _markdown_render_targets(self) -> tuple[object, ...]:
        targets: list[object] = []
        if self._markdown_viewer is not None:
            targets.append(self._markdown_viewer)
        if self._markdown_split_surface is not None and self._split_action_active():
            targets.append(self._markdown_split_surface)
        return tuple(targets)

    def _action_markdown_viewer(self, *_args) -> None:
        if self._markdown_viewer_focus_suspended:
            return
        viewer = self._ensure_markdown_viewer()
        self._refresh_markdown_viewer_now()
        viewer.show_all()
        viewer.present()

    def _schedule_markdown_viewer_refresh(self) -> None:
        if not self._markdown_render_targets():
            return
        if self._markdown_viewer_focus_suspended:
            self._markdown_viewer_focus_stale = True
            return
        source_id = int(self._markdown_viewer_refresh_source_id)
        if source_id:
            GLib.source_remove(source_id)
        self._markdown_viewer_refresh_source_id = GLib.timeout_add(
            _MARKDOWN_VIEWER_REFRESH_DELAY_MS, self._refresh_markdown_viewer_now
        )

    def _refresh_markdown_viewer_now(self) -> bool:
        self._markdown_viewer_refresh_source_id = 0
        targets = self._markdown_render_targets()
        if not targets:
            return False
        snapshot = self.core.editor.capture_programmatic_source()
        document_map = build_markdown_document_map(snapshot.text)
        plan = build_markdown_viewer_plan(
            snapshot.text, document_map, source_state_id=snapshot.state_id
        )
        current = self.core.editor.capture_programmatic_source()
        if not plan.matches_source(current.text, current.state_id):
            self._schedule_markdown_viewer_refresh()
            return False
        for viewer in targets:
            viewer.render(plan, document_path=self.core.session.logical_path)
        return False

    def _on_document_text_changed(self) -> None:
        if self._markdown_render_targets():
            self._schedule_markdown_viewer_refresh()

    def _on_document_context_changed(
        self, before_path: str | None, after_path: str | None
    ) -> None:
        super()._on_document_context_changed(before_path, after_path)
        if before_path != after_path and self._markdown_render_targets():
            self._schedule_markdown_viewer_refresh()

    def _enter_focus_mode(self, snapshot) -> None:
        viewer = self._markdown_viewer
        split_host = self._markdown_split_host
        viewer_was_visible = bool(viewer is not None and viewer.get_visible())
        split_was_visible = bool(split_host is not None and split_host.get_visible())
        super()._enter_focus_mode(snapshot)

        self._markdown_viewer_focus_suspended = True
        self._markdown_viewer_focus_was_visible = viewer_was_visible
        self._markdown_split_focus_was_visible = split_was_visible
        source_id = int(self._markdown_viewer_refresh_source_id)
        self._markdown_viewer_refresh_source_id = 0
        if source_id:
            self._markdown_viewer_focus_stale = True
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        if viewer is not None:
            viewer.remember_reading_position()
            viewer.hide()
        if split_was_visible and self._markdown_split_surface is not None:
            self._markdown_split_surface.remember_reading_position()
            self._project_source_preview_split(False, remember=False)
        self._actions["markdown-viewer"].set_enabled(False)
        self._actions["source-preview-split"].set_enabled(False)

    def _exit_focus_mode(self, snapshot) -> None:
        super()._exit_focus_mode(snapshot)
        self._markdown_viewer_focus_suspended = False
        self._actions["markdown-viewer"].set_enabled(True)
        self._actions["source-preview-split"].set_enabled(True)

        viewer = self._markdown_viewer
        if self._markdown_viewer_focus_was_visible or self._markdown_split_focus_was_visible:
            if self._markdown_viewer_focus_stale:
                self._refresh_markdown_viewer_now()
        if self._markdown_viewer_focus_was_visible and viewer is not None:
            viewer.show()
        if self._markdown_split_focus_was_visible and self._split_action_active():
            self._project_source_preview_split(True, remember=False)
        self._markdown_viewer_focus_was_visible = False
        self._markdown_split_focus_was_visible = False
        self._markdown_viewer_focus_stale = False
        self.text_view.grab_focus()

    def _on_markdown_viewer_destroy(self, viewer, *_args) -> None:
        if viewer is not self._markdown_viewer:
            return
        self._markdown_viewer = None
        if not self._split_action_active():
            source_id = int(self._markdown_viewer_refresh_source_id)
            self._markdown_viewer_refresh_source_id = 0
            if source_id:
                try:
                    GLib.source_remove(source_id)
                except Exception:
                    pass

    def _on_markdown_viewer_parent_destroy(self, *_args) -> None:
        source_id = int(self._markdown_viewer_refresh_source_id)
        self._markdown_viewer_refresh_source_id = 0
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        viewer = self._markdown_viewer
        self._markdown_viewer = None
        self._markdown_split_surface = None
        self._markdown_split_host = None
        if viewer is not None:
            try:
                viewer.destroy()
            except Exception:
                pass

    def _action_user_guide(self, *_args) -> None:
        show_text_document(
            self,
            title="Graphium Ultra User Guide",
            path=self._help_path("GRAPHIUM_ULTRA_USER_GUIDE.txt"),
        )

    def _action_keyboard_shortcuts(self, *_args) -> None:
        show_text_document(
            self,
            title="Graphium Ultra Keyboard Shortcuts",
            path=self._help_path("GRAPHIUM_ULTRA_KEYBOARD_SHORTCUTS.txt"),
        )
