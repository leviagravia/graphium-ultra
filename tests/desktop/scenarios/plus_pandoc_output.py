from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

from tests.desktop.harness.runtime import drain, drain_for, load_gtk3, text_of, wait_until


_FAKE_PANDOC = r'''#!/usr/bin/env python3
import pathlib
import sys
import time
if "--version" in sys.argv:
    print("pandoc 3.1.11")
    print("Features: fake")
    raise SystemExit(0)
output = None
writer = None
input_path = None
bibliography = None
for index, item in enumerate(sys.argv):
    if index == 1 and not item.startswith("-"):
        input_path = item
    elif item == "--output":
        output = sys.argv[index + 1]
    elif item == "--to":
        writer = sys.argv[index + 1]
    elif item == "--bibliography":
        bibliography = sys.argv[index + 1]
source = pathlib.Path(input_path).read_text(encoding="utf-8") if input_path else ""
if "SLOW-PANDOC-CLOSE" in source:
    time.sleep(30)
bib = pathlib.Path(bibliography).read_text(encoding="utf-8") if bibliography else ""
payload = ("WRITER=" + str(writer) + "\nSOURCE=" + source + "\nBIB=" + bib).encode("utf-8")
pathlib.Path(output).write_bytes(payload)
print("ok")
'''


def _menu_item(parent, Gtk, label):
    return next(
        item for item in parent.get_children()
        if isinstance(item, Gtk.MenuItem) and item.get_label() == label
    )


def _matches_dialog(window, Gtk, title: str) -> bool:
    return isinstance(window, Gtk.Dialog) and window.get_title() == title


def _same_local_path(value: str | None, expected: Path) -> bool:
    if not value:
        return False
    try:
        return Path(value).resolve(strict=False) == expected.resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def _schedule_save_selection(GLib, Gtk, failures: list[str], *, title: str, path: Path) -> None:
    deadline = time.monotonic() + 4.0
    phase = 0

    def poll():
        nonlocal phase
        for window in Gtk.Window.list_toplevels():
            if not _matches_dialog(window, Gtk, title):
                continue
            try:
                if phase == 0:
                    window.set_current_folder(str(path.parent))
                    phase = 1
                    return True
                if phase == 1:
                    if not _same_local_path(window.get_current_folder(), path.parent):
                        return time.monotonic() < deadline
                    window.set_current_name(path.name)
                    phase = 2
                    return True
                if not _same_local_path(window.get_filename(), path):
                    if time.monotonic() < deadline:
                        return True
                    raise AssertionError("save target did not settle")
                window.response(Gtk.ResponseType.ACCEPT)
                return False
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, GLib, Gtk = load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication
    from graphium_plus.pandoc import prepare_pandoc_export_plan
    from graphium_plus.pandoc_process import PandocArtifactBuilder, PandocOutputWorker, PandocProcessRunner
    from graphium_plus.references import ReferenceRecord

    root = Path(os.environ["XDG_DATA_HOME"]).parent
    fake = root / "pandoc-fake"
    fake.write_text(_FAKE_PANDOC, encoding="utf-8")
    fake.chmod(0o755)
    output = root / "paper.html"
    slow_output = root / "slow.html"

    app = GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    failures: list[str] = []

    runner = PandocProcessRunner(executable_name=str(fake))
    window.pandoc_worker.cancel_and_join(timeout_seconds=2.0)
    window.pandoc_runner = runner
    window.pandoc_worker = PandocOutputWorker(PandocArtifactBuilder(runner, timeout_seconds=10.0))

    try:
        menubar = next(
            child for child in window._root_box.get_children() if isinstance(child, Gtk.MenuBar)
        )
        file_menu = _menu_item(menubar, Gtk, "File")
        pandoc_item = _menu_item(file_menu.get_submenu(), Gtk, "Export with Pandoc")
        labels = [
            item.get_label() for item in pandoc_item.get_submenu().get_children()
            if isinstance(item, Gtk.MenuItem)
        ]
        assert labels == ["HTML", "Microsoft Word", "OpenDocument Text", "LaTeX source"]
        assert window.reference_store._writer is window.core.writer
        assert window.pandoc_worker.builder.runner is runner
        assert "pandoc-output" in window._actions

        empty = window.reference_store.load()
        assert not empty.token.exists
        saved = window.reference_store.save(
            (
                ReferenceRecord(
                    key="alpha2020", type="article", title="Alpha Study",
                    authors=("Alpha, A.",), year="2020", container_title="Journal A",
                ),
            ),
            empty.token,
        )
        assert saved.saved

        source_text = "Draft unsaved [@alpha2020]."
        window.core.editor.initialize_new_text(source_text, clean=False); drain(Gtk)
        source_before = text_of(window.text_view)
        state_before = window.core.history.current_state_id
        assert window.core.session.modified

        _schedule_save_selection(
            GLib, Gtk, failures,
            title="Export with Pandoc — HTML",
            path=output,
        )
        window._actions["pandoc-output"].activate(GLib.Variant.new_string("html"))
        drain_for(Gtk, 0.08)
        assert not failures, failures
        assert output.is_file()
        payload = output.read_text(encoding="utf-8")
        assert "WRITER=html5" in payload
        assert "SOURCE=Draft unsaved [@alpha2020]." in payload
        assert "@article{alpha2020," in payload
        assert "title = {Alpha Study}" in payload
        assert text_of(window.text_view) == source_before
        assert window.core.history.current_state_id == state_before
        assert window.core.session.modified
        assert not window.pandoc_worker.active
        assert runner.active_pid is None

        # Real close boundary: a live Pandoc child must be cancelled/joined before
        # Graphium accepts a clean-window close.
        clean_state = window.core.editor.initialize_new_text("SLOW-PANDOC-CLOSE", clean=True)
        identity = runner.detect(timeout_seconds=2.0)
        refs = window.reference_store.load()
        plan = prepare_pandoc_export_plan(
            identity=identity,
            format_id="html",
            destination=slow_output,
            document_text="SLOW-PANDOC-CLOSE",
            source_state_id=clean_state.state_id,
            document_directory=root,
            reference_records=refs.records,
            reference_token=refs.token,
        )
        window.pandoc_worker.start_build(plan)
        assert wait_until(Gtk, lambda: runner.active_pid is not None, timeout=3.0)
        pid = runner.active_pid
        assert pid is not None
        window.close()
        assert wait_until(Gtk, lambda: not window.pandoc_worker.active, timeout=4.0)
        drain_for(Gtk, 0.05)
        assert runner.active_pid is None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("Pandoc child survived Graphium Plus close")
        assert not window.get_visible()

        print("PLUS_A5B_TRUE_GTK_AUTOMATED=PASS", flush=True)
        print("PLUS_A5B_UNSAVED_BUFFER_EXPORT=PASS", flush=True)
        print("PLUS_A5B_CITED_BIBLATEX_PROJECTION=PASS", flush=True)
        print("PLUS_A5B_DOCUMENT_HISTORY_NEUTRALITY=PASS", flush=True)
        print("PLUS_A5B_ACTIVE_PANDOC_CLOSE_CANCEL=PASS", flush=True)
        return 0
    finally:
        try:
            window.pandoc_worker.cancel_and_join(timeout_seconds=2.0)
        except Exception:
            pass
        if window.get_visible():
            window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
