from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from tests.desktop.harness.runtime import drain, load_gtk3, text_of, drain_for, wait_until


def action_bool(window, name: str) -> bool:
    action = window.lookup_action(name)
    if action is None or action.get_state() is None:
        raise AssertionError(f"missing stateful View action: {name}")
    return action.get_state().get_boolean()


def assert_clean(window, label: str) -> None:
    if window.core.session.modified:
        raise AssertionError(f"{label}: document is Modified")
    if window.core.history.current_state_id != window.core.session.saved_editor_state_id:
        raise AssertionError(f"{label}: history is not at the exact Saved state")


def open_clean(window, Gtk, path: Path, label: str) -> None:
    assert_clean(window, f"{label} pre-open")
    if not window.open_path(str(path)):
        raise AssertionError(f"{label}: open_path failed")
    drain_for(Gtk, 0.03)
    assert_clean(window, f"{label} post-open")
    for top in Gtk.Window.list_toplevels():
        if isinstance(top, Gtk.Dialog) and top.get_visible():
            raise AssertionError(f"{label}: unexpected visible dialog {top.get_title()!r}")


def user_insert(window, Gtk, value: str) -> None:
    buffer = window.buffer
    buffer.begin_user_action()
    try:
        buffer.insert(buffer.get_end_iter(), value)
    finally:
        buffer.end_user_action()
    drain_for(Gtk, 0.02)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    Gdk, _GLib, Gtk = load_gtk3()
    import graphium.adapters.gtk.window as window_module
    from graphium.adapters.gtk.application import GraphiumApplication
    from graphium.infrastructure.view_settings_store import JsonViewSettingsStore
    from graphium.paths import resolve_xdg_paths

    app = GraphiumApplication()
    if not app.register(None):
        return 1
    app.activate()
    drain_for(Gtk, 0.08, 0.005)
    window = app.window
    if window is None:
        return 1

    try:
        required = (
            "status-bar",
            "line-numbers",
            "word-wrap",
            "font",
            "zoom-in",
            "zoom-out",
            "zoom-reset",
            "full-screen",
        )
        if any(window.lookup_action(name) is None for name in required):
            return 1

        # Default View state is intentionally uncluttered except for Compact Status.
        if not action_bool(window, "status-bar"):
            return 1
        if action_bool(window, "line-numbers") or action_bool(window, "word-wrap"):
            return 1
        if window.text_view.zoom_percent != 100:
            return 1

        before_text = text_of(window.text_view)
        before_state = window.core.history.current_state_id
        before_saved = window.core.session.saved_editor_state_id

        # Drive the same activation path as the user-visible menu/action command.
        window.lookup_action("line-numbers").activate(None)
        drain_for(Gtk, 0.03)
        if not action_bool(window, "line-numbers"):
            return 1
        if not window.text_view.line_numbers_visible:
            return 1
        if window.text_view.get_border_window_size(Gtk.TextWindowType.LEFT) <= 0:
            return 1

        window.lookup_action("word-wrap").activate(None)
        drain_for(Gtk, 0.03)
        if not action_bool(window, "word-wrap"):
            return 1
        if window.text_view.get_wrap_mode() != Gtk.WrapMode.WORD_CHAR:
            return 1

        window.lookup_action("status-bar").activate(None)
        drain_for(Gtk, 0.02)
        if action_bool(window, "status-bar") or window._status_bar.get_visible():
            return 1
        window.lookup_action("status-bar").activate(None)
        drain_for(Gtk, 0.02)
        if not action_bool(window, "status-bar") or not window._status_bar.get_visible():
            return 1

        original_choose_font = window_module.choose_font
        try:
            window_module.choose_font = lambda *_args, **_kwargs: ("DejaVu Sans Mono", 13.0)
            window.lookup_action("font").activate(None)
            drain_for(Gtk, 0.03)
        finally:
            window_module.choose_font = original_choose_font
        if window.text_view.base_font != ("DejaVu Sans Mono", 13.0):
            return 1

        window.lookup_action("zoom-in").activate(None)
        drain_for(Gtk, 0.02)
        if window.text_view.zoom_percent != 110:
            return 1
        window.lookup_action("zoom-reset").activate(None)
        drain_for(Gtk, 0.02)
        if window.text_view.zoom_percent != 100:
            return 1

        # Fullscreen is transient: verify both the action and the real GDK window state.
        window.present()
        drain_for(Gtk, 0.05)
        fullscreen = window.lookup_action("full-screen")
        fullscreen.activate(None)
        if not wait_until(
            Gtk,
            lambda: action_bool(window, "full-screen")
            and window.get_window() is not None
            and bool(window.get_window().get_state() & Gdk.WindowState.FULLSCREEN),
        ):
            return 1
        fullscreen.activate(None)
        if not wait_until(
            Gtk,
            lambda: not action_bool(window, "full-screen")
            and (
                window.get_window() is None
                or not bool(window.get_window().get_state() & Gdk.WindowState.FULLSCREEN)
            ),
        ):
            return 1

        # View commands must never become document edits or move the savepoint.
        if text_of(window.text_view) != before_text:
            return 1
        if window.core.history.current_state_id != before_state:
            return 1
        if window.core.session.saved_editor_state_id != before_saved:
            return 1

        config_path = resolve_xdg_paths().config / "view.json"
        persisted = JsonViewSettingsStore(config_path).load()
        if not (persisted.line_numbers and persisted.word_wrap and persisted.status_bar):
            return 1
        if persisted.font_family != "DejaVu Sans Mono" or persisted.font_size_points != 13.0:
            return 1
        config_text = config_path.read_text(encoding="utf-8").lower()
        if "zoom" in config_text or "full" in config_text:
            return 1

        # Compact Status: real CRLF representation, cursor position and Saved/Modified transition.
        fixture_root = Path(resolve_xdg_paths().cache) / "desktop-view-fixtures"
        fixture_root.mkdir(parents=True, exist_ok=True)
        crlf = fixture_root / "crlf.txt"
        crlf.write_bytes(b"one\r\ntwo\r\n")
        open_clean(window, Gtk, crlf, "CRLF fixture")
        if window._status_document.get_text() != "UTF-8 · CRLF · Saved":
            return 1
        target = window.buffer.get_iter_at_line_offset(1, 1)
        window.buffer.place_cursor(target)
        drain_for(Gtk, 0.02)
        if window._status_position.get_text() != "Ln 2, Col 2":
            return 1
        user_insert(window, Gtk, "X")
        if not window._status_document.get_text().endswith("Modified"):
            return 1
        undo = window.lookup_action("undo")
        if undo is None or not undo.get_enabled():
            return 1
        undo.activate(None)
        drain_for(Gtk, 0.02)
        assert_clean(window, "Compact Status after Undo")
        if window._status_document.get_text() != "UTF-8 · CRLF · Saved":
            return 1
        if text_of(window.text_view) != "one\ntwo\n":
            return 1

        # One realistic multiline fixture: preserve the native gutter and remain responsive.
        big = fixture_root / "one-mib-multiline.txt"
        line = "Graphium semantic View regression 0123456789 abcdefghijklmnopqrstuvwxyz\n"
        text = (line * ((1024 * 1024 // len(line)) + 2))[: 1024 * 1024]
        big.write_text(text, encoding="utf-8")
        open_clean(window, Gtk, big, "1 MiB multiline fixture")
        end = window.buffer.get_end_iter()
        window.buffer.place_cursor(end)
        start = time.monotonic()
        window.text_view.scroll_to_mark(window.buffer.get_insert(), 0.0, False, 0.0, 0.0)
        drain_for(Gtk, 0.12)
        if time.monotonic() - start > 1.0:
            return 1
        if window.text_view.get_border_window_size(Gtk.TextWindowType.LEFT) <= 0:
            return 1
        assert_clean(window, "View scenario final boundary")

        return 0
    except (AssertionError, OSError, ValueError):
        return 1
    finally:
        window.destroy()
        drain_for(Gtk, 0.02)
        app.quit()


if __name__ == "__main__":
    raise SystemExit(main())
