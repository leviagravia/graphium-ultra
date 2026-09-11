<p align="center">
  <img src="assets/graphium-ultra.svg" width="128" height="128" alt="Graphium Ultra logo">
</p>

<h1 align="center">Graphium Ultra</h1>

<p align="center">
  <strong>Graphium Plus + native Markdown reading and writing surfaces</strong>
</p>

<p align="center"><strong>Release line: 0.0.1</strong></p>

<p align="center">
  A lightweight native GTK writing editor for Linux with a bounded local Workspace, academic-writing tools and a read-only native Markdown Viewer.
</p>

Graphium Ultra is the cumulative Graphium edition for users who want the Graphium file-safety model and the bounded Graphium Plus writing Workspace together with native Markdown reading tools.

It remains a **single-document editor**. Ultra does not introduce tabs, a project/session database, a plugin platform, a second document buffer, a second file writer or a second Markdown parser.

## What Ultra adds

Graphium Ultra includes the Graphium Plus surface and adds:

- a native read-only **Markdown Viewer**;
- local image rendering with fail-closed fallback;
- native read-only Markdown tables;
- internal heading links and explicit HTTP/HTTPS links;
- reading-position preservation across Viewer refresh and Focus hide/show;
- **Find in Preview**, including native table-cell text;
- optional **Source | Preview** split view using the same Viewer renderer as the separate Viewer window.

The separate Viewer and Source | Preview split share one `MarkdownViewerSurface`. One editor snapshot, one Markdown structural map and one Viewer plan are built per refresh and projected to the active surfaces.

## Inherited writing workspace

From Graphium Plus, Ultra retains the bounded local Workspace and academic-writing layer, including the compact toolbar, Outliner, Scratchpad, Workspace file operations, scholarly Markdown/Pandoc helpers, Reference Library and Pandoc-output workflow.

These surfaces remain projections around the same active Graphium document lifecycle. Workspace and Markdown features do not become independent Save, dirty-state, Undo/Redo or document-identity authorities.

## File safety

Graphium Ultra inherits Graphium's guarded file model:

- same-directory staged Save with late destination revalidation;
- strong accepted file identity rather than pathname/timestamp-only trust;
- no silent overwrite of unexpectedly replaced or changed files;
- explicit Reload from Disk;
- strong nonmodal external-file monitoring;
- encoding, BOM and line-ending fidelity;
- Save a Copy and Save Version Copy without rebinding the active document;
- crash recovery and on-demand external Hunspell spell checking.

Normal Open and Save do not silently trim whitespace, add a final newline or normalize representation choices.

## Lightweight architecture

Ultra deliberately avoids turning Markdown support into a browser or IDE subsystem.

It adds no WebKit runtime, remote Markdown fetcher, Workspace indexer, background recursive scanner, generic plugin framework, hidden document database or second editor session authority. External Viewer links are explicit-click HTTP/HTTPS only and are delegated through GIO.

## Run from source

Requirements:

- Linux
- Python 3
- PyGObject
- GTK 3
- Hunspell (optional, for spell checking)
- Pandoc (optional, only for the inherited Pandoc-output workflow)

```bash
git clone https://github.com/leviagravia/graphium-ultra.git
cd graphium-ultra
./bin/graphium-ultra
```

### Install for the current user

```bash
./bin/graphium-ultra-install
```

The default installation prefix is `~/.local`. Graphium Ultra uses its own `graphium-ultra` XDG namespace and installs its own launcher, desktop file and gold/yellow Graphium icon family.

## Documentation

- [`Graphium Ultra User Guide`](docs/user/GRAPHIUM_ULTRA_USER_GUIDE.txt)
- [`Graphium Ultra Keyboard Shortcuts`](docs/user/GRAPHIUM_ULTRA_KEYBOARD_SHORTCUTS.txt)

Both are also available offline from the application's **Help** menu.

## Product line

Graphium Ultra is cumulative:

```text
Graphium Core ⊂ Graphium Plus ⊂ Graphium Ultra
```

The editions are separate products, not runtime feature flags. Core does not import Plus or Ultra, and Plus does not import Ultra.

## License

Graphium Ultra is free software released under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)**. See [`LICENSE`](LICENSE).

## Author

**leviagravia**  
`leviagravia@zohomail.eu`

---

**Graphium Ultra** — the lightweight Graphium writing workspace with native Markdown reading tools.
