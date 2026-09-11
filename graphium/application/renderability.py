"""Interactive-renderability safety policy, GTK-free.

Graphium's document loader accepts valid plain text independently from any particular
widget. The editor adds a narrower *interactive editor* admission budget because Gtk.TextView
can become unresponsive on pathological single logical lines.  The policy never changes
text: content is either admitted exactly or refused before installation into the editor.
"""
from __future__ import annotations


MAX_INTERACTIVE_LINE_CHARS = 20_000


class InteractiveRenderabilityError(RuntimeError):
    """Valid text cannot be installed safely in the current interactive editor."""

    def __init__(self, *, limit: int, line_number: int | None = None, observed_chars: int | None = None) -> None:
        self.limit = int(limit)
        self.line_number = int(line_number) if line_number is not None else None
        self.observed_chars = int(observed_chars) if observed_chars is not None else None
        where = f"Logical line {self.line_number}" if self.line_number is not None else "The resulting logical line"
        observed = (
            f" ({self.observed_chars} characters)" if self.observed_chars is not None else ""
        )
        super().__init__(
            f"{where}{observed} exceeds Graphium's interactive rendering safety budget "
            f"of {self.limit} characters per logical line. The text was not changed."
        )


def _validate_limit(limit: int) -> int:
    limit = int(limit)
    if limit <= 0:
        raise ValueError("interactive line limit must be positive")
    return limit


def ensure_interactive_text_renderable(
    text: str,
    *,
    max_line_chars: int = MAX_INTERACTIVE_LINE_CHARS,
) -> None:
    """Reject text containing a logical line wider than the interactive safety budget.

    The scan uses ``str.find`` rather than ``split`` so normal large multiline documents
    are not duplicated into a list of line strings.  Input is expected to be Graphium's
    LF-normalized internal text.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    limit = _validate_limit(max_line_chars)
    start = 0
    line_number = 1
    text_len = len(text)
    while True:
        newline = text.find("\n", start)
        end = text_len if newline < 0 else newline
        width = end - start
        if width > limit:
            raise InteractiveRenderabilityError(
                limit=limit,
                line_number=line_number,
                observed_chars=width,
            )
        if newline < 0:
            return
        start = newline + 1
        line_number += 1


def ensure_insert_renderable(
    *,
    prefix_chars: int,
    suffix_chars: int,
    inserted_text: str,
    max_line_chars: int = MAX_INTERACTIVE_LINE_CHARS,
) -> None:
    """Validate line widths that would result from one insertion at a safe buffer state."""
    if prefix_chars < 0 or suffix_chars < 0:
        raise ValueError("prefix/suffix character counts must be non-negative")
    if not isinstance(inserted_text, str):
        raise TypeError("inserted_text must be a string")
    if not inserted_text:
        return
    limit = _validate_limit(max_line_chars)

    first_newline = inserted_text.find("\n")
    if first_newline < 0:
        resulting = prefix_chars + len(inserted_text) + suffix_chars
        if resulting > limit:
            raise InteractiveRenderabilityError(limit=limit, observed_chars=resulting)
        return

    first_width = prefix_chars + first_newline
    if first_width > limit:
        raise InteractiveRenderabilityError(limit=limit, observed_chars=first_width)

    start = first_newline + 1
    while True:
        newline = inserted_text.find("\n", start)
        if newline < 0:
            last_width = (len(inserted_text) - start) + suffix_chars
            if last_width > limit:
                raise InteractiveRenderabilityError(limit=limit, observed_chars=last_width)
            return
        width = newline - start
        if width > limit:
            raise InteractiveRenderabilityError(limit=limit, observed_chars=width)
        start = newline + 1


def ensure_join_renderable(
    *,
    prefix_chars: int,
    suffix_chars: int,
    max_line_chars: int = MAX_INTERACTIVE_LINE_CHARS,
) -> None:
    """Validate a deletion that removes one or more newlines and joins two line edges."""
    if prefix_chars < 0 or suffix_chars < 0:
        raise ValueError("prefix/suffix character counts must be non-negative")
    limit = _validate_limit(max_line_chars)
    resulting = prefix_chars + suffix_chars
    if resulting > limit:
        raise InteractiveRenderabilityError(limit=limit, observed_chars=resulting)
