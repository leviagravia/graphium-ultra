"""Thin GTK projection for Graphium Plus academic review diagnostics."""
from __future__ import annotations

from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from graphium_plus.references import ReferenceFileToken, reference_file_token
from graphium_plus.review import AcademicReviewDiagnostic, AcademicReviewReport


_COL_SEVERITY = 0
_COL_CATEGORY = 1
_COL_LOCATION = 2
_COL_MESSAGE = 3
_COL_INDEX = 4

_CATEGORY_LABELS = {
    "markdown": "Markdown",
    "footnotes": "Footnotes",
    "citations": "Citations",
    "references": "References",
    "writing-hygiene": "Writing Hygiene",
}


def _location(item: AcademicReviewDiagnostic) -> str:
    if item.target.scope == "reference-library":
        return f"References, line {item.target.line}" if item.target.line else "References"
    if item.target.line is not None:
        return f"Line {item.target.line}"
    return "Document"


def run_academic_review(
    parent: Gtk.Window,
    report: AcademicReviewReport,
) -> AcademicReviewDiagnostic | None:
    """Show one immutable report and return the selected navigation target."""
    if not isinstance(report, AcademicReviewReport):
        raise TypeError("report must be AcademicReviewReport")

    dialog = Gtk.Dialog(title="Academic Review", transient_for=parent, modal=True)
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    go_to = dialog.add_button("Go to", Gtk.ResponseType.OK)
    dialog.set_default_size(860, 500)

    area = dialog.get_content_area()
    area.set_spacing(8)
    area.set_border_width(10)
    summary = Gtk.Label(
        label=f"{len(report.errors)} errors, {len(report.warnings)} warnings",
        xalign=0.0,
    )
    area.pack_start(summary, False, False, 0)

    model = Gtk.ListStore(str, str, str, str, int)
    for index, item in enumerate(report.diagnostics):
        model.append(
            (
                item.severity.capitalize(),
                _CATEGORY_LABELS[item.category],
                _location(item),
                item.message,
                index,
            )
        )

    tree = Gtk.TreeView(model=model)
    tree.set_headers_visible(True)
    for title, column, expand in (
        ("Severity", _COL_SEVERITY, False),
        ("Category", _COL_CATEGORY, False),
        ("Location", _COL_LOCATION, False),
        ("Issue", _COL_MESSAGE, True),
    ):
        renderer = Gtk.CellRendererText()
        view_column = Gtk.TreeViewColumn(title, renderer, text=column)
        view_column.set_expand(expand)
        view_column.set_resizable(True)
        tree.append_column(view_column)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.add(tree)
    area.pack_start(scroller, True, True, 0)

    selection = tree.get_selection()
    go_to.set_sensitive(False)

    def sync_go_to(*_args) -> None:
        model_value, tree_iter = selection.get_selected()
        go_to.set_sensitive(model_value is not None and tree_iter is not None)

    selection.connect("changed", sync_go_to)
    tree.connect("row-activated", lambda *_args: dialog.response(Gtk.ResponseType.OK))
    if len(model):
        selection.select_path(0)
    sync_go_to()

    dialog.show_all()
    try:
        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                return None
            selected_model, tree_iter = selection.get_selected()
            if selected_model is None or tree_iter is None:
                continue
            index = int(selected_model[tree_iter][_COL_INDEX])
            if 0 <= index < len(report.diagnostics):
                return report.diagnostics[index]
    finally:
        dialog.destroy()


def show_reference_source_target(
    parent: Gtk.Window,
    *,
    path: str | Path,
    line: int,
    expected_token: ReferenceFileToken,
) -> bool:
    """Show the canonical reference source at one exact stale-fenced line."""
    target = Path(path)
    if line < 1:
        raise ValueError("reference source line must be positive")
    if reference_file_token(target) != expected_token:
        return False
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if reference_file_token(target) != expected_token:
        return False

    dialog = Gtk.Dialog(title="Reference Library Source", transient_for=parent, modal=True)
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    dialog.set_default_size(760, 520)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    view = Gtk.TextView()
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_monospace(True)
    view.set_wrap_mode(Gtk.WrapMode.NONE)
    view.set_left_margin(10)
    view.set_right_margin(10)
    view.set_top_margin(8)
    view.set_bottom_margin(8)
    buffer = view.get_buffer()
    buffer.set_text(text)

    max_line = max(0, buffer.get_line_count() - 1)
    start = buffer.get_iter_at_line(min(line - 1, max_line))
    end = start.copy()
    end.forward_to_line_end()
    buffer.select_range(end, start)
    scroller.add(view)
    dialog.get_content_area().pack_start(scroller, True, True, 0)
    dialog.show_all()
    view.scroll_to_iter(start, 0.08, True, 0.0, 0.25)
    try:
        dialog.run()
        return True
    finally:
        dialog.destroy()
