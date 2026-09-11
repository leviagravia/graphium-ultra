"""Graphium product-owned command catalog.

The catalog is the single command authority shared by menus, accelerators and Help.
Boolean direct View settings are stateful actions; their persistence remains owned by
the view-settings authority, not by GTK widgets.
"""
from __future__ import annotations
from dataclasses import dataclass
from graphium.domain.document_identity import BomKind, LineEnding
from graphium.domain.document_serialization import DocumentSerializationProfile


@dataclass(frozen=True)
class CommandSpec:
    action: str
    label: str
    accelerator: str | None = None
    menu: str = ""
    stateful: bool = False
    submenu: str | None = None
    choices: tuple[tuple[str, str], ...] = ()


TOP_LEVEL_MENUS = ("File", "Edit", "Search", "View", "Document", "Help")

CHECK_SPELLING_COMMAND = CommandSpec("check-spelling", "Check Spelling…", "F2", "Document")

ENCODING_CHOICES = {
    "utf-8": ("UTF-8", "utf-8", BomKind.NONE), "utf-8-bom": ("UTF-8 BOM", "utf-8", BomKind.UTF8),
    "utf-16-le-bom": ("UTF-16 LE BOM", "utf-16-le", BomKind.UTF16_LE), "utf-16-be-bom": ("UTF-16 BE BOM", "utf-16-be", BomKind.UTF16_BE),
    "utf-32-le-bom": ("UTF-32 LE BOM", "utf-32-le", BomKind.UTF32_LE), "utf-32-be-bom": ("UTF-32 BE BOM", "utf-32-be", BomKind.UTF32_BE),
}
LINE_ENDING_CHOICES = {"lf": ("LF", LineEnding.LF), "crlf": ("CRLF", LineEnding.CRLF), "cr": ("CR", LineEnding.CR)}

def encoding_choice_value(profile: DocumentSerializationProfile) -> str:
    return next(value for value, (_, encoding, bom) in ENCODING_CHOICES.items() if (encoding, bom) == (profile.encoding, profile.bom))

def encoding_choice_target(value: str):
    item = ENCODING_CHOICES.get(value); return None if item is None else item[1:]

def line_ending_choice_target(value: str):
    item = LINE_ENDING_CHOICES.get(value); return None if item is None else item[1]


COMMANDS = (
    CommandSpec("new", "New", "<Ctrl>N", "File"),
    CommandSpec("open", "Open…", "<Ctrl>O", "File"),
    CommandSpec("open-recent", "Open Recent", None, "File"),
    CommandSpec("clear-recent", "Clear Recent", None, "Recent"),
    CommandSpec("save", "Save", "<Ctrl>S", "File"),
    CommandSpec("save-as", "Save As…", "<Ctrl><Shift>S", "File"),
    CommandSpec("save-copy", "Save a Copy…", None, "File"),
    CommandSpec("save-version-copy", "Save Version Copy…", None, "File"),
    CommandSpec("reload", "Reload from Disk", "F5", "File"),
    CommandSpec("properties", "Properties…", None, "File"),
    CommandSpec("page-setup", "Page Setup…", None, "File"),
    CommandSpec("print-preview", "Print Preview", "<Ctrl><Shift>P", "File"),
    CommandSpec("print", "Print…", "<Ctrl>P", "File"),
    CommandSpec("quit", "Quit", "<Ctrl>Q", "File"),
    CommandSpec("undo", "Undo", "<Ctrl>Z", "Edit"),
    CommandSpec("redo", "Redo", "<Ctrl><Shift>Z", "Edit"),
    CommandSpec("cut", "Cut", "<Ctrl>X", "Edit"),
    CommandSpec("copy", "Copy", "<Ctrl>C", "Edit"),
    CommandSpec("paste", "Paste", "<Ctrl>V", "Edit"),
    CommandSpec("delete", "Delete", "Delete", "Edit"),
    CommandSpec("select-all", "Select All", "<Ctrl>A", "Edit"),
    CommandSpec(
        "tab-width", "Tab Width", None, "Edit", False, None,
        (("2", "2"), ("3", "3"), ("4", "4"), ("8", "8"), ("Other…", "other")),
    ),
    CommandSpec("insert-spaces", "Insert Spaces Instead of Tabs", None, "Edit", True),
    CommandSpec("uppercase", "Uppercase", "<Ctrl>U", "Edit", False, "Transform Text"),
    CommandSpec("lowercase", "Lowercase", "<Ctrl><Shift>L", "Edit", False, "Transform Text"),
    CommandSpec("duplicate-line-selection", "Duplicate Line / Selection", None, "Edit", False, "Transform Text"),
    CommandSpec("move-lines-up", "Move Lines Up", "<Alt>Up", "Edit", False, "Transform Text"),
    CommandSpec("move-lines-down", "Move Lines Down", "<Alt>Down", "Edit", False, "Transform Text"),
    CommandSpec("trim-trailing-spaces", "Trim Trailing Spaces", None, "Edit", False, "Transform Text"),
    CommandSpec("find", "Find…", "<Ctrl>F", "Search"),
    CommandSpec("find-next", "Find Next", "F3", "Search"),
    CommandSpec("find-previous", "Find Previous", "<Shift>F3", "Search"),
    CommandSpec("replace", "Replace…", "<Ctrl>H", "Search"),
    CommandSpec("go-to-line", "Go to Line…", "<Ctrl>G", "Search"),
    CommandSpec("status-bar", "Status Bar", None, "View", True),
    CommandSpec("line-numbers", "Line Numbers", None, "View", True),
    CommandSpec("word-wrap", "Word Wrap", None, "View", True),
    CommandSpec(
        "appearance", "Appearance", None, "View", False, None,
        (("System", "system"), ("Light", "light"), ("Dark", "dark")),
    ),
    CommandSpec("font", "Font…", None, "View"),
    CommandSpec("zoom-in", "Zoom In", "<Ctrl>plus", "View"),
    CommandSpec("zoom-out", "Zoom Out", "<Ctrl>minus", "View"),
    CommandSpec("zoom-reset", "Reset Zoom", "<Ctrl>0", "View"),
    CommandSpec("full-screen", "Full Screen", "F11", "View", True),
    CommandSpec(
        "encoding", "Encoding", None, "Document", False, None,
        tuple((label, value) for value, (label, _encoding, _bom) in ENCODING_CHOICES.items()),
    ),
    CommandSpec(
        "line-endings", "Line Endings", None, "Document", False, None,
        tuple((label, value) for value, (label, _line_ending) in LINE_ENDING_CHOICES.items()),
    ),
    CHECK_SPELLING_COMMAND,
    CommandSpec("statistics", "Statistics…", None, "Document"),
    CommandSpec("user-guide", "User Guide", None, "Help"),
    CommandSpec("keyboard-shortcuts", "Keyboard Shortcuts", None, "Help"),
    CommandSpec("about", "About", None, "Help"),
)

FORBIDDEN_ACCELERATORS = ("<Ctrl><Alt>L", "<Ctrl><Shift>U")


def accelerator_map() -> dict[str, str]:
    return {item.action: item.accelerator for item in COMMANDS if item.accelerator}


@dataclass(frozen=True)
class CommandAvailability:
    save: bool
    reload: bool
    undo: bool
    redo: bool
    cut: bool
    copy: bool
    delete: bool
    uppercase: bool
    lowercase: bool


def command_availability(
    *,
    modified: bool,
    has_path: bool,
    can_undo: bool,
    can_redo: bool,
    has_selection: bool,
) -> CommandAvailability:
    return CommandAvailability(
        save=modified or not has_path,
        reload=has_path,
        undo=can_undo,
        redo=can_redo,
        cut=has_selection,
        copy=has_selection,
        delete=has_selection,
        uppercase=has_selection,
        lowercase=has_selection,
    )
