"""Lazy thin GTK3 printing adapter for Graphium.

This module is deliberately absent from normal Graphium startup.  GraphiumWindow imports
it only inside the first Page Setup / Print Preview / Print action.  It owns no document
state and creates one fresh Gtk.PrintOperation per Preview/Print invocation.  Large native
print/preview jobs use GTK's own asynchronous PrintOperation lifecycle; Graphium owns no
worker, thread, timer, queue or custom-preview implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo

from graphium.paths import resolve_xdg_paths
from .print_pagination import (
    IncrementalVisualPage,
    IncrementalVisualPaginator,
    logical_line_chunk_end,
)


_HEADER_HEIGHT_POINTS = 18.0
_FOOTER_HEIGHT_POINTS = 18.0
_META_FONT_POINTS = 8.0
_PAGINATION_CHUNK_TARGET_CHARS = 16 * 1024
_PAGINATION_CHUNK_MAX_LOGICAL_LINES = 64


from graphium.application.print_model import PrintSnapshot


class _PageSetupStore:
    """Product-local lazy Gtk.PageSetup payload store.

    GTK remains the payload serialization authority.  Graphium owns only location,
    regular-file acceptance, atomic replacement, permissions and fail-soft loading.
    """

    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self):
        try:
            metadata = os.lstat(self.path)
            if not stat.S_ISREG(metadata.st_mode):
                return Gtk.PageSetup()
            return Gtk.PageSetup.new_from_file(str(self.path))
        except Exception:
            return Gtk.PageSetup()

    def save(self, setup) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".page-setup-", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            os.close(fd)
            fd = -1
            os.chmod(temp_name, 0o600)
            setup.to_file(temp_name)
            os.chmod(temp_name, 0o600)
            with open(temp_name, "rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise


def _page_setup_signature(setup) -> tuple[object, ...]:
    """Stable semantic comparison for native Page Setup cancel/unchanged handling."""
    paper = setup.get_paper_size()
    unit = Gtk.Unit.MM
    return (
        int(setup.get_orientation()),
        paper.get_name(),
        paper.get_display_name(),
        round(float(paper.get_width(unit)), 6),
        round(float(paper.get_height(unit)), 6),
        round(float(setup.get_top_margin(unit)), 6),
        round(float(setup.get_bottom_margin(unit)), 6),
        round(float(setup.get_left_margin(unit)), 6),
        round(float(setup.get_right_margin(unit)), 6),
    )


class _LayoutChunk:
    """One bounded Pango layout retained until the operation finishes."""

    __slots__ = ("layout", "line_heights")

    def __init__(self, layout, line_heights: tuple[float, ...]) -> None:
        self.layout = layout
        self.line_heights = line_heights


class _PrintJob:
    """Per-operation render state retained only until GtkPrintOperation completion.

    begin-print is intentionally O(1) with respect to document size.  GTK's async
    ``paginate`` signal then asks Graphium to measure one bounded text chunk per callback,
    yielding the main loop between chunks while page boundaries remain Pango visual lines.
    """

    __slots__ = (
        "snapshot",
        "chunks",
        "pages",
        "page_width",
        "_font",
        "_layout_width_pango",
        "_cursor",
        "_paginator",
        "_pagination_finished",
        "_render_released",
    )

    def __init__(self, snapshot: PrintSnapshot) -> None:
        self.snapshot = snapshot
        self.chunks: list[_LayoutChunk] = []
        self.pages: tuple[IncrementalVisualPage, ...] = ()
        self.page_width = 0.0
        self._font = None
        self._layout_width_pango = 0
        self._cursor = 0
        self._paginator = None
        self._pagination_finished = False
        self._render_released = False

    @staticmethod
    def _body_height(context) -> float:
        return max(
            1.0,
            float(context.get_height()) - _HEADER_HEIGHT_POINTS - _FOOTER_HEIGHT_POINTS,
        )

    def begin_print(self, _operation, context) -> None:
        """Initialize only geometry/state; never lay out the complete document here."""
        font = Pango.FontDescription()
        font.set_family(self.snapshot.font_family)
        font.set_size(int(round(float(self.snapshot.font_size_points) * Pango.SCALE)))
        self._font = font
        self._layout_width_pango = max(
            1,
            int(round(float(context.get_width()) * Pango.SCALE)),
        )
        self.page_width = float(context.get_width())
        self._cursor = 0
        self.chunks = []
        self.pages = ()
        self._paginator = IncrementalVisualPaginator(
            usable_height=self._body_height(context),
        )
        self._pagination_finished = False
        self._render_released = False

    def _next_text_chunk(self) -> str | None:
        """Return a bounded chunk ending only on a logical-line boundary."""
        text = self.snapshot.text
        if self._cursor >= len(text):
            return None
        start = self._cursor
        end = logical_line_chunk_end(
            text,
            start,
            target_chars=_PAGINATION_CHUNK_TARGET_CHARS,
            max_logical_lines=_PAGINATION_CHUNK_MAX_LOGICAL_LINES,
        )
        self._cursor = end
        return text[start:end]

    def _measure_chunk(self, context, chunk_text: str, *, is_final: bool) -> _LayoutChunk:
        layout = context.create_pango_layout()
        layout.set_font_description(self._font)
        layout.set_width(self._layout_width_pango)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(chunk_text, -1)
        heights: list[float] = []
        count = layout.get_line_count()
        # Pango represents a trailing paragraph separator with a final empty visual line.
        # At an internal chunk boundary that line belongs to the following chunk, otherwise
        # every boundary would manufacture one blank printed line.  At EOF it is real text
        # semantics and is retained.
        if not is_final and chunk_text.endswith("\n") and count > 1:
            count -= 1
        for index in range(count):
            line = layout.get_line_readonly(index)
            if line is None:
                continue
            _ink, logical = line.get_extents()
            heights.append(max(1.0 / Pango.SCALE, float(logical.height) / Pango.SCALE))
        if not heights:
            heights = [max(1.0, float(self.snapshot.font_size_points))]
        return _LayoutChunk(layout, tuple(heights))

    def paginate(self, operation, context) -> bool:
        """Measure at most one bounded layout chunk, then yield back to GTK."""
        if self._pagination_finished:
            return True
        if self._paginator is None:
            raise RuntimeError("paginate called before begin_print")

        if not self.snapshot.text:
            # Empty documents still own one printable page for header/footer.
            self.pages = ()
            operation.set_n_pages(1)
            self._pagination_finished = True
            return True

        chunk_text = self._next_text_chunk()
        if chunk_text is None:
            self.pages = self._paginator.finish()
            operation.set_n_pages(max(1, len(self.pages)))
            self._pagination_finished = True
            return True

        chunk = self._measure_chunk(
            context,
            chunk_text,
            is_final=self._cursor >= len(self.snapshot.text),
        )
        chunk_index = len(self.chunks)
        self.chunks.append(chunk)
        self._paginator.add_chunk(chunk_index, chunk.line_heights)

        if self._cursor >= len(self.snapshot.text):
            self.pages = self._paginator.finish()
            operation.set_n_pages(max(1, len(self.pages)))
            self._pagination_finished = True
            return True
        return False

    def _meta_layout(self, context, text: str, *, align_right: bool = False):
        layout = context.create_pango_layout()
        font = Pango.FontDescription()
        font.set_family("Sans")
        font.set_size(int(round(_META_FONT_POINTS * Pango.SCALE)))
        layout.set_font_description(font)
        layout.set_text(text, -1)
        layout.set_width(max(1, int(round(self.page_width * Pango.SCALE))))
        if align_right:
            layout.set_alignment(Pango.Alignment.RIGHT)
        else:
            layout.set_ellipsize(Pango.EllipsizeMode.END)
        return layout

    def draw_page(self, _operation, context, page_number: int) -> None:
        cr = context.get_cairo_context()
        page_count = max(1, len(self.pages))

        header = self._meta_layout(context, self.snapshot.title)
        cr.move_to(0.0, 0.0)
        PangoCairo.show_layout(cr, header)

        footer = self._meta_layout(
            context,
            f"Page {page_number + 1} of {page_count}",
            align_right=True,
        )
        cr.move_to(0.0, max(0.0, float(context.get_height()) - _FOOTER_HEIGHT_POINTS))
        PangoCairo.show_layout(cr, footer)

        if not self.pages:
            return
        page = self.pages[page_number]
        y = _HEADER_HEIGHT_POINTS
        for span in page.spans:
            chunk = self.chunks[span.chunk_index]
            for index in range(span.start_line, span.end_line):
                line = chunk.layout.get_line_readonly(index)
                if line is None:
                    continue
                _ink, logical = line.get_extents()
                baseline_y = y - (float(logical.y) / Pango.SCALE)
                cr.move_to(0.0, baseline_y)
                PangoCairo.show_layout_line(cr, line)
                y += chunk.line_heights[index]

    @property
    def render_released(self) -> bool:
        return self._render_released

    def end_print(self, *_args) -> None:
        if self._render_released:
            return
        self.chunks = []
        self.pages = ()
        self.page_width = 0.0
        self._font = None
        self._layout_width_pango = 0
        self._cursor = 0
        self._paginator = None
        self._pagination_finished = False
        self._render_released = True


class GraphiumPrintController:
    """Lazy process-local Page Setup and Print Settings owner for one Graphium window."""

    __slots__ = (
        "_parent",
        "_show_error",
        "_show_warning",
        "_page_setup_store",
        "_page_setup",
        "_print_settings",
        "_active_operation",
        "_active_job",
        "_active_retain_settings",
        "_active_done_handler_id",
    )

    def __init__(
        self,
        parent: Gtk.Window,
        *,
        show_error: Callable[[str, str], None],
        show_warning: Callable[[str, str], None],
        page_setup_path: Path | None = None,
    ) -> None:
        self._parent = parent
        self._show_error = show_error
        self._show_warning = show_warning
        if page_setup_path is None:
            page_setup_path = resolve_xdg_paths().config / "page-setup.ini"
        self._page_setup_store = _PageSetupStore(page_setup_path)
        self._page_setup = self._page_setup_store.load()
        self._print_settings = Gtk.PrintSettings()
        self._active_operation = None
        self._active_job = None
        self._active_retain_settings = False
        self._active_done_handler_id = 0

    @property
    def busy(self) -> bool:
        return self._active_operation is not None

    def _reject_if_busy(self) -> bool:
        if not self.busy:
            return False
        self._show_warning(
            "Printing is already in progress",
            "Wait for the current Print or Print Preview operation to finish.",
        )
        return True

    def run_page_setup(self) -> None:
        if self._reject_if_busy():
            return
        before = self._page_setup.copy()
        candidate = Gtk.print_run_page_setup_dialog(
            self._parent,
            before,
            self._print_settings.copy(),
        )
        if candidate is None or _page_setup_signature(candidate) == _page_setup_signature(before):
            return
        try:
            self._page_setup_store.save(candidate)
        except Exception as exc:
            self._show_warning("Could not save Page Setup", str(exc))
            return
        self._page_setup = candidate.copy()

    def _new_operation(self, snapshot: PrintSnapshot):
        operation = Gtk.PrintOperation()
        operation.set_unit(Gtk.Unit.POINTS)
        operation.set_default_page_setup(self._page_setup.copy())
        operation.set_print_settings(self._print_settings.copy())
        operation.set_allow_async(True)
        job = _PrintJob(snapshot)
        operation.connect("begin-print", job.begin_print)
        operation.connect("paginate", job.paginate)
        operation.connect("draw-page", job.draw_page)
        operation.connect("end-print", job.end_print)
        return operation, job

    def _clear_active(self, operation) -> tuple[_PrintJob | None, bool]:
        if operation is not self._active_operation:
            return None, False
        job = self._active_job
        retain_settings = self._active_retain_settings
        handler_id = self._active_done_handler_id
        self._active_operation = None
        self._active_job = None
        self._active_retain_settings = False
        self._active_done_handler_id = 0
        if handler_id:
            try:
                operation.disconnect(handler_id)
            except Exception:
                pass
        if job is not None and not job.render_released:
            # Normal GTK lifecycle releases render geometry from the native end-print
            # signal.  This fallback is only for exceptional/immediate paths where GTK
            # never emitted end-print; done must not invoke render cleanup a second time.
            job.end_print()
        return job, retain_settings

    def _complete_operation(self, operation, result) -> None:
        _job, retain_settings = self._clear_active(operation)
        if _job is None:
            return
        if result == Gtk.PrintOperationResult.ERROR:
            self._show_error("Could not print", "GTK reported a printing error.")
        elif retain_settings and result == Gtk.PrintOperationResult.APPLY:
            settings = operation.get_print_settings()
            if settings is not None:
                self._print_settings = settings.copy()

    def _on_done(self, operation, result) -> None:
        self._complete_operation(operation, result)

    def _run(self, action, snapshot: PrintSnapshot, *, retain_settings: bool) -> None:
        if self._reject_if_busy():
            return
        operation, job = self._new_operation(snapshot)
        self._active_operation = operation
        self._active_job = job
        self._active_retain_settings = bool(retain_settings)
        self._active_done_handler_id = operation.connect("done", self._on_done)
        try:
            result = operation.run(action, self._parent)
        except Exception as exc:
            # The operation may already have completed through a nested GTK loop.
            # Only the still-active owner is allowed to publish the error/cleanup.
            if operation is self._active_operation:
                self._clear_active(operation)
                self._show_error("Could not print", str(exc))
            return

        if result != Gtk.PrintOperationResult.IN_PROGRESS:
            # Platforms that do not run this operation asynchronously complete here.
            # If GTK already emitted done from a nested loop, identity gating makes
            # this a harmless no-op instead of double-publishing APPLY/ERROR.
            self._complete_operation(operation, result)

    def preview(self, snapshot: PrintSnapshot) -> None:
        self._run(Gtk.PrintOperationAction.PREVIEW, snapshot, retain_settings=False)

    def print_dialog(self, snapshot: PrintSnapshot) -> None:
        self._run(Gtk.PrintOperationAction.PRINT_DIALOG, snapshot, retain_settings=True)
