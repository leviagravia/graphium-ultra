from __future__ import annotations
import unittest
from graphium.application.commands import COMMANDS, TOP_LEVEL_MENUS, accelerator_map, command_availability, encoding_choice_target, encoding_choice_value, line_ending_choice_target
from graphium.domain.document_identity import BomKind, LineEnding
from graphium.domain.document_serialization import DocumentSerializationProfile
from graphium.domain.edit_history import EditKind, ReplayOperation

class ContractArchitectureTests(unittest.TestCase):

    def test_file_command_surface_and_accelerators_are_exact(self):
        file_commands = [(c.action, c.label, c.accelerator) for c in COMMANDS if c.menu == 'File']
        self.assertEqual(file_commands, [('new', 'New', '<Ctrl>N'), ('open', 'Open…', '<Ctrl>O'), ('open-recent', 'Open Recent', None), ('save', 'Save', '<Ctrl>S'), ('save-as', 'Save As…', '<Ctrl><Shift>S'), ('save-copy', 'Save a Copy…', None), ('save-version-copy', 'Save Version Copy…', None), ('reload', 'Reload from Disk', 'F5'), ('properties', 'Properties…', None), ('page-setup', 'Page Setup…', None), ('print-preview', 'Print Preview', '<Ctrl><Shift>P'), ('print', 'Print…', '<Ctrl>P'), ('quit', 'Quit', '<Ctrl>Q')])
        self.assertEqual(accelerator_map()['print'], '<Ctrl>P')
        self.assertEqual(accelerator_map()['print-preview'], '<Ctrl><Shift>P')
        self.assertNotIn('page-setup', accelerator_map())
        self.assertEqual(accelerator_map()['reload'], 'F5')

    def test_search_commands_are_product_owned_and_exact(self):
        search = [(c.action, c.label, c.accelerator) for c in COMMANDS if c.menu == 'Search']
        self.assertEqual(search, [('find', 'Find…', '<Ctrl>F'), ('find-next', 'Find Next', 'F3'), ('find-previous', 'Find Previous', '<Shift>F3'), ('replace', 'Replace…', '<Ctrl>H'), ('go-to-line', 'Go to Line…', '<Ctrl>G')])

def apply_ops(source: str, operations: tuple[ReplayOperation, ...]) -> str:
    text = source
    for op in operations:
        if op.kind is EditKind.INSERT:
            text = text[:op.offset] + op.text + text[op.offset:]
        else:
            assert text[op.offset:op.offset + len(op.text)] == op.text
            text = text[:op.offset] + text[op.offset + len(op.text):]
    return text

class ArchitectureTests(unittest.TestCase):

    def test_32_command_surface_has_exact_six_transform_actions_and_four_accelerators(self):
        transforms = [c for c in COMMANDS if c.submenu == 'Transform Text']
        self.assertEqual([(c.action, c.label, c.accelerator) for c in transforms], [('uppercase', 'Uppercase', '<Ctrl>U'), ('lowercase', 'Lowercase', '<Ctrl><Shift>L'), ('duplicate-line-selection', 'Duplicate Line / Selection', None), ('move-lines-up', 'Move Lines Up', '<Alt>Up'), ('move-lines-down', 'Move Lines Down', '<Alt>Down'), ('trim-trailing-spaces', 'Trim Trailing Spaces', None)])
        amap = accelerator_map()
        self.assertEqual(amap['uppercase'], '<Ctrl>U')
        self.assertEqual(amap['lowercase'], '<Ctrl><Shift>L')
        self.assertEqual(amap['move-lines-up'], '<Alt>Up')
        self.assertEqual(amap['move-lines-down'], '<Alt>Down')

    def test_33_case_command_availability_tracks_nonempty_selection_only(self):
        off = command_availability(modified=False, has_path=True, can_undo=False, can_redo=False, has_selection=False)
        on = command_availability(modified=False, has_path=True, can_undo=False, can_redo=False, has_selection=True)
        self.assertFalse(off.uppercase)
        self.assertFalse(off.lowercase)
        self.assertTrue(on.uppercase)
        self.assertTrue(on.lowercase)
        self.assertTrue(off.reload)
        untitled = command_availability(modified=False, has_path=False, can_undo=False, can_redo=False, has_selection=False)
        self.assertFalse(untitled.reload)

class CurrentCommandSurfaceTests(unittest.TestCase):

    def test_top_level_menu_authority_is_exact(self):
        self.assertEqual(TOP_LEVEL_MENUS, ("File", "Edit", "Search", "View", "Document", "Help"))
        self.assertEqual({c.menu for c in COMMANDS if c.menu != "Recent"}, set(TOP_LEVEL_MENUS))

    def test_command_surface_is_classic_and_has_no_format_menu(self):
        actual = {(c.menu, c.action) for c in COMMANDS}
        for pair in {('File', 'new'), ('File', 'open'), ('File', 'save'), ('File', 'save-as'), ('File', 'reload'), ('File', 'quit'), ('Edit', 'undo'), ('Edit', 'redo'), ('Edit', 'cut'), ('Edit', 'copy'), ('Edit', 'paste'), ('Edit', 'delete'), ('Edit', 'select-all'), ('Help', 'user-guide'), ('Help', 'keyboard-shortcuts'), ('Help', 'about')}:
            self.assertIn(pair, actual)
        self.assertNotIn('Format', {c.menu for c in COMMANDS})
        document = [c for c in COMMANDS if c.menu == 'Document']
        self.assertEqual([c.action for c in document], ['encoding', 'line-endings', 'check-spelling', 'statistics'])
        self.assertEqual(document[0].choices, (("UTF-8", "utf-8"), ("UTF-8 BOM", "utf-8-bom"), ("UTF-16 LE BOM", "utf-16-le-bom"), ("UTF-16 BE BOM", "utf-16-be-bom"), ("UTF-32 LE BOM", "utf-32-le-bom"), ("UTF-32 BE BOM", "utf-32-be-bom")))
        self.assertEqual(document[1].choices, (("LF", "lf"), ("CRLF", "crlf"), ("CR", "cr")))
        self.assertEqual(encoding_choice_target("utf-16-le-bom"), ("utf-16-le", BomKind.UTF16_LE))
        self.assertEqual(encoding_choice_value(DocumentSerializationProfile("utf-16-le", BomKind.UTF16_LE, LineEnding.CRLF)), "utf-16-le-bom")
        self.assertEqual(line_ending_choice_target("crlf"), LineEnding.CRLF); self.assertFalse(document[0].accelerator or document[1].accelerator); self.assertEqual(document[2].accelerator,'F2')


class DirectSettingsCommandSurfaceTests(unittest.TestCase):
    def test_tab_controls_are_direct_unique_and_have_no_accelerators(self):
        self.assertFalse([c for c in COMMANDS if c.action == "preferences"])
        tab=[c for c in COMMANDS if c.action=="tab-width"]
        spaces=[c for c in COMMANDS if c.action=="insert-spaces"]
        appearance=[c for c in COMMANDS if c.action=="appearance"]
        self.assertEqual(len(tab),1); self.assertEqual(len(spaces),1); self.assertEqual(len(appearance),1)
        self.assertEqual((tab[0].label,tab[0].menu,tab[0].accelerator),("Tab Width","Edit",None))
        self.assertEqual(tab[0].choices,(("2","2"),("3","3"),("4","4"),("8","8"),("Other…","other")))
        self.assertEqual((spaces[0].label,spaces[0].menu,spaces[0].stateful,spaces[0].accelerator),("Insert Spaces Instead of Tabs","Edit",True,None))
        self.assertEqual((appearance[0].menu,appearance[0].accelerator),("View",None))
        self.assertEqual(appearance[0].choices,(("System","system"),("Light","light"),("Dark","dark")))
        amap=accelerator_map(); self.assertNotIn("tab-width",amap); self.assertNotIn("insert-spaces",amap); self.assertNotIn("appearance",amap)
