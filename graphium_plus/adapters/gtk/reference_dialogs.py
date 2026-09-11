"""Bounded GTK dialogs for Graphium Plus references and citations.

These widgets project :mod:`graphium_plus.references` records only.  They own no
bibliographic parser, database, index, background worker or document mutation
logic.  Citation insertion remains owned by the A3 citation planner and the
existing NativeEditorController transaction authority.
"""
from __future__ import annotations

from dataclasses import replace

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from graphium_plus.references import (
    MarkdownReferenceLibraryStore,
    ReferenceLibrarySnapshot,
    ReferenceRecord,
    search_references,
)


_COL_KEY = 0
_COL_AUTHOR = 1
_COL_YEAR = 2
_COL_TITLE = 3


def _author_text(record: ReferenceRecord) -> str:
    return "; ".join(record.authors)


def _records_store(records) -> Gtk.ListStore:
    store = Gtk.ListStore(str, str, str, str)
    for record in records:
        store.append((record.key, _author_text(record), record.year, record.title))
    return store


def _reference_tree(store: Gtk.ListStore) -> Gtk.TreeView:
    tree = Gtk.TreeView(model=store)
    tree.set_headers_visible(True)
    for title, column in (("Key", _COL_KEY), ("Author", _COL_AUTHOR), ("Year", _COL_YEAR), ("Title", _COL_TITLE)):
        renderer = Gtk.CellRendererText()
        view_column = Gtk.TreeViewColumn(title, renderer, text=column)
        view_column.set_resizable(True)
        tree.append_column(view_column)
    return tree


def _selected_key(tree: Gtk.TreeView) -> str | None:
    model, tree_iter = tree.get_selection().get_selected()
    if model is None or tree_iter is None:
        return None
    return str(model[tree_iter][_COL_KEY])


def _show_error(parent: Gtk.Window, title: str, message: str) -> None:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=title,
    )
    dialog.format_secondary_text(message)
    try:
        dialog.run()
    finally:
        dialog.destroy()


def _reference_form(parent: Gtk.Window, record: ReferenceRecord | None = None) -> ReferenceRecord | None:
    dialog = Gtk.Dialog(
        title="Edit Reference" if record is not None else "Add Reference",
        transient_for=parent,
        modal=True,
    )
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    grid = Gtk.Grid(column_spacing=10, row_spacing=8, margin=12)
    dialog.get_content_area().pack_start(grid, True, True, 0)

    values = {
        "Key": "" if record is None else record.key,
        "Type": "article" if record is None else record.type,
        "Author(s)": "" if record is None else "; ".join(record.authors),
        "Title": "" if record is None else record.title,
        "Year": "" if record is None else record.year,
        "DOI": "" if record is None else record.doi,
        "URL": "" if record is None else record.url,
    }
    entries: dict[str, Gtk.Entry] = {}
    for row, (label, value) in enumerate(values.items()):
        caption = Gtk.Label(label=f"{label}:")
        caption.set_xalign(1.0)
        entry = Gtk.Entry()
        entry.set_text(value)
        entry.set_hexpand(True)
        grid.attach(caption, 0, row, 1, 1)
        grid.attach(entry, 1, row, 1, 1)
        entries[label] = entry

    if record is not None:
        entries["Key"].set_sensitive(False)
        entries["Key"].set_tooltip_text(
            "Reference keys are immutable; create a new reference if a different key is required."
        )

    ok = dialog.get_widget_for_response(Gtk.ResponseType.OK)

    def sync_ok(*_args) -> None:
        ok.set_sensitive(bool(entries["Key"].get_text().strip() and entries["Title"].get_text().strip()))

    entries["Key"].connect("changed", sync_ok)
    entries["Title"].connect("changed", sync_ok)
    sync_ok()
    dialog.set_default_size(620, -1)
    dialog.show_all()
    entries["Key"].grab_focus()
    try:
        if dialog.run() != Gtk.ResponseType.OK:
            return None
        authors = tuple(
            item.strip() for item in entries["Author(s)"].get_text().split(";") if item.strip()
        )
        if record is None:
            return ReferenceRecord(
                key=entries["Key"].get_text(),
                type=entries["Type"].get_text(),
                authors=authors,
                title=entries["Title"].get_text(),
                year=entries["Year"].get_text(),
                doi=entries["DOI"].get_text(),
                url=entries["URL"].get_text(),
            )
        return replace(
            record,
            key=record.key,
            type=entries["Type"].get_text(),
            authors=authors,
            title=entries["Title"].get_text(),
            year=entries["Year"].get_text(),
            doi=entries["DOI"].get_text(),
            url=entries["URL"].get_text(),
        )
    except ValueError as exc:
        _show_error(parent, "Reference was not accepted", str(exc))
        return None
    finally:
        dialog.destroy()


def _confirm_delete(parent: Gtk.Window, record: ReferenceRecord) -> bool:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.NONE,
        text=f"Delete reference {record.key}?",
    )
    dialog.format_secondary_text(record.title)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Delete", Gtk.ResponseType.OK)
    try:
        return dialog.run() == Gtk.ResponseType.OK
    finally:
        dialog.destroy()


def run_reference_library(parent: Gtk.Window, store: MarkdownReferenceLibraryStore) -> ReferenceLibrarySnapshot:
    """Edit the one canonical local reference library and save explicitly on OK."""
    snapshot = store.load()
    if snapshot.diagnostics:
        _show_error(
            parent,
            "Reference Library cannot be edited",
            "\n".join(item.message for item in snapshot.diagnostics),
        )
        return snapshot

    records = list(snapshot.records)
    dialog = Gtk.Dialog(title="Reference Library", transient_for=parent, modal=True)
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
    dialog.set_default_size(780, 460)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(10)

    search = Gtk.SearchEntry()
    search.set_placeholder_text("Search references")
    box.pack_start(search, False, False, 0)

    model = _records_store(records)
    tree = _reference_tree(model)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.add(tree)
    box.pack_start(scroll, True, True, 0)

    buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    add_button = Gtk.Button(label="Add")
    edit_button = Gtk.Button(label="Edit")
    delete_button = Gtk.Button(label="Delete")
    buttons.pack_start(add_button, False, False, 0)
    buttons.pack_start(edit_button, False, False, 0)
    buttons.pack_start(delete_button, False, False, 0)
    box.pack_start(buttons, False, False, 0)

    def render(query: str = "") -> None:
        model.clear()
        visible = search_references(records, query, limit=500)
        for item in visible:
            model.append((item.key, _author_text(item), item.year, item.title))

    def add_reference(_button) -> None:
        created = _reference_form(parent)
        if created is None:
            return
        if any(item.key == created.key for item in records):
            _show_error(parent, "Duplicate reference key", created.key)
            return
        records.append(created)
        render(search.get_text())

    def edit_reference(_button) -> None:
        key = _selected_key(tree)
        if key is None:
            return
        index = next((i for i, item in enumerate(records) if item.key == key), None)
        if index is None:
            return
        edited = _reference_form(parent, records[index])
        if edited is None:
            return
        if edited.key != key and any(item.key == edited.key for item in records):
            _show_error(parent, "Duplicate reference key", edited.key)
            return
        records[index] = edited
        render(search.get_text())

    def delete_reference(_button) -> None:
        key = _selected_key(tree)
        if key is None:
            return
        record = next((item for item in records if item.key == key), None)
        if record is None or not _confirm_delete(parent, record):
            return
        records[:] = [item for item in records if item.key != key]
        render(search.get_text())

    search.connect("search-changed", lambda entry: render(entry.get_text()))
    add_button.connect("clicked", add_reference)
    edit_button.connect("clicked", edit_reference)
    delete_button.connect("clicked", delete_reference)
    tree.connect("row-activated", lambda *_args: edit_reference(None))

    dialog.show_all()
    search.grab_focus()
    try:
        if dialog.run() != Gtk.ResponseType.OK:
            return snapshot
        result = store.save(records, snapshot.token)
        if not result.saved:
            _show_error(parent, "Reference Library was not saved", result.message or result.status)
        return result.snapshot
    finally:
        dialog.destroy()


def run_reference_picker(
    parent: Gtk.Window,
    records: tuple[ReferenceRecord, ...],
    *,
    title: str,
    accept_label: str,
    with_locator: bool = False,
) -> tuple[ReferenceRecord, str] | None:
    """Choose one local record using bounded on-demand search."""
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, accept_label, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_default_size(760, 420)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(10)

    search = Gtk.SearchEntry()
    search.set_placeholder_text("Key, author, title, year, DOI…")
    search.set_activates_default(True)
    box.pack_start(search, False, False, 0)
    model = _records_store(records)
    tree = _reference_tree(model)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.add(tree)
    box.pack_start(scroll, True, True, 0)

    locator = Gtk.Entry() if with_locator else None
    if locator is not None:
        locator.set_placeholder_text("Locator, e.g. p. 42 or ch. 3")
        locator.set_activates_default(True)
        box.pack_start(locator, False, False, 0)

    ok = dialog.get_widget_for_response(Gtk.ResponseType.OK)
    ok.set_sensitive(False)

    def render(query: str) -> None:
        model.clear()
        for item in search_references(records, query, limit=100):
            model.append((item.key, _author_text(item), item.year, item.title))
        if len(model):
            tree.get_selection().select_path(0)
        ok.set_sensitive(_selected_key(tree) is not None)

    search.connect("search-changed", lambda entry: render(entry.get_text()))
    tree.get_selection().connect("changed", lambda *_args: ok.set_sensitive(_selected_key(tree) is not None))
    tree.connect("row-activated", lambda *_args: dialog.response(Gtk.ResponseType.OK))
    dialog.show_all()
    search.grab_focus()
    try:
        if dialog.run() != Gtk.ResponseType.OK:
            return None
        key = _selected_key(tree)
        if key is None:
            return None
        record = next((item for item in records if item.key == key), None)
        if record is None:
            return None
        return record, "" if locator is None else locator.get_text()
    finally:
        dialog.destroy()


def show_reference_details(parent: Gtk.Window, record: ReferenceRecord) -> None:
    values = [
        ("Key", record.key),
        ("Type", record.type),
        ("Author(s)", "; ".join(record.authors)),
        ("Title", record.title),
        ("Year", record.year),
        ("Container", record.container_title),
        ("Publisher", record.publisher),
        ("Pages", record.pages),
        ("DOI", record.doi),
        ("URL", record.url),
    ]
    text = "\n".join(f"{label}: {value}" for label, value in values if value)
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.CLOSE,
        text=record.display_label,
    )
    dialog.format_secondary_text(text)
    try:
        dialog.run()
    finally:
        dialog.destroy()
