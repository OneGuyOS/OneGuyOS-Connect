"""
OneGuyOS Connect - UI Components
Flat squared tiles, clean cards, and responsive controls.
"""

import os
import subprocess
from datetime import datetime
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, Pango


class ActionTile(Gtk.Button):
    """Square action button styled for OneGuyOS"""

    def __init__(self, icon_name: str, title: str, subtitle: str, on_clicked):
        super().__init__()
        self.add_css_class("oneguy-action-tile")
        self.connect("clicked", lambda b: on_clicked())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(28)
        icon.add_css_class("tile-icon")
        box.append(icon)

        lbl = Gtk.Label(label=title)
        lbl.add_css_class("tile-label")
        box.append(lbl)

        if subtitle:
            sub = Gtk.Label(label=subtitle)
            sub.add_css_class("oneguy-subtitle")
            box.append(sub)

        self.set_child(box)


class RibbonAction(Gtk.Button):
    """Compact dashboard action for a single horizontal ribbon."""

    def __init__(self, icon_name: str, title: str, tooltip: str, on_clicked):
        super().__init__()
        self.add_css_class("oneguy-ribbon-action")
        self.set_tooltip_text(tooltip)
        self.connect("clicked", lambda _button: on_clicked())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(20)
        box.append(icon)

        label = Gtk.Label(label=title)
        label.add_css_class("oneguy-ribbon-label")
        label.set_max_width_chars(11)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(label)

        self.set_child(box)


class DeviceHeaderCard(Gtk.Box):
    """Device status overview card with battery and connectivity"""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("oneguy-card")

        # Top row: Device name and connectivity badge
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        
        icon = Gtk.Image.new_from_icon_name("phone-symbolic")
        icon.set_pixel_size(36)
        top_row.append(icon)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_box.set_hexpand(True)
        self.lbl_name = Gtk.Label(label="Searching for devices...")
        self.lbl_name.set_halign(Gtk.Align.START)
        self.lbl_name.add_css_class("oneguy-header")
        title_box.append(self.lbl_name)

        self.lbl_status = Gtk.Label(label="Scanning local network")
        self.lbl_status.set_halign(Gtk.Align.START)
        self.lbl_status.add_css_class("oneguy-subtitle")
        title_box.append(self.lbl_status)
        top_row.append(title_box)

        # Battery pill
        self.battery_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.battery_box.add_css_class("oneguy-battery-badge")
        self.battery_icon = Gtk.Image.new_from_icon_name("battery-symbolic")
        self.battery_box.append(self.battery_icon)
        self.lbl_battery = Gtk.Label(label="--%")
        self.battery_box.append(self.lbl_battery)
        top_row.append(self.battery_box)

        self.append(top_row)

        # Battery progress bar
        self.battery_progress = Gtk.ProgressBar()
        self.battery_progress.set_fraction(0.0)
        self.append(self.battery_progress)

    def update_device(self, info):
        if not info:
            self.lbl_name.set_text("No Device Connected")
            self.lbl_status.set_text("Ensure your phone has KDE Connect open on the same Wi-Fi")
            self.lbl_battery.set_text("--%")
            self.battery_progress.set_fraction(0.0)
            return

        self.lbl_name.set_text(info.display_name)
        status_text = f"{info.device_type.capitalize()} • "
        if info.is_reachable:
            status_text += "Connected & Reachable"
        elif info.is_paired:
            status_text += "Paired (Offline)"
        else:
            status_text += "Unpaired"
        self.lbl_status.set_text(status_text)

        if info.battery_charge >= 0:
            charge = info.battery_charge
            charging_str = " ⚡" if info.is_charging else ""
            self.lbl_battery.set_text(f"{charge}%{charging_str}")
            self.battery_progress.set_fraction(charge / 100.0)
            if info.is_charging:
                self.battery_icon.set_from_icon_name("battery-charging-symbolic")
            elif charge > 80:
                self.battery_icon.set_from_icon_name("battery-level-90-symbolic")
            elif charge > 50:
                self.battery_icon.set_from_icon_name("battery-level-60-symbolic")
            elif charge > 20:
                self.battery_icon.set_from_icon_name("battery-level-30-symbolic")
            else:
                self.battery_icon.set_from_icon_name("battery-level-10-symbolic")
        else:
            self.lbl_battery.set_text("Unknown")
            self.battery_progress.set_fraction(0.0)
