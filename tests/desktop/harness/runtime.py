from __future__ import annotations
import os
import pathlib
import tempfile
from contextlib import contextmanager


def load_gtk3():
    import gi
    gi.require_version('Gdk', '3.0')
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gdk, GLib, Gtk
    return Gdk, GLib, Gtk


def drain(Gtk, limit=200):
    n = 0
    while Gtk.events_pending() and n < limit:
        Gtk.main_iteration_do(False)
        n += 1


def text_of(view):
    buffer = view.get_buffer()
    start, end = buffer.get_bounds()
    return buffer.get_text(start, end, True)


@contextmanager
def isolated_env(prefix='graphium-desktop-'):
    with tempfile.TemporaryDirectory(prefix=prefix) as td:
        root = pathlib.Path(td)
        env = os.environ.copy()
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        for key, sub in [('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state')]:
            path = root / sub
            path.mkdir()
            env[key] = str(path)
        yield root, env

def drain_for(Gtk, seconds=0.02, step=0.003):
    import time
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline: drain(Gtk); time.sleep(step)
    drain(Gtk)

def wait_until(Gtk, predicate, timeout=1.0):
    import time
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        drain(Gtk)
        if predicate(): return True
        time.sleep(0.01)
    drain(Gtk); return bool(predicate())
