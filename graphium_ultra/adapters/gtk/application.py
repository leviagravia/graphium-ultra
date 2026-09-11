"""GTK3 application root for Graphium Ultra."""
from __future__ import annotations

from pathlib import Path

from graphium_plus.adapters.gtk.application import GraphiumPlusApplication
from graphium_ultra.product import ULTRA_PRODUCT_IDENTITY
from .window import GraphiumUltraWindow


_ICON_ROOT = Path(__file__).resolve().parents[2] / "data" / "icons" / "hicolor"


class GraphiumUltraApplication(GraphiumPlusApplication):
    def __init__(self) -> None:
        super().__init__(
            identity=ULTRA_PRODUCT_IDENTITY,
            window_factory=GraphiumUltraWindow,
            icon_root=_ICON_ROOT,
        )
