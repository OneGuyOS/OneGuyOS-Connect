"""
OneGuyOS Connect - Main Application Window
"""

import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, Gdk, Pango

from backend.service import ConnectBackend, DeviceInfo, PhoneNotification
from backend.screen_mirror import ScreenMirrorManager
from backend.companion_discovery import CompanionDiscovery
from backend.companion_stream import discover_active_streams
from backend.bluetooth_discovery import BluetoothPhoneDiscovery
from ui.mirror_sidebar import MirrorSidebar
from ui.views import DeviceHeaderCard, RibbonAction
from ui.chat_view import AIChatView
from ui.recap_view import DailyRecapView
from ui.settings_view import SettingsView


class DeviceDashboardPanel(Gtk.Box):
    """One complete dashboard card and controls shade per paired phone."""

    def __init__(self, window, device: DeviceInfo):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.device = device
        self.add_css_class("oneguy-phone-panel")

        self.header_card = DeviceHeaderCard()
        self.header_card.add_css_class("connected-device-card")
        self.header_card.update_device(device)
        self.append(self.header_card)

        self.device_pull_button = Gtk.Button()
        self.device_pull_button.add_css_class("oneguy-device-pull")
        self.device_pull_button.set_tooltip_text("Pull down device controls")
        self.device_pull_icon = Gtk.Image.new_from_icon_name("pan-down-symbolic")
        pull_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pull_row.set_halign(Gtk.Align.CENTER)
        pull_label = Gtk.Label(label="Device controls")
        pull_label.add_css_class("oneguy-ribbon-label")
        pull_row.append(pull_label)
        pull_row.append(self.device_pull_icon)
        self.device_pull_button.set_child(pull_row)
        self.device_pull_button.connect("clicked", self.on_toggle_controls)
        self.append(self.device_pull_button)

        self.device_controls_revealer = Gtk.Revealer()
        self.device_controls_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self.device_controls_revealer.set_transition_duration(220)
        self.device_controls_revealer.set_reveal_child(False)

        controls_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        controls_panel.add_css_class("oneguy-device-shade")
        controls_grid = Gtk.Grid()
        controls_grid.set_column_spacing(6)
        controls_grid.set_row_spacing(6)
        controls_grid.set_column_homogeneous(True)
        controls_panel.append(controls_grid)

        actions = (
            ("network-transmit-receive-symbolic", "Ping", "Test this phone connection", window.action_send_ping),
            ("audio-volume-muted-symbolic", "Mute", "Mute this phone", window.action_mute_device),
            ("edit-paste-symbolic", "Clipboard", "Push clipboard text to this phone", window.action_send_clipboard),
            ("document-send-symbolic", "Send File", "Send a photo or document", window.action_send_file),
            ("folder-remote-symbolic", "Browse", "Browse this phone storage over SFTP", window.action_browse_files),
            ("mail-send-symbolic", "Send Note", "Send a note or URL to this phone", window.action_send_note_dialog),
            ("phone-symbolic", "Open Phone", "Launch this phone screen for interactive use", window.action_mirror_screen),
        )
        for index, (icon_name, title, tooltip, callback) in enumerate(actions):
            action = RibbonAction(
                icon_name,
                title,
                tooltip,
                lambda fn=callback, target=device: fn(target),
            )
            controls_grid.attach(action, index % 3, index // 3, 1, 1)

        self.notification_stack = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0
        )
        self.notification_stack.add_css_class("oneguy-notification-stack")
        controls_panel.append(self.notification_stack)

        self.notification_preview_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0
        )
        self.notification_stack.append(self.notification_preview_list)

        self.notification_pull_button = Gtk.Button()
        self.notification_pull_button.add_css_class("oneguy-notification-toggle")
        self.notification_pull_button.set_tooltip_text("Show all phone notifications")
        self.notification_pull_button.connect("clicked", self.on_toggle_notifications)
        notification_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        notification_row.set_halign(Gtk.Align.CENTER)
        notification_row.append(
            Gtk.Image.new_from_icon_name("mail-unread-symbolic")
        )
        self.notification_pull_label = Gtk.Label(label="All notifications")
        self.notification_pull_label.add_css_class("oneguy-ribbon-label")
        self.notification_pull_arrow = Gtk.Image.new_from_icon_name(
            "pan-down-symbolic"
        )
        notification_row.append(self.notification_pull_label)
        notification_row.append(self.notification_pull_arrow)
        self.notification_pull_button.set_child(notification_row)
        self.notification_stack.append(self.notification_pull_button)

        self.notification_revealer = Gtk.Revealer()
        self.notification_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self.notification_revealer.set_transition_duration(220)
        self.notification_revealer.set_reveal_child(False)
        notification_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        notification_panel.add_css_class("oneguy-notification-expanded")
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading = Gtk.Label(label=f"{device.name} notifications")
        heading.set_halign(Gtk.Align.START)
        heading.set_hexpand(True)
        heading.add_css_class("heading")
        toolbar.append(heading)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Refresh phone notifications")
        refresh.connect(
            "clicked", lambda _button: self.reload_notifications(show_status=True)
        )
        toolbar.append(refresh)
        notification_panel.append(toolbar)

        self.notification_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        notification_panel.append(self.notification_list)
        self.notification_revealer.set_child(notification_panel)
        self.notification_stack.append(self.notification_revealer)

        self.device_controls_revealer.set_child(controls_panel)
        self.append(self.device_controls_revealer)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-end", self.on_controls_drag_end)
        self.header_card.add_controller(drag)
        self.reload_notifications()

    def update_device(self, device: DeviceInfo):
        self.device = device
        self.header_card.update_device(device)

    def on_toggle_controls(self, _button):
        self.set_controls_expanded(
            not self.device_controls_revealer.get_reveal_child()
        )

    def on_controls_drag_end(self, _gesture, _offset_x, offset_y):
        if offset_y >= 24:
            self.set_controls_expanded(True)
        elif offset_y <= -24:
            self.set_controls_expanded(False)

    def set_controls_expanded(self, expanded: bool):
        self.device_controls_revealer.set_reveal_child(expanded)
        self.device_pull_icon.set_from_icon_name(
            "pan-up-symbolic" if expanded else "pan-down-symbolic"
        )
        self.device_pull_button.set_tooltip_text(
            "Pull up device controls" if expanded else "Pull down device controls"
        )

    def on_toggle_notifications(self, _button):
        expanded = not self.notification_revealer.get_reveal_child()
        self.notification_revealer.set_reveal_child(expanded)
        self.notification_pull_arrow.set_from_icon_name(
            "pan-up-symbolic" if expanded else "pan-down-symbolic"
        )
        self.notification_pull_button.set_tooltip_text(
            "Hide all phone notifications" if expanded else "Show all phone notifications"
        )
        if expanded:
            self.reload_notifications()

    def reload_notifications(self, show_status=False):
        notifications = []
        if self.device and self.device.is_reachable:
            notifications = self.window.backend.get_notifications(self.device.id)
        notifications.sort(
            key=lambda item: int(item.id) if item.id.isdigit() else -1,
            reverse=True,
        )
        self.notification_pull_label.set_text(
            f"All notifications • {len(notifications)}"
            if notifications
            else "All notifications"
        )

        self._clear_box(self.notification_preview_list)
        self._clear_box(self.notification_list)
        if not self.device.is_reachable:
            self._status("Phone is offline", self.notification_preview_list)
            self._status("Phone is offline", self.notification_list)
        elif not notifications:
            self._status("No new phone notifications", self.notification_preview_list)
            self._status(
                "No phone notifications · Check Notification sync in KDE Connect",
                self.notification_list,
            )
        else:
            by_app = {}
            for item in notifications:
                by_app.setdefault(item.app_name, []).append(item)
            preview_apps = []
            for item in notifications:
                if item.app_name not in preview_apps:
                    preview_apps.append(item.app_name)
                if len(preview_apps) == 2:
                    break
            for index, app_name in enumerate(preview_apps):
                app_items = by_app[app_name]
                row = self.window._build_notification_row(
                    app_items[0], len(app_items), compact=True, device_id=self.device.id
                )
                if index == len(preview_apps) - 1:
                    row.add_css_class("oneguy-notification-preview-last")
                self.notification_preview_list.append(row)
            for item in notifications:
                self.notification_list.append(
                    self.window._build_notification_row(
                        item, device_id=self.device.id
                    )
                )
        if show_status:
            count = len(notifications)
            self.window.show_toast(
                f"{self.device.name}: {count} notification"
                f"{'s' if count != 1 else ''}"
            )

    @staticmethod
    def _clear_box(box):
        child = box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            box.remove(child)
            child = next_child

    @staticmethod
    def _status(message, parent):
        label = Gtk.Label(label=message)
        label.set_wrap(True)
        label.set_halign(Gtk.Align.START)
        label.add_css_class("dim-label")
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        parent.append(label)


class ConnectWindow(Adw.ApplicationWindow):
    def __init__(self, app, backend: ConnectBackend):
        super().__init__(application=app, title="OneGuyOS Connect")
        self.backend = backend
        self.screen_mirror = ScreenMirrorManager()
        self.current_device: DeviceInfo = None
        self.devices = []
        self.device_panels = {}
        self._mirror_auto_seen = set()
        self._companions = {}
        self._companion_rows = {}
        self._bluetooth_rows = {}
        self._device_model_profiles = {
            device_id: (profile.get("manufacturer", ""), profile.get("model", ""))
            for device_id, profile in self.screen_mirror.model_profiles.items()
            if profile.get("model")
        }
        self._last_device_model_scan = 0.0
        self._device_model_scan_running = False

        self.set_default_size(940, 700)
        self.set_size_request(360, 320)

        # Main layout with Toast overlay
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.toast_overlay.set_child(main_box)

        # Header bar with switchable View Dropdown
        header = Adw.HeaderBar()
        self.view_stack = Adw.ViewStack()
        self.view_stack.set_vexpand(True)
        self.view_stack.set_hexpand(True)

        # Main sections. Provider management lives inside the AI workspace.
        view_names = ["Device Dashboard", "AI Chat Workspace", "Daily Chat Recap"]
        self.view_dropdown = Gtk.DropDown.new_from_strings(view_names)
        self.view_dropdown.set_selected(0)
        self.view_dropdown.connect("notify::selected", self.on_view_dropdown_changed)
        header.set_title_widget(self.view_dropdown)

        # Emergency Kill Switch button
        btn_kill = Gtk.Button(icon_name="process-stop-symbolic")
        btn_kill.add_css_class("oneguy-destructive-btn")
        btn_kill.set_tooltip_text("EMERGENCY KILL SWITCH: Immediately kill all connection processes and stop ringing")
        btn_kill.connect("clicked", self.on_emergency_kill_clicked)
        header.pack_end(btn_kill)

        # Refresh button
        btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_refresh.set_tooltip_text("Refresh network & device status")
        btn_refresh.connect("clicked", self.on_refresh_clicked)
        header.pack_end(btn_refresh)

        main_box.append(header)
        main_box.append(self.view_stack)

        # Tab 1: Device Dashboard
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content_box.set_margin_top(16)
        content_box.set_margin_bottom(16)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        scroller.set_child(content_box)

        self.device_panels_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        content_box.append(self.device_panels_box)
        companion_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        companion_section.add_css_class("oneguy-card")
        companion_title = Gtk.Label(label="Nearby phone discovery")
        companion_title.set_halign(Gtk.Align.START)
        companion_title.add_css_class("heading")
        companion_section.append(companion_title)
        companion_hint = Gtk.Label(
            label="OneGuyOS companions appear over Wi-Fi. Bluetooth names recognized by this computer are listed separately; that alone does not mean a phone is connected."
        )
        companion_hint.set_halign(Gtk.Align.START)
        companion_hint.set_wrap(True)
        companion_hint.add_css_class("dim-label")
        companion_section.append(companion_hint)
        self.companion_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        companion_section.append(self.companion_list_box)
        self.companion_empty = Gtk.Label(label="No additional companion phones found.")
        self.companion_empty.set_halign(Gtk.Align.START)
        self.companion_empty.add_css_class("dim-label")
        self.companion_list_box.append(self.companion_empty)
        content_box.append(companion_section)

        self.mirror_sidebar = MirrorSidebar(self)
        dashboard_pane = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.dashboard_pane = dashboard_pane
        dashboard_pane.set_wide_handle(True)
        dashboard_pane.set_start_child(scroller)
        dashboard_pane.set_end_child(self.mirror_sidebar)
        dashboard_pane.set_resize_start_child(True)
        dashboard_pane.set_resize_end_child(False)
        dashboard_pane.set_shrink_end_child(False)
        dashboard_pane.set_position(620)
        self.view_stack.add_titled_with_icon(
            dashboard_pane, "dashboard", "Dashboard", "phone-symbolic"
        )

        # Tab 2: AI workspace. Provider management opens from Chat controls.
        self.ai_workspace = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.ai_stack = Adw.ViewStack()
        self.ai_stack.set_vexpand(True)
        self.ai_stack.set_hexpand(True)
        self.ai_workspace.append(self.ai_stack)

        self.chat_view = AIChatView(
            self.backend,
            lambda: self.current_device,
            self.show_toast,
            open_provider_manager_fn=self.open_provider_manager,
        )
        self.ai_stack.add_named(self.chat_view, "chat")

        self.settings_view = SettingsView(
            self.backend,
            self.show_toast,
            on_settings_changed_fn=self.chat_view.update_dropdown_items,
            ai_manager=self.chat_view.ai_manager,
            on_back_fn=self.close_provider_manager,
        )
        self.ai_stack.add_named(self.settings_view, "providers")

        self.view_stack.add_titled_with_icon(
            self.ai_workspace, "chat", "AI Chat", "avatar-default-symbolic"
        )

        self.recap_view = DailyRecapView(self.show_toast)
        self.view_stack.add_titled_with_icon(
            self.recap_view, "recap", "Daily Recap", "view-list-symbolic"
        )

        self.empty_devices_label = Gtk.Label(
            label="No phone connected. Pair a phone with KDE Connect to get started."
        )
        self.empty_devices_label.set_wrap(True)
        self.empty_devices_label.add_css_class("dim-label")
        self.empty_devices_label.set_margin_top(24)
        self.empty_devices_label.set_margin_bottom(24)
        self.device_panels_box.append(self.empty_devices_label)

        # 3. Status Bar
        self.lbl_status_bar = Gtk.Label(label="Ready")
        self.lbl_status_bar.set_halign(Gtk.Align.START)
        self.lbl_status_bar.add_css_class("status-bar-label")
        main_box.append(self.lbl_status_bar)

        # Auto-refresh timer (every 8 seconds)
        GLib.timeout_add_seconds(8, self.on_poll_timer)

        # Initial device load
        self.reload_devices()
        self.companion_discovery = CompanionDiscovery(self._on_companion_update)
        self.companion_discovery.start()
        self.bluetooth_discovery = BluetoothPhoneDiscovery(self._on_bluetooth_update)
        self.bluetooth_discovery.start()
        self.connect("close-request", self._on_close_request)

    def _on_companion_update(self, profile, online):
        GLib.idle_add(self._apply_companion_update, profile, online)

    def _on_bluetooth_update(self, devices):
        GLib.idle_add(self._apply_bluetooth_update, devices)

    def _apply_bluetooth_update(self, devices):
        current = {f"bt:{address}": name for address, name in devices.items()}
        for key in set(self._bluetooth_rows) - set(current):
            self.companion_list_box.remove(self._bluetooth_rows.pop(key))
        for key, name in current.items():
            row = self._bluetooth_rows.get(key)
            if row is None:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                label = Gtk.Label()
                label.set_halign(Gtk.Align.START)
                label.set_hexpand(True)
                label.set_wrap(True)
                row.append(label)
                self._bluetooth_rows[key] = row
                self.companion_list_box.append(row)
            row.get_first_child().set_text(f"{name} · Bluetooth recognized; not connected")
        self.companion_empty.set_visible(not self._companion_rows and not self._bluetooth_rows)
        return False

    def _apply_companion_update(self, profile, online):
        device_id = profile.get("id")
        if not device_id:
            return False
        if not online:
            self._companions.pop(device_id, None)
            row = self._companion_rows.pop(device_id, None)
            if row:
                self.companion_list_box.remove(row)
            self.companion_empty.set_visible(not self._companion_rows and not self._bluetooth_rows)
            return False

        self._companions[device_id] = profile
        row = self._companion_rows.get(device_id)
        if row is None:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label()
            label.set_halign(Gtk.Align.START)
            label.set_hexpand(True)
            label.set_wrap(True)
            row.append(label)
            self._companion_rows[device_id] = row
            self.companion_list_box.append(row)
        label = row.get_first_child()
        paired = self._companion_matches_paired_device(profile)
        suffix = "Already on dashboard · Wi-Fi" if paired else "Nearby · Wi-Fi"
        label.set_text(f"{profile.get('name', 'Android phone')} · {suffix}")
        self.companion_empty.set_visible(False)
        return False

    def _companion_matches_paired_device(self, profile):
        maker = profile.get("manufacturer", "").casefold()
        model = "".join(ch for ch in profile.get("name", "").casefold() if ch.isalnum())
        for device in self.devices:
            existing = "".join(ch for ch in device.display_name.casefold() if ch.isalnum())
            if model and (model in existing or existing in model):
                return True
            if maker == "motorola" and "moto" in existing:
                return True
            if maker == "samsung" and "galaxy" in existing:
                return True
        return False

    def _on_close_request(self, *_args):
        self.companion_discovery.stop()
        self.bluetooth_discovery.stop()
        return False

    def show_toast(self, message: str):
        toast = Adw.Toast.new(message)
        toast.set_timeout(3)
        self.toast_overlay.add_toast(toast)
        self.lbl_status_bar.set_text(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def reload_devices(self):
        devices = self.backend.get_devices()
        for device in devices:
            profile = self._device_model_profiles.get(device.id)
            if profile:
                device.manufacturer, device.hardware_model = profile
        newly_reachable = [
            device for device in devices
            if device.is_reachable and device.id not in self._mirror_auto_seen
        ]
        self.devices = devices
        self.current_device = devices[0] if devices else None
        self.mirror_sidebar.set_phones(devices)
        self.empty_devices_label.set_visible(not devices)

        active_ids = {device.id for device in devices}
        for device_id in list(self.device_panels):
            if device_id not in active_ids:
                panel = self.device_panels.pop(device_id)
                self.device_panels_box.remove(panel)

        for device in devices:
            panel = self.device_panels.get(device.id)
            if panel is None:
                panel = DeviceDashboardPanel(self, device)
                self.device_panels[device.id] = panel
                self.device_panels_box.append(panel)
            else:
                panel.update_device(device)
                panel.reload_notifications()

        # When a phone first appears, open the docked panel and try its saved
        # ADB mapping automatically. Restore the user's last selected phone on
        # startup rather than switching to whichever phone KDE lists last.
        for device in newly_reachable:
            self._mirror_auto_seen.add(device.id)
        if newly_reachable:
            last_phone = next(
                (
                    device for device in devices
                    if device.id == self.mirror_sidebar._last_phone_id
                ),
                None,
            )
            self.mirror_sidebar.show_panel(last_phone or newly_reachable[-1])
        self._schedule_device_model_scan(devices)

    def _schedule_device_model_scan(self, devices):
        """Refresh friendly labels from live Android identity without blocking GTK."""
        now = time.monotonic()
        if self._device_model_scan_running or now - self._last_device_model_scan < 60:
            return
        self._last_device_model_scan = now
        self._device_model_scan_running = True
        snapshot = list(devices)

        def scan():
            profiles = {}
            result = self.screen_mirror.discover()
            if not result.get("error"):
                for device in snapshot:
                    target = self.screen_mirror.resolve(device, result["devices"])
                    if not target:
                        continue
                    profile = self.screen_mirror.profile(
                        result["adb"], target["serial"]
                    )
                    model = profile.get("model", "").strip()
                    manufacturer = profile.get("manufacturer", "").strip()
                    if not model or not manufacturer:
                        continue
                    # Samsung's ro.product.model is a hardware code; KDE's
                    # existing Galaxy name is already the user-friendly one.
                    if model.upper().startswith("SM-"):
                        continue
                    if manufacturer.lower() == "motorola":
                        model = " ".join(model.replace("_", " ").split()).title()
                    self.screen_mirror.remember_profile(
                        device.id, manufacturer, model
                    )
                    profiles[device.id] = (manufacturer, model)
            GLib.idle_add(self._finish_device_model_scan, profiles)

        threading.Thread(target=scan, daemon=True).start()

    def _finish_device_model_scan(self, profiles):
        self._device_model_scan_running = False
        self._device_model_profiles.update(profiles)
        for device in self.devices:
            profile = self._device_model_profiles.get(device.id)
            if not profile:
                continue
            device.manufacturer, device.hardware_model = profile
            panel = self.device_panels.get(device.id)
            if panel:
                panel.update_device(device)
        self.mirror_sidebar.set_phones(self.devices)
        return False

    def _build_notification_row(
        self,
        notification: PhoneNotification,
        app_count=1,
        compact=False,
        device_id=None,
    ):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("oneguy-notification-row")
        if compact:
            row.add_css_class("oneguy-notification-preview")

        if notification.icon_path and os.path.isfile(notification.icon_path):
            icon = Gtk.Image.new_from_file(notification.icon_path)
        else:
            icon = Gtk.Image.new_from_icon_name("mail-unread-symbolic")
        icon.set_pixel_size(32)
        icon.set_valign(Gtk.Align.START)
        row.append(icon)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        app_text = notification.app_name
        if app_count > 1:
            app_text += f" · {app_count} notifications"
        app_label = Gtk.Label(label=app_text)
        app_label.set_halign(Gtk.Align.START)
        app_label.add_css_class("caption")
        app_label.add_css_class("dim-label")
        text_box.append(app_label)

        title_label = Gtk.Label(label=notification.title)
        title_label.set_halign(Gtk.Align.START)
        title_label.set_wrap(not compact)
        title_label.set_xalign(0)
        if compact:
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            title_label.set_max_width_chars(48)
        title_label.add_css_class("heading")
        text_box.append(title_label)

        if notification.text:
            body_label = Gtk.Label(label=notification.text)
            body_label.set_halign(Gtk.Align.START)
            body_label.set_wrap(True)
            body_label.set_xalign(0)
            body_label.set_max_width_chars(48)
            if compact:
                body_label.set_ellipsize(Pango.EllipsizeMode.END)
            body_label.add_css_class("dim-label")
            text_box.append(body_label)
        row.append(text_box)

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        actions.set_valign(Gtk.Align.START)
        if notification.repliable and not compact:
            reply_button = Gtk.Button(icon_name="mail-reply-sender-symbolic")
            reply_button.add_css_class("flat")
            reply_button.set_tooltip_text("Reply from OneGuyOS")
            reply_button.connect(
                "clicked",
                lambda _button, item=notification, target_id=device_id:
                self._show_notification_reply(item, target_id),
            )
            actions.append(reply_button)
        if notification.dismissable and not compact:
            dismiss_button = Gtk.Button(icon_name="window-close-symbolic")
            dismiss_button.add_css_class("flat")
            dismiss_button.set_tooltip_text("Dismiss on phone")
            dismiss_button.connect(
                "clicked",
                lambda _button, item=notification, target_id=device_id:
                self._dismiss_notification(item, target_id),
            )
            actions.append(dismiss_button)
        if actions.get_first_child():
            row.append(actions)
        return row

    def _dismiss_notification(
        self, notification: PhoneNotification, device_id: str = None
    ):
        device_id = device_id or (
            self.current_device.id if self.current_device else None
        )
        if not device_id:
            return
        if self.backend.dismiss_notification(device_id, notification.id):
            self.show_toast(f"Dismissed {notification.app_name} notification")
            GLib.timeout_add(250, self._refresh_notifications_for_device, device_id)
        else:
            self.show_toast("Could not dismiss notification")

    def _refresh_notifications_for_device(self, device_id: str):
        panel = self.device_panels.get(device_id)
        if panel:
            panel.reload_notifications()
        return False

    def _show_notification_reply(
        self, notification: PhoneNotification, device_id: str = None
    ):
        device_id = device_id or (
            self.current_device.id if self.current_device else None
        )
        if not device_id:
            return
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Reply to {notification.app_name}",
            body=notification.title,
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("send", "Send Reply")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Type a reply…")
        entry.set_activates_default(True)
        entry.set_margin_top(8)
        entry.set_margin_bottom(8)
        entry.set_margin_start(16)
        entry.set_margin_end(16)
        dialog.set_extra_child(entry)
        dialog.set_default_response("send")

        def on_response(_dialog, response):
            message = entry.get_text().strip()
            if response != "send" or not message:
                return
            if self.backend.reply_to_notification(
                device_id, notification.id, message
            ):
                self.show_toast("Reply sent through the phone")
            else:
                self.show_toast("Could not send reply")

        dialog.connect("response", on_response)
        dialog.present()

    def on_poll_timer(self):
        self.reload_devices()
        self.mirror_sidebar.check_connection_profile(self.screen_mirror)
        return True

    def on_emergency_kill_clicked(self, btn):
        helper = shutil.which("oneguyos-connect-kill")
        if not helper:
            self.show_toast("Connection reset helper is not installed")
            return
        self.show_toast("Resetting connections...")
        subprocess.run([helper], check=False)
        self.reload_devices()

    def on_view_dropdown_changed(self, dropdown, param):
        idx = dropdown.get_selected()
        if idx == 0:
            self.view_stack.set_visible_child_name("dashboard")
        elif idx == 1:
            self.view_stack.set_visible_child_name("chat")
        elif idx == 2:
            self.view_stack.set_visible_child_name("recap")
    def open_provider_manager(self):
        """Open the full provider editor inside the AI workspace."""
        self.view_dropdown.set_selected(1)
        self.view_stack.set_visible_child_name("chat")
        self.settings_view.update_provider_list()
        self.settings_view.populate_form_from_active()
        self.ai_stack.set_visible_child_name("providers")

    def close_provider_manager(self):
        self.chat_view.update_dropdown_items()
        self.ai_stack.set_visible_child_name("chat")

    def on_refresh_clicked(self, btn):
        self.backend.refresh_discovery()
        self.reload_devices()
        self.show_toast("Refreshed network & devices")

    # --- Actions ---

    def action_mirror_screen(self, device=None, popout=True):
        device = device or self.current_device
        if not device:
            self.show_toast("No device connected")
            return
        self.mirror_sidebar.set_phones(self.devices)
        self.mirror_sidebar.show_panel(device, popout=popout)

    def start_mirror_for_phone(self, device):
        self._discover_mirror(device)

    def _discover_mirror(self, device):
        self.mirror_sidebar.set_status(
            f"Checking {device.name}'s wired screen connection…"
        )
        def discover():
            # Prefer an already-authorized USB/Wireless ADB connection. It is
            # immediate and needs no companion pairing code. Use the companion
            # flow only when this phone has no matching ADB connection.
            adb_result = self.screen_mirror.discover()
            if not adb_result.get("error"):
                adb_target = self.screen_mirror.resolve(
                    device, adb_result["devices"]
                )
                if adb_target:
                    GLib.idle_add(
                        self._finish_mirror_discovery, device, adb_result
                    )
                    return

            GLib.idle_add(
                self.mirror_sidebar.set_status,
                f"Looking for {device.name}'s screen-sharing companion…",
            )
            streams = discover_active_streams(timeout=3.0)
            matching = [
                profile for profile in streams
                if self._companion_stream_matches(device, profile)
            ]
            if len(matching) == 1:
                GLib.idle_add(
                    self._request_companion_pairing, device, matching[0]
                )
                return
            if not matching and len(streams) == 1:
                # The owner opened a specific phone card and only one phone is
                # actively sharing; let its on-phone code confirm the mapping.
                GLib.idle_add(
                    self._request_companion_pairing, device, streams[0]
                )
                return
            if len(matching) > 1:
                GLib.idle_add(self._choose_companion_stream, device, matching)
                return
            GLib.idle_add(self._finish_mirror_discovery, device, adb_result)

        threading.Thread(target=discover, daemon=True).start()

    @staticmethod
    def _companion_stream_matches(device, profile):
        def normalized(value):
            return "".join(ch for ch in value.casefold() if ch.isalnum())

        phone_name = normalized(f"{device.display_name} {device.name}")
        stream_name = normalized(
            f"{profile.get('manufacturer', '')} {profile.get('model', '')}"
        )
        model = normalized(profile.get("model", ""))
        if model and len(model) >= 5 and model in phone_name:
            return True
        if phone_name and len(phone_name) >= 5 and phone_name in stream_name:
            return True
        manufacturer = profile.get("manufacturer", "").casefold()
        if manufacturer == "samsung" and "galaxy" in phone_name:
            return True
        return False

    def _request_companion_pairing(self, device, profile):
        if self.mirror_sidebar.active_phone_id != device.id:
            return False
        code = Gtk.Entry()
        code.set_input_purpose(Gtk.InputPurpose.DIGITS)
        code.set_max_length(6)
        code.set_placeholder_text("Six-digit code from the phone")
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Connect to {profile['manufacturer']} {profile['model']}",
            body=(
                f"Enter the code shown in OneGuyOS Connect on the phone selected as "
                f"{device.display_name}. Android screen sharing must be active."
            ),
        )
        dialog.set_extra_child(code)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", "Connect")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")

        def on_response(_dialog, response):
            value = code.get_text().strip()
            dialog.destroy()
            if response != "connect":
                self.mirror_sidebar.set_status("Phone screen pairing canceled.")
                return
            if self.mirror_sidebar.active_phone_id != device.id:
                return
            self.mirror_sidebar.start_companion_stream(
                profile, value, device.display_name
            )

        dialog.connect("response", on_response)
        dialog.present()
        return False

    def _choose_companion_stream(self, device, profiles):
        if self.mirror_sidebar.active_phone_id != device.id:
            return False
        choices = [
            f"{profile['manufacturer']} {profile['model']} · {profile['address']}"
            for profile in profiles
        ]
        selector = Gtk.DropDown.new_from_strings(choices)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Choose the phone screen",
            body=f"Select the active companion for {device.display_name}.",
        )
        dialog.set_extra_child(selector)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("choose", "Continue")
        dialog.set_response_appearance("choose", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_dialog, response):
            selected = selector.get_selected()
            dialog.destroy()
            if response == "choose" and selected < len(profiles):
                self._request_companion_pairing(device, profiles[selected])

        dialog.connect("response", on_response)
        dialog.present()
        return False

    def _finish_mirror_discovery(self, device, result):
        # A phone switch can happen while ADB discovery runs in the background.
        # Ignore late results so a previous phone never replaces the new screen.
        if self.mirror_sidebar.active_phone_id != device.id:
            return False
        if result.get("error"):
            self.mirror_sidebar._update_setup_ui()
            self.mirror_sidebar.set_status(result["error"])
            return False

        devices = result["devices"]
        if not devices:
            if result["unauthorized"]:
                message = (
                    "Unlock this phone and tap Allow on the USB debugging "
                    "prompt. Then tap Mirror again."
                )
            else:
                message = (
                    "Connect by USB and approve the phone's USB debugging "
                    "prompt. For Wi-Fi, pair Wireless debugging with ADB first."
                )
            if not ScreenMirrorManager.video_device_ready():
                message += " Use Set up mirroring below for the one-time desktop setup."
                self.mirror_sidebar._update_setup_ui()
            self.mirror_sidebar.set_status(message)
            return False

        target = self.screen_mirror.resolve(device, devices)
        if target:
            self._launch_phone_mirror(device, result["adb"], target["serial"])
        elif device.id in self.screen_mirror.mappings:
            self.mirror_sidebar.set_status(
                f"{device.name} is still paired through KDE Connect, but its "
                "screen connection is unavailable. Reconnect this phone's "
                "screen link; another phone will not be substituted."
            )
        else:
            self._choose_mirror_device(device, result["adb"], devices)
        return False

    def _show_mirror_help(self, message):
        self.mirror_sidebar.show_panel()
        self.mirror_sidebar.set_status(message)

    def _choose_mirror_device(self, phone, adb_path, adb_devices):
        choices = [
            f"{item['label']} · {item['serial']}" for item in adb_devices
        ]
        selector = Gtk.DropDown.new_from_strings(choices)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Choose the ADB connection for {phone.name}",
            body="This phone-to-computer mapping will be saved for next time.",
        )
        dialog.set_extra_child(selector)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("mirror", "Mirror")
        dialog.set_response_appearance("mirror", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("mirror")

        def on_response(_dialog, response):
            if response != "mirror":
                return
            if self.mirror_sidebar.active_phone_id != phone.id:
                return
            selected = selector.get_selected()
            if selected >= len(adb_devices):
                self.show_toast("Choose a phone first")
                return
            target = adb_devices[selected]
            self.screen_mirror.save_mapping(phone.id, target["serial"])
            self._launch_phone_mirror(phone, adb_path, target["serial"])

        dialog.connect("response", on_response)
        dialog.present()

    def _launch_phone_mirror(self, phone, adb_path, serial):
        if self.mirror_sidebar.active_phone_id != phone.id:
            return
        try:
            self.mirror_sidebar.start_stream(adb_path, serial, phone.name)
        except OSError as error:
            self.mirror_sidebar.set_status(
                f"Could not start screen mirroring: {error}"
            )

    def action_send_ping(self, device=None):
        device = device or self.current_device
        if not device:
            self.show_toast("No device connected")
            return
        success = self.backend.send_ping(device.id, "OneGuyOS Ping")
        if success:
            self.show_toast(f"Ping sent to {device.name}")
        else:
            self.show_toast("Failed to send ping")

    def action_mute_device(self, device=None):
        device = device or self.current_device
        if not device:
            self.show_toast("No device connected")
            return
        success = self.backend.mute_device(device.id, True)
        if success:
            self.show_toast(f"Muted {device.name}")
        else:
            self.show_toast("Unable to mute the connected device")

    def action_send_clipboard(self, device=None):
        device = device or self.current_device
        if not device:
            self.show_toast("No device connected")
            return
        success = self.backend.send_clipboard(device.id)
        if success:
            self.show_toast(f"Clipboard synced to {device.name}")
        else:
            self.show_toast("Failed to sync clipboard")

    def action_send_file(self, device=None):
        device = device or self.current_device
        if not device:
            self.show_toast("No device connected")
            return

        file_dialog = Gtk.FileDialog.new()
        file_dialog.set_title(f"Select file to send to {device.name}")

        def on_file_selected(dialog, result):
            try:
                gfile = dialog.open_finish(result)
                if gfile:
                    path = gfile.get_path()
                    if path:
                        ok = self.backend.share_file(device.id, path)
                        if ok:
                            self.show_toast(f"Sent {os.path.basename(path)}")
                        else:
                            self.show_toast("Failed to send file")
            except Exception as e:
                # Cancelled or error
                pass

        file_dialog.open(self, None, on_file_selected)

    def action_browse_files(self, device=None):
        device = device or self.current_device
        if not device:
            self.show_toast("No device connected")
            return

        self.show_toast(f"Connecting to {device.name} storage...")
        threading.Thread(
            target=self._do_browse_files,
            args=(device.id, device.name),
            daemon=True,
            name="phone-sftp-browse",
        ).start()

    def _do_browse_files(self, device_id: str, device_name: str):
        # KDE Connect's startBrowsing() already mounts and waits for the phone.
        # Run it off the GTK thread and avoid issuing a second blocking mount.
        success = self.backend.browse_sftp(device_id)
        if success:
            GLib.idle_add(self.show_toast, f"Opened {device_name} storage in file manager")
        else:
            error = self.backend.sftp_mount_error(device_id)
            if "permissions missing" in error.casefold():
                message = f"{device_name}: allow filesystem access in KDE Connect on the phone, then retry"
            elif "device not responding" in error.casefold():
                message = f"{device_name} did not answer the file request; open KDE Connect on the phone and retry"
            elif error:
                message = f"Could not browse {device_name}: {error}"
            else:
                message = f"Could not browse {device_name}; check its KDE Connect file access settings"
            GLib.idle_add(self.show_toast, message)

    def action_send_note_dialog(self, device=None):
        device = device or self.current_device
        if not device:
            self.show_toast("No device connected")
            return

        # Simple entry dialog
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Send Link or Note",
            body=f"Type a web link or message to open instantly on {device.name}.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("send", "Send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry()
        entry.set_placeholder_text("https://example.com or quick note...")
        entry.set_margin_top(8)
        entry.set_margin_bottom(8)
        entry.set_margin_start(16)
        entry.set_margin_end(16)
        dialog.set_extra_child(entry)

        def on_response(dlg, response):
            if response == "send":
                text = entry.get_text().strip()
                if text:
                    if text.startswith("http://") or text.startswith("https://"):
                            ok = self.backend.share_url(device.id, text)
                    else:
                            ok = self.backend.share_text(device.id, text)
                    if ok:
                        self.show_toast("Sent note/link to phone")
                    else:
                        self.show_toast("Failed to send note/link")

        dialog.connect("response", on_response)
        dialog.present()

    def action_toggle_direct(self):
        # Dialog to choose Start or Stop a configured direct P2P link.
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Wi-Fi Direct / Off-Grid P2P",
            body=(
                "Use your configured helper to start or stop a direct wireless link."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("stop", "Disconnect Direct")
        dialog.add_response("start", "Start Direct AP")
        dialog.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)

        def on_response(dlg, response):
            helper = shutil.which("oneguyos-direct")
            if response in ("start", "stop") and not helper:
                self.show_toast("OneGuyOS-Direct helper is not installed")
                return
            if response == "start":
                result = subprocess.run([helper, "start"], check=False)
                self.show_toast("Direct hotspot started" if result.returncode == 0 else "Could not start direct hotspot")
            elif response == "stop":
                result = subprocess.run([helper, "stop"], check=False)
                self.show_toast("Direct hotspot stopped" if result.returncode == 0 else "Could not stop direct hotspot")

        dialog.connect("response", on_response)
        dialog.present()

