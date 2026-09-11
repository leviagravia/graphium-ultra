"""Graphium Ultra product identity."""
from __future__ import annotations

from dataclasses import replace

from graphium_plus.product import PLUS_PRODUCT_IDENTITY


ULTRA_PRODUCT_IDENTITY = replace(
    PLUS_PRODUCT_IDENTITY,
    product_name="Graphium Ultra",
    package_name="graphium-ultra",
    executable_name="graphium-ultra",
    version="0.0.1",
    desktop_application_id="io.github.leviagravia.GraphiumUltra",
    application_icon_name="io.github.leviagravia.GraphiumUltra",
    xdg_namespace="graphium-ultra",
    repository_url="https://github.com/leviagravia/graphium-ultra",
    repository_label="Graphium Ultra repository",
)
