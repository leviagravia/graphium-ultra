from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from graphium.infrastructure.guarded_file_writer import GuardedFileWriter
from graphium_plus.companion_storage import CheckedTextAuthority, CompanionAuthorityError
from graphium_plus.clips import ClipStore, new_clip, update_clip, parse_clips, serialize_clips, search_clips
from graphium_plus.scratchpad import ScratchpadStore, new_scratch, update_scratch, parse_scratchpad, serialize_scratchpad, scratchpad_path, search_scratchpad


class CompanionModelTests(unittest.TestCase):
    def test_clip_round_trip_preserves_multiline_backticks(self):
        item = new_clip("Fence", "alpha\n```inside```\nomega")
        self.assertEqual(parse_clips(serialize_clips((item,))), (item,))

    def test_scratch_round_trip_preserves_tags_status_and_body(self):
        item = new_scratch("Note", "body\n````inside````", "one, two")
        item = update_scratch(item, title=item.title, body=item.body, status="archived")
        self.assertEqual(parse_scratchpad(serialize_scratchpad((item,))), (item,))

    def test_clip_search_is_title_body_case_insensitive(self):
        a = new_clip("Alpha", "Beta body"); b = new_clip("Other", "Gamma")
        self.assertEqual(search_clips((a,b), "ALPHA beta"), (a,))

    def test_scratch_search_filters_status_and_tags(self):
        a = new_scratch("A", "body", "tag-x"); b = update_scratch(new_scratch("B","body","tag-x"), title="B", body="body", status="archived")
        self.assertEqual(search_scratchpad((a,b), "TAG-X", "active"), (a,))
        self.assertEqual(search_scratchpad((a,b), "TAG-X", "archived"), (b,))
        self.assertEqual(search_scratchpad((a,b), "TAG-X", "all"), (a,b))

    def test_scratchpad_path_is_path_local_and_untitled_unavailable(self):
        self.assertIsNone(scratchpad_path(None))
        self.assertEqual(scratchpad_path("/tmp/paper.md"), Path("/tmp/paper.md.scratchpad.md"))

    def test_clip_update_preserves_stable_id_and_created(self):
        a=new_clip("A","B"); b=update_clip(a,title="C",body="D")
        self.assertEqual((b.id,b.created),(a.id,a.created)); self.assertEqual((b.title,b.body),("C","D"))

    def test_malformed_clip_header_fails_closed(self):
        with self.assertRaises(ValueError): parse_clips("# wrong\n")

    def test_scratch_unknown_metadata_round_trips_losslessly(self):
        item = new_scratch("A", "B", "tag")
        text = serialize_scratchpad((item,)).replace("Created:", "Future: retained\nCreated:")
        parsed = parse_scratchpad(text)
        self.assertEqual(parsed[0].extra_fields, (("Future", "retained"),))
        self.assertIn("Future: retained", serialize_scratchpad(parsed))

    def test_malformed_scratch_status_fails_closed(self):
        item=new_scratch("A","B"); text=serialize_scratchpad((item,)).replace("Status: active","Status: unknown")
        with self.assertRaises(ValueError): parse_scratchpad(text)


class CompanionStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.writer=GuardedFileWriter()
    def tearDown(self): self.tmp.cleanup()

    def test_clips_persist_through_core_writer(self):
        store=ClipStore(self.writer,self.root/'clips.md'); snap=store.load(); item=new_clip("A","B"); saved=store.save((item,),snap.token)
        self.assertEqual(saved.records,(item,)); self.assertTrue((self.root/'clips.md').is_file())

    def test_scratchpad_persist_through_core_writer(self):
        store=ScratchpadStore(self.writer,self.root/'paper.md.scratchpad.md'); snap=store.load(); item=new_scratch("A","B","tag")
        self.assertEqual(store.save((item,),snap.token).records,(item,))

    def test_stale_authority_is_rejected(self):
        path=self.root/'clips.md'; store=ClipStore(self.writer,path); snap=store.load(); path.write_text("external",encoding='utf-8')
        with self.assertRaises(CompanionAuthorityError): store.save((new_clip("A","B"),),snap.token)

    def test_symlink_authority_is_rejected(self):
        target=self.root/'real'; target.write_text('',encoding='utf-8'); link=self.root/'clips.md'; link.symlink_to(target)
        with self.assertRaises(CompanionAuthorityError): ClipStore(self.writer,link).load()

    def test_bounded_authority_rejects_oversize(self):
        path=self.root/'x'; path.write_bytes(b'x'*33); store=CheckedTextAuthority(self.writer,path,max_bytes=32)
        with self.assertRaises(CompanionAuthorityError): store.load()


class CompanionSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[2]
        cls.window=(cls.root/'graphium_plus/adapters/gtk/window.py').read_text(encoding='utf-8')
        cls.panel=(cls.root/'graphium_plus/adapters/gtk/companion_panel.py').read_text(encoding='utf-8')

    def test_exact_one_new_editor_companion_paned_topology(self):
        self.assertEqual(self.window.count('self.editor_companion_paned = Gtk.Paned'),1)
        self.assertIn('self.content_paned.pack2(self.editor_companion_paned',self.window)
        self.assertIn('self.editor_companion_paned.pack1(self.editor_box',self.window)

    def test_actions_have_no_accelerator_and_view_menu_owns_companion(self):
        self.assertIn('("companion-clips", "clips")',self.window)
        self.assertIn('("companion-scratchpad", "scratchpad")',self.window)
        app=(self.root/'graphium_plus/adapters/gtk/application.py').read_text(encoding='utf-8')
        self.assertNotIn('win.companion-',app)
        self.assertIn('Gtk.CheckMenuItem(label="Companion Panel")',self.window)
        self.assertIn('win.companion-visible', self.window)

    def test_insert_uses_grouped_editor_authority(self):
        segment=self.window[self.window.index('def _companion_insert_text'):self.window.index('def _companion_copy_text')]
        self.assertIn('apply_prevalidated_programmatic_group',segment)
        self.assertNotIn('.insert(',segment); self.assertNotIn('.delete(',segment); self.assertNotIn('.set_text(',segment)

    def test_focus_snapshot_has_real_companion_fields_and_no_state_write(self):
        self.assertIn('companion_constructed: bool',self.window); self.assertIn('companion_position: int',self.window)
        enter=self.window[self.window.index('def _enter_focus_mode'):self.window.index('def _exit_focus_mode')]
        self.assertIn('_set_focus_hidden(self._companion_panel.widget)',enter)
        self.assertNotIn('_companion_state_store.save',enter)


    def test_companion_shows_clips_and_scratchpad_simultaneously(self):
        self.assertIn('self.clients_paned=Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)', self.panel)
        self.assertIn("self.clips_frame=Gtk.Frame(label='Clips')", self.panel)
        self.assertIn("self.scratch_frame=Gtk.Frame(label='Scratchpad')", self.panel)
        self.assertIn('self.clients_paned.pack1(self.clips_frame,resize=True,shrink=True)', self.panel)
        self.assertIn('self.clients_paned.pack2(self.scratch_frame,resize=True,shrink=True)', self.panel)
        self.assertNotIn('Gtk.StackSwitcher', self.panel)
        self.assertNotIn('Gtk.Stack()', self.panel)
        self.assertNotIn('Gtk.Notebook', self.panel)
        self.assertNotIn('self.selector=Gtk.ComboBoxText()', self.panel)

    def test_companion_client_actions_focus_without_hiding_the_other_client(self):
        self.assertIn("def focus_client(self,name):", self.panel)
        self.assertIn("client=self.clips if name=='clips' else self.scratch", self.panel)
        self.assertIn('client.search.grab_focus()', self.panel)
        self.assertIn("def active_client(self): return self._active_client", self.panel)
        self.assertNotIn('set_visible_child', self.panel)

    def test_companion_visibility_is_one_stateful_authority_and_client_actions_only_focus(self):
        install=self.window[self.window.index('def _install_companion_controls'):self.window.index('def _companion_document_path')]
        self.assertIn('Gio.SimpleAction.new_stateful', install)
        self.assertIn('"companion-visible"', install)
        self.assertIn('action.connect("change-state", self._change_companion_visibility)', install.replace('visible_action.connect', 'action.connect'))
        self.assertIn('Gtk.CheckMenuItem(label="Companion Panel")', install)
        route=self.window[self.window.index('def _action_companion'):self.window.index('def _request_companion_close')]
        self.assertIn('panel.focus_client(client)', route)
        self.assertNotIn('_hide_companion', route)
        self.assertNotIn('panel.active_client() == client', route)
        self.assertNotIn('.hide()', route)

    def test_all_panel_close_requests_route_through_visibility_actions(self):
        for method, action_name in (
            ('_request_workspace_close', 'workspace-visible'),
            ('_request_outline_close', 'outline-visible'),
            ('_request_companion_close', 'companion-visible'),
        ):
            start=self.window.index(f'def {method}')
            tail=self.window[start:]
            next_def=tail.find('\n    def ', 1)
            part=tail if next_def < 0 else tail[:next_def]
            self.assertIn(f'self._actions["{action_name}"]', part)
            self.assertIn('change_state(GLib.Variant.new_boolean(False))', part)
            self.assertNotIn('.hide()', part)

    def test_panel_chrome_is_shared_by_workspace_outline_and_companion(self):
        workspace=(self.root/'graphium_plus/adapters/gtk/workspace_panel.py').read_text(encoding='utf-8')
        outline=(self.root/'graphium_plus/adapters/gtk/outliner_panel.py').read_text(encoding='utf-8')
        chrome=(self.root/'graphium_plus/adapters/gtk/panel_chrome.py').read_text(encoding='utf-8')
        self.assertIn('window-close-symbolic', chrome)
        self.assertIn('set_size_request(18, 18)', chrome)
        self.assertIn('build_compact_close_button(', workspace)
        self.assertIn('tooltip="Hide Workspace"', workspace)
        self.assertIn('build_compact_close_button(', outline)
        self.assertIn('tooltip="Hide Outline"', outline)
        self.assertIn('build_compact_close_button(', self.panel)
        self.assertIn("tooltip='Hide Companion Panel'", self.panel)

    def test_sidecar_rebind_has_no_move_or_copy(self):
        segment=self.window[self.window.index('def _rebind_companion_scratchpad'):]
        self.assertNotIn('shutil.move',segment); self.assertNotIn('shutil.copy',segment)


if __name__ == '__main__': unittest.main()
