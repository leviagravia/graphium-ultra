"""Native GTK read-only Markdown viewer for a disposable viewer plan."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import html

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from graphium.domain.text_search import SearchInputError, SearchScaleError
from graphium_ultra.markdown_viewer import (
    MarkdownViewerLinkActionKind,
    MarkdownViewerPlan,
    MarkdownViewerReadingPosition,
    MarkdownViewerSearchHit,
    MarkdownViewerSpanKind,
    MarkdownViewerTable,
    MarkdownViewerTableCell,
    capture_markdown_viewer_reading_position,
    find_markdown_viewer_search_hits,
    resolve_markdown_viewer_link,
    resolve_markdown_viewer_reading_position,
)
from graphium_ultra.markdown_viewer_images import resolve_markdown_viewer_image


_HEADING_SCALE = {1: 1.70, 2: 1.48, 3: 1.30, 4: 1.18, 5: 1.10, 6: 1.04}
_ALLOWED_IMAGE_FORMATS = frozenset({"png", "jpeg", "webp"})
_MAX_SOURCE_IMAGE_DIMENSION = 12_000
_MAX_SOURCE_IMAGE_PIXELS = 48_000_000
_MAX_DISPLAY_IMAGE_WIDTH = 640
_MAX_DISPLAY_IMAGE_HEIGHT = 900
_MAX_PREVIEW_SEARCH_MATCHES = 2000


@dataclass(slots=True)
class _PreviewSearchSegment:
    text: str
    body_start: int | None = None
    table_label: object | None = None
    table_anchor_offset: int | None = None


class MarkdownViewerSurface(Gtk.Box):
    """Reusable read-only presentation surface; never a document authority."""

    def __init__(self, *, is_effectively_visible: Callable[[], bool] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._is_effectively_visible = is_effectively_visible or self.get_visible

        self.buffer = Gtk.TextBuffer()
        self.text_view = Gtk.TextView.new_with_buffer(self.buffer)
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_left_margin(24)
        self.text_view.set_right_margin(24)
        self.text_view.set_pixels_above_lines(2)
        self.text_view.set_pixels_below_lines(2)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.add(self.text_view)

        self._search_bar = Gtk.SearchBar()
        self._search_bar.set_show_close_button(True)
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_row.set_border_width(6)
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_width_chars(24)
        self._search_previous = Gtk.Button(label="Previous")
        self._search_next = Gtk.Button(label="Next")
        self._search_match_case = Gtk.CheckButton(label="Match Case")
        self._search_status = Gtk.Label(label="")
        self._search_status.set_xalign(0.0)
        search_row.pack_start(Gtk.Label(label="Find:"), False, False, 0)
        search_row.pack_start(self._search_entry, True, True, 0)
        search_row.pack_start(self._search_previous, False, False, 0)
        search_row.pack_start(self._search_next, False, False, 0)
        search_row.pack_start(self._search_match_case, False, False, 0)
        search_row.pack_start(self._search_status, False, False, 0)
        self._search_bar.add(search_row)
        self._search_bar.connect_entry(self._search_entry)

        self.pack_start(self._search_bar, False, False, 0)
        self.pack_start(self.scroller, True, True, 0)

        for level, scale in _HEADING_SCALE.items():
            self.buffer.create_tag(
                f"viewer-heading-{level}",
                weight=Pango.Weight.BOLD,
                scale=scale,
                pixels_above_lines=8 if level <= 2 else 5,
                pixels_below_lines=4,
            )
        self.buffer.create_tag("viewer-emphasis", style=Pango.Style.ITALIC)
        self.buffer.create_tag("viewer-strong", weight=Pango.Weight.BOLD)
        self.buffer.create_tag("viewer-inline-code", family="monospace")
        self.buffer.create_tag(
            "viewer-code-block",
            family="monospace",
            left_margin=20,
            right_margin=12,
            pixels_above_lines=5,
            pixels_below_lines=5,
        )
        self.buffer.create_tag(
            "viewer-blockquote",
            style=Pango.Style.ITALIC,
            left_margin=22,
            right_margin=14,
        )
        self.buffer.create_tag("viewer-list-item", left_margin=20, indent=-12)
        self.buffer.create_tag("viewer-list-marker", weight=Pango.Weight.BOLD)
        self.buffer.create_tag("viewer-image-placeholder", style=Pango.Style.ITALIC)
        self._link_tag = self.buffer.create_tag(
            "viewer-link", underline=Pango.Underline.SINGLE
        )
        self._search_tag = self.buffer.create_tag(
            "viewer-search-current",
            background="#f6e58d",
            foreground="#202020",
        )
        self.text_view.connect("event-after", self._on_text_view_event_after)
        self.buffer.create_tag(
            "viewer-thematic-break",
            justification=Gtk.Justification.CENTER,
            pixels_above_lines=5,
            pixels_below_lines=5,
        )
        self._plan: MarkdownViewerPlan | None = None
        self._embedded_widgets: list[Gtk.Widget] = []
        self._active_links: list[tuple[int, int, str]] = []
        self._replacement_ranges: tuple[tuple[int, int], ...] = ()
        self._table_search_cells: list[tuple[int, int, int, Gtk.Label, str]] = []
        self._search_segments: tuple[_PreviewSearchSegment, ...] = ()
        self._search_hits: tuple[MarkdownViewerSearchHit, ...] = ()
        self._search_current_index = -1
        self._search_selected_label: Gtk.Label | None = None
        self._pending_reading_position: MarkdownViewerReadingPosition | None = None
        self._reading_restore_source_id = 0

        self._search_entry.connect("changed", self._on_search_query_changed)
        self._search_entry.connect("activate", lambda *_args: self._search_move(1))
        self._search_entry.connect("key-press-event", self._on_search_entry_key_press)
        self._search_previous.connect("clicked", lambda *_args: self._search_move(-1))
        self._search_next.connect("clicked", lambda *_args: self._search_move(1))
        self._search_match_case.connect("toggled", self._on_search_option_changed)
        self._search_bar.connect("notify::search-mode-enabled", self._on_search_mode_changed)
        self.connect("key-press-event", self._on_window_key_press)
        self.connect("show", self._on_viewer_show)
        self.connect("destroy", self._on_viewer_destroy)
        self._search_bar.set_search_mode(False)

    @staticmethod
    def _tag_name(kind: MarkdownViewerSpanKind, level: int | None) -> str | None:
        if kind is MarkdownViewerSpanKind.HEADING:
            return f"viewer-heading-{level}"
        return {
            MarkdownViewerSpanKind.EMPHASIS: "viewer-emphasis",
            MarkdownViewerSpanKind.STRONG: "viewer-strong",
            MarkdownViewerSpanKind.INLINE_CODE: "viewer-inline-code",
            MarkdownViewerSpanKind.CODE_BLOCK: "viewer-code-block",
            MarkdownViewerSpanKind.BLOCKQUOTE: "viewer-blockquote",
            MarkdownViewerSpanKind.LIST_ITEM: "viewer-list-item",
            MarkdownViewerSpanKind.LIST_MARKER: "viewer-list-marker",
            MarkdownViewerSpanKind.LINK_LABEL: None,
            MarkdownViewerSpanKind.IMAGE_PLACEHOLDER: "viewer-image-placeholder",
            MarkdownViewerSpanKind.TABLE_PLACEHOLDER: None,
            MarkdownViewerSpanKind.THEMATIC_BREAK: "viewer-thematic-break",
        }[kind]

    @staticmethod
    def _load_local_pixbuf(path: str):
        try:
            image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
            if image_format is None or width <= 0 or height <= 0:
                return None
            if image_format.get_name().lower() not in _ALLOWED_IMAGE_FORMATS:
                return None
            if width > _MAX_SOURCE_IMAGE_DIMENSION or height > _MAX_SOURCE_IMAGE_DIMENSION:
                return None
            if width * height > _MAX_SOURCE_IMAGE_PIXELS:
                return None
            scale = min(
                1.0,
                _MAX_DISPLAY_IMAGE_WIDTH / width,
                _MAX_DISPLAY_IMAGE_HEIGHT / height,
            )
            if scale < 1.0:
                target_width = max(1, int(width * scale))
                target_height = max(1, int(height * scale))
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    path, target_width, target_height, True
                )
            else:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
            oriented = pixbuf.apply_embedded_orientation()
            return pixbuf if oriented is None else oriented
        except Exception:
            return None

    def _cell_markup(self, cell: MarkdownViewerTableCell, plan: MarkdownViewerPlan) -> str:
        wrappers = {
            MarkdownViewerSpanKind.EMPHASIS: ("<i>", "</i>"),
            MarkdownViewerSpanKind.STRONG: ("<b>", "</b>"),
            MarkdownViewerSpanKind.INLINE_CODE: ("<tt>", "</tt>"),
            MarkdownViewerSpanKind.IMAGE_PLACEHOLDER: ("<i>", "</i>"),
        }
        parts: list[str] = []
        cursor = 0
        for span in cell.spans:
            if span.start < cursor or span.end > len(cell.text):
                continue
            parts.append(html.escape(cell.text[cursor:span.start]))
            content = html.escape(cell.text[span.start:span.end])
            action = (
                resolve_markdown_viewer_link(plan, span.target)
                if span.kind is MarkdownViewerSpanKind.LINK_LABEL
                else None
            )
            if action is not None:
                target = html.escape(action.target, quote=True)
                content = f'<a href="{target}">{content}</a>'
            else:
                wrapper = wrappers.get(span.kind)
                if wrapper is not None:
                    content = wrapper[0] + content + wrapper[1]
            parts.append(content)
            cursor = span.end
        parts.append(html.escape(cell.text[cursor:]))
        markup = "".join(parts)
        return f"<b>{markup}</b>" if cell.header else markup

    def _table_cell_widget(
        self,
        cell: MarkdownViewerTableCell,
        plan: MarkdownViewerPlan,
        *,
        table_start: int,
        row: int,
        column: int,
    ) -> Gtk.Widget:
        label = Gtk.Label()
        label.set_use_markup(True)
        label.set_markup(self._cell_markup(cell, plan))
        label.connect("activate-link", self._on_label_activate_link)
        label.set_selectable(True)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(56)
        label.set_margin_start(7)
        label.set_margin_end(7)
        label.set_margin_top(5)
        label.set_margin_bottom(5)
        label.set_hexpand(True)
        label.set_xalign({"center": 0.5, "right": 1.0}.get(cell.alignment.value, 0.0))
        self._table_search_cells.append((table_start, row, column, label, cell.text))
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.set_hexpand(True)
        frame.add(label)
        return frame

    def _build_table_widget(
        self, table: MarkdownViewerTable, plan: MarkdownViewerPlan
    ) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=0, row_spacing=0)
        for column, cell in enumerate(table.header):
            grid.attach(
                self._table_cell_widget(
                    cell, plan, table_start=table.start, row=0, column=column
                ),
                column, 0, 1, 1,
            )
        for row_index, row in enumerate(table.rows, start=1):
            for column, cell in enumerate(row):
                grid.attach(
                    self._table_cell_widget(
                        cell, plan, table_start=table.start, row=row_index, column=column
                    ),
                    column, row_index, 1, 1,
                )
        return grid

    @staticmethod
    def _translated_offset(offset: int, replacements: tuple[tuple[int, int], ...]) -> int:
        translated = offset
        for start, end in replacements:
            if end <= offset:
                translated -= (end - start) - 1
        return translated

    @staticmethod
    def _plan_offset_from_buffer_offset(
        offset: int, replacements: tuple[tuple[int, int], ...]
    ) -> int:
        plan_offset = max(0, int(offset))
        removed = 0
        for start, end in replacements:
            rendered_start = start - removed
            if offset <= rendered_start:
                break
            shrink = (end - start) - 1
            removed += shrink
            plan_offset += shrink
        return plan_offset

    def _on_window_key_press(self, _widget, event) -> bool:
        state = event.state
        if event.keyval in (Gdk.KEY_f, Gdk.KEY_F) and state & Gdk.ModifierType.CONTROL_MASK:
            self._show_preview_search()
            return True
        if event.keyval == Gdk.KEY_F3:
            self._search_move(-1 if state & Gdk.ModifierType.SHIFT_MASK else 1)
            return True
        if event.keyval == Gdk.KEY_Escape and self._search_bar.get_search_mode():
            self._close_preview_search()
            return True
        return False

    def _on_search_entry_key_press(self, _entry, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._close_preview_search()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and (
            event.state & Gdk.ModifierType.SHIFT_MASK
        ):
            self._search_move(-1)
            return True
        return False

    def _show_preview_search(self) -> None:
        self._search_bar.set_search_mode(True)
        self._search_bar.show_all()
        self._search_entry.grab_focus()
        self._search_entry.select_region(0, -1)

    def _close_preview_search(self) -> None:
        self._search_bar.set_search_mode(False)
        self._clear_search_projection()
        self._search_current_index = -1
        self.text_view.grab_focus()

    def _on_search_mode_changed(self, bar: Gtk.SearchBar, _param) -> None:
        if not bar.get_search_mode():
            self._clear_search_projection()
            self._search_current_index = -1

    def _on_search_query_changed(self, _entry: Gtk.SearchEntry) -> None:
        self._refresh_preview_search(reset_current=True)

    def _on_search_option_changed(self, _button: Gtk.CheckButton) -> None:
        self._refresh_preview_search(reset_current=True)

    def _set_search_status(self, message: str) -> None:
        self._search_status.set_text(message)

    def _clear_search_projection(self) -> None:
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(self._search_tag, start, end)
        label = self._search_selected_label
        self._search_selected_label = None
        if label is not None:
            try:
                label.select_region(0, 0)
            except Exception:
                pass

    def _build_preview_search_segments(self) -> tuple[_PreviewSearchSegment, ...]:
        start, end = self.buffer.get_bounds()
        body = self.buffer.get_slice(start, end, True)
        tables: dict[int, list[tuple[int, int, Gtk.Label, str]]] = {}
        for table_start, row, column, label, text in self._table_search_cells:
            anchor = self._translated_offset(table_start, self._replacement_ranges)
            tables.setdefault(anchor, []).append((row, column, label, text))

        segments: list[_PreviewSearchSegment] = []
        cursor = 0
        for anchor in sorted(tables):
            anchor = min(len(body), max(cursor, int(anchor)))
            if anchor > cursor:
                segments.append(_PreviewSearchSegment(body[cursor:anchor], body_start=cursor))
            for _row, _column, label, text in sorted(tables[anchor], key=lambda item: (item[0], item[1])):
                segments.append(
                    _PreviewSearchSegment(
                        text,
                        table_label=label,
                        table_anchor_offset=anchor,
                    )
                )
            cursor = min(len(body), anchor + 1)
        if cursor < len(body):
            segments.append(_PreviewSearchSegment(body[cursor:], body_start=cursor))
        if not segments and body:
            segments.append(_PreviewSearchSegment(body, body_start=0))
        return tuple(segments)

    def _refresh_preview_search(self, *, reset_current: bool) -> None:
        self._clear_search_projection()
        self._search_segments = self._build_preview_search_segments()
        if reset_current:
            self._search_current_index = -1
        query = self._search_entry.get_text()
        if not query:
            self._search_hits = ()
            self._search_previous.set_sensitive(False)
            self._search_next.set_sensitive(False)
            self._set_search_status("")
            return
        try:
            self._search_hits = find_markdown_viewer_search_hits(
                tuple(segment.text for segment in self._search_segments),
                query,
                match_case=self._search_match_case.get_active(),
                max_matches=_MAX_PREVIEW_SEARCH_MATCHES,
            )
        except (SearchInputError, SearchScaleError) as exc:
            self._search_hits = ()
            self._search_previous.set_sensitive(False)
            self._search_next.set_sensitive(False)
            self._set_search_status(str(exc))
            return
        enabled = bool(self._search_hits)
        self._search_previous.set_sensitive(enabled)
        self._search_next.set_sensitive(enabled)
        self._set_search_status(
            f"{len(self._search_hits)} match" + ("" if len(self._search_hits) == 1 else "es")
            if enabled else "Not found"
        )

    def _project_search_hit(self, hit: MarkdownViewerSearchHit) -> None:
        self._clear_search_projection()
        self._cancel_reading_position_restore(clear_pending=True)
        segment = self._search_segments[hit.segment_index]
        if segment.body_start is not None:
            start_offset = segment.body_start + hit.start
            end_offset = segment.body_start + hit.end
            start = self.buffer.get_iter_at_offset(start_offset)
            end = self.buffer.get_iter_at_offset(end_offset)
            self.buffer.apply_tag(self._search_tag, start, end)
            mark = self.buffer.create_mark(None, start, True)
            self.text_view.scroll_to_mark(mark, 0.08, True, 0.0, 0.05)
            self.buffer.delete_mark(mark)
            return

        label = segment.table_label
        anchor_offset = segment.table_anchor_offset
        if label is None or anchor_offset is None:
            return
        label.select_region(hit.start, hit.end)
        self._search_selected_label = label
        iterator = self.buffer.get_iter_at_offset(anchor_offset)
        mark = self.buffer.create_mark(None, iterator, True)
        self.text_view.scroll_to_mark(mark, 0.08, True, 0.0, 0.05)
        self.buffer.delete_mark(mark)

    def _search_move(self, direction: int) -> None:
        if direction not in (-1, 1):
            raise ValueError("search direction must be -1 or 1")
        if not self._search_entry.get_text():
            self._show_preview_search()
            self._set_search_status("Type text to find")
            return
        if not self._search_hits:
            self._refresh_preview_search(reset_current=True)
        if not self._search_hits:
            return
        previous = self._search_current_index
        count = len(self._search_hits)
        if previous < 0:
            index = 0 if direction > 0 else count - 1
            wrapped = False
        else:
            index = (previous + direction) % count
            wrapped = (direction > 0 and index <= previous) or (direction < 0 and index >= previous)
        self._search_current_index = index
        self._project_search_hit(self._search_hits[index])
        suffix = " · Wrapped" if wrapped else ""
        self._set_search_status(f"{index + 1} of {count}{suffix}")

    def _capture_reading_position(self) -> MarkdownViewerReadingPosition | None:
        plan = self._plan
        if plan is None:
            return None
        visible = self.text_view.get_visible_rect()
        iterator, _line_top = self.text_view.get_line_at_y(int(visible.y))
        plan_offset = self._plan_offset_from_buffer_offset(
            iterator.get_offset(), self._replacement_ranges
        )
        return capture_markdown_viewer_reading_position(plan, plan_offset)

    def remember_reading_position(self) -> None:
        position = self._capture_reading_position()
        if position is not None:
            self._pending_reading_position = position

    def _cancel_reading_position_restore(self, *, clear_pending: bool = False) -> None:
        source_id = int(self._reading_restore_source_id)
        self._reading_restore_source_id = 0
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        if clear_pending:
            self._pending_reading_position = None

    def _viewer_visible(self) -> bool:
        try:
            return bool(self._is_effectively_visible())
        except Exception:
            return False

    def _schedule_reading_position_restore(self) -> None:
        if self._pending_reading_position is None or not self._viewer_visible():
            return
        self._cancel_reading_position_restore()
        self._reading_restore_source_id = GLib.idle_add(
            self._restore_reading_position_idle
        )

    def _restore_reading_position_idle(self) -> bool:
        self._reading_restore_source_id = 0
        position = self._pending_reading_position
        plan = self._plan
        if position is None or plan is None or not self._viewer_visible():
            return False
        plan_offset = resolve_markdown_viewer_reading_position(plan, position)
        buffer_offset = self._translated_offset(plan_offset, self._replacement_ranges)
        buffer_offset = min(self.buffer.get_char_count(), max(0, buffer_offset))
        iterator = self.buffer.get_iter_at_offset(buffer_offset)
        mark = self.buffer.create_mark(None, iterator, True)
        self.text_view.scroll_to_mark(mark, 0.05, True, 0.0, 0.0)
        self.buffer.delete_mark(mark)
        self._pending_reading_position = None
        return False

    def _on_viewer_show(self, *_args) -> None:
        self._schedule_reading_position_restore()

    def _on_viewer_destroy(self, *_args) -> None:
        self._cancel_reading_position_restore(clear_pending=True)

    def _activate_target(self, target: str) -> bool:
        self._cancel_reading_position_restore(clear_pending=True)
        plan = self._plan
        if plan is None:
            return False
        action = resolve_markdown_viewer_link(plan, target)
        if action is None:
            return False
        if action.kind is MarkdownViewerLinkActionKind.INTERNAL:
            assert action.offset is not None
            offset = self._translated_offset(action.offset, self._replacement_ranges)
            iterator = self.buffer.get_iter_at_offset(offset)
            mark = self.buffer.create_mark(None, iterator, True)
            self.text_view.scroll_to_mark(mark, 0.1, True, 0.0, 0.05)
            self.buffer.delete_mark(mark)
            return True
        try:
            Gio.AppInfo.launch_default_for_uri(action.target, None)
        except Exception:
            pass
        return True

    def _on_label_activate_link(self, _label, target: str) -> bool:
        self._activate_target(target)
        return True

    def _target_at_offset(self, offset: int) -> str | None:
        for start, end, target in self._active_links:
            if start <= offset < end:
                return target
        return None

    def _on_text_view_event_after(self, text_view, event) -> None:
        if event.type != Gdk.EventType.BUTTON_RELEASE:
            return
        delivered_button, button = event.get_button()
        if not delivered_button or button != Gdk.BUTTON_PRIMARY:
            return
        if self.buffer.get_selection_bounds():
            return
        delivered_coords, x, y = event.get_coords()
        if not delivered_coords:
            return
        buffer_x, buffer_y = text_view.window_to_buffer_coords(
            Gtk.TextWindowType.WIDGET, int(x), int(y)
        )
        over_text, iterator = text_view.get_iter_at_location(buffer_x, buffer_y)
        if not over_text:
            return
        target = self._target_at_offset(iterator.get_offset())
        if target is not None:
            self._activate_target(target)

    def _clear_embedded_widgets(self) -> None:
        self._table_search_cells.clear()
        for widget in self._embedded_widgets:
            try:
                widget.destroy()
            except Exception:
                pass
        self._embedded_widgets.clear()

    def _render_embedded_content(
        self, plan: MarkdownViewerPlan, document_path: str | None
    ) -> tuple[tuple[int, int], ...]:
        replacements: list[tuple[int, str, object]] = [
            (table.start, "table", table) for table in plan.tables
        ]
        replacements.extend(
            (span.start, "image", span)
            for span in plan.spans
            if span.kind is MarkdownViewerSpanKind.IMAGE_PLACEHOLDER
        )
        applied: list[tuple[int, int]] = []
        for _offset, kind, item in sorted(replacements, key=lambda row: row[0], reverse=True):
            if kind == "image":
                span = item
                resolution = resolve_markdown_viewer_image(
                    span.target, document_path=document_path
                )
                if not resolution.ready:
                    continue
                pixbuf = self._load_local_pixbuf(resolution.path)
                if pixbuf is None:
                    continue
                start = self.buffer.get_iter_at_offset(span.start)
                end = self.buffer.get_iter_at_offset(span.end)
                self.buffer.delete(start, end)
                start = self.buffer.get_iter_at_offset(span.start)
                self.buffer.insert_pixbuf(start, pixbuf)
                applied.append((span.start, span.end))
                continue

            table = item
            try:
                widget = self._build_table_widget(table, plan)
            except Exception:
                continue
            start = self.buffer.get_iter_at_offset(table.start)
            end = self.buffer.get_iter_at_offset(table.end)
            self.buffer.delete(start, end)
            anchor_at = self.buffer.get_iter_at_offset(table.start)
            anchor = self.buffer.create_child_anchor(anchor_at)
            self.text_view.add_child_at_anchor(widget, anchor)
            widget.show_all()
            self._embedded_widgets.append(widget)
            applied.append((table.start, table.end))
        return tuple(sorted(applied))

    def _apply_active_links(
        self, plan: MarkdownViewerPlan, replacements: tuple[tuple[int, int], ...]
    ) -> None:
        self._active_links.clear()
        for span in plan.spans:
            if span.kind is not MarkdownViewerSpanKind.LINK_LABEL:
                continue
            if resolve_markdown_viewer_link(plan, span.target) is None:
                continue
            start = self._translated_offset(span.start, replacements)
            end = self._translated_offset(span.end, replacements)
            if end <= start:
                continue
            self.buffer.apply_tag_by_name(
                "viewer-link",
                self.buffer.get_iter_at_offset(start),
                self.buffer.get_iter_at_offset(end),
            )
            assert span.target is not None
            self._active_links.append((start, end, span.target))

    def render(self, plan: MarkdownViewerPlan, *, document_path: str | None = None) -> None:
        position = self._pending_reading_position or self._capture_reading_position()
        self._cancel_reading_position_restore()
        self._pending_reading_position = position
        self._clear_search_projection()
        self._clear_embedded_widgets()
        self._active_links.clear()
        self._replacement_ranges = ()
        self._plan = plan
        self.buffer.set_text(plan.text)
        for span in plan.spans:
            if span.kind is MarkdownViewerSpanKind.LINK_LABEL:
                continue
            tag_name = self._tag_name(span.kind, span.level)
            if tag_name is None:
                continue
            self.buffer.apply_tag_by_name(
                tag_name,
                self.buffer.get_iter_at_offset(span.start),
                self.buffer.get_iter_at_offset(span.end),
            )
        self._replacement_ranges = self._render_embedded_content(plan, document_path)
        self._apply_active_links(plan, self._replacement_ranges)
        self._refresh_preview_search(reset_current=True)
        self._schedule_reading_position_restore()


class MarkdownViewerWindow(Gtk.Window):
    """Separate U1.2 host around the shared MarkdownViewerSurface."""

    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(title="Markdown Viewer")
        self.set_transient_for(parent)
        self.set_destroy_with_parent(True)
        self.set_default_size(720, 620)
        self.surface = MarkdownViewerSurface(is_effectively_visible=self.get_visible)
        self.add(self.surface)
        self.connect("show", lambda *_args: self.surface._on_viewer_show())

    @property
    def buffer(self):
        return self.surface.buffer

    @property
    def text_view(self):
        return self.surface.text_view

    @property
    def scroller(self):
        return self.surface.scroller

    @property
    def _plan(self):
        return self.surface._plan

    @property
    def _embedded_widgets(self):
        return self.surface._embedded_widgets

    @property
    def _active_links(self):
        return self.surface._active_links

    @property
    def _replacement_ranges(self):
        return self.surface._replacement_ranges

    @property
    def _search_entry(self):
        return self.surface._search_entry

    @property
    def _search_hits(self):
        return self.surface._search_hits

    @property
    def _search_current_index(self):
        return self.surface._search_current_index

    @property
    def _search_segments(self):
        return self.surface._search_segments

    @property
    def _search_selected_label(self):
        return self.surface._search_selected_label

    @property
    def _search_match_case(self):
        return self.surface._search_match_case

    @property
    def _pending_reading_position(self):
        return self.surface._pending_reading_position

    def render(self, plan: MarkdownViewerPlan, *, document_path: str | None = None) -> None:
        self.surface.render(plan, document_path=document_path)

    def remember_reading_position(self) -> None:
        self.surface.remember_reading_position()

    def _translated_offset(self, *args):
        return self.surface._translated_offset(*args)

    def _capture_reading_position(self):
        return self.surface._capture_reading_position()

    def _cancel_reading_position_restore(self, *, clear_pending: bool = False) -> None:
        self.surface._cancel_reading_position_restore(clear_pending=clear_pending)

    def _activate_target(self, target: str) -> bool:
        return self.surface._activate_target(target)

    def _show_preview_search(self) -> None:
        self.surface._show_preview_search()

    def _search_move(self, direction: int) -> None:
        self.surface._search_move(direction)
