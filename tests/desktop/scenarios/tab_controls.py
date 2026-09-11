from __future__ import annotations
import argparse, sys
from tests.desktop.harness.runtime import load_gtk3, text_of, drain_for


def establish(window, Gtk, text, insert=None, bound=None):
    window.core.editor.initialize_new_text(text, clean=True)
    if insert is None: insert=len(text)
    if bound is None: bound=insert
    a=window.buffer.get_iter_at_offset(insert); b=window.buffer.get_iter_at_offset(bound)
    window.buffer.select_range(a,b); window._refresh_projection(); drain_for(Gtk)


def string_state(action):
    state=action.get_state(); return None if state is None else state.get_string()

def bool_state(action):
    state=action.get_state(); return None if state is None else state.get_boolean()


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo',required=True); ap.add_argument('--manual',action='store_true'); ns=ap.parse_args(); sys.path.insert(0,ns.repo)
    Gdk,GLib,Gtk=load_gtk3()
    import graphium.adapters.gtk.window as window_module
    from graphium.adapters.gtk.application import GraphiumApplication
    from graphium.infrastructure.view_settings_store import JsonViewSettingsStore
    from graphium.paths import resolve_xdg_paths

    class FakeKeyEvent:
        def __init__(self,keyval,state=Gdk.ModifierType(0)): self.keyval=keyval; self.state=state

    app=GraphiumApplication()
    if not app.register(None): return 1
    app.activate(); drain_for(Gtk,0.08); w=app.window
    if w is None: return 1
    native=Gtk.Settings.get_default(); baseline=app.system_prefer_dark_theme
    try:
        if w.lookup_action('preferences') is not None: return 1
        if any(w.lookup_action(x) is None for x in ('tab-width','insert-spaces','appearance')): return 1
        s=w.core.view_settings.current
        if (s.tab_width,s.insert_spaces,s.appearance)!=(8,False,'system'): return 1
        if (w.text_view.tab_width,w.text_view.insert_spaces)!=(8,False): return 1
        if string_state(w.lookup_action('tab-width'))!='8' or bool_state(w.lookup_action('insert-spaces')) is not False: return 1
        if any(isinstance(x,Gtk.Dialog) and x.get_title()=='Tab Width' and x.get_visible() for x in Gtk.Window.list_toplevels()): return 1

        # Fixed width is immediate and document-neutral.
        establish(w,Gtk,'a\tb',3,3); before_text=text_of(w.text_view); before_state=w.core.history.current_state_id; before_modified=w.core.session.modified
        w.lookup_action('tab-width').activate(GLib.Variant.new_string('4')); drain_for(Gtk)
        s=w.core.view_settings.current
        if s.tab_width!=4 or w.text_view.tab_width!=4 or string_state(w.lookup_action('tab-width'))!='4': return 1
        if text_of(w.text_view)!=before_text or w.core.history.current_state_id!=before_state or w.core.session.modified!=before_modified: return 1

        # Insert Spaces is direct, checked, persistent and document-neutral.
        w.lookup_action('insert-spaces').activate(None); drain_for(Gtk)
        s=w.core.view_settings.current
        if not s.insert_spaces or not w.text_view.insert_spaces or bool_state(w.lookup_action('insert-spaces')) is not True: return 1
        if text_of(w.text_view)!=before_text or w.core.history.current_state_id!=before_state or w.core.session.modified!=before_modified: return 1

        # Other is lazy and Cancel-neutral.
        chooser=window_module.choose_tab_width
        try:
            window_module.choose_tab_width=lambda *_a,**_k: None
            before=w.core.view_settings.current; before_action=string_state(w.lookup_action('tab-width'))
            w.lookup_action('tab-width').activate(GLib.Variant.new_string('other')); drain_for(Gtk)
        finally: window_module.choose_tab_width=chooser
        if w.core.view_settings.current!=before or string_state(w.lookup_action('tab-width'))!=before_action: return 1

        # A custom width remains represented by Other and persists exactly once.
        try:
            window_module.choose_tab_width=lambda *_a,**_k: 7
            w.lookup_action('tab-width').activate(GLib.Variant.new_string('other')); drain_for(Gtk)
        finally: window_module.choose_tab_width=chooser
        if w.core.view_settings.current.tab_width!=7 or w.text_view.tab_width!=7 or string_state(w.lookup_action('tab-width'))!='other': return 1
        config=resolve_xdg_paths().config/'view.json'
        persisted=JsonViewSettingsStore(config).load()
        if (persisted.tab_width,persisted.insert_spaces)!=(7,True): return 1

        # Persistence failure cannot publish false action or TextView state.
        class FailingStore:
            def save(self,_settings): raise OSError('simulated view-settings persistence failure')
        controller=w.core.view_settings; old_store=controller._store; old_ui=w._ui
        class QuietUI:
            def __init__(self): self.warnings=[]
            def show_warning(self,title,message): self.warnings.append((title,message))
        quiet=QuietUI(); w._ui=quiet; controller._store=FailingStore()
        try:
            prior=controller.current; prior_width=w.text_view.tab_width; prior_tab_state=string_state(w.lookup_action('tab-width'))
            w.lookup_action('tab-width').activate(GLib.Variant.new_string('2')); drain_for(Gtk)
            if controller.current!=prior or w.text_view.tab_width!=prior_width or string_state(w.lookup_action('tab-width'))!=prior_tab_state: return 1
            prior_spaces=w.text_view.insert_spaces; prior_spaces_state=bool_state(w.lookup_action('insert-spaces'))
            w.lookup_action('insert-spaces').activate(None); drain_for(Gtk)
            if controller.current!=prior or w.text_view.insert_spaces!=prior_spaces or bool_state(w.lookup_action('insert-spaces'))!=prior_spaces_state: return 1
            if len(quiet.warnings)!=2: return 1
        finally:
            controller._store=old_store; w._ui=old_ui

        # Plain Tab still follows logical tab stops and remains one native Undo unit.
        establish(w,Gtk,'a',1,1)
        if not w.text_view._on_key_press_event(w.text_view,FakeKeyEvent(Gdk.KEY_Tab)): return 1
        if text_of(w.text_view)!='a      ' or not w.core.session.modified: return 1
        w.lookup_action('undo').activate(None); drain_for(Gtk)
        if text_of(w.text_view)!='a' or w.core.session.modified: return 1
        for mod in (Gdk.ModifierType.SHIFT_MASK,Gdk.ModifierType.CONTROL_MASK,Gdk.ModifierType.MOD1_MASK):
            if w.text_view._plain_tab_event(FakeKeyEvent(Gdk.KEY_Tab,mod)): return 1

        # Existing appearance and window-geometry ownership remain independent.
        if w._appearance_renderer is not None: return 1
        if 'graphium.adapters.gtk.appearance' in sys.modules: return 1
        establish(w,Gtk,'appearance neutral',3,3); before_text=text_of(w.text_view); before_state=w.core.history.current_state_id
        for value,expected,explicit in (('dark',True,True),('light',False,True),('system',baseline,False)):
            w.lookup_action('appearance').activate(GLib.Variant.new_string(value)); drain_for(Gtk)
            renderer=w._appearance_renderer
            if renderer is None: return 1
            if bool(native.get_property('gtk-application-prefer-dark-theme'))!=bool(expected): return 1
            if w.core.view_settings.current.appearance!=value: return 1
            if renderer.mode!=value or renderer.explicit_projection_active is not explicit: return 1
        if text_of(w.text_view)!=before_text or w.core.history.current_state_id!=before_state: return 1

        establish(w,Gtk,'clean')
        w.present(); w.resize(900,600); drain_for(Gtk,0.12)
        width,height=w.get_size()
        if width<850 or height<550: return 1
        normal=w._normal_window_size
        if normal[0]<850 or normal[1]<550: return 1
        prior=JsonViewSettingsStore(config).load()
        if (prior.window_width,prior.window_height)==normal: return 1
        if w._on_delete_event() is not False: return 1
        persisted=JsonViewSettingsStore(config).load()
        if (persisted.window_width,persisted.window_height)!=normal: return 1
        if any(hasattr(persisted,x) for x in ('window_x','window_y','maximized','fullscreen','monitor')): return 1
        return 0
    except (AssertionError,OSError,ValueError,RuntimeError):
        return 1
    finally:
        w.destroy(); drain_for(Gtk); app.quit()

if __name__=='__main__': raise SystemExit(main())
