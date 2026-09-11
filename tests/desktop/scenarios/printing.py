from __future__ import annotations
import argparse,sys,tempfile
from pathlib import Path
from tests.desktop.harness.runtime import load_gtk3
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo',required=True); ap.add_argument('--manual',action='store_true'); ns=ap.parse_args(); sys.path.insert(0,ns.repo)
    Gdk,GLib,Gtk=load_gtk3(); from graphium.adapters.gtk.printing import _PageSetupStore,GraphiumPrintController
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/'page-setup.ini'; store=_PageSetupStore(p); setup=store.load(); store.save(setup); loaded=store.load()
        if not p.is_file():return 1
    op=Gtk.PrintOperation()
    if not isinstance(op,Gtk.PrintOperation):return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
