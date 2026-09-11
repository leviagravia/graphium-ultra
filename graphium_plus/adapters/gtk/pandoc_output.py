"""Thin GTK presentation for explicit Graphium Plus Pandoc output."""
from __future__ import annotations

from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from graphium_plus.pandoc import PandocFormat, PandocIdentity, PandocExportPlan
from graphium_plus.pandoc_process import PandocArtifact, PandocOutputWorker


_POLL_MS = 50


def choose_pandoc_destination(
    parent: Gtk.Window,
    descriptor: PandocFormat,
    *,
    suggested_stem: str,
) -> str | None:
    if not isinstance(descriptor, PandocFormat):
        raise TypeError("descriptor must be PandocFormat")
    clean_stem = Path(suggested_stem or "Untitled").stem.strip() or "Untitled"
    dialog = Gtk.FileChooserDialog(
        title=f"Export with Pandoc — {descriptor.label}",
        parent=parent,
        action=Gtk.FileChooserAction.SAVE,
    )
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Export", Gtk.ResponseType.ACCEPT)
    dialog.set_default_response(Gtk.ResponseType.ACCEPT)
    dialog.set_do_overwrite_confirmation(False)
    dialog.set_current_name(clean_stem + descriptor.extension)
    try:
        response = dialog.run()
        if response != Gtk.ResponseType.ACCEPT:
            return None
        selected = dialog.get_filename()
        return selected or None
    finally:
        dialog.destroy()


def _run_worker_dialog(
    parent: Gtk.Window,
    worker: PandocOutputWorker,
    *,
    title: str,
    start,
    poll,
):
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.set_default_size(420, 120)
    label = Gtk.Label(label="Pandoc is working…")
    label.set_margin_start(16)
    label.set_margin_end(16)
    label.set_margin_top(16)
    label.set_margin_bottom(16)
    dialog.get_content_area().pack_start(label, True, True, 0)
    state = {"result": None, "error": ""}
    source_id = 0

    def check() -> bool:
        nonlocal source_id
        try:
            result = poll()
        except Exception as exc:
            state["error"] = str(exc)
            source_id = 0
            dialog.response(Gtk.ResponseType.REJECT)
            return False
        if result is None:
            return True
        state["result"] = result
        source_id = 0
        dialog.response(Gtk.ResponseType.ACCEPT)
        return False

    try:
        start()
        source_id = GLib.timeout_add(_POLL_MS, check)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            if state["result"] is None:
                # A response should only be produced by the settled poll path.
                raise RuntimeError("Pandoc worker dialog closed before a result was available.")
            return state["result"]
        if response == Gtk.ResponseType.REJECT and state["error"]:
            raise RuntimeError(state["error"])
        worker.cancel_and_join(timeout_seconds=5.0)
        return None
    finally:
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        if worker.active:
            worker.cancel_and_join(timeout_seconds=5.0)
        dialog.destroy()


def detect_pandoc(parent: Gtk.Window, worker: PandocOutputWorker) -> PandocIdentity | None:
    result = _run_worker_dialog(
        parent,
        worker,
        title="Checking Pandoc",
        start=worker.start_detect,
        poll=worker.poll_identity,
    )
    if result is None:
        return None
    if not isinstance(result, PandocIdentity):
        raise RuntimeError("Pandoc detection returned an invalid result.")
    return result


def build_pandoc_artifact(
    parent: Gtk.Window,
    worker: PandocOutputWorker,
    plan: PandocExportPlan,
) -> PandocArtifact | None:
    if not isinstance(plan, PandocExportPlan):
        raise TypeError("plan must be PandocExportPlan")
    result = _run_worker_dialog(
        parent,
        worker,
        title=f"Generating {plan.format.label}",
        start=lambda: worker.start_build(plan),
        poll=worker.poll_artifact,
    )
    if result is None:
        return None
    if not isinstance(result, PandocArtifact):
        raise RuntimeError("Pandoc build returned an invalid artifact result.")
    return result
