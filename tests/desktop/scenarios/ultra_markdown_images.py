from __future__ import annotations

import argparse
import base64
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from tests.desktop.harness.runtime import drain, load_gtk3, text_of, wait_until


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _viewer_windows(Gtk):
    return [
        window for window in Gtk.Window.list_toplevels()
        if window.get_title() == "Markdown Viewer" and window.get_visible()
    ]


def _viewer_text(viewer) -> str:
    buffer = viewer.buffer
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


def _pixbuf_count(viewer) -> int:
    count = 0
    it = viewer.buffer.get_start_iter()
    while not it.is_end():
        if it.get_pixbuf() is not None:
            count += 1
        if not it.forward_char():
            break
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "figure.png").write_bytes(_PNG_1X1)
        markdown = root / "images.md"
        source_text = "# Images\n\n![local](figure.png) and ![remote](https://example.invalid/a.png)\n"
        markdown.write_text(source_text, encoding="utf-8")

        _Gdk, _GLib, Gtk = load_gtk3()
        from graphium_ultra.adapters.gtk.application import GraphiumUltraApplication

        app = GraphiumUltraApplication()
        assert app.register(None)
        app.activate(); drain(Gtk)
        window = app.window
        assert window is not None
        try:
            # U1.3 owns a clean, file-bound fixture.  No editor mutation is
            # permitted before or during this image-presentation boundary.
            assert not window.core.session.modified
            assert window.open_path(str(markdown)); drain(Gtk)
            assert window.core.session.logical_path == str(markdown)
            assert text_of(window.text_view) == source_text
            assert not window.core.session.modified
            undo_before = window.core.editor.can_undo

            action = window.lookup_action("markdown-viewer")
            assert action is not None
            action.activate(None); drain(Gtk)
            viewers = _viewer_windows(Gtk)
            assert len(viewers) == 1
            viewer = viewers[0]
            assert viewer is window._markdown_viewer
            assert wait_until(Gtk, lambda: _pixbuf_count(viewer) == 1)
            rendered = _viewer_text(viewer)
            assert "[Image: remote]" in rendered
            assert "[Image: local]" not in rendered

            # Viewer image rendering is a presentation-only operation.
            assert text_of(window.text_view) == source_text
            assert window.core.session.logical_path == str(markdown)
            assert not window.core.session.modified
            assert window.core.editor.can_undo == undo_before

            viewer.destroy(); drain(Gtk)
            assert window._markdown_viewer is None
            print("ULTRA_U1_3_TRUE_GTK_LOCAL_IMAGE_RENDERING=PASS", flush=True)
            return 0
        finally:
            window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
