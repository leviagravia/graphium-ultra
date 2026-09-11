"""Pure explicit/on-demand text statistics for Graphium."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextStatistics:
    lines: int
    words: int
    characters: int


def count_text_statistics(text: str) -> TextStatistics:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    words = 0
    in_word = False
    for char in text:
        nonspace = not char.isspace()
        if nonspace and not in_word:
            words += 1
        in_word = nonspace
    return TextStatistics(
        lines=0 if not text else text.count("\n") + 1,
        words=words,
        characters=len(text),
    )
