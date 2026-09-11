from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
import time

from tests.desktop.harness.runtime import drain, load_gtk3, text_of


def _menu_item(parent, Gtk, label):
    return next(
        item for item in parent.get_children()
        if isinstance(item, Gtk.MenuItem) and item.get_label() == label
    )


def _widgets(root, Gtk, widget_type):
    found = []
    if isinstance(root, widget_type):
        found.append(root)
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            found.extend(_widgets(child, Gtk, widget_type))
    return found


def _schedule(GLib, Gtk, failures, *, title: str, action, timeout: float = 4.0):
    deadline = time.monotonic() + timeout

    def poll():
        for window in Gtk.Window.list_toplevels():
            if not isinstance(window, Gtk.Dialog) or window.get_title() != title:
                continue
            try:
                action(window)
            except Exception as exc:
                failures.append(f"{title}: {type(exc).__name__}: {exc}")
                try:
                    window.response(Gtk.ResponseType.CANCEL)
                except Exception:
                    pass
            return False
        if time.monotonic() < deadline:
            return True
        failures.append(f"dialog not found before timeout: {title}")
        for window in Gtk.Window.list_toplevels():
            if isinstance(window, Gtk.Dialog):
                try:
                    window.response(Gtk.ResponseType.CANCEL)
                except Exception:
                    pass
        return False

    GLib.timeout_add(10, poll)


def _enter_query(Gtk, query: str):
    def apply(dialog):
        entries = _widgets(dialog, Gtk, Gtk.SearchEntry)
        assert len(entries) == 1
        entries[0].set_text(query)
        assert entries[0].get_text() == query
        dialog.response(Gtk.ResponseType.OK)
    return apply


def _select_result(Gtk, relative_path: str):
    def apply(dialog):
        trees = _widgets(dialog, Gtk, Gtk.TreeView)
        assert len(trees) == 1
        tree = trees[0]
        model = tree.get_model()
        index = next(i for i, row in enumerate(model) if str(row[0]) == relative_path)
        tree.get_selection().select_path(index)
        selected_model, tree_iter = tree.get_selection().get_selected()
        assert selected_model is not None and tree_iter is not None
        assert str(selected_model[tree_iter][0]) == relative_path
        dialog.response(Gtk.ResponseType.OK)
    return apply


def _run_search(window, GLib, Gtk, failures, query: str, relative_path: str) -> None:
    _schedule(GLib, Gtk, failures, title="Find in Workspace", action=_enter_query(Gtk, query))
    _schedule(GLib, Gtk, failures, title="Workspace Search Results", action=_select_result(Gtk, relative_path))
    window._actions["find-in-workspace"].activate(None)
    drain(Gtk)
    assert not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, GLib, Gtk = load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    failures: list[str] = []
    try:
        menubar = next(
            child for child in window._root_box.get_children() if isinstance(child, Gtk.MenuBar)
        )
        search_menu = _menu_item(menubar, Gtk, "Search").get_submenu()
        labels = [
            item.get_label() for item in search_menu.get_children()
            if isinstance(item, Gtk.MenuItem)
        ]
        assert "Find in Workspace…" in labels
        assert "find-in-workspace" in window._actions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Workspace"
            nested = root / "Notes"
            nested.mkdir(parents=True)
            active_path = root / "draft.md"
            other_path = nested / "paper.txt"
            active_path.write_text("Disk baseline.\n", encoding="utf-8")
            other_path.write_text("First line.\nNested needle target.\n", encoding="utf-8")

            window._open_workspace_root(str(root)); drain(Gtk)
            assert window.workspace.root == str(root)
            assert window.open_path(str(active_path)); drain(Gtk)
            assert text_of(window.text_view) == "Disk baseline.\n"
            assert not window.core.session.modified

            # The authoritative unsaved buffer must override the disk copy for the active file.
            window.buffer.begin_user_action()
            end = window.buffer.get_end_iter()
            window.buffer.insert(end, "UNSAVED needle here.\n")
            window.buffer.end_user_action(); drain(Gtk)
            assert window.core.session.modified
            before_text = text_of(window.text_view)
            before_undo = len(window.core.history.undo_stack)
            _run_search(window, GLib, Gtk, failures, "needle", "draft.md")
            bounds = window.buffer.get_selection_bounds()
            assert len(bounds) == 2
            start, end = bounds
            assert window.buffer.get_text(start, end, True) == "needle"
            assert text_of(window.text_view) == before_text
            assert window.core.session.modified
            assert len(window.core.history.undo_stack) == before_undo

            # Save only to remove the normal dirty-open prompt before testing another file.
            result = window.core.lifecycle.save()
            assert result.completed and result.saved
            drain(Gtk)
            assert not window.core.session.modified

            relative_other = str(Path("Notes") / "paper.txt")
            _run_search(window, GLib, Gtk, failures, "Nested needle", relative_other)
            assert window.core.session.logical_path == str(other_path)
            assert text_of(window.text_view) == "First line.\nNested needle target.\n"
            bounds = window.buffer.get_selection_bounds()
            assert len(bounds) == 2
            start, end = bounds
            assert window.buffer.get_text(start, end, True) == "Nested needle"
            assert not window.core.session.modified

        print("PLUS_P2_1_TRUE_GTK_AUTOMATED=PASS", flush=True)
        print("PLUS_P2_1_UNSAVED_BUFFER_OVERRIDE=PASS", flush=True)
        print("PLUS_P2_1_DISK_OPEN_EXACT_RANGE=PASS", flush=True)
        print("PLUS_P2_1_DOCUMENT_HISTORY_NEUTRALITY=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
