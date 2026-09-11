# Graphium Ultra S0 — structural stabilization contract

1. `graphium/` MUST NOT import `graphium_plus` or `graphium_ultra`.
2. `graphium_plus/` MUST NOT import `graphium_ultra`; Plus owns its toolbar, Workspace, Outliner, Markdown writing toolbar, academic/references/Pandoc/Review/Workspace Search surfaces.
3. `graphium_ultra/` MAY depend on Plus and owns Native Markdown Viewer plus U1.x Viewer extensions.
4. Ultra has distinct product/application/executable/XDG identities.
5. The isolated working launcher sets private XDG CONFIG/DATA/CACHE/STATE roots and disables bytecode writes.
6. One Plus-owned document-context notification covers New/Open/Open Recent/Open Path/first Save/Save As and active-document Workspace retarget. Plus uses it for Companion; Ultra extends it for Viewer path-dependent refresh.
7. Focus Mode disables Companion actions while active and destroy persistence records semantic preferred visibility, never temporary Focus visibility.
8. No second document/save/Markdown authority, DB, watcher, worker or network stack is introduced by S0.
