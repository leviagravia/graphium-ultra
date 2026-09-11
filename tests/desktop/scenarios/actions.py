from __future__ import annotations
import argparse,re,subprocess,sys
from tests.desktop.harness.runtime import load_gtk3
from tests.desktop.harness.runtime import drain
def norm(v):
    v=v.strip().replace('<Primary>','<Control>'); mods=[{'ctrl':'control','primary':'control'}.get(x.lower(),x.lower()) for x in re.findall(r'<([^>]+)>',v)]; key=re.sub(r'<[^>]+>','',v).strip().lower(); return '+'.join(sorted(mods)+[key]) if key else ''
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo',required=True); ap.add_argument('--manual',action='store_true'); ns=ap.parse_args(); sys.path.insert(0,ns.repo)
    from graphium.application.commands import COMMANDS,FORBIDDEN_ACCELERATORS,accelerator_map
    amap=accelerator_map();
    if len(amap)!=len(set(amap.values())): return 1
    if any(x in set(amap.values()) for x in FORBIDDEN_ACCELERATORS): return 1
    try:
        proc=subprocess.run(['gsettings','list-recursively'],text=True,capture_output=True)
    except FileNotFoundError:
        return 2
    if proc.returncode!=0:return 2
    active=set(); tok=re.compile(r"<[^'\"]+>[^'\",\]\s]*")
    for line in proc.stdout.splitlines():
        if line.startswith('org.cinnamon') or line.startswith('org.gnome.desktop'):
            active.update(norm(x) for x in tok.findall(line) if norm(x))
    if any(norm(x) in active for x in amap.values()): return 1
    Gdk,GLib,Gtk=load_gtk3(); from graphium.adapters.gtk.application import GraphiumApplication
    app=GraphiumApplication();
    if not app.register(None):return 1
    app.activate(); drain(Gtk); w=app.window
    if w is None:return 1
    for c in COMMANDS:
        if c.menu not in {'Recent'} and w.lookup_action(c.action) is None and c.action!='clear-recent': return 1
    if any(isinstance(x,Gtk.Dialog) and x.get_visible() for x in Gtk.Window.list_toplevels() if x is not w): return 1
    w.destroy(); drain(Gtk); return 0
if __name__=='__main__': raise SystemExit(main())
