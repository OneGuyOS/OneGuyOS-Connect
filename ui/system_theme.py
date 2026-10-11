"""Bridge the active KDE color scheme into this GTK/Libadwaita process.

KDE exports the user's palette through ``kdeglobals``.  On this desktop the
GTK user stylesheet can lag behind that setting, so relying on GTK's named
colors alone can leave a GTK application on the previous dark palette.  This
module reads the active KDE colors at startup and exposes the standard GTK /
Libadwaita color names to the application.
"""

import configparser
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk


KDE_GLOBALS = Path.home() / ".config" / "kdeglobals"


def _rgb(parser, section, key, fallback):
    try:
        values = [int(part.strip()) for part in parser[section][key].split(",")[:3]]
        if len(values) == 3 and all(0 <= value <= 255 for value in values):
            return f"rgb({values[0]}, {values[1]}, {values[2]})"
    except (KeyError, TypeError, ValueError):
        pass
    return fallback


def install_system_theme():
    """Apply the current desktop palette to this application only."""
    display = Gdk.Display.get_default()
    if not display:
        return None

    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if KDE_GLOBALS.exists():
        parser.read(KDE_GLOBALS, encoding="utf-8")

    window_bg = _rgb(parser, "Colors:Window", "BackgroundNormal", "#ece9d8")
    window_fg = _rgb(parser, "Colors:Window", "ForegroundNormal", "#000000")
    view_bg = _rgb(parser, "Colors:View", "BackgroundNormal", "#ffffff")
    view_alt = _rgb(parser, "Colors:View", "BackgroundAlternate", "#f9f7ea")
    view_fg = _rgb(parser, "Colors:View", "ForegroundNormal", "#000000")
    muted_fg = _rgb(parser, "ColorEffects:Inactive", "Color", "#706f6e")
    button_bg = _rgb(parser, "Colors:Button", "BackgroundNormal", "#f2f0e3")
    button_fg = _rgb(parser, "Colors:Button", "ForegroundNormal", "#000000")
    accent_bg = _rgb(parser, "Colors:Selection", "BackgroundNormal", "#326ac4")
    accent_fg = _rgb(parser, "Colors:Selection", "ForegroundNormal", "#ffffff")
    destructive = _rgb(parser, "Colors:View", "ForegroundNegative", "#f45e37")
    header_bg = _rgb(parser, "WM", "activeBackground", accent_bg)
    header_fg = _rgb(parser, "WM", "activeForeground", accent_fg)

    # Keep Libadwaita on the light branch when KDE reports a light palette.
    # Luminance is sufficient here because KDE already supplies final colors.
    def luminance(color):
        values = [int(value) for value in color[4:-1].split(",")]
        return (values[0] * 299 + values[1] * 587 + values[2] * 114) / 1000

    if window_bg.startswith("rgb("):
        scheme = (
            Adw.ColorScheme.FORCE_LIGHT
            if luminance(window_bg) >= 128
            else Adw.ColorScheme.FORCE_DARK
        )
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    css = f"""
    @define-color window_bg_color {window_bg};
    @define-color window_fg_color {window_fg};
    @define-color view_bg_color {view_bg};
    @define-color view_fg_color {view_fg};
    @define-color headerbar_bg_color {header_bg};
    @define-color headerbar_fg_color {header_fg};
    @define-color card_bg_color {view_bg};
    @define-color card_fg_color {view_fg};
    @define-color popover_bg_color {view_bg};
    @define-color popover_fg_color {view_fg};
    @define-color accent_color {accent_bg};
    @define-color accent_bg_color {accent_bg};
    @define-color accent_fg_color {accent_fg};
    @define-color destructive_color {destructive};
    @define-color destructive_bg_color {destructive};
    @define-color destructive_fg_color #ffffff;
    @define-color borders alpha({window_fg}, 0.35);
    @define-color text_muted {muted_fg};

    window, .background {{
      background-color: {window_bg};
      color: {window_fg};
    }}

    headerbar {{
      background-color: {header_bg};
      color: {header_fg};
      border-bottom: 1px solid alpha({header_fg}, 0.45);
    }}

    headerbar button {{
      background-color: transparent;
      color: {header_fg};
      border-color: alpha({header_fg}, 0.45);
    }}

    headerbar button:hover {{
      background-color: alpha({header_fg}, 0.16);
    }}

    button {{
      background-color: {button_bg};
      color: {button_fg};
      border: 1px solid alpha({window_fg}, 0.45);
    }}

    button:hover {{
      border-color: {accent_bg};
      background-color: {view_alt};
    }}

    button.suggested-action, button.oneguy-primary-btn {{
      background-color: {accent_bg};
      color: {accent_fg};
      border-color: {accent_bg};
    }}

    button.destructive-action, button.oneguy-destructive-btn {{
      background-color: {destructive};
      color: #ffffff;
      border-color: {destructive};
    }}

    entry, textview, textview text, list, listview, row, spinbutton,
    dropdown > button, popover > contents {{
      background-color: {view_bg};
      color: {view_fg};
    }}

    entry, textview, spinbutton, dropdown > button {{
      border-color: alpha({window_fg}, 0.45);
    }}

    row:selected, selection {{
      background-color: {accent_bg};
      color: {accent_fg};
    }}

    toast {{
      background-color: {view_bg};
      color: {view_fg};
      border: 1px solid alpha({window_fg}, 0.45);
    }}

    toast button {{
      background-color: {button_bg};
      color: {button_fg};
      border-color: alpha({window_fg}, 0.45);
    }}

    .card, .boxed-list, .oneguy-card, .oneguy-device-pull,
    .oneguy-device-shade {{
      background-color: {view_bg};
      color: {view_fg};
      border-color: alpha({window_fg}, 0.35);
    }}

    progressbar trough {{ background-color: {button_bg}; }}
    progressbar progress {{ background-color: {accent_bg}; }}
    """

    provider = Gtk.CssProvider()
    provider.load_from_string(css)
    # The user's stale GTK override is installed at USER priority.  This
    # provider is scoped to this process and must win so the active KDE palette
    # shown by the rest of the desktop is authoritative.
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1
    )
    return provider
