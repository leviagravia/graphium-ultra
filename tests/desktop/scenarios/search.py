from __future__ import annotations
import argparse,sys
from tests.desktop.harness.runtime import load_gtk3
from tests.desktop.harness.runtime import drain, text_of
def sel(buffer):
    x=buffer.get_selection_bounds();
    if not x:return None
    a,b=x; return min(a.get_offset(),b.get_offset()),max(a.get_offset(),b.get_offset())
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo',required=True); ap.add_argument('--manual',action='store_true'); ns=ap.parse_args(); sys.path.insert(0,ns.repo)
    Gdk,GLib,Gtk=load_gtk3(); from graphium.adapters.gtk.application import GraphiumApplication
    app=GraphiumApplication();
    if not app.register(None):return 1
    app.activate(); drain(Gtk); w=app.window
    if w._search_bar is not None:return 1
    w.lookup_action('find').activate(None); drain(Gtk)
    if w._search_bar is None or not w._search_bar.get_search_mode():return 1
    source='Straße alpha STRASSE\n'; w.core.editor.initialize_new_text(source,clean=True); w._refresh_projection();
    w._search_query_entry.set_text('strasse'); w._search_match_case.set_active(False); w._perform_find_next(); drain(Gtk)
    if sel(w.buffer)!=(0,6) or w.core.session.modified:return 1
    w._search_query_entry.set_text('alpha'); w._search_replace_entry.set_text('BETA'); w._search_match_case.set_active(True); w._perform_replace_one(); drain(Gtk)
    if text_of(w.text_view)!='Straße BETA STRASSE\n' or not w.core.session.modified:return 1
    w.lookup_action('undo').activate(None); drain(Gtk)
    if text_of(w.text_view)!=source or w.core.session.modified:return 1
    w.destroy(); drain(Gtk); return 0
if __name__=='__main__': raise SystemExit(main())
