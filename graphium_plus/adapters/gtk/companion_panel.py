"""GTK Companion Panel with exactly Clips and Scratchpad clients."""
from __future__ import annotations

from dataclasses import dataclass
import json, os, tempfile
from pathlib import Path
from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from graphium_plus.clips import ClipStore, ClipRecord, new_clip, update_clip, search_clips
from graphium_plus.scratchpad import ScratchpadStore, ScratchRecord, new_scratch, update_scratch, search_scratchpad, scratchpad_path
from .panel_chrome import build_compact_close_button

_DEFAULT_WIDTH=190
_MIN_WIDTH=184
_EDITOR_FLOOR=360

@dataclass(frozen=True)
class CompanionHost:
    selected_text: Callable[[], str]
    insert_text: Callable[[str], None]
    document_path: Callable[[], str | None]
    scratchpad_unavailable_reason: Callable[[], str | None]
    copy_text: Callable[[str], None]
    focus_editor: Callable[[], None]
    report_error: Callable[[str], None]

class CompanionPanelStateStore:
    def __init__(self,path): self.path=Path(path)
    def load(self):
        try: data=json.loads(self.path.read_text(encoding='utf-8'))
        except (FileNotFoundError,OSError,UnicodeError,json.JSONDecodeError,TypeError,ValueError): return False,_DEFAULT_WIDTH
        if not isinstance(data,dict) or data.get('schema')!=1: return False,_DEFAULT_WIDTH
        visible=data.get('visible'); width=data.get('width')
        if not isinstance(visible,bool) or not isinstance(width,int) or isinstance(width,bool): return False,_DEFAULT_WIDTH
        return visible,max(_MIN_WIDTH,min(width,4096))
    def save(self,visible,width):
        parent=self.path.parent; parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix='.companion-panel-',suffix='.tmp',dir=parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as h:
                json.dump({'schema':1,'visible':bool(visible),'width':max(_MIN_WIDTH,int(width))},h,separators=(',',':')); h.write('\n'); h.flush(); os.fsync(h.fileno())
            os.replace(temp,self.path)
        finally:
            try: os.unlink(temp)
            except FileNotFoundError: pass

class _RecordClient:
    def __init__(self,title, *, on_new,on_capture,on_edit,on_delete,on_insert,on_copy,on_refresh,on_archive=None):
        self.widget=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4)
        self.search=Gtk.SearchEntry(); self.search.set_placeholder_text(f"Search {title}")
        self.widget.pack_start(self.search,False,False,0)
        self.status=None
        if title=='Scratchpad':
            self.status=Gtk.ComboBoxText()
            for key,label in [('active','Active'),('archived','Archived'),('all','All')]: self.status.append(key,label)
            self.status.set_active_id('active'); self.widget.pack_start(self.status,False,False,0)
        paned=Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.store=Gtk.ListStore(str,str)
        self.tree=Gtk.TreeView(model=self.store); self.tree.set_headers_visible(False)
        cell=Gtk.CellRendererText(); col=Gtk.TreeViewColumn('Title',cell,text=1); self.tree.append_column(col)
        scroll=Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.AUTOMATIC,Gtk.PolicyType.AUTOMATIC); scroll.add(self.tree)
        paned.pack1(scroll,resize=True,shrink=True)
        self.body=Gtk.TextView(); self.body.set_editable(False); self.body.set_cursor_visible(False); self.body.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        body_scroll=Gtk.ScrolledWindow(); body_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,Gtk.PolicyType.AUTOMATIC); body_scroll.add(self.body)
        paned.pack2(body_scroll,resize=True,shrink=True); paned.set_position(210)
        self.widget.pack_start(paned,True,True,0)
        buttons=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=2)
        defs=[('New',on_new),('Capture',on_capture),('Edit',on_edit)]
        if on_archive is not None: defs.append(('Archive/Restore',on_archive))
        defs += [('Delete',on_delete),('Insert',on_insert),('Copy',on_copy),('Refresh',on_refresh)]
        for label,cb in defs:
            b=Gtk.Button(label=label); b.connect('clicked',lambda _b,fn=cb: fn()); buttons.pack_start(b,False,False,0)
        self.widget.pack_end(buttons,False,False,0)
        self.search.connect('search-changed',lambda *_: on_refresh(False))
        if self.status is not None: self.status.connect('changed',lambda *_: on_refresh(False))
        self.tree.get_selection().connect('changed',self._selection_changed)
        self._records={}
    def selected_id(self):
        model,it=self.tree.get_selection().get_selected(); return None if it is None else model.get_value(it,0)
    def selected(self): return self._records.get(self.selected_id())
    def render(self,records):
        selected=self.selected_id(); self._records={r.id:r for r in records}; self.store.clear(); target=None
        for r in records:
            it=self.store.append((r.id,r.title));
            if r.id==selected: target=it
        if target is None and len(self.store): target=self.store.get_iter_first()
        if target is not None: self.tree.get_selection().select_iter(target)
        else: self.body.get_buffer().set_text('')
    def _selection_changed(self,*_):
        r=self.selected(); self.body.get_buffer().set_text('' if r is None else r.body)

def _edit_dialog(parent,title,initial_title='',initial_body='',initial_tags=''):
    dialog=Gtk.Dialog(title=title,transient_for=parent,modal=True)
    dialog.add_button('Cancel',Gtk.ResponseType.CANCEL); dialog.add_button('Save',Gtk.ResponseType.OK)
    box=dialog.get_content_area(); box.set_spacing(6); box.set_border_width(8)
    te=Gtk.Entry(); te.set_text(initial_title); te.set_placeholder_text('Title'); box.pack_start(te,False,False,0)
    tags=None
    if initial_tags is not None:
        tags=Gtk.Entry(); tags.set_text(initial_tags); tags.set_placeholder_text('Tags, comma separated'); box.pack_start(tags,False,False,0)
    tv=Gtk.TextView(); tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR); tv.get_buffer().set_text(initial_body)
    sc=Gtk.ScrolledWindow(); sc.set_min_content_height(220); sc.add(tv); box.pack_start(sc,True,True,0)
    dialog.set_default_size(460,360); dialog.show_all(); response=dialog.run()
    result=None
    if response==Gtk.ResponseType.OK:
        buf=tv.get_buffer(); body=buf.get_text(buf.get_start_iter(),buf.get_end_iter(),True); result=(te.get_text(),body,'' if tags is None else tags.get_text())
    dialog.destroy(); return result

def _confirm(parent,message):
    d=Gtk.MessageDialog(transient_for=parent,modal=True,message_type=Gtk.MessageType.QUESTION,buttons=Gtk.ButtonsType.OK_CANCEL,text=message); r=d.run(); d.destroy(); return r==Gtk.ResponseType.OK

class CompanionPanel:
    def __init__(self,parent,writer,data_path:Path,host:CompanionHost,on_close:Callable[[],None]):
        self.parent=parent; self.host=host; self.writer=writer; self.clip_store=ClipStore(writer,data_path/'clips.md')
        try:
            self._clip_snapshot=self.clip_store.load()
        except Exception as exc:
            self._clip_snapshot=None
            host.report_error(str(exc))
        self._scratch_store=None; self._scratch_snapshot=None; self._scratch_bound_key=None
        self.widget=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5); self.widget.set_size_request(-1,-1); self.widget.set_border_width(4)
        head=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=4); label=Gtk.Label(label='Companion Panel'); label.set_xalign(0); label.set_hexpand(True)
        self.close_button=build_compact_close_button(on_close,name='graphium-companion-close',tooltip='Hide Companion Panel')
        head.pack_start(label,True,True,0); head.pack_end(self.close_button,False,False,0); self.widget.pack_start(head,False,False,0)
        self.clips=_RecordClient('Clips',on_new=self._clip_new,on_capture=self._clip_capture,on_edit=self._clip_edit,on_delete=self._clip_delete,on_insert=self._clip_insert,on_copy=self._clip_copy,on_refresh=self._clip_refresh)
        self.scratch=_RecordClient('Scratchpad',on_new=self._scratch_new,on_capture=self._scratch_capture,on_edit=self._scratch_edit,on_delete=self._scratch_delete,on_insert=self._scratch_insert,on_copy=self._scratch_copy,on_refresh=self._scratch_refresh,on_archive=self._scratch_archive)
        self.clients_paned=Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.clips_frame=Gtk.Frame(label='Clips'); self.clips_frame.add(self.clips.widget)
        self.scratch_frame=Gtk.Frame(label='Scratchpad'); self.scratch_frame.add(self.scratch.widget)
        self.clients_paned.pack1(self.clips_frame,resize=True,shrink=True)
        self.clients_paned.pack2(self.scratch_frame,resize=True,shrink=True)
        self.clients_paned.set_position(300)
        self.widget.pack_start(self.clients_paned,True,True,0)
        self._active_client='clips'
        self._clip_render(); self.rebind_scratchpad()
    def focus_client(self,name):
        if name not in {'clips','scratchpad'}: raise ValueError(name)
        self._active_client=name
        client=self.clips if name=='clips' else self.scratch
        if name=='scratchpad': self.rebind_scratchpad()
        client.search.grab_focus()
    def active_client(self): return self._active_client
    def rebind_scratchpad(self):
        document_path=self.host.document_path()
        blocked_reason=self.host.scratchpad_unavailable_reason()
        path=scratchpad_path(document_path)
        key=(path,blocked_reason)
        if key==self._scratch_bound_key: return
        self._scratch_bound_key=key; self._scratch_store=None; self._scratch_snapshot=None
        if blocked_reason:
            self.scratch.render(()); self.scratch.body.get_buffer().set_text(blocked_reason); return
        if path is None:
            self.scratch.render(()); self.scratch.body.get_buffer().set_text('Scratchpad is unavailable for Untitled documents.'); return
        try:
            self._scratch_store=ScratchpadStore(self.writer,path); self._scratch_snapshot=self._scratch_store.load(); self._scratch_render()
        except Exception as exc: self.host.report_error(str(exc)); self.scratch.render(())
    def _clip_render(self):
        self.clips.render(() if self._clip_snapshot is None else search_clips(self._clip_snapshot.records,self.clips.search.get_text()))
    def _clip_refresh(self,disk=True):
        try:
            if disk: self._clip_snapshot=self.clip_store.load()
            self._clip_render()
        except Exception as exc: self.host.report_error(str(exc))
    def _clip_commit(self,records):
        if self._clip_snapshot is None:
            self.host.report_error('Clips authority is unavailable or malformed; Refresh before editing.')
            return
        try: self._clip_snapshot=self.clip_store.save(records,self._clip_snapshot.token); self._clip_render()
        except Exception as exc: self.host.report_error(str(exc))
    def _clip_new(self):
        v=_edit_dialog(self.parent,'New Clip',initial_tags=None)
        if v and self._clip_snapshot is not None: self._clip_commit((new_clip(v[0],v[1]),*self._clip_snapshot.records))
    def _clip_capture(self):
        text=self.host.selected_text()
        if not text: self.host.report_error('Capture requires a non-empty editor selection.'); return
        v=_edit_dialog(self.parent,'Capture Clip',initial_body=text,initial_tags=None)
        if v and self._clip_snapshot is not None: self._clip_commit((new_clip(v[0],v[1]),*self._clip_snapshot.records))
    def _clip_edit(self):
        r=self.clips.selected();
        if not r or self._clip_snapshot is None:return
        v=_edit_dialog(self.parent,'Edit Clip',r.title,r.body,None)
        if v: self._clip_commit(tuple(update_clip(x,title=v[0],body=v[1]) if x.id==r.id else x for x in self._clip_snapshot.records))
    def _clip_delete(self):
        r=self.clips.selected();
        if self._clip_snapshot is not None and r and _confirm(self.parent,f'Delete Clip “{r.title}”?'): self._clip_commit(tuple(x for x in self._clip_snapshot.records if x.id!=r.id))
    def _clip_fresh_selected(self):
        rid=self.clips.selected_id(); self._clip_snapshot=self.clip_store.load(); return next((r for r in self._clip_snapshot.records if r.id==rid),None)
    def _clip_insert(self):
        try:r=self._clip_fresh_selected()
        except Exception as exc:self.host.report_error(str(exc));return
        if r:self.host.insert_text(r.body)
    def _clip_copy(self):
        r=self.clips.selected();
        if r:self.host.copy_text(r.body)
    def _scratch_render(self):
        if self._scratch_snapshot is None: self.scratch.render(()); return
        status=self.scratch.status.get_active_id() or 'active'; self.scratch.render(search_scratchpad(self._scratch_snapshot.records,self.scratch.search.get_text(),status))
    def _scratch_refresh(self,disk=True):
        self.rebind_scratchpad()
        if self._scratch_store is None:return
        try:
            if disk:self._scratch_snapshot=self._scratch_store.load()
            self._scratch_render()
        except Exception as exc:self.host.report_error(str(exc))
    def _scratch_commit(self,records):
        if self._scratch_store is None or self._scratch_snapshot is None:self.host.report_error('Scratchpad is unavailable for Untitled documents.');return
        try:self._scratch_snapshot=self._scratch_store.save(records,self._scratch_snapshot.token);self._scratch_render()
        except Exception as exc:self.host.report_error(str(exc))
    def _scratch_new(self):
        if self._scratch_store is None:self.host.report_error('Scratchpad is unavailable for Untitled documents.');return
        v=_edit_dialog(self.parent,'New Scratchpad Note')
        if v:self._scratch_commit((new_scratch(v[0],v[1],v[2]),*self._scratch_snapshot.records))
    def _scratch_capture(self):
        text=self.host.selected_text()
        if not text:self.host.report_error('Capture requires a non-empty editor selection.');return
        v=_edit_dialog(self.parent,'Capture Scratchpad Note',initial_body=text)
        if v:self._scratch_commit((new_scratch(v[0],v[1],v[2]),*self._scratch_snapshot.records))
    def _scratch_edit(self):
        r=self.scratch.selected();
        if not r:return
        v=_edit_dialog(self.parent,'Edit Scratchpad Note',r.title,r.body,', '.join(r.tags))
        if v:self._scratch_commit(tuple(update_scratch(x,title=v[0],body=v[1],tags=v[2]) if x.id==r.id else x for x in self._scratch_snapshot.records))
    def _scratch_archive(self):
        r=self.scratch.selected();
        if r:self._scratch_commit(tuple(update_scratch(x,title=x.title,body=x.body,status='active' if x.status=='archived' else 'archived') if x.id==r.id else x for x in self._scratch_snapshot.records))
    def _scratch_delete(self):
        r=self.scratch.selected();
        if r and _confirm(self.parent,f'Delete Scratchpad note “{r.title}”?'):self._scratch_commit(tuple(x for x in self._scratch_snapshot.records if x.id!=r.id))
    def _scratch_fresh_selected(self):
        if self._scratch_store is None:return None
        rid=self.scratch.selected_id();self._scratch_snapshot=self._scratch_store.load();return next((r for r in self._scratch_snapshot.records if r.id==rid),None)
    def _scratch_insert(self):
        try:r=self._scratch_fresh_selected()
        except Exception as exc:self.host.report_error(str(exc));return
        if r:self.host.insert_text(r.body)
    def _scratch_copy(self):
        r=self.scratch.selected();
        if r:self.host.copy_text(r.body)
