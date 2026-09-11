from __future__ import annotations
import argparse, statistics, sys, time
from tests.desktop.harness.runtime import drain, load_gtk3, text_of, drain_for
from tests.desktop.harness.fixtures import realistic_text

SAMPLES=5
TAB_CHOOSER_LIMIT_MS=500.0
APPEARANCE_LIMIT_MS=1000.0
TAB_LIMIT_MS=1000.0
TRANSFORM_LIMIT_S=3.0

def median_ms(fn):
    fn()
    values=[]
    for _ in range(SAMPLES):
        t=time.perf_counter(); fn(); values.append((time.perf_counter()-t)*1000.0)
    return statistics.median(values)

def multiline(size):
    line='Graphium performance sample alpha beta 0123456789\n'
    return (line*(size//len(line)+2))[:size]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo',required=True); ap.add_argument('--manual',action='store_true'); ns=ap.parse_args(); sys.path.insert(0,ns.repo)
    _Gdk,GLib,Gtk=load_gtk3()
    from graphium.adapters.gtk.application import GraphiumApplication
    from graphium.adapters.gtk.dialogs import choose_tab_width
    app=GraphiumApplication()
    if not app.register(None): return 1
    app.activate(); drain_for(Gtk,0.08,0.002); w=app.window
    if w is None:return 1
    try:
        # Existing realistic 1 MiB transformation budget remains permanent.
        text=realistic_text(); w.core.editor.initialize_new_text(text,clean=True); w._refresh_projection(); b=w.buffer; a,z=b.get_bounds(); b.select_range(a,z)
        t=time.monotonic(); w.lookup_action('uppercase').activate(None); drain_for(Gtk,step=0.002); elapsed=time.monotonic()-t
        if elapsed>TRANSFORM_LIMIT_S or not w.core.session.modified:return 1
        w.lookup_action('undo').activate(None); drain_for(Gtk)
        if text_of(w.text_view)!=text or w.core.session.modified:return 1

        # The narrow Other… tab-width chooser is lazy and cheap; modal is auto-cancelled.
        w.core.editor.initialize_new_text(multiline(5*1024),clean=True); w._refresh_projection()
        def cancel_dialog():
            for top in Gtk.Window.list_toplevels():
                if isinstance(top,Gtk.Dialog) and top.get_title()=='Tab Width':
                    top.response(Gtk.ResponseType.CANCEL); return False
            return True
        def one_tab_chooser():
            GLib.timeout_add(1,cancel_dialog)
            got=choose_tab_width(w,current=w.core.view_settings.current.tab_width)
            if got is not None: raise AssertionError('cancel returned tab width')
            drain_for(Gtk)
        if median_ms(one_tab_chooser)>TAB_CHOOSER_LIMIT_MS:return 1

        # Appearance must remain independent of document size.
        for size in (5*1024,1024*1024):
            w.core.editor.initialize_new_text(multiline(size),clean=True); w._refresh_projection()
            for value in ('dark','light','system'):
                def one(value=value):
                    w.lookup_action('appearance').activate(GLib.Variant.new_string(value)); drain_for(Gtk)
                if median_ms(one)>APPEARANCE_LIMIT_MS:return 1

        # Tab-width projection on 10 MiB must not scan/rewrite the document.
        ten=multiline(10*1024*1024); w.core.editor.initialize_new_text(ten,clean=True); w._refresh_projection(); before=text_of(w.text_view)
        widths=iter([4,8]*20)
        def one_tab():
            w.lookup_action('tab-width').activate(GLib.Variant.new_string(str(next(widths)))); drain_for(Gtk,0.002)
        if median_ms(one_tab)>TAB_LIMIT_MS:return 1
        if text_of(w.text_view)!=before or w.core.session.modified:return 1
        return 0
    except (AssertionError,OSError,RuntimeError,ValueError): return 1
    finally:
        w.destroy(); drain_for(Gtk,step=0.002); app.quit()
if __name__=='__main__': raise SystemExit(main())
