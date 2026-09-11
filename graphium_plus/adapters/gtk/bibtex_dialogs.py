"""GTK workflow for explicit BibTeX/BibLaTeX import and derived export.

The canonical bibliography remains ``references.md``.  This adapter owns only
one-shot file chooser/review interaction.  Parsing/planning belongs to
``graphium_plus.bibtex``; canonical persistence belongs to the existing
``MarkdownReferenceLibraryStore`` and its injected Core writer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from graphium_plus.bibtex import (
    BibDiagnostic,
    BibImportAnalysis,
    analyze_bibliography_import,
    export_bibliography,
    import_bibliography,
    plan_bibliography_import,
)
from graphium_plus.references import MarkdownReferenceLibraryStore, ReferenceRecord
from graphium_plus.bibtex_io import read_bibliography_text




class ExportWriterPort(Protocol):
    def observe_target(self, path: str): ...
    def commit(self, observation, data: bytes): ...


def _show_message(parent: Gtk.Window, title: str, message: str, *, error: bool = False) -> None:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.CLOSE,
        text=title,
    )
    dialog.format_secondary_text(message)
    try:
        dialog.run()
    finally:
        dialog.destroy()


def _diagnostic_text(diagnostics: tuple[BibDiagnostic, ...]) -> str:
    if not diagnostics:
        return "No diagnostics."
    return "\n".join(
        f"Line {item.line}: {item.message}" + ("" if item.blocking else " [notice]")
        for item in diagnostics[:100]
    ) + ("\n…additional diagnostics omitted." if len(diagnostics) > 100 else "")


def _choose_import_path(parent: Gtk.Window) -> str | None:
    dialog = Gtk.FileChooserDialog(
        title="Import BibTeX/BibLaTeX",
        transient_for=parent,
        action=Gtk.FileChooserAction.OPEN,
    )
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Open", Gtk.ResponseType.OK)
    bib_filter = Gtk.FileFilter()
    bib_filter.set_name("BibTeX/BibLaTeX (*.bib)")
    bib_filter.add_pattern("*.bib")
    dialog.add_filter(bib_filter)
    all_filter = Gtk.FileFilter()
    all_filter.set_name("All files")
    all_filter.add_pattern("*")
    dialog.add_filter(all_filter)
    try:
        if dialog.run() != Gtk.ResponseType.OK:
            return None
        return dialog.get_filename()
    finally:
        dialog.destroy()



def _author(record: ReferenceRecord) -> str:
    return "; ".join(record.authors)


def _import_statuses(
    incoming: tuple[ReferenceRecord, ...], analysis: BibImportAnalysis
) -> tuple[tuple[str, ReferenceRecord], ...]:
    new_keys = {record.key for record in analysis.new_records}
    same_keys = {record.key for record in analysis.identical_records}
    conflict_keys = {item.key for item in analysis.conflicts}
    result: list[tuple[str, ReferenceRecord]] = []
    # Conflicts first so a bounded preview never hides the most consequential rows.
    for status, keys in (("Conflict", conflict_keys), ("New", new_keys), ("Identical", same_keys)):
        result.extend((status, record) for record in incoming if record.key in keys)
    return tuple(result)


def _review_import(
    parent: Gtk.Window,
    *,
    source_name: str,
    incoming: tuple[ReferenceRecord, ...],
    analysis: BibImportAnalysis,
    diagnostics: tuple[BibDiagnostic, ...],
) -> str | None:
    dialog = Gtk.Dialog(title="Review Bibliography Import", transient_for=parent, modal=True)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Import", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_default_size(820, 500)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(10)

    summary = Gtk.Label()
    summary.set_xalign(0.0)
    summary.set_line_wrap(True)
    summary.set_text(
        f"{source_name}\n"
        f"New: {len(analysis.new_records)}   "
        f"Conflicts: {len(analysis.conflicts)}   "
        f"Already identical: {len(analysis.identical_records)}"
    )
    box.pack_start(summary, False, False, 0)

    model = Gtk.ListStore(str, str, str, str, str)
    rows = _import_statuses(incoming, analysis)
    for status, record in rows[:500]:
        model.append((status, record.key, _author(record), record.year, record.title))
    tree = Gtk.TreeView(model=model)
    for title, column in (("Status", 0), ("Key", 1), ("Author", 2), ("Year", 3), ("Title", 4)):
        renderer = Gtk.CellRendererText()
        view_column = Gtk.TreeViewColumn(title, renderer, text=column)
        view_column.set_resizable(True)
        tree.append_column(view_column)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.add(tree)
    box.pack_start(scroll, True, True, 0)
    if len(rows) > 500:
        note = Gtk.Label(label=f"Showing 500 of {len(rows)} import rows; all conflicts are counted in the summary.")
        note.set_xalign(0.0)
        box.pack_start(note, False, False, 0)

    keep = Gtk.RadioButton.new_with_label_from_widget(None, "Keep existing records for conflicting keys")
    replace = Gtk.RadioButton.new_with_label_from_widget(
        keep, "Replace whole existing records with imported versions"
    )
    keep.set_active(True)
    if analysis.conflicts:
        box.pack_start(keep, False, False, 0)
        box.pack_start(replace, False, False, 0)
        warning = Gtk.Label(label="No field-by-field or automatic merge is performed.")
        warning.set_xalign(0.0)
        box.pack_start(warning, False, False, 0)

    notices = tuple(item for item in diagnostics if not item.blocking)
    if notices:
        expander = Gtk.Expander(label=f"Import notices ({len(notices)})")
        label = Gtk.Label(label=_diagnostic_text(notices))
        label.set_xalign(0.0)
        label.set_selectable(True)
        label.set_line_wrap(True)
        expander.add(label)
        box.pack_start(expander, False, False, 0)

    dialog.show_all()
    try:
        if dialog.run() != Gtk.ResponseType.OK:
            return None
        return "replace-existing" if analysis.conflicts and replace.get_active() else "keep-existing"
    finally:
        dialog.destroy()


def run_bibliography_import(parent: Gtk.Window, store: MarkdownReferenceLibraryStore) -> bool:
    """Explicitly import a local .bib projection into the canonical library."""
    path = _choose_import_path(parent)
    if not path:
        return False
    try:
        text = read_bibliography_text(path)
    except (OSError, ValueError) as exc:
        _show_message(parent, "Bibliography import failed", str(exc), error=True)
        return False
    parsed = import_bibliography(text)
    blocking = tuple(item for item in parsed.diagnostics if item.blocking)
    if blocking:
        _show_message(
            parent,
            "Bibliography import is blocked",
            _diagnostic_text(parsed.diagnostics),
            error=True,
        )
        return False
    if not parsed.records:
        _show_message(parent, "Nothing to import", "The selected bibliography contains no usable records.")
        return False

    snapshot = store.load()
    if not snapshot.writable:
        _show_message(
            parent,
            "Reference Library cannot be changed",
            "\n".join(item.message for item in snapshot.diagnostics),
            error=True,
        )
        return False
    analysis = analyze_bibliography_import(snapshot.records, parsed.records)
    policy = _review_import(
        parent,
        source_name=Path(path).name,
        incoming=parsed.records,
        analysis=analysis,
        diagnostics=parsed.diagnostics,
    )
    if policy is None:
        return False
    plan = plan_bibliography_import(snapshot.records, parsed.records, conflict_policy=policy)
    if plan.records == snapshot.records:
        _show_message(parent, "Bibliography import", "No canonical reference records need to change.")
        return True
    saved = store.save(plan.records, snapshot.token)
    if not saved.saved:
        _show_message(
            parent,
            "Bibliography import was not saved",
            saved.message or saved.status,
            error=True,
        )
        return False
    _show_message(
        parent,
        "Bibliography import complete",
        f"Added {len(plan.added_keys)}; replaced {len(plan.replaced_keys)}; unchanged {len(plan.unchanged_keys)}.",
    )
    return True


def _choose_export_path(parent: Gtk.Window, *, flavor: str) -> str | None:
    label = "BibLaTeX" if flavor == "biblatex" else "BibTeX"
    dialog = Gtk.FileChooserDialog(
        title=f"Export {label}",
        transient_for=parent,
        action=Gtk.FileChooserAction.SAVE,
    )
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Export", Gtk.ResponseType.OK)
    dialog.set_do_overwrite_confirmation(True)
    dialog.set_current_name("references.bib")
    bib_filter = Gtk.FileFilter()
    bib_filter.set_name("Bibliography (*.bib)")
    bib_filter.add_pattern("*.bib")
    dialog.add_filter(bib_filter)
    try:
        if dialog.run() != Gtk.ResponseType.OK:
            return None
        path = dialog.get_filename()
        if path and not path.casefold().endswith(".bib"):
            path += ".bib"
        return path
    finally:
        dialog.destroy()


def _confirm_export_notices(parent: Gtk.Window, diagnostics: tuple[BibDiagnostic, ...]) -> bool:
    if not diagnostics:
        return True
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.NONE,
        text="Bibliography export has notices",
    )
    dialog.format_secondary_text(_diagnostic_text(diagnostics))
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Continue Export", Gtk.ResponseType.OK)
    try:
        return dialog.run() == Gtk.ResponseType.OK
    finally:
        dialog.destroy()


def run_bibliography_export(
    parent: Gtk.Window,
    store: MarkdownReferenceLibraryStore,
    writer: ExportWriterPort,
    *,
    flavor: str,
) -> bool:
    """Export a derived .bib file using the already-owned Core writer."""
    snapshot = store.load()
    if not snapshot.writable:
        _show_message(
            parent,
            "Reference Library cannot be exported",
            "\n".join(item.message for item in snapshot.diagnostics),
            error=True,
        )
        return False
    result = export_bibliography(snapshot.records, flavor=flavor)
    if not result.complete:
        _show_message(parent, "Bibliography export is blocked", _diagnostic_text(result.diagnostics), error=True)
        return False
    if result.diagnostics and not _confirm_export_notices(parent, result.diagnostics):
        return False
    path = _choose_export_path(parent, flavor=flavor)
    if not path:
        return False
    try:
        observation = writer.observe_target(path)
        writer.commit(observation, result.text.encode("utf-8"))
    except Exception as exc:
        _show_message(parent, "Bibliography export failed", str(exc), error=True)
        return False
    _show_message(parent, "Bibliography export complete", path)
    return True
