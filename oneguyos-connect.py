#!/usr/bin/env python3
"""
OneGuyOS Connect - Primary Entry Point
Seamless Android & Device Integration for OneGuyOS.
"""

import sys
import os
import signal
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, Gio

# Ensure directory is on python path
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from backend.service import ConnectBackend
from ui.window import ConnectWindow
from ui.system_theme import install_system_theme


class OneGuyOSConnectApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.oneguyos.connect",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.backend = None
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.load_css()
        self.system_theme_provider = install_system_theme()
        self.backend = ConnectBackend()

    def do_activate(self):
        if not self.window:
            self.window = ConnectWindow(self, self.backend)
        self.window.present()

    def load_css(self):
        css_file = os.path.join(APP_DIR, "ui", "style.css")
        if os.path.exists(css_file):
            provider = Gtk.CssProvider()
            provider.load_from_path(css_file)
            display = Gdk.Display.get_default()
            if display:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )


def main():
    # Allow clean Ctrl+C in terminal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = OneGuyOSConnectApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
