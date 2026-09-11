"""Pure bounded word-span model for Graphium's explicit spell-check flow."""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata

MAX_SPELL_TOKEN_CODEPOINTS = 1024
_JOINERS = frozenset(("'", "\u2019", "-"))


def _word_material(ch: str) -> bool:
    return unicodedata.category(ch)[0] in {"L", "M"}


def _alphabetic(ch: str) -> bool:
    return unicodedata.category(ch)[0] == "L"


@dataclass(frozen=True)
class WordSpan:
    start: int
    end: int
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.start, int) or isinstance(self.start, bool) or self.start < 0:
            raise ValueError("start must be a non-negative integer")
        if not isinstance(self.end, int) or isinstance(self.end, bool) or self.end <= self.start:
            raise ValueError("end must be greater than start")
        if not isinstance(self.text, str) or len(self.text) != self.end - self.start:
            raise ValueError("text length must match the code-point span")


def iter_word_spans(text: str, *, start: int = 0, max_codepoints: int = MAX_SPELL_TOKEN_CODEPOINTS):
    """Yield lexical spans in code-point offsets; pathological overlong spans are skipped."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(start, int) or isinstance(start, bool) or start < 0 or start > len(text):
        raise ValueError("start must be a valid code-point offset")
    if not isinstance(max_codepoints, int) or isinstance(max_codepoints, bool) or max_codepoints <= 0:
        raise ValueError("max_codepoints must be a positive integer")
    n = len(text); i = start
    while i < n:
        if not _word_material(text[i]):
            i += 1; continue
        start = i; has_letter = False; overlong = False
        while i < n:
            ch = text[i]
            if _word_material(ch):
                has_letter = has_letter or _alphabetic(ch); i += 1
            elif ch in _JOINERS and i > start and i + 1 < n and _word_material(text[i - 1]) and _word_material(text[i + 1]):
                i += 1
            else:
                break
            if i - start > max_codepoints:
                overlong = True
        if has_letter and not overlong:
            yield WordSpan(start, i, text[start:i])
        if i == start:
            i += 1
