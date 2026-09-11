from __future__ import annotations

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from tests.desktop.harness.runtime import drain, load_gtk3, wait_until


def _pixbufs(viewer):
    values=[]
    it=viewer.buffer.get_start_iter()
    while not it.is_end():
        pixbuf=it.get_pixbuf()
        if pixbuf is not None:
            values.append(pixbuf)
        if not it.forward_char():
            break
    return values


def _write_png(GdkPixbuf,path:Path,width:int,height:int) -> None:
    pixbuf=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,True,8,width,height)
    pixbuf.fill(0x336699FF)
    pixbuf.savev(str(path),"png",[],[])


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--repo",required=True)
    parser.add_argument("--manual",action="store_true")
    args=parser.parse_args()
    sys.path.insert(0,args.repo)

    _Gdk,_GLib,Gtk=load_gtk3()
    import gi
    gi.require_version("GdkPixbuf","2.0")
    from gi.repository import GdkPixbuf
    from graphium_ultra.adapters.gtk.application import GraphiumUltraApplication

    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        a=root/"a"; b=root/"b"; a.mkdir(); b.mkdir()
        text="# Same source\n\n![local](figure.png)\n"
        for directory,width in ((a,1),(b,2)):
            (directory/"doc.md").write_text(text,encoding="utf-8")
            _write_png(GdkPixbuf,directory/"figure.png",width,1)

        app=GraphiumUltraApplication()
        assert app.register(None)
        app.activate(); drain(Gtk)
        window=app.window
        assert window is not None
        try:
            assert window._identity.product_name == "Graphium Ultra"
            assert window._identity.xdg_namespace == "graphium-ultra"
            assert Path(window._xdg_paths.state).name == "graphium-ultra"
            assert "Graphium Ultra" in window.get_title()
            assert window.workspace_panel.context_open_in_graphium.get_label() == "Open with Graphium Ultra"
            viewer_action=window.lookup_action("markdown-viewer")
            assert viewer_action is not None

            assert window.open_path(str(a/"doc.md")); drain(Gtk)
            assert not window.core.session.modified
            viewer_action.activate(None); drain(Gtk)
            viewer=window._markdown_viewer
            assert viewer is not None
            assert wait_until(Gtk,lambda: len(_pixbufs(viewer))==1)
            assert _pixbufs(viewer)[0].get_width()==1

            # Same source text, different logical directory. The Viewer must
            # invalidate document context and resolve the relative image anew.
            assert window.open_path(str(b/"doc.md")); drain(Gtk)
            assert not window.core.session.modified
            assert wait_until(Gtk,lambda: len(_pixbufs(viewer))==1 and _pixbufs(viewer)[0].get_width()==2)
            assert window.core.session.logical_path == str(b/"doc.md")
            assert window._markdown_viewer is viewer

            viewer.destroy(); drain(Gtk)
            assert window._markdown_viewer is None
            print("ULTRA_S0_TRUE_GTK_IDENTITY_CONTEXT_INVALIDATION=PASS",flush=True)
            return 0
        finally:
            window.destroy(); drain(Gtk)


if __name__ == "__main__":
    raise SystemExit(main())
