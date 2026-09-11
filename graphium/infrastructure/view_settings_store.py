"""Small atomic JSON store for Graphium view/preferences settings.

This is convenience configuration, never document authority. Loading is fail-soft and
read-only; filesystem mutation occurs only after an explicit user setting change or the
single accepted-close window-size persistence event.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from graphium.application.view_settings import ViewSettings


class JsonViewSettingsStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            path = Path(path)
        self.path = path

    def load(self) -> ViewSettings:
        try:
            raw = self.path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return ViewSettings()
            # The dataclass validates the complete snapshot. One malformed owned value
            # invalidates the convenience payload and falls back to complete defaults.
            return ViewSettings(
                word_wrap=payload.get("word_wrap", False),
                line_numbers=payload.get("line_numbers", False),
                status_bar=payload.get("status_bar", True),
                font_family=payload.get("font_family", "Monospace"),
                font_size_points=payload.get("font_size_points", 11.0),
                appearance=payload.get("appearance", "system"),
                tab_width=payload.get("tab_width", 8),
                insert_spaces=payload.get("insert_spaces", False),
                window_width=payload.get("window_width", 720),
                window_height=payload.get("window_height", 520),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return ViewSettings()

    def save(self, settings: ViewSettings) -> None:
        if not isinstance(settings, ViewSettings):
            raise TypeError("settings must be ViewSettings")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "word_wrap": settings.word_wrap,
            "line_numbers": settings.line_numbers,
            "status_bar": settings.status_bar,
            "font_family": settings.font_family,
            "font_size_points": settings.font_size_points,
            "appearance": settings.appearance,
            "tab_width": settings.tab_width,
            "insert_spaces": settings.insert_spaces,
            "window_width": settings.window_width,
            "window_height": settings.window_height,
        }
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        fd, temp_name = tempfile.mkstemp(
            prefix=".view-settings-", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
