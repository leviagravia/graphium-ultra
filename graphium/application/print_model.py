"""GTK-free print snapshot semantics.

The GTK adapter owns native PrintOperation mechanics; this module owns only the immutable
product inputs captured for one print-family operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class PrintSnapshot:
    text: str
    title: str
    font_family: str
    font_size_points: float

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be str")
        if not isinstance(self.title, str) or not self.title:
            raise ValueError("title must be non-empty")
        if not isinstance(self.font_family, str) or not self.font_family.strip():
            raise ValueError("font_family must be non-empty")
        if float(self.font_size_points) <= 0:
            raise ValueError("font_size_points must be positive")


def build_print_snapshot(
    *,
    text: str,
    logical_path: str | None,
    base_font: tuple[str, float],
) -> PrintSnapshot:
    family, size_points = base_font
    title = os.path.basename(logical_path) if logical_path else "Untitled"
    return PrintSnapshot(
        text=text,
        title=title,
        font_family=family,
        font_size_points=size_points,
    )
