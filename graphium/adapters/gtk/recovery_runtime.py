"""GTK main-context scheduler for Graphium's recovery controller."""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib


class GLibRecoveryScheduler:
    __slots__ = ()

    @staticmethod
    def schedule_once(delay_seconds: int, callback: Callable[[], None]) -> object:
        if int(delay_seconds) <= 0:
            raise ValueError("delay_seconds must be positive")

        def run_once() -> bool:
            callback()
            return False

        return int(GLib.timeout_add_seconds(int(delay_seconds), run_once))

    @staticmethod
    def cancel(handle: object) -> None:
        source_id = int(handle)
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass

    @staticmethod
    def dispatch(callback: Callable[[], None]) -> None:
        def run_once() -> bool:
            callback()
            return False

        GLib.idle_add(run_once)
