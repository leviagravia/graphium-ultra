"""Thin GTK adapter for the Graphium Plus document Outliner.

The tree owns no document or Markdown authority.  It renders an immutable
OutlineProjection and reports explicit row activations back to the window.
"""
from __future__ import annotations

from collections.abc import Callable

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango

from graphium_plus.outliner import OutlineProjection
from .panel_chrome import build_compact_close_button


_COL_DISPLAY = 0
_COL_SOURCE_START = 1
_COL_CONTENT_START = 2


class OutlinerPanel:
    def __init__(
        self, *, on_activate: Callable[[int], None], on_close: Callable[[], None]
    ) -> None:
        self._on_activate = on_activate
        self._path_by_source_start: dict[int, Gtk.TreePath] = {}
        self._selected_source_start: int | None = None

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.widget.set_border_width(6)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title = Gtk.Label(label="Outline")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        title.set_markup("<b>Outline</b>")
        self.close_button = build_compact_close_button(
            on_close, name="graphium-outline-close", tooltip="Hide Outline"
        )
        header.pack_start(title, True, True, 0)
        header.pack_end(self.close_button, False, False, 0)
        self.widget.pack_start(header, False, False, 0)

        self.store = Gtk.ListStore(str, int, int)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(False)
        self.tree.set_enable_search(True)
        self.tree.set_search_column(_COL_DISPLAY)
        self.tree.set_activate_on_single_click(True)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)

        renderer = Gtk.CellRendererText()
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        column = Gtk.TreeViewColumn("Outline", renderer, text=_COL_DISPLAY)
        column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        column.set_fixed_width(1)
        column.set_expand(True)
        self.tree.append_column(column)
        self.tree.connect("row-activated", self._row_activated)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add(self.tree)
        self.widget.pack_start(scroll, True, True, 0)

        self.status = Gtk.Label(label="No headings")
        self.status.set_xalign(0.0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.widget.pack_start(self.status, False, False, 0)

    @staticmethod
    def _display_title(level: int, title: str) -> str:
        visible = title if title else "(Untitled heading)"
        return ("    " * max(0, int(level) - 1)) + visible

    def render(self, projection: OutlineProjection) -> None:
        self.store.clear()
        self._path_by_source_start.clear()
        self._selected_source_start = None
        for entry in projection.entries:
            tree_iter = self.store.append((
                self._display_title(entry.level, entry.title),
                entry.source_start,
                entry.content_start,
            ))
            self._path_by_source_start[entry.source_start] = self.store.get_path(tree_iter)
        count = len(projection.entries)
        self.status.set_text("No headings" if count == 0 else f"{count} heading{'s' if count != 1 else ''}")

    def select_source_start(self, source_start: int | None) -> None:
        if source_start == self._selected_source_start:
            return
        selection = self.tree.get_selection()
        if source_start is None:
            selection.unselect_all()
            self._selected_source_start = None
            return
        path = self._path_by_source_start.get(int(source_start))
        if path is None:
            selection.unselect_all()
            self._selected_source_start = None
            return
        selection.select_path(path)
        self.tree.scroll_to_cell(path, None, False, 0.0, 0.0)
        self._selected_source_start = int(source_start)

    def _row_activated(self, _tree, path, _column) -> None:
        tree_iter = self.store.get_iter(path)
        if tree_iter is None:
            return
        self._on_activate(int(self.store[tree_iter][_COL_CONTENT_START]))
