# Graphium Ultra S0 — source-first audit

Baseline: isolated post-modularity-stabilization working source. Canonical Git is not touched.

## Diagnoses
- Ultra U1.2/U1.3 Viewer runtime was physically owned by `graphium_plus` and launched through `GraphiumPlusApplication`; Plus therefore imported and instantiated an Ultra-only surface.
- The working copy source was physically isolated but direct launch inherited host XDG homes, so user state was not isolated.
- Path-dependent projections had ad-hoc rebinding: Companion handled New/Open/Open Recent/Open Path/Save As, but first Save and Ultra Viewer path changes were not governed by one boundary; Workspace active-file move performed a direct special-case rebind.
- Focus Mode hid Companion temporarily but left its actions enabled and destroy-time persistence sampled temporary widget visibility instead of semantic preference.

## Mature-source rule adopted
Use the already-proven cumulative Graphium layering: Core knows nothing about Plus; therefore Plus must likewise know nothing about Ultra. Edition-specific runtime is composed only by the higher entrypoint/window. Path-dependent projections consume one logical document-context notification rather than each inventing lifecycle ownership.
