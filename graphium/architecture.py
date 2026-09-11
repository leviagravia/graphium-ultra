"""Machine-readable permanent Graphium architecture boundaries."""
from __future__ import annotations

CANONICAL_DOCUMENTS = (
    "GRAPHIUM_PRODUCT_ARCHITECTURE_CONTRACT.md",
    "GRAPHIUM_ROADMAP.md",
    "GRAPHIUM_MEMORIA_OPERATIVA.txt",
)
MAX_CANONICAL_DOCUMENTS = 3

LAYERS = (
    "domain",
    "application",
    "adapters/gtk",
    "infrastructure",
    "composition",
)

GTK_IMPORT_ALLOWED_PREFIXES = ("graphium/adapters/gtk/",)

FORBIDDEN_PRODUCT_CLUSTERS_V1 = (
    "research",
    "bibliography",
    "citations",
    "source_notes",
    "workspace",
    "document_overview",
    "navigator",
    "scratchpad",
    "clips",
    "tags",
    "pandoc",
    "plugins",
    "cloud",
    "ai",
)
