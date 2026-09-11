"""GTK-free Graphium view/preferences settings authority.

Persistent presentation and lightweight editor preferences live in one immutable snapshot.
Transient zoom/fullscreen and document content remain outside this model.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol


DEFAULT_FONT_FAMILY = "Monospace"
DEFAULT_FONT_SIZE_POINTS = 11.0
MIN_FONT_SIZE_POINTS = 6.0
MAX_FONT_SIZE_POINTS = 72.0

APPEARANCE_SYSTEM = "system"
APPEARANCE_LIGHT = "light"
APPEARANCE_DARK = "dark"
APPEARANCE_VALUES = (APPEARANCE_SYSTEM, APPEARANCE_LIGHT, APPEARANCE_DARK)

DEFAULT_TAB_WIDTH = 8
MIN_TAB_WIDTH = 1
MAX_TAB_WIDTH = 32

DEFAULT_WINDOW_WIDTH = 720
DEFAULT_WINDOW_HEIGHT = 520
MIN_WINDOW_WIDTH = 320
MIN_WINDOW_HEIGHT = 240
MAX_WINDOW_WIDTH = 8192
MAX_WINDOW_HEIGHT = 8192


@dataclass(frozen=True)
class ViewSettings:
    word_wrap: bool = False
    line_numbers: bool = False
    status_bar: bool = True
    font_family: str = DEFAULT_FONT_FAMILY
    font_size_points: float = DEFAULT_FONT_SIZE_POINTS
    appearance: str = APPEARANCE_SYSTEM
    tab_width: int = DEFAULT_TAB_WIDTH
    insert_spaces: bool = False
    window_width: int = DEFAULT_WINDOW_WIDTH
    window_height: int = DEFAULT_WINDOW_HEIGHT

    def __post_init__(self) -> None:
        if not isinstance(self.word_wrap, bool):
            raise TypeError("word_wrap must be bool")
        if not isinstance(self.line_numbers, bool):
            raise TypeError("line_numbers must be bool")
        if not isinstance(self.status_bar, bool):
            raise TypeError("status_bar must be bool")
        if not isinstance(self.font_family, str) or not self.font_family.strip():
            raise ValueError("font_family must be a non-empty string")
        size = float(self.font_size_points)
        if not MIN_FONT_SIZE_POINTS <= size <= MAX_FONT_SIZE_POINTS:
            raise ValueError(
                f"font_size_points must be between {MIN_FONT_SIZE_POINTS:g} and "
                f"{MAX_FONT_SIZE_POINTS:g}"
            )
        if not isinstance(self.appearance, str) or self.appearance not in APPEARANCE_VALUES:
            raise ValueError(f"appearance must be one of {APPEARANCE_VALUES!r}")
        if isinstance(self.tab_width, bool) or not isinstance(self.tab_width, int):
            raise TypeError("tab_width must be int")
        if not MIN_TAB_WIDTH <= self.tab_width <= MAX_TAB_WIDTH:
            raise ValueError(f"tab_width must be between {MIN_TAB_WIDTH} and {MAX_TAB_WIDTH}")
        if not isinstance(self.insert_spaces, bool):
            raise TypeError("insert_spaces must be bool")
        if isinstance(self.window_width, bool) or not isinstance(self.window_width, int):
            raise TypeError("window_width must be int")
        if isinstance(self.window_height, bool) or not isinstance(self.window_height, int):
            raise TypeError("window_height must be int")
        if not MIN_WINDOW_WIDTH <= self.window_width <= MAX_WINDOW_WIDTH:
            raise ValueError(
                f"window_width must be between {MIN_WINDOW_WIDTH} and {MAX_WINDOW_WIDTH}"
            )
        if not MIN_WINDOW_HEIGHT <= self.window_height <= MAX_WINDOW_HEIGHT:
            raise ValueError(
                f"window_height must be between {MIN_WINDOW_HEIGHT} and {MAX_WINDOW_HEIGHT}"
            )

    def updated(self, **changes) -> "ViewSettings":
        return replace(self, **changes)


def logical_column_for_prefix(prefix: str, tab_width: int) -> int:
    """Return the logical tab-stop column after *prefix* without touching document state."""
    if not isinstance(prefix, str):
        raise TypeError("prefix must be str")
    if isinstance(tab_width, bool) or not isinstance(tab_width, int):
        raise TypeError("tab_width must be int")
    if not MIN_TAB_WIDTH <= tab_width <= MAX_TAB_WIDTH:
        raise ValueError(f"tab_width must be between {MIN_TAB_WIDTH} and {MAX_TAB_WIDTH}")
    column = 0
    for char in prefix:
        if char == "\t":
            column += tab_width - (column % tab_width)
        else:
            column += 1
    return column


def spaces_to_next_tab_stop(prefix: str, tab_width: int) -> int:
    column = logical_column_for_prefix(prefix, tab_width)
    return tab_width - (column % tab_width)


class ViewSettingsStorePort(Protocol):
    def load(self) -> ViewSettings: ...
    def save(self, settings: ViewSettings) -> None: ...


class ViewSettingsController:
    """Single in-memory authority for persistent View settings and editor preferences."""

    __slots__ = ("_store", "_current")

    def __init__(self, store: ViewSettingsStorePort) -> None:
        self._store = store
        self._current = store.load()

    @property
    def current(self) -> ViewSettings:
        return self._current

    def update(self, **changes) -> ViewSettings:
        candidate = self._current.updated(**changes)
        # Commit to the config store before publishing the new in-memory setting.
        # If persistence fails, active settings remain exactly the prior snapshot.
        self._store.save(candidate)
        self._current = candidate
        return candidate
