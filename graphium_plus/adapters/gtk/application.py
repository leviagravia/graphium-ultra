"""GTK3 application root for Graphium Plus."""
from __future__ import annotations

from pathlib import Path

from graphium.adapters.gtk.application import GraphiumApplication
from graphium_plus.product import PLUS_PRODUCT_IDENTITY
from .window import GraphiumPlusWindow


_ICON_ROOT = Path(__file__).resolve().parents[2] / "data" / "icons" / "hicolor"


class GraphiumPlusApplication(GraphiumApplication):
    def __init__(
        self,
        *,
        identity=PLUS_PRODUCT_IDENTITY,
        window_factory=GraphiumPlusWindow,
        icon_root=_ICON_ROOT,
    ) -> None:
        super().__init__(
            identity=identity,
            window_factory=window_factory,
            icon_root=icon_root,
        )

    def do_startup(self) -> None:
        super().do_startup()
        self.set_accels_for_action("win.focus-mode", ["F9"])
        self.set_accels_for_action("win.command-palette", ["<Ctrl>K"])
