from __future__ import annotations

import argparse
import sys

from tests.desktop.harness.runtime import drain, load_gtk3, text_of, wait_until


def _viewer_windows(Gtk):
    return [
        window for window in Gtk.Window.list_toplevels()
        if window.get_title() == "Markdown Viewer" and window.get_visible()
    ]


def _table_labels(grid):
    values = {}
    for child in grid.get_children():
        left = int(grid.child_get_property(child, "left-attach"))
        top = int(grid.child_get_property(child, "top-attach"))
        label = child.get_child()
        values[(top, left)] = label
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    _Gdk, _GLib, Gtk = load_gtk3()
    from graphium_ultra.adapters.gtk.application import GraphiumUltraApplication

    app = GraphiumUltraApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window = app.window
    assert window is not None
    try:
        source = (
            "# Native table\n\n"
            "| Item | Count |\n"
            "| :--- | ---: |\n"
            "| **Alpha** | 2 |\n"
            "| Beta | `3` |\n"
        )
        window.core.editor.initialize_new_text(source, clean=True); drain(Gtk)
        assert text_of(window.text_view) == source
        assert not window.core.session.modified
        assert not window.core.editor.can_undo

        action = window.lookup_action("markdown-viewer")
        assert action is not None
        action.activate(None); drain(Gtk)
        viewers = _viewer_windows(Gtk)
        assert len(viewers) == 1
        viewer = viewers[0]
        assert viewer is window._markdown_viewer
        assert not viewer.text_view.get_editable()
        assert len(viewer._embedded_widgets) == 1
        grid = viewer._embedded_widgets[0]
        assert isinstance(grid, Gtk.Grid)

        labels = _table_labels(grid)
        assert len(labels) == 6
        assert labels[(0, 0)].get_text() == "Item"
        assert labels[(0, 1)].get_text() == "Count"
        assert labels[(1, 0)].get_text() == "Alpha"
        assert labels[(1, 1)].get_text() == "2"
        assert labels[(2, 0)].get_text() == "Beta"
        assert labels[(2, 1)].get_text() == "3"
        assert labels[(0, 0)].get_selectable()
        assert labels[(1, 1)].get_xalign() == 1.0

        # U1.4 is presentation-only: no source/session/Undo mutation.
        assert text_of(window.text_view) == source
        assert not window.core.session.modified
        assert not window.core.editor.can_undo

        end = window.buffer.get_end_iter()
        window.buffer.begin_user_action()
        try:
            window.buffer.insert(end, "| Gamma | 4 |\n")
        finally:
            window.buffer.end_user_action()
        assert window.core.session.modified
        assert wait_until(Gtk, lambda: len(viewer._embedded_widgets) == 1 and len(_table_labels(viewer._embedded_widgets[0])) == 8)
        labels = _table_labels(viewer._embedded_widgets[0])
        assert labels[(3, 0)].get_text() == "Gamma"
        assert labels[(3, 1)].get_text() == "4"
        assert text_of(window.text_view).endswith("| Gamma | 4 |\n")

        viewer.destroy(); drain(Gtk)
        assert window._markdown_viewer is None
        print("ULTRA_U1_4_TRUE_GTK_NATIVE_TABLES=PASS", flush=True)
        return 0
    finally:
        window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
