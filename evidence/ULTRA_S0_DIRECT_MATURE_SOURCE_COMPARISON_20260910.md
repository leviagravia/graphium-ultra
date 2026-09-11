# Graphium Ultra S0 — direct mature-source comparison

No web research was used. The comparison uses the exact Graphium Core/Plus source in this working baseline and the bundled Calamus W79-W84 source-first evidence.

## Graphium Core -> Plus
The mature product-line rule already present in `tests/release/test_plus_product_line.py` is asymmetric: `graphium/` never imports `graphium_plus`; the higher edition composes the lower one through `GraphiumPlusApplication(GraphiumApplication)` and `GraphiumPlusWindow(GraphiumWindow)`. S0 applies the same rule one level higher: Plus must never import Ultra.

## Calamus Writing Workspace evidence
The bundled W79-W84 evidence explicitly requires that a higher product compose Workspace only in its higher entrypoint and not copy application/lifecycle ownership. S0 follows that pattern for Ultra Viewer: presentation lives in the Ultra adapter, while canonical Open/Save/document identity remain inherited.

## Adopted remedy
- preserve Plus-owned Workspace/Outliner/Markdown-writing features in Plus;
- move only U1.x Viewer plan/image resolver/GTK window to `graphium_ultra`;
- make Plus window/application identity-parameterized extension seams rather than importing Ultra;
- introduce one path-context invalidation hook in Plus and extend it in Ultra;
- keep the one `graphium_plus.markdown.build_markdown_document_map` authority;
- keep Focus temporary hiding distinct from persisted Companion preference.

## Rejected remedies
- feature flags inside Graphium Core;
- leaving Ultra Viewer code under `graphium_plus` and merely changing labels;
- duplicating the Markdown parser or document lifecycle in Ultra;
- separate Save/Open implementations for Ultra;
- using the host user's Plus XDG state for an isolated working copy.
