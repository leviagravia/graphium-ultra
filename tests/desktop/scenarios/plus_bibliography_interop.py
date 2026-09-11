from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

from tests.desktop.harness.runtime import drain, load_gtk3, text_of


def _menu_item(parent, Gtk, label):
    return next(
        item for item in parent.get_children()
        if isinstance(item, Gtk.MenuItem) and item.get_label() == label
    )


def _matches_dialog(window, Gtk, title: str) -> bool:
    if not isinstance(window, Gtk.Dialog):
        return False
    if window.get_title() == title:
        return True
    if isinstance(window, Gtk.MessageDialog):
        try:
            return window.get_property("text") == title
        except Exception:
            return False
    return False


def _widgets(root, Gtk, widget_type):
    found = []
    if isinstance(root, widget_type):
        found.append(root)
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            found.extend(_widgets(child, Gtk, widget_type))
    return found


def _same_local_path(value: str | None, expected: Path) -> bool:
    if not value:
        return False
    try:
        return Path(value).resolve(strict=False) == expected.resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def _schedule_dialog_action(
    GLib, Gtk, failures: list[str], *, title: str, action, timeout: float = 3.0
) -> None:
    deadline = time.monotonic() + timeout

    def poll():
        for window in Gtk.Window.list_toplevels():
            if not _matches_dialog(window, Gtk, title):
                continue
            try:
                settled = action(window)
            except Exception as exc:
                failures.append(f"{title}: {type(exc).__name__}: {exc}")
                try:
                    window.response(Gtk.ResponseType.CANCEL)
                except Exception:
                    pass
                return False
            if settled is False:
                if time.monotonic() < deadline:
                    return True
                failures.append(f"dialog selection did not settle before timeout: {title}")
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


def _choose_open_file(path: Path, response):
    phase = 0

    def apply(dialog):
        nonlocal phase
        assert path.is_file()
        if phase == 0:
            # GTK documents FileChooser setter return values as non-authoritative.
            # First request the parent folder, then wait until its model is current.
            dialog.set_current_folder(str(path.parent))
            phase = 1
            return False
        if phase == 1:
            if not _same_local_path(dialog.get_current_folder(), path.parent):
                return False
            # Now selection is requested within an already-settled directory.
            # The return value is intentionally ignored; completion is observable.
            dialog.select_filename(str(path))
            phase = 2
            return False
        if not _same_local_path(dialog.get_filename(), path):
            dialog.select_filename(str(path))
            return False
        dialog.response(response)
        return True

    return apply


def _choose_save_file(path: Path, response):
    phase = 0

    def apply(dialog):
        nonlocal phase
        assert path.parent.is_dir()
        if phase == 0:
            dialog.set_current_folder(str(path.parent))
            phase = 1
            return False
        if phase == 1:
            if not _same_local_path(dialog.get_current_folder(), path.parent):
                return False
            dialog.set_current_name(path.name)
            phase = 2
            return False
        if not _same_local_path(dialog.get_filename(), path):
            return False
        dialog.response(response)
        return True

    return apply


def _review_keep_existing(Gtk, response):
    def apply(dialog):
        radios = _widgets(dialog, Gtk, Gtk.RadioButton)
        keep = next(
            button for button in radios
            if button.get_label() == "Keep existing records for conflicting keys"
        )
        assert keep.get_active()
        trees = _widgets(dialog, Gtk, Gtk.TreeView)
        assert len(trees) == 1
        model = trees[0].get_model()
        statuses = [str(row[0]) for row in model]
        assert "Conflict" in statuses and "New" in statuses
        dialog.response(response)
    return apply


def _respond(response):
    return lambda dialog: dialog.response(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, GLib, Gtk = load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication
    from graphium_plus.references import ReferenceRecord

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    root = Path(os.environ["XDG_DATA_HOME"]).parent
    input_path = root / "import.bib"
    export_path = root / "exported-references.bib"
    input_path.write_text(
        "@article{alpha2020,\n"
        "  author={Alpha, A.}, title={Imported Alpha Replacement}, year={2024},\n"
        "  journal={Journal A}\n"
        "}\n"
        "@book{beta2024, author={Beta, B.}, title={Beta Book}, year={2024},\n"
        "  publisher={Press}, location={Rome}\n"
        "}\n",
        encoding="utf-8",
    )
    failures: list[str] = []
    try:
        menubar = next(
            child for child in window._root_box.get_children() if isinstance(child, Gtk.MenuBar)
        )
        commands = _menu_item(menubar, Gtk, "Commands")
        references = _menu_item(commands.get_submenu(), Gtk, "References")
        labels = [
            item.get_label() for item in references.get_submenu().get_children()
            if isinstance(item, Gtk.MenuItem)
        ]
        assert labels == [
            "Reference Library…", "Find Reference…", "Import BibTeX/BibLaTeX…",
            "Export BibTeX…", "Export BibLaTeX…",
        ]
        assert window.reference_store._writer is window.core.writer
        assert "import-bibliography" in window._actions
        assert "export-bibtex" in window._actions
        assert "export-biblatex" in window._actions

        empty = window.reference_store.load()
        assert not empty.token.exists
        baseline = window.reference_store.save(
            (
                ReferenceRecord(
                    key="alpha2020", type="article", title="Canonical Alpha",
                    authors=("Alpha, A.",), year="2020", container_title="Journal A",
                ),
            ),
            empty.token,
        )
        assert baseline.saved

        window.core.editor.initialize_new_text("Draft.", clean=True); drain(Gtk)
        document_before = text_of(window.text_view)
        history_before = window.core.history.current_state_id
        assert not window.core.session.modified

        _schedule_dialog_action(
            GLib, Gtk, failures, title="Import BibTeX/BibLaTeX",
            action=_choose_open_file(input_path, Gtk.ResponseType.OK),
        )
        _schedule_dialog_action(
            GLib, Gtk, failures, title="Review Bibliography Import",
            action=_review_keep_existing(Gtk, Gtk.ResponseType.OK),
        )
        _schedule_dialog_action(
            GLib, Gtk, failures, title="Bibliography import complete",
            action=_respond(Gtk.ResponseType.CLOSE),
        )
        window._actions["import-bibliography"].activate(None); drain(Gtk)
        assert not failures, failures

        imported_snapshot = window.reference_store.load()
        assert imported_snapshot.writable
        by_key = {record.key: record for record in imported_snapshot.records}
        assert tuple(by_key) == ("alpha2020", "beta2024")
        assert by_key["alpha2020"].title == "Canonical Alpha"  # explicit default: keep existing
        imported = by_key["beta2024"]
        assert imported.key == "beta2024"
        assert imported.title == "Beta Book"
        assert imported.location == "Rome"

        # Bibliography workflow is library/output state only, never document state.
        assert text_of(window.text_view) == document_before
        assert window.core.history.current_state_id == history_before
        assert not window.core.session.modified

        _schedule_dialog_action(
            GLib, Gtk, failures, title="Export BibLaTeX",
            action=_choose_save_file(export_path, Gtk.ResponseType.OK),
        )
        _schedule_dialog_action(
            GLib, Gtk, failures, title="Bibliography export complete",
            action=_respond(Gtk.ResponseType.CLOSE),
        )
        window._actions["export-biblatex"].activate(None); drain(Gtk)
        assert not failures, failures
        assert export_path.is_file()
        exported_text = export_path.read_text(encoding="utf-8")
        assert "@article{alpha2020," in exported_text
        assert "title = {Canonical Alpha}" in exported_text
        assert "journaltitle = {Journal A}" in exported_text
        assert "@book{beta2024," in exported_text
        assert "location = {Rome}" in exported_text
        assert text_of(window.text_view) == document_before
        assert window.core.history.current_state_id == history_before
        assert not window.core.session.modified

        print("PLUS_A4B_TRUE_GTK_AUTOMATED=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
