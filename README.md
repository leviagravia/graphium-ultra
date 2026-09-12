<p align="center">
  <img src="assets/graphium-ultra.svg" width="128" height="128" alt="Graphium Ultra logo">
</p>

<h1 align="center">Graphium Ultra</h1>

<p align="center">
  <strong>Lightweight native GTK writing editor for Linux with a local workspace and native Markdown preview.</strong>
</p>

<p align="center"><strong>Current release: 0.0.1</strong></p>

Graphium Ultra is the cumulative Graphium edition. It combines Graphium's guarded single-document editing model, the bounded writing workspace of Graphium Plus, and native Markdown reading surfaces without turning the editor into an IDE or browser-based application.

## Highlights

- native GTK 3 application for Linux;
- guarded Save with strong file-identity checks and representation fidelity;
- bounded local Workspace with writing and academic tools;
- Outliner and Scratchpad;
- native read-only Markdown Viewer;
- local image rendering and native Markdown tables;
- internal heading links and explicit HTTP/HTTPS links;
- reading-position preservation across refresh and Focus hide/show;
- Find in Preview, including native table cells;
- optional **Source | Preview** split using the same Viewer renderer;
- on-demand Hunspell spell checking;
- optional Pandoc workflow.

Graphium Ultra remains deliberately small in architecture: one active editor document, one save authority, one Markdown structural authority and one native Viewer renderer. It has no tabs, WebKit, cloud service, plugin framework, project database, background recursive indexer or second editor session.

## Install

### Debian package

Download `graphium-ultra_0.0.1-1_all.deb` from the [v0.0.1 release](https://github.com/leviagravia/graphium-ultra/releases/tag/v0.0.1), then install it with:

```bash
sudo apt install ./graphium-ultra_0.0.1-1_all.deb
```

### From source

Requirements: Linux, Python 3, PyGObject and GTK 3. Hunspell and Pandoc are optional.

```bash
git clone https://github.com/leviagravia/graphium-ultra.git
cd graphium-ultra
./bin/graphium-ultra
```

Install for the current user:

```bash
./bin/graphium-ultra-install
```

The default prefix is `~/.local`.

## Product line

```text
Graphium Core ⊂ Graphium Plus ⊂ Graphium Ultra
```

The editions are separate products, not runtime feature flags. Core does not import Plus or Ultra, and Plus does not import Ultra.

## Documentation

- [Graphium Ultra User Guide](docs/user/GRAPHIUM_ULTRA_USER_GUIDE.txt)
- [Graphium Ultra Keyboard Shortcuts](docs/user/GRAPHIUM_ULTRA_KEYBOARD_SHORTCUTS.txt)

Both documents are also available offline from the application's **Help** menu.

## File safety

Graphium Ultra inherits Graphium's guarded file model: staged same-directory Save, late destination revalidation, explicit Reload from Disk, strong external-file monitoring, encoding/BOM/line-ending fidelity, Save a Copy, Save Version Copy and crash recovery. Normal Open and Save do not silently normalize the document.

## License

Graphium Ultra is free software under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)**. See [LICENSE](LICENSE).

## Author

**leviagravia**  
`leviagravia@zohomail.eu`

## Disclaimer

This software was built with substantial assistance from AI. AI wrote a substantial amount of this software. Architecture, product decisions, testing criteria and release acceptance remain directed and reviewed by the project author.
