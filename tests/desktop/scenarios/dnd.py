from __future__ import annotations
import argparse, sys
from pathlib import Path
from tests.desktop.harness.runtime import drain, load_gtk3, text_of, drain_for

def found(Gdk,target_list,name):
    result=target_list.find(Gdk.atom_intern(name,False))
    if isinstance(result,tuple): return (bool(result[0]), result[1] if len(result)>1 else None)
    return bool(result),None

def has_text_target(Gdk,target_list):
    return any(found(Gdk,target_list,n)[0] for n in ('UTF8_STRING','text/plain;charset=utf-8','text/plain','STRING','TEXT'))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo',required=True); ap.add_argument('--manual',action='store_true'); ns=ap.parse_args(); sys.path.insert(0,ns.repo)
    Gdk,_GLib,Gtk=load_gtk3()
    from graphium.adapters.gtk.application import GraphiumApplication
    from graphium.adapters.gtk.editor_view import DND_TARGET_URI_LIST, GraphiumTextView
    # Do not infer DnD correctness from binding introspection. Construct the real
    # TextView and verify its owned targets/overrides; real negotiation is covered
    # by the desktop drag oracle below the permanent authority.
    raw=GraphiumTextView(); targets=raw.drag_dest_get_target_list()
    if targets is None or not has_text_target(Gdk,targets): raw.destroy(); return 1
    raw.destroy(); drain_for(Gtk)
    app=GraphiumApplication()
    if not app.register(None): return 1
    app.activate(); drain_for(Gtk,0.08); w=app.window
    if w is None:return 1
    try:
        tt=w.text_view.drag_dest_get_target_list()
        if tt is None:return 1
        if not has_text_target(Gdk,tt):return 1
        ok,info=found(Gdk,tt,'text/uri-list')
        if not ok or info!=DND_TARGET_URI_LIST:return 1
        ft=getattr(w.text_view,'_file_drop_targets',None)
        if ft is None:return 1
        ok,info=found(Gdk,ft,'text/uri-list')
        if not ok or info!=DND_TARGET_URI_LIST:return 1
        if not callable(getattr(w.text_view,'_file_drop_handler',None)):return 1
        # URI target identity is semantic in PyGObject; never translate GDK_NONE.
        uri_atom=Gdk.atom_intern('text/uri-list',False)
        plain_atom=Gdk.atom_intern('text/plain',False)
        if not w.text_view._is_uri_drop_target(uri_atom):return 1
        if w.text_view._is_uri_drop_target(plain_atom):return 1
        if w.text_view._is_uri_drop_target(None):return 1
        # The window must not be a second URI negotiation authority.
        wt=w.drag_dest_get_target_list()
        if wt is not None and found(Gdk,wt,'text/uri-list')[0]:return 1
        # URI negotiation must be virtual-method ownership in the TextView subclass.
        if not callable(getattr(GraphiumTextView,'do_drag_motion',None)):return 1
        if not callable(getattr(GraphiumTextView,'do_drag_drop',None)):return 1
        if not callable(getattr(GraphiumTextView,'do_drag_data_received',None)):return 1
        if GraphiumTextView.do_drag_motion is Gtk.TextView.do_drag_motion:return 1
        if GraphiumTextView.do_drag_drop is Gtk.TextView.do_drag_drop:return 1
        if GraphiumTextView.do_drag_data_received is Gtk.TextView.do_drag_data_received:return 1
        root=Path(ns.repo)/'.desktop-dnd-tmp'; root.mkdir(exist_ok=True)
        local=root/'local.txt'; local.write_text('local drop\n',encoding='utf-8'); directory=root/'folder'; directory.mkdir(exist_ok=True)
        try:
            got=w._local_file_paths_from_uris([local.as_uri(),'https://example.invalid/remote.txt',directory.as_uri()])
            if got!=[str(local)]:return 1
            w.core.editor.initialize_new_text('clean',clean=True); w._refresh_projection(); drain_for(Gtk)
            # Exercise the permanent TextView->window ownership boundary, not the downstream
            # open helper directly. Real target negotiation remains a real-desktop oracle.
            if not w.text_view._dispatch_file_drop_uris([local.as_uri()]):return 1
            if text_of(w.text_view)!='local drop\n':return 1
        finally:
            local.unlink(missing_ok=True); directory.rmdir(); root.rmdir()
        return 0
    except (AssertionError,OSError,ValueError): return 1
    finally:
        w.destroy(); drain_for(Gtk); app.quit()
if __name__=='__main__': raise SystemExit(main())
