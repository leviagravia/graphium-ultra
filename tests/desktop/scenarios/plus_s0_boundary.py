from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tests.desktop.harness.runtime import drain, load_gtk3


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--repo",required=True)
    parser.add_argument("--manual",action="store_true")
    args=parser.parse_args()
    sys.path.insert(0,args.repo)

    _Gdk,_GLib,Gtk=load_gtk3()
    from graphium_plus.adapters.gtk.application import GraphiumPlusApplication

    app=GraphiumPlusApplication()
    assert app.register(None)
    app.activate(); drain(Gtk)
    window=app.window
    assert window is not None
    state_path=window._companion_state_store.path
    try:
        assert window._identity.product_name == "Graphium Plus"
        assert window._identity.xdg_namespace == "graphium-plus"
        assert Path(window._xdg_paths.state).name == "graphium-plus"
        assert window.lookup_action("markdown-viewer") is None
        assert window.workspace_panel.context_open_in_graphium.get_label() == "Open with Graphium Plus"

        clips=window.lookup_action("companion-clips")
        scratch=window.lookup_action("companion-scratchpad")
        focus=window.lookup_action("focus-mode")
        assert clips is not None and scratch is not None and focus is not None
        clips.activate(None); drain(Gtk)
        assert window._companion_preferred_visible is True
        assert window._companion_panel is not None and window._companion_panel.widget.get_visible()

        focus.activate(None); drain(Gtk)
        assert not clips.get_enabled()
        assert not scratch.get_enabled()
        assert not window._companion_panel.widget.get_visible()
        assert window._companion_preferred_visible is True

        focus.activate(None); drain(Gtk)
        assert clips.get_enabled() and scratch.get_enabled()
        assert window._companion_panel.widget.get_visible()

        # Destroy once more while Focus is active: persisted state must represent
        # semantic preference (visible=True), not temporary Focus hiding.
        focus.activate(None); drain(Gtk)
        assert not window._companion_panel.widget.get_visible()
        assert window._companion_preferred_visible is True
    finally:
        window.destroy(); drain(Gtk)

    data=json.loads(state_path.read_text(encoding="utf-8"))
    assert data.get("visible") is True
    print("PLUS_S0_TRUE_GTK_LAYER_FOCUS_PERSISTENCE=PASS",flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
