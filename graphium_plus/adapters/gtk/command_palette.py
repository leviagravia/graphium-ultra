"""GTK3 transient command palette projected from Graphium's live menu/action surface."""
from __future__ import annotations

from collections.abc import Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from graphium.application.commands import COMMANDS, TOP_LEVEL_MENUS
from graphium_plus.command_palette import PaletteEntry, filter_palette_entries


_MAX_LEAVES = 256
_MAX_DEPTH = 8
_SELF_ACTION = "win.command-palette"


class CommandPaletteProjectionError(RuntimeError):
    pass


def _parameter_is_string(action: Gio.Action) -> bool:
    parameter_type = action.get_parameter_type()
    if parameter_type is None:
        return False
    try:
        return bool(parameter_type.equal(GLib.VariantType.new("s")))
    except AttributeError:
        try:
            return parameter_type.dup_string() == "s"
        except AttributeError:
            return False


def _project_target(action: Gio.Action, item: Gtk.MenuItem) -> str | None | object:
    target = item.get_action_target_value()
    parameter_type = action.get_parameter_type()
    if parameter_type is None:
        return None if target is None else _UNSUPPORTED
    if target is None or not _parameter_is_string(action):
        return _UNSUPPORTED
    if target.get_type_string() != "s":
        return _UNSUPPORTED
    return target.get_string()


def _shortcut_hint(window: Gtk.ApplicationWindow, action_name: str, target: str | None) -> str:
    application = window.get_application()
    if application is None:
        return ""
    detailed = action_name
    if target is not None:
        try:
            detailed = Gio.Action.print_detailed_name(action_name, GLib.Variant.new_string(target))
        except Exception:
            detailed = action_name
    accels = tuple(application.get_accels_for_action(detailed) or ())
    if not accels and detailed != action_name:
        accels = tuple(application.get_accels_for_action(action_name) or ())
    if not accels:
        return ""
    accelerator = str(accels[0])
    try:
        key, modifiers = Gtk.accelerator_parse(accelerator)
        label = Gtk.accelerator_get_label(key, modifiers)
        return str(label or accelerator)
    except Exception:
        return accelerator


_UNSUPPORTED = object()


def collect_command_palette_entries(
    window: Gtk.ApplicationWindow,
    menubar: Gtk.MenuBar,
) -> tuple[PaletteEntry, ...]:
    """Project current commands from their existing Core/Plus authorities.

    Core menu widgets are a GTK projection of ``COMMANDS`` through ``Gio.Menu`` and
    ``Gtk.MenuBar.new_from_model()``.  They are therefore deliberately *not* parsed
    back from transient ``GtkModelMenuItem`` wrappers.  Plus additions are native
    action-bound ``Gtk.MenuItem`` objects and remain projected from the live menu.
    In both cases the current ``Gio.Action`` and accelerator tables are revalidated.
    """

    entries: list[PaletteEntry] = []
    order = 0
    core_action_names = frozenset(f"win.{spec.action}" for spec in COMMANDS)

    def append_leaf(
        *,
        label: str,
        breadcrumb: str,
        action_name: str,
        target: str | None,
    ) -> None:
        nonlocal order
        if action_name == _SELF_ACTION or not action_name.startswith("win."):
            return
        action = window.lookup_action(action_name[4:])
        if action is None or not action.get_enabled():
            return
        parameter_type = action.get_parameter_type()
        if target is None:
            if parameter_type is not None:
                return
        elif parameter_type is None or not _parameter_is_string(action):
            return
        clean_label = str(label or "").strip()
        if not clean_label:
            return
        if len(entries) >= _MAX_LEAVES:
            raise CommandPaletteProjectionError("Command menu exceeds the 256-leaf safe bound.")
        entries.append(
            PaletteEntry(
                label=clean_label,
                breadcrumb=breadcrumb,
                action_name=action_name,
                target=target,
                shortcut=_shortcut_hint(window, action_name, target),
                order=order,
            )
        )
        order += 1

    def append_core_menu(menu_name: str) -> None:
        # Mirror the Core Gio.Menu construction order from graphium.adapters.gtk.window:
        # ordinary leaves/choice submenus first, then Edit > Transform Text.
        for spec in COMMANDS:
            if spec.menu != menu_name or spec.submenu is not None:
                continue
            if spec.action == "open-recent":
                # The whole dynamic Recent subtree is outside P3.3 scope.
                continue
            action_name = f"win.{spec.action}"
            if spec.choices:
                breadcrumb = f"{menu_name} > {spec.label}"
                for choice_label, choice_value in spec.choices:
                    append_leaf(
                        label=choice_label,
                        breadcrumb=breadcrumb,
                        action_name=action_name,
                        target=choice_value,
                    )
            else:
                append_leaf(
                    label=spec.label,
                    breadcrumb=menu_name,
                    action_name=action_name,
                    target=None,
                )
        if menu_name == "Edit":
            for spec in COMMANDS:
                if spec.menu == "Edit" and spec.submenu == "Transform Text":
                    append_leaf(
                        label=spec.label,
                        breadcrumb="Edit > Transform Text",
                        action_name=f"win.{spec.action}",
                        target=None,
                    )

    def walk_plus(
        container: Gtk.Container,
        parents: tuple[str, ...],
        depth: int,
        ancestors: tuple[Gtk.Container, ...],
    ) -> None:
        if depth > _MAX_DEPTH:
            raise CommandPaletteProjectionError("Command menu nesting exceeds the safe bound.")
        if any(container is ancestor for ancestor in ancestors):
            raise CommandPaletteProjectionError("Command menu contains a cyclic widget.")
        next_ancestors = ancestors + (container,)
        for child in container.get_children():
            if not isinstance(child, Gtk.MenuItem):
                continue
            label = str(child.get_label() or "").strip()
            submenu = child.get_submenu()
            path = parents + ((label,) if label else ())
            if label == "Open Recent" and parents and parents[-1] == "File":
                continue
            if submenu is not None:
                walk_plus(submenu, path, depth + 1, next_ancestors)
                continue
            action_name = child.get_action_name()
            if not action_name or action_name in core_action_names:
                continue
            action = window.lookup_action(action_name[4:]) if action_name.startswith("win.") else None
            if action is None or not action.get_enabled():
                continue
            target = _project_target(action, child)
            if target is _UNSUPPORTED:
                continue
            append_leaf(
                label=label,
                breadcrumb=" > ".join(parents),
                action_name=action_name,
                target=target,
            )

    # Follow the real top-level menu order.  For Core menus, the stable command
    # metadata comes from COMMANDS; Plus leaves appended into those same menus are
    # then collected from their live GTK widgets.  Plus-only top-level menus are
    # collected entirely from their live widgets.
    for top_item in menubar.get_children():
        if not isinstance(top_item, Gtk.MenuItem):
            continue
        label = str(top_item.get_label() or "").strip()
        submenu = top_item.get_submenu()
        if not label or submenu is None:
            continue
        if label in TOP_LEVEL_MENUS:
            append_core_menu(label)
        walk_plus(submenu, (label,), 1, ())

    return tuple(entries)


def _resolve_live_action(
    window: Gtk.ApplicationWindow,
    entry: PaletteEntry,
) -> tuple[Gio.Action, GLib.Variant | None] | None:
    if not entry.action_name.startswith("win."):
        return None
    action = window.lookup_action(entry.action_name[4:])
    if action is None or not action.get_enabled():
        return None
    parameter_type = action.get_parameter_type()
    if entry.target is None:
        if parameter_type is not None:
            return None
        return action, None
    if parameter_type is None or not _parameter_is_string(action):
        return None
    return action, GLib.Variant.new_string(entry.target)


class CommandPalette:
    """One transient palette instance; all command authority stays on Gio.Action."""

    def __init__(
        self,
        window: Gtk.ApplicationWindow,
        entries: tuple[PaletteEntry, ...],
        report_warning: Callable[[str], None],
    ) -> None:
        self.window = window
        self.entries = entries
        self.report_warning = report_warning
        self.previous_focus = window.get_focus()
        self.visible_entries: tuple[PaletteEntry, ...] = entries
        self.closed = False

        dialog = Gtk.Dialog(title="Command Palette", transient_for=window, modal=True)
        dialog.set_default_size(640, 420)
        dialog.set_destroy_with_parent(True)
        dialog.connect("delete-event", self._on_delete_event)
        dialog.connect("key-press-event", self._on_dialog_key_press)
        self.dialog = dialog

        content = dialog.get_content_area()
        content.set_border_width(10)
        content.set_spacing(8)
        self.query = Gtk.Entry()
        self.query.set_placeholder_text("Type a command…")
        self.query.connect("changed", self._on_query_changed)
        self.query.connect("key-press-event", self._on_query_key_press)
        content.pack_start(self.query, False, False, 0)

        self.store = Gtk.ListStore(str, str, str, int)
        self.results = Gtk.TreeView(model=self.store)
        self.results.set_headers_visible(False)
        self.results.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        self.results.connect("row-activated", self._on_row_activated)
        label_renderer = Gtk.CellRendererText()
        crumb_renderer = Gtk.CellRendererText()
        shortcut_renderer = Gtk.CellRendererText()
        label_column = Gtk.TreeViewColumn("Command", label_renderer, text=0)
        label_column.set_expand(True)
        crumb_column = Gtk.TreeViewColumn("Path", crumb_renderer, text=1)
        crumb_column.set_expand(True)
        shortcut_column = Gtk.TreeViewColumn("Shortcut", shortcut_renderer, text=2)
        self.results.append_column(label_column)
        self.results.append_column(crumb_column)
        self.results.append_column(shortcut_column)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.add(self.results)
        content.pack_start(self.scroller, True, True, 0)

        self.no_results = Gtk.Label(label="No matching commands")
        self.no_results.set_xalign(0.0)
        content.pack_start(self.no_results, False, False, 0)

    def show(self) -> None:
        self.dialog.show_all()
        self._refresh("")
        self.query.grab_focus()

    def _refresh(self, query: str) -> None:
        self.visible_entries = filter_palette_entries(self.entries, query)
        self.store.clear()
        for index, entry in enumerate(self.visible_entries):
            self.store.append((entry.label, entry.breadcrumb, entry.shortcut, index))
        has_results = bool(self.visible_entries)
        self.scroller.set_visible(has_results)
        self.no_results.set_visible(not has_results)
        if has_results:
            self.results.get_selection().select_path(Gtk.TreePath.new_from_string("0"))

    def _on_query_changed(self, entry: Gtk.Entry) -> None:
        self._refresh(entry.get_text())

    def _selected_index(self) -> int | None:
        model, tree_iter = self.results.get_selection().get_selected()
        if model is None or tree_iter is None:
            return None
        path = model.get_path(tree_iter)
        indices = path.get_indices()
        return int(indices[0]) if indices else None

    def _move_selection(self, delta: int) -> None:
        if not self.visible_entries:
            return
        current = self._selected_index()
        index = 0 if current is None else max(0, min(len(self.visible_entries) - 1, current + delta))
        path = Gtk.TreePath.new_from_string(str(index))
        self.results.get_selection().select_path(path)
        self.results.scroll_to_cell(path, None, False, 0.0, 0.0)

    def _on_query_key_press(self, _entry: Gtk.Entry, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Down:
            self._move_selection(1)
            return True
        if event.keyval == Gdk.KEY_Up:
            self._move_selection(-1)
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._activate_selected()
            return True
        if event.keyval == Gdk.KEY_Escape:
            self._cancel()
            return True
        return False

    def _on_dialog_key_press(self, _dialog: Gtk.Dialog, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._cancel()
            return True
        return False

    def _on_row_activated(self, _tree: Gtk.TreeView, path: Gtk.TreePath, _column) -> None:
        self.results.get_selection().select_path(path)
        self._activate_selected()

    def _on_delete_event(self, *_args) -> bool:
        self._cancel()
        return True

    def _restore_previous_focus(self) -> None:
        focus = self.previous_focus
        if focus is not None:
            try:
                focus.grab_focus()
            except Exception:
                pass

    def _close(self, *, restore_focus: bool) -> None:
        if self.closed:
            return
        self.closed = True
        self.dialog.destroy()
        if restore_focus:
            self._restore_previous_focus()

    def _cancel(self) -> None:
        self._close(restore_focus=True)

    def _activate_selected(self) -> None:
        index = self._selected_index()
        if index is None or index >= len(self.visible_entries):
            return
        entry = self.visible_entries[index]
        resolved = _resolve_live_action(self.window, entry)
        if resolved is None:
            self._close(restore_focus=True)
            self.report_warning("The selected command is no longer available.")
            return
        action, target = resolved
        self._close(restore_focus=False)
        action.activate(target)


def show_command_palette(
    window: Gtk.ApplicationWindow,
    menubar: Gtk.MenuBar,
    report_warning: Callable[[str], None],
) -> CommandPalette | None:
    try:
        entries = collect_command_palette_entries(window, menubar)
    except CommandPaletteProjectionError as exc:
        report_warning(str(exc))
        return None
    palette = CommandPalette(window, entries, report_warning)
    palette.show()
    return palette
