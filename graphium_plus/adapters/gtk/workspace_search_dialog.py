"""Thin GTK3 dialogs for explicit Workspace search."""
from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gtk

from graphium_plus.workspace.search import WorkspaceSearchReport, WorkspaceSearchResult


@dataclass(frozen=True)
class WorkspaceSearchRequest:
    query: str
    match_case: bool


def request_workspace_search(parent) -> WorkspaceSearchRequest | None:
    dialog = Gtk.Dialog(title="Find in Workspace", transient_for=parent, modal=True)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Search", Gtk.ResponseType.OK)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(10)
    label = Gtk.Label(label="Find text in .md and .txt files in the current Workspace:")
    label.set_xalign(0.0)
    entry = Gtk.SearchEntry()
    entry.set_activates_default(True)
    match_case = Gtk.CheckButton(label="Match case")
    box.pack_start(label, False, False, 0)
    box.pack_start(entry, False, False, 0)
    box.pack_start(match_case, False, False, 0)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.show_all()
    try:
        if dialog.run() != Gtk.ResponseType.OK:
            return None
        query = entry.get_text()
        return WorkspaceSearchRequest(query=query, match_case=match_case.get_active())
    finally:
        dialog.destroy()


def choose_workspace_search_result(parent, report: WorkspaceSearchReport) -> WorkspaceSearchResult | None:
    dialog = Gtk.Dialog(title="Workspace Search Results", transient_for=parent, modal=True)
    dialog.set_default_size(760, 440)
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    dialog.add_button("Go to", Gtk.ResponseType.OK)
    content = dialog.get_content_area()
    content.set_spacing(6)
    content.set_border_width(8)

    summary = Gtk.Label()
    summary.set_xalign(0.0)
    suffix = " (truncated; refine the query)" if report.truncated else ""
    summary.set_text(f"{len(report.results)} matches in {report.files_considered} files{suffix}")
    content.pack_start(summary, False, False, 0)

    model = Gtk.ListStore(str, int, str, object)
    for result in report.results:
        model.append([result.relative_path, result.line, result.context, result])
    tree = Gtk.TreeView(model=model)
    tree.set_headers_visible(True)
    for title, column, expand in (("File", 0, False), ("Line", 1, False), ("Context", 2, True)):
        renderer = Gtk.CellRendererText()
        if title == "Context":
            renderer.set_property("ellipsize", 3)
        view_column = Gtk.TreeViewColumn(title, renderer, text=column)
        view_column.set_resizable(True)
        view_column.set_expand(expand)
        tree.append_column(view_column)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.add(tree)
    content.pack_start(scroller, True, True, 0)

    if report.diagnostics:
        status = Gtk.Label(label=f"{len(report.diagnostics)} files/folders were skipped or bounded.")
        status.set_xalign(0.0)
        content.pack_start(status, False, False, 0)

    def row_activated(_tree, _path, _column) -> None:
        dialog.response(Gtk.ResponseType.OK)

    tree.connect("row-activated", row_activated)
    dialog.show_all()
    if len(model):
        tree.get_selection().select_path(0)
    try:
        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                return None
            selected_model, tree_iter = tree.get_selection().get_selected()
            if selected_model is None or tree_iter is None:
                continue
            return selected_model[tree_iter][3]
    finally:
        dialog.destroy()
