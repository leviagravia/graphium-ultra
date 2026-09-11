"""Small GTK-free pagination helpers for Graphium.

GTK supplies measured heights for complete visual Pango layout lines.  These helpers only
combine those indivisible lines into pages; they never derive layout from characters,
Python source lines, or editor viewport geometry.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VisualLinePage:
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.start_line < 0 or self.end_line <= self.start_line:
            raise ValueError("page must contain at least one visual line")


@dataclass(frozen=True)
class VisualLineSpan:
    """Contiguous visual-line range owned by one measured layout chunk."""

    chunk_index: int
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.chunk_index < 0:
            raise ValueError("chunk_index must be non-negative")
        if self.start_line < 0 or self.end_line <= self.start_line:
            raise ValueError("span must contain at least one visual line")


@dataclass(frozen=True)
class IncrementalVisualPage:
    spans: tuple[VisualLineSpan, ...]

    def __post_init__(self) -> None:
        if not self.spans:
            raise ValueError("incremental page must contain at least one span")


class IncrementalVisualPaginator:
    """Accumulate measured visual lines across independently measured layout chunks.

    The GTK adapter can therefore yield between chunks while preserving exact visual-line
    page boundaries.  This object stores no text and performs no layout work itself.
    """

    __slots__ = ("_usable_height", "_pages", "_current", "_used", "_finished")

    def __init__(self, *, usable_height: float) -> None:
        usable = float(usable_height)
        if usable <= 0:
            raise ValueError("usable_height must be positive")
        self._usable_height = usable
        self._pages: list[IncrementalVisualPage] = []
        self._current: list[VisualLineSpan] = []
        self._used = 0.0
        self._finished = False

    @property
    def finished(self) -> bool:
        return self._finished

    def _commit_page(self) -> None:
        if self._current:
            self._pages.append(IncrementalVisualPage(tuple(self._current)))
            self._current = []
            self._used = 0.0

    def add_chunk(self, chunk_index: int, line_heights: tuple[float, ...] | list[float]) -> None:
        if self._finished:
            raise RuntimeError("cannot add visual lines after finish")
        if chunk_index < 0:
            raise ValueError("chunk_index must be non-negative")
        heights = tuple(float(value) for value in line_heights)
        if not heights or any(value <= 0 for value in heights):
            raise ValueError("line_heights must contain positive values")

        for line_index, height in enumerate(heights):
            if self._current and self._used + height > self._usable_height:
                self._commit_page()

            if (
                self._current
                and self._current[-1].chunk_index == chunk_index
                and self._current[-1].end_line == line_index
            ):
                previous = self._current[-1]
                self._current[-1] = VisualLineSpan(
                    chunk_index,
                    previous.start_line,
                    line_index + 1,
                )
            else:
                self._current.append(VisualLineSpan(chunk_index, line_index, line_index + 1))
            self._used += height

    def finish(self) -> tuple[IncrementalVisualPage, ...]:
        if not self._finished:
            self._commit_page()
            self._finished = True
        return tuple(self._pages)


def logical_line_chunk_end(
    text: str,
    start: int,
    *,
    target_chars: int,
    max_logical_lines: int,
) -> int:
    """Return a forward-progress chunk end that never splits a logical source line."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if start < 0 or start > len(text):
        raise ValueError("start outside text")
    if target_chars <= 0 or max_logical_lines <= 0:
        raise ValueError("chunk budgets must be positive")
    if start == len(text):
        return start

    end = start
    logical_lines = 0
    while end < len(text) and logical_lines < max_logical_lines:
        newline = text.find("\n", end)
        next_end = len(text) if newline < 0 else newline + 1
        if end > start and next_end - start > target_chars:
            break
        end = next_end
        logical_lines += 1
        if end - start >= target_chars:
            break
    if end <= start:
        newline = text.find("\n", start)
        end = len(text) if newline < 0 else newline + 1
    return end


def paginate_visual_line_heights(
    line_heights: tuple[float, ...] | list[float], *, usable_height: float
) -> tuple[VisualLinePage, ...]:
    """Group complete measured visual lines into flat pages.

    Retained for the simple headless contract and small callers.  A visual line taller than
    the printable body is kept whole on its own page.
    """
    usable = float(usable_height)
    if usable <= 0:
        raise ValueError("usable_height must be positive")
    if not line_heights:
        return (VisualLinePage(0, 1),)

    heights = tuple(float(value) for value in line_heights)
    if any(value <= 0 for value in heights):
        raise ValueError("visual line heights must all be positive")

    pages: list[VisualLinePage] = []
    start = 0
    used = 0.0
    for index, height in enumerate(heights):
        if index > start and used + height > usable:
            pages.append(VisualLinePage(start, index))
            start = index
            used = 0.0
        used += height
    pages.append(VisualLinePage(start, len(heights)))
    return tuple(pages)
