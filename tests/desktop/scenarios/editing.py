from __future__ import annotations
import argparse,sys
from tests.desktop.harness.runtime import load_gtk3
from tests.desktop.harness.runtime import drain, text_of
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo',required=True); ap.add_argument('--manual',action='store_true'); ns=ap.parse_args(); sys.path.insert(0,ns.repo)
    Gdk,GLib,Gtk=load_gtk3(); from graphium.adapters.gtk.application import GraphiumApplication
    app=GraphiumApplication();
    if not app.register(None):return 1
    app.activate(); drain(Gtk); w=app.window
    w.core.editor.initialize_new_text('alpha',clean=True); w._refresh_projection(); drain(Gtk)
    b=w.buffer; b.begin_user_action(); b.insert(b.get_end_iter(),' beta'); b.end_user_action(); drain(Gtk)
    if text_of(w.text_view)!='alpha beta' or not w.core.session.modified:return 1
    w.lookup_action('undo').activate(None); drain(Gtk)
    if text_of(w.text_view)!='alpha' or w.core.session.modified:return 1
    w.core.editor.initialize_new_text('straße alpha\n',clean=True); w._refresh_projection(); b=w.buffer; a=b.get_start_iter(); z=b.get_iter_at_offset(6); b.select_range(a,z)
    w.lookup_action('uppercase').activate(None); drain(Gtk)
    if text_of(w.text_view)!='STRASSE alpha\n' or not w.core.session.modified:return 1
    w.lookup_action('undo').activate(None); drain(Gtk)
    if text_of(w.text_view)!='straße alpha\n' or w.core.session.modified:return 1
    w.destroy(); drain(Gtk); return 0
if __name__=='__main__': raise SystemExit(main())
