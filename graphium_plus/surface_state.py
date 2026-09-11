"""Small atomic boolean state for optional Plus/Ultra presentation surfaces.

Each surface owns only one preference: visible or hidden.  This is convenience
presentation state, never document/session/content authority.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


class SurfaceVisibilityStore:
    __slots__ = ("path", "default")

    def __init__(self, path: str | os.PathLike[str], *, default: bool = True) -> None:
        self.path = Path(path)
        self.default = bool(default)

    def load(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return self.default
        if not isinstance(data, dict) or set(data) != {"schema", "visible"} or data.get("schema") != 1:
            return self.default
        visible = data.get("visible")
        return visible if isinstance(visible, bool) else self.default

    def save(self, visible: bool) -> None:
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
        fd, temp_name = tempfile.mkstemp(prefix=".surface-visible-", suffix=".tmp", dir=parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump({"schema": 1, "visible": bool(visible)}, handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
