"""Shared compact close chrome for Graphium Plus side panels."""
from __future__ import annotations

from collections.abc import Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


def build_compact_close_button(
    on_activate: Callable[[], None], *, name: str, tooltip: str
) -> Gtk.Button:
    """Build one small symbolic close button; caller owns visibility semantics."""
    if not callable(on_activate):
        raise TypeError("on_activate must be callable")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if not isinstance(tooltip, str) or not tooltip.strip():
        raise ValueError("tooltip must be a non-empty string")

    widget_name = name.strip()
    button = Gtk.Button()
    button.set_name(widget_name)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_focus_on_click(False)
    button.set_can_focus(True)
    button.set_size_request(18, 18)
    button.set_valign(Gtk.Align.CENTER)
    button.set_halign(Gtk.Align.END)
    button.set_tooltip_text(tooltip.strip())

    image = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
    if hasattr(image, "set_pixel_size"):
        image.set_pixel_size(10)
    button.add(image)

    provider = Gtk.CssProvider()
    selector = f"button#{widget_name}"
    provider.load_from_data(
        f"""
{selector},
{selector}:hover,
{selector}:active,
{selector}:checked,
{selector}:focus {{
    min-width: 16px;
    min-height: 16px;
    padding: 0;
    margin: 0;
    border: 0;
    border-radius: 2px;
    background: transparent;
    background-image: none;
    box-shadow: none;
    outline-width: 1px;
}}
""".encode("utf-8")
    )
    button.get_style_context().add_provider(
        provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 50
    )
    button._graphium_css_provider = provider
    button.connect("clicked", lambda *_: on_activate())
    try:
        button.get_accessible().set_name(tooltip.strip())
    except Exception:
        pass
    return button
