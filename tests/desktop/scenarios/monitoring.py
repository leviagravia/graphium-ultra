from __future__ import annotations

import argparse
import os
import tempfile
import threading
from pathlib import Path

from tests.desktop.harness.runtime import drain, load_gtk3, text_of, wait_until


def monitor_settled(monitor, previous_started):
    return monitor.observations_started > previous_started and monitor._initial_source_id == 0 and monitor._debounce_source_id == 0 and monitor._inflight_generation is None and monitor._pending_generation is None


def append_user_text(window, text):
    window.buffer.begin_user_action()
    try:
        window.buffer.insert(window.buffer.get_end_iter(), text)
    finally:
        window.buffer.end_user_action()


def arm_message_dialog(GLib, Gtk, *, title, response):
    state = {"seen": False}
    def responder():
        for top in Gtk.Window.list_toplevels():
            if not isinstance(top, Gtk.MessageDialog) or not top.get_visible():
                continue
            primary = getattr(getattr(top, "props", None), "text", None)
            if top.get_title() != title and primary != title:
                continue
            state["seen"] = True
            top.response(response)
            return False
        return True
    return state, GLib.timeout_add(1, responder)


def remove_source(GLib, source_id):
    if not source_id:
        return
    try:
        GLib.source_remove(source_id)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--manual", action="store_true")
    ns = ap.parse_args()
    import sys
    sys.path.insert(0, ns.repo)

    Gdk, GLib, Gtk = load_gtk3()
    from gi.repository import Gio
    from graphium.adapters.gtk.application import GraphiumApplication
    from graphium.adapters.gtk.external_monitor import StrongExternalFileMonitor
    from graphium.infrastructure.document_observer import observe_document

    if StrongExternalFileMonitor._relevant_event(Gio.FileMonitorEvent.CHANGES_DONE_HINT): return 1

    app = GraphiumApplication()
    if not app.register(None):
        return 1
    app.activate(); drain(Gtk)
    w = app.window
    if w is None:
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="graphium-monitoring-") as td:
            root = Path(td)
            path = root / "live.txt"
            path.write_text("alpha\n", encoding="utf-8")
            print("MONITOR_CHECK=INITIAL_BIND", flush=True)
            previous_started = w._external_file_monitor.observations_started
            if not w.open_path(str(path)):
                return 1
            if not wait_until(Gtk, lambda: w._external_file_monitor.bound):
                return 1
            if not wait_until(Gtk, lambda: monitor_settled(w._external_file_monitor, previous_started)):
                return 1
            if w._external_info_bar.get_visible():
                return 1

            print("MONITOR_CHECK=CONTENT_CHANGE_STRONG_TRUTH", flush=True)
            st = path.stat()
            path.write_text("bravo\n", encoding="utf-8")
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
            if not wait_until(
                Gtk,
                lambda: w._external_info_bar.get_visible()
                and w._external_info_label.get_text() == "File changed on disk.",
            ):
                return 1
            if not w._external_info_reload_button.get_visible():
                return 1
            if text_of(w.text_view) != "alpha\n":
                return 1

            print("MONITOR_CHECK=CLEAN_RELOAD_REBIND", flush=True)
            previous_started = w._external_file_monitor.observations_started
            w.lookup_action("reload").activate(None); drain(Gtk)
            if text_of(w.text_view) != "bravo\n" or w.core.session.modified:
                return 1
            if not wait_until(Gtk, lambda: w._external_file_monitor.bound):
                return 1
            if not wait_until(Gtk, lambda: monitor_settled(w._external_file_monitor, previous_started)):
                return 1
            if w._external_info_bar.get_visible():
                return 1

            print("MONITOR_CHECK=OWN_SAVE_NO_FALSE_ALERT", flush=True)
            append_user_text(w, "mine"); drain(Gtk)
            if not w.core.session.modified:
                return 1
            previous_started = w._external_file_monitor.observations_started
            w.lookup_action("save").activate(None); drain(Gtk)
            if w.core.session.modified or path.read_text(encoding="utf-8") != "bravo\nmine":
                return 1
            if not wait_until(Gtk, lambda: monitor_settled(w._external_file_monitor, previous_started)):
                return 1
            if w._external_info_bar.get_visible():
                return 1

            print("MONITOR_CHECK=ATOMIC_REPLACEMENT_SAVE_FAIL_CLOSED", flush=True)
            replacement = root / "replacement.tmp"
            replacement.write_text("external replacement\n", encoding="utf-8")
            replacement.replace(path)
            if not wait_until(
                Gtk,
                lambda: w._external_info_bar.get_visible()
                and "replaced" in w._external_info_label.get_text().lower(),
            ):
                return 1
            append_user_text(w, "local"); drain(Gtk)
            before_disk = path.read_bytes()
            state, source_id = arm_message_dialog(
                GLib, Gtk, title="Could not save file", response=Gtk.ResponseType.CLOSE
            )
            w.lookup_action("save").activate(None); drain(Gtk)
            remove_source(GLib, source_id)
            if not state["seen"]:
                return 1
            if path.read_bytes() != before_disk or not w.core.session.modified:
                return 1

            print("MONITOR_CHECK=DESTRUCTIVE_RELOAD_RECOVERY", flush=True)
            state, source_id = arm_message_dialog(
                GLib, Gtk, title="Reload from Disk", response=Gtk.ResponseType.REJECT
            )
            w.lookup_action("reload").activate(None); drain(Gtk)
            remove_source(GLib, source_id)
            if not state["seen"]:
                return 1
            if text_of(w.text_view) != "external replacement\n" or w.core.session.modified:
                return 1

            print("MONITOR_CHECK=MISSING_NONDESTRUCTIVE", flush=True)
            path.unlink()
            if not wait_until(
                Gtk,
                lambda: w._external_info_bar.get_visible()
                and w._external_info_label.get_text() == "File no longer exists on disk.",
            ):
                return 1
            if text_of(w.text_view) != "external replacement\n":
                return 1

            print("MONITOR_CHECK=SYMLINK_TARGET_AND_RETARGET", flush=True)
            target1 = root / "target-one.txt"
            target2 = root / "target-two.txt"
            link = root / "logical-link.txt"
            target1.write_text("one\n", encoding="utf-8")
            target2.write_text("two\n", encoding="utf-8")
            link.symlink_to(target1.name)
            previous_started = w._external_file_monitor.observations_started
            if not w.open_path(str(link)):
                return 1
            if not wait_until(Gtk, lambda: w._external_file_monitor.bound):
                return 1
            if not wait_until(Gtk, lambda: monitor_settled(w._external_file_monitor, previous_started)):
                return 1
            target1.write_text("ONE\n", encoding="utf-8")
            if not wait_until(
                Gtk,
                lambda: w._external_info_bar.get_visible()
                and w._external_info_label.get_text() == "File changed on disk.",
            ):
                return 1
            w.lookup_action("reload").activate(None); drain(Gtk)
            if text_of(w.text_view) != "ONE\n" or w.core.session.modified:
                return 1
            if not wait_until(Gtk, lambda: w._external_file_monitor.bound):
                return 1
            link.unlink(); link.symlink_to(target2.name)
            if not wait_until(
                Gtk,
                lambda: w._external_info_bar.get_visible()
                and "replaced" in w._external_info_label.get_text().lower(),
            ):
                return 1
            if text_of(w.text_view) != "ONE\n":
                return 1

            print("MONITOR_CHECK=SLOW_OBSERVER_MAIN_LOOP_RESPONSIVE", flush=True)
            slow_path = root / "slow.txt"
            slow_path.write_text("slow-base\n", encoding="utf-8")
            if not w.open_path(str(slow_path)) or not wait_until(Gtk, lambda: w._external_file_monitor.bound):
                return 1
            w._suspend_external_monitor()
            started, release = threading.Event(), threading.Event()
            def slow_observer(path, *, capture_bytes=False, retries=1):
                started.set()
                if not release.wait(2.0):
                    raise TimeoutError("slow observer was not released")
                return observe_document(path, capture_bytes=capture_bytes, retries=retries)
            class NoGioStrongExternalFileMonitor(StrongExternalFileMonitor):
                def _create_monitors(self, logical_path, accepted, generation): return []

            slow = NoGioStrongExternalFileMonitor(
                session=w.core.session, on_result=lambda _result: None, observer=slow_observer,
                debounce_ms=20, initial_delay_ms=10,
            )
            try:
                if not slow.bind_current() or not wait_until(Gtk, started.is_set, timeout=1.0):
                    return 1
                heartbeat = {"seen": False}
                GLib.idle_add(lambda: heartbeat.__setitem__("seen", True) or False)
                if not wait_until(Gtk, lambda: heartbeat["seen"], timeout=1.0):
                    return 1
                if slow.max_concurrent_observations != 1:
                    return 1
                release.set()
                if not wait_until(Gtk, lambda: slow._inflight_generation is None, timeout=2.0):
                    return 1
            finally:
                release.set(); slow.close()
            w._schedule_external_monitor_bind()

            print("MONITOR_CHECK=NONMODAL_NO_DIALOG_STORM", flush=True)
            if any(
                isinstance(top, Gtk.Dialog) and top.get_visible()
                for top in Gtk.Window.list_toplevels() if top is not w
            ):
                return 1

        print("MONITORING_AUTHORITY=PASS")
        return 0
    except (AssertionError, OSError, RuntimeError, ValueError) as exc:
        print(f"MONITOR_EXCEPTION={type(exc).__name__}:{exc}", flush=True); return 1
    finally:
        w.destroy(); drain(Gtk); app.quit()


if __name__ == "__main__":
    raise SystemExit(main())
