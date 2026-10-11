"""Docked Android screen preview with a standalone pop-out."""

import subprocess
import socket
import select
import threading
import time
import json
import os
import shutil

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, GLib, Gtk
from gi.repository import Gst

from backend.screen_mirror import ScreenMirrorManager
from backend.companion_stream import connect_screen_stream, receive_frame


Gst.init(None)


def _h264_start_codes(data):
    """Return Annex-B start-code offsets and lengths in a byte buffer."""
    result = []
    index = 0
    while index <= len(data) - 3:
        if data[index:index + 4] == b"\x00\x00\x00\x01":
            result.append((index, 4))
            index += 4
        elif data[index:index + 3] == b"\x00\x00\x01":
            result.append((index, 3))
            index += 3
        else:
            index += 1
    return result


LAST_PHONE_PATH = os.path.expanduser(
    "~/.config/oneguyos-connect/last_mirror_phone.json"
)


class MirrorSidebar(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window = window
        self.add_css_class("oneguy-mirror-sidebar")
        self.set_size_request(280, -1)
        self.set_visible(False)
        self.phones = []
        self.active_phone_id = None
        try:
            with open(LAST_PHONE_PATH, encoding="utf-8") as saved_phone_file:
                saved_phone = json.load(saved_phone_file)
            self._last_phone_id = saved_phone.get("device_id")
        except (OSError, ValueError, AttributeError):
            self._last_phone_id = None
        self.stream_process = None
        self.desktop_process = None
        self.stream_socket = None
        self.stream_mode = None
        self.desktop_feature = None
        self.external_displays = []
        self.navigation_buttons = []
        self.popout = None
        self._stream_epoch = 0
        self._updating_selector = False
        self._adb_path = None
        self._serial = None
        self._frame_width = 0
        self._frame_height = 0
        self._display_width = 0
        self._display_height = 0
        self._input_display_id = None
        self._input_source = None
        self._drag_start = None
        self._drag_moved = False
        self._last_profile_check = 0.0
        self._profile_check_running = False

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="Phone screen")
        title.add_css_class("heading")
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        header.append(title)
        self.popout_button = Gtk.MenuButton(icon_name="window-new-symbolic")
        self.popout_button.add_css_class("flat")
        self.popout_button.set_tooltip_text(
            "Pop out Android screen or start the phone's desktop mode"
        )
        self.popout_menu = Gtk.Popover()
        popout_choices = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        self.android_popout_choice = Gtk.Button(label="Pop out Android screen")
        self.android_popout_choice.add_css_class("flat")
        self.android_popout_choice.connect("clicked", self.on_android_popout_choice)
        popout_choices.append(self.android_popout_choice)
        self.desktop_popout_choice = Gtk.Button(label="Open DeX desktop")
        self.desktop_popout_choice.add_css_class("flat")
        self.desktop_popout_choice.set_tooltip_text(
            "Open Samsung DeX in an interactive 1600 × 900 desktop window."
        )
        self.desktop_popout_choice.set_visible(False)
        self.desktop_popout_choice.connect("clicked", self.on_desktop_mode_clicked)
        popout_choices.append(self.desktop_popout_choice)
        self.popout_menu.set_child(popout_choices)
        self.popout_button.set_popover(self.popout_menu)
        self.popout_button.connect(
            "notify::active", self.on_popout_menu_toggled
        )
        header.append(self.popout_button)
        close = Gtk.Button(icon_name="window-close-symbolic")
        close.add_css_class("flat")
        close.set_tooltip_text("Close screen panel")
        close.connect("clicked", lambda *_: self.hide_panel())
        header.append(close)
        self.append(header)

        navigation = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        navigation.set_halign(Gtk.Align.CENTER)
        for label, key, tooltip in (
            ("Back", "KEYCODE_BACK", "Go back on phone"),
            ("Home", "KEYCODE_HOME", "Go to phone home screen"),
            ("Recent", "KEYCODE_APP_SWITCH", "Show recent phone apps"),
        ):
            button = Gtk.Button(label=label)
            button.add_css_class("flat")
            button.set_tooltip_text(tooltip)
            button.connect("clicked", self.on_navigation, key)
            button.set_sensitive(False)
            self.navigation_buttons.append(button)
            navigation.append(button)
        self.append(navigation)

        self.selector = Gtk.DropDown.new_from_strings(["Select a phone"])
        self.selector.connect("notify::selected", self.on_selected)
        self.append(self.selector)

        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_focusable(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_vexpand(True)
        self.picture.set_hexpand(True)
        click = Gtk.GestureClick()
        click.connect("released", self.on_screen_click)
        self.picture.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-end", self.on_drag_end)
        self.picture.add_controller(drag)
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        scroll.connect("scroll", self.on_scroll)
        self.picture.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key_pressed)
        self.picture.add_controller(keys)
        self.picture_parent = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.picture_parent.add_css_class("oneguy-mirror-frame")
        self.picture_parent.set_vexpand(True)
        self.picture_parent.append(self.picture)
        self.append(self.picture_parent)

        self.status = Gtk.Label(label="Connect a phone to begin.")
        self.status.set_wrap(True)
        self.status.set_xalign(0)
        self.status.add_css_class("dim-label")
        self.append(self.status)

        self.setup_button = Gtk.Button(label="Install Android connection tools")
        self.setup_button.add_css_class("suggested-action")
        self.setup_button.connect("clicked", self.on_setup)
        self.append(self.setup_button)
        self._update_setup_ui()

    def set_phones(self, phones):
        old_id = self.active_phone_id
        self.phones = list(phones)
        labels = [phone.display_name for phone in self.phones] or ["No phones connected"]
        model = Gtk.StringList.new(labels)
        self._updating_selector = True
        self.selector.set_model(model)
        self.selector.set_sensitive(bool(self.phones))
        chosen = next(
            (
                i for i, phone in enumerate(self.phones)
                if phone.id == (old_id or self._last_phone_id)
            ),
            0,
        )
        self.selector.set_selected(chosen)
        self._updating_selector = False
        if not self.phones:
            self.stop_stream()
            self.active_phone_id = None
            self.status.set_text("Connect a phone to begin.")
        elif old_id and all(phone.id != old_id for phone in self.phones):
            self.active_phone_id = None
            if self.get_visible():
                self.select_phone(self.phones[chosen])

    def select_phone(self, phone):
        if self.active_phone_id == phone.id:
            return
        self.active_phone_id = phone.id
        self._last_phone_id = phone.id
        os.makedirs(os.path.dirname(LAST_PHONE_PATH), exist_ok=True)
        temporary_path = f"{LAST_PHONE_PATH}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as saved_phone_file:
            json.dump({"device_id": phone.id}, saved_phone_file)
            saved_phone_file.write("\n")
        os.replace(temporary_path, LAST_PHONE_PATH)
        self.stop_stream()
        self.stream_mode = None
        self._set_navigation_enabled(False)
        self._adb_path = None
        self._serial = None
        self._frame_width = 0
        self._frame_height = 0
        self._display_width = 0
        self._display_height = 0
        self.desktop_feature = None
        self.desktop_popout_choice.set_visible(False)
        self.desktop_popout_choice.set_sensitive(False)
        self.external_displays = []
        self.picture.set_paintable(None)
        self.status.set_text(f"Ready to mirror {phone.display_name}.")
        self.window.start_mirror_for_phone(phone)

    def on_selected(self, dropdown, _param):
        if self._updating_selector:
            return
        index = dropdown.get_selected()
        if index < len(self.phones):
            self.select_phone(self.phones[index])

    def show_panel(self, phone=None, popout=False):
        self.set_visible(True)
        if phone:
            index = next(
                (i for i, item in enumerate(self.phones) if item.id == phone.id),
                None,
            )
            if index is not None:
                self.selector.set_selected(index)
                self.select_phone(phone)
        if popout:
            self.on_popout(None)

    def hide_panel(self):
        self.set_visible(False)
        self.stop_stream()

    def set_status(self, text):
        self.status.set_text(text)

    def check_connection_profile(self, manager):
        """Periodically verify that the live preview still belongs to this phone."""
        if not self.get_visible() or not self.active_phone_id or self.stream_mode != "adb":
            return
        now = time.monotonic()
        if self._profile_check_running or now - self._last_profile_check < 30:
            return
        phone = next(
            (item for item in self.phones if item.id == self.active_phone_id), None
        )
        if phone is None:
            return
        self._last_profile_check = now
        self._profile_check_running = True
        phone_id = phone.id
        epoch = self._stream_epoch
        selected_serial = self._serial

        def scan():
            result = manager.discover()
            profile = None
            target = None
            if not result.get("error"):
                target = manager.resolve(phone, result["devices"])
                if target:
                    profile = manager.profile(result["adb"], target["serial"])
            GLib.idle_add(
                self._finish_profile_check, manager, phone, phone_id, epoch,
                selected_serial, result, target, profile,
            )

        threading.Thread(target=scan, daemon=True).start()

    def _finish_profile_check(
        self, manager, phone, phone_id, epoch, selected_serial,
        result, target, profile,
    ):
        self._profile_check_running = False
        if (self.active_phone_id != phone_id or self._stream_epoch != epoch
                or self._serial != selected_serial):
            return False
        if result.get("error"):
            self.status.set_text(f"Phone profile check failed: {result['error']}")
            return False
        if not target:
            self.stop_stream()
            self._serial = None
            self._adb_path = None
            self.picture.set_paintable(None)
            self.status.set_text(
                f"{phone.name} is not available to the screen connection. "
                "Reconnect it and the profile will be checked again."
            )
            return False
        if selected_serial and target["serial"] != selected_serial:
            self.stop_stream()
            self.picture.set_paintable(None)
            self._serial = None
        if self._serial is None:
            manager.save_mapping(phone_id, target["serial"])
            self.start_stream(result["adb"], target["serial"], phone.name)
        if profile:
            maker = profile.get("manufacturer", "").strip()
            model = profile.get("model", "").strip() or target.get("model", "Android phone")
            identity = " ".join(part for part in (maker, model) if part)
            size = f"{profile['width']}×{profile['height']}"
            self.status.set_text(
                f"Verified · {phone.name} · {identity} · {size} · checked now"
            )
        return False

    def set_setup_visible(self, visible):
        self.setup_button.set_visible(visible)

    def _update_setup_ui(self):
        tools_ready = bool(ScreenMirrorManager._tool_path("adb"))
        self.setup_button.set_visible(not tools_ready)

    def on_setup(self, _button):
        self.setup_button.set_sensitive(False)
        self.status.set_text("Installing Google's Android tools in your home folder…")

        def install_tools():
            try:
                paths = ScreenMirrorManager.ensure_tools()
                GLib.idle_add(self._continue_setup, paths, None)
            except Exception as error:
                GLib.idle_add(self._continue_setup, None, str(error))

        threading.Thread(target=install_tools, daemon=True).start()

    def _continue_setup(self, paths, error):
        self.setup_button.set_sensitive(True)
        if error:
            self.status.set_text(f"Phone-tool setup failed: {error}")
            return False
        self.setup_button.set_visible(False)
        self.status.set_text("Android tools are ready. Checking connected phones…")
        index = self.selector.get_selected()
        if index < len(self.phones):
            self.active_phone_id = None
            self.select_phone(self.phones[index])
        return False

    def start_stream(self, adb_path, serial, phone_name):
        try:
            self._adb_path = adb_path
            self._serial = serial
            self._display_width, self._display_height = (
                ScreenMirrorManager._display_size(adb_path, serial)
            )
        except (OSError, FileNotFoundError) as error:
            self.status.set_text(str(error))
            return
        self._stream_epoch += 1
        self.stream_mode = "adb"
        self._input_display_id = None
        self._input_source = None
        self._set_navigation_enabled(True)
        epoch = self._stream_epoch
        self.status.set_text(f"Starting {phone_name}…")
        threading.Thread(
            target=self._capture_frames,
            args=(adb_path, serial, phone_name, epoch),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._detect_desktop_capability,
            args=(adb_path, serial, epoch),
            daemon=True,
        ).start()

    def _detect_desktop_capability(self, adb_path, serial, epoch):
        feature = ScreenMirrorManager.desktop_mode_capability(adb_path, serial)
        displays = ScreenMirrorManager.external_displays(adb_path, serial)
        GLib.idle_add(self._set_desktop_capability, epoch, feature, displays)

    def on_popout_menu_toggled(self, button, _param):
        if button.get_active() and self._adb_path and self._serial:
            threading.Thread(
                target=self._detect_desktop_capability,
                args=(self._adb_path, self._serial, self._stream_epoch),
                daemon=True,
            ).start()

    def _set_desktop_capability(self, epoch, feature, displays):
        if epoch != self._stream_epoch:
            return False
        self.desktop_feature = feature
        self.external_displays = displays
        if feature == "DeX" and displays:
            label = f"Open DeX desktop · {displays[0]['name']}"
        elif feature == "DeX":
            label = "Open DeX desktop · 1600 × 900"
        elif feature == "Smart Connect":
            label = "Smart Connect (wireless receiver needed)"
        else:
            label = "Desktop mode"
        self.desktop_popout_choice.set_label(label)
        self.desktop_popout_choice.set_visible(bool(feature))
        self.desktop_popout_choice.set_sensitive(
            feature == "DeX" and bool(self._adb_path and self._serial)
            and (bool(displays) or bool(shutil.which("scrcpy")))
            and self.stream_mode in ("adb", "dex")
        )
        return False

    def on_android_popout_choice(self, _button):
        self.popout_menu.popdown()
        self.on_popout(None)

    def on_desktop_mode_clicked(self, _button):
        self.popout_menu.popdown()
        if self.desktop_feature != "DeX" or not self._adb_path or not self._serial:
            return
        if self.external_displays:
            self._start_external_display(self.external_displays[0])
        elif shutil.which("scrcpy"):
            self._start_virtual_dex()

    def _start_virtual_dex(self):
        """Restore the previously working interactive Samsung DeX window."""
        if self.desktop_process and self.desktop_process.poll() is None:
            self.status.set_text("Samsung DeX is already open in its desktop window.")
            return
        self.status.set_text("Starting Samsung DeX · 1600 × 900…")
        threading.Thread(
            target=self._launch_virtual_dex,
            args=(self._adb_path, self._serial, self._stream_epoch),
            daemon=True,
        ).start()

    def _launch_virtual_dex(self, adb_path, serial, epoch):
        """Ask Samsung to use desktop mode on scrcpy's virtual display."""
        command = [
            adb_path, "-s", serial, "shell", "settings", "put", "global",
            "force_desktop_mode_on_external_displays", "1",
        ]
        try:
            configured = subprocess.run(
                command, capture_output=True, text=True, timeout=8, check=False,
            )
            if configured.returncode:
                raise RuntimeError(
                    configured.stderr.strip() or "Could not enable Samsung desktop mode."
                )
            # Samsung supplies the DeX launcher/taskbar automatically on this
            # landscape virtual display. Avoid launching a stock Android app;
            # that would cover the desktop home when the window opens.
            process = subprocess.Popen(
                [
                    shutil.which("scrcpy"), "-s", serial,
                    "--window-title=Samsung DeX · OneGuyOS Connect",
                    "--new-display=1600x900/180",
                    "--max-fps=60", "--video-bit-rate=20M",
                    "--keyboard=uhid", "--mouse=uhid",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(1.2)
            if process.poll() is not None:
                _stdout, stderr = process.communicate(timeout=2)
                raise RuntimeError(stderr.strip() or "DeX desktop window closed during startup.")
            self.desktop_process = process
            GLib.idle_add(
                self._set_dex_status,
                "Samsung DeX is open · 1600 × 900 · keyboard and mouse enabled",
                epoch,
            )
            process.wait()
            if self.desktop_process is process:
                self.desktop_process = None
            GLib.idle_add(self._set_dex_status, "Samsung DeX window closed.", epoch)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            GLib.idle_add(
                self._set_dex_status, f"Could not open Samsung DeX: {error}", epoch,
            )

    def _set_dex_status(self, message, epoch):
        if epoch == self._stream_epoch:
            self.status.set_text(message)
        return False

    def _start_external_display(self, display):
        self.stop_stream()
        self.stream_mode = "dex"
        self._display_width = display.get("width") or 1600
        self._display_height = display.get("height") or 900
        self._input_display_id = display.get("logical_id")
        self._set_navigation_enabled(self._input_display_id is not None)
        epoch = self._stream_epoch
        phone = next(
            (item for item in self.phones if item.id == self.active_phone_id),
            None,
        )
        phone_name = phone.display_name if phone else "Phone"
        self.status.set_text(f"Capturing {display['name']} over ADB…")
        threading.Thread(
            target=self._capture_frames,
            args=(self._adb_path, self._serial, f"{phone_name} DeX", epoch, display),
            daemon=True,
        ).start()

    def start_companion_stream(self, profile, pairing_code, phone_name):
        """Connect the shared phone view to an owner-approved Android stream."""
        self.stop_stream()
        self.stream_mode = "companion"
        self._adb_path = None
        self._serial = None
        self._display_width = 0
        self._display_height = 0
        self._set_navigation_enabled(False)
        self._stream_epoch += 1
        epoch = self._stream_epoch
        self.status.set_text(f"Pairing with {profile['manufacturer']} {profile['model']}…")
        threading.Thread(
            target=self._receive_companion_frames,
            args=(profile, pairing_code, phone_name, epoch),
            daemon=True,
        ).start()

    def _receive_companion_frames(self, profile, pairing_code, phone_name, epoch):
        connection = None
        try:
            connection = connect_screen_stream(profile, pairing_code)
            if epoch != self._stream_epoch:
                connection.close()
                return
            self.stream_socket = connection
            GLib.idle_add(
                self.status.set_text,
                f"Live screen · {phone_name} · view only",
            )
            while epoch == self._stream_epoch:
                frame = receive_frame(connection)
                GLib.idle_add(self._show_screenshot, GLib.Bytes.new(frame), epoch)
        except (OSError, ConnectionError, PermissionError, ValueError) as error:
            GLib.idle_add(self._set_stream_error, str(error), epoch)
        finally:
            if connection:
                connection.close()
            if self.stream_socket is connection:
                self.stream_socket = None

    def _set_navigation_enabled(self, enabled):
        for button in self.navigation_buttons:
            button.set_sensitive(enabled)

    def _capture_frames(self, adb_path, serial, phone_name, epoch, display=None):
        """Decode a continuous ADB H.264 stream and deliver JPEG frames to GTK."""
        if epoch != self._stream_epoch:
            return
        process = None
        pipeline = None
        try:
            # Keep a clean still visible while the video decoder waits for a
            # complete H.264 access unit. A still Android display may not emit
            # another NAL immediately after its first frame.
            screenshot_command = [
                adb_path, "-s", serial, "exec-out", "screencap", "-p",
            ]
            if display:
                screenshot_command.extend(("-d", display["physical_id"]))
            screenshot = subprocess.run(
                screenshot_command,
                capture_output=True,
                timeout=8,
                check=False,
            )
            if (screenshot.returncode == 0
                    and screenshot.stdout.startswith(b"\x89PNG\r\n\x1a\n")):
                GLib.idle_add(
                    self._show_screenshot,
                    GLib.Bytes.new(screenshot.stdout), epoch,
                )
                GLib.idle_add(
                    self.status.set_text,
                    f"{phone_name} ready · starting live video…",
                )

            process = ScreenMirrorManager.start_stream(
                adb_path, serial, display=display
            )
            self.stream_process = process
            pipeline = Gst.parse_launch(
                'appsrc name=phone_source is-live=true format=time '
                'do-timestamp=true block=true '
                'caps="video/x-h264,stream-format=byte-stream,'
                'alignment=nal,framerate=30/1" ! '
                'h264parse ! avdec_h264 ! videoconvert ! '
                'jpegenc quality=72 ! '
                'appsink name=phone_sink sync=false max-buffers=1 drop=true'
            )
            source = pipeline.get_by_name("phone_source")
            sink = pipeline.get_by_name("phone_sink")
            pipeline.set_state(Gst.State.PLAYING)
            pending = bytearray()
            announced = False

            while epoch == self._stream_epoch:
                readable, _, _ = select.select(
                    [process.stdout], [], [], 0.025
                )
                if readable:
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        raise ConnectionError("Android stopped the video stream.")
                    pending.extend(chunk)

                start_codes = _h264_start_codes(pending)
                if len(start_codes) > 1:
                    if start_codes[0][0] > 0:
                        del pending[:start_codes[0][0]]
                        start_codes = _h264_start_codes(pending)
                    for index in range(len(start_codes) - 1):
                        start = start_codes[index][0]
                        end = start_codes[index + 1][0]
                        buffer = Gst.Buffer.new_allocate(None, end - start, None)
                        buffer.fill(0, pending[start:end])
                        if source.emit("push-buffer", buffer) != Gst.FlowReturn.OK:
                            raise RuntimeError("The phone video decoder stopped accepting frames.")
                    del pending[:start_codes[-1][0]]

                while True:
                    sample = sink.emit("try-pull-sample", 0)
                    if sample is None:
                        break
                    frame = sample.get_buffer()
                    mapped, frame_map = frame.map(Gst.MapFlags.READ)
                    if not mapped:
                        continue
                    try:
                        frame_bytes = GLib.Bytes.new(frame_map.data)
                    finally:
                        frame.unmap(frame_map)
                    GLib.idle_add(self._show_screenshot, frame_bytes, epoch)
                    if not announced:
                        announced = True
                        GLib.idle_add(
                            self.status.set_text,
                            f"Live screen · {phone_name} · tap or swipe to control",
                        )

                message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
                if message:
                    error = message.parse_error()[0]
                    raise RuntimeError(error.message)

        except (OSError, RuntimeError, ConnectionError, Gst.ParseError) as error:
            GLib.idle_add(self._set_stream_error, str(error), epoch)
        finally:
            if pipeline:
                pipeline.set_state(Gst.State.NULL)
            if process:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                if process.stdout:
                    process.stdout.close()
            if self.stream_process is process:
                self.stream_process = None

    def _set_stream_error(self, message, epoch):
        if epoch == self._stream_epoch:
            self.status.set_text(f"Screen input unavailable: {message}")
        return False

    def _show_screenshot(self, frame_bytes, epoch):
        if epoch != self._stream_epoch:
            return False
        try:
            texture = Gdk.Texture.new_from_bytes(frame_bytes)
        except GLib.Error as error:
            self.status.set_text(f"Could not display phone screen: {error.message}")
            return False
        self.picture.set_paintable(texture)
        self._frame_width = texture.get_width()
        self._frame_height = texture.get_height()
        return False

    def _screen_coordinates(self, x, y):
        if not self._adb_path or not self._serial or not self._frame_width:
            return None
        allocation = self.picture.get_allocation()
        scale = min(
            allocation.width / self._frame_width,
            allocation.height / self._frame_height,
        )
        drawn_width = self._frame_width * scale
        drawn_height = self._frame_height * scale
        left = (allocation.width - drawn_width) / 2
        top = (allocation.height - drawn_height) / 2
        x = min(max(x - left, 0), drawn_width - 1)
        y = min(max(y - top, 0), drawn_height - 1)
        return (
            round(x / drawn_width * self._display_width),
            round(y / drawn_height * self._display_height),
        )

    def set_input_target(self, display_id=None, source=None, width=None, height=None):
        """Route subsequent pointer and keyboard input to an Android display."""
        self._input_display_id = display_id
        self._input_source = source
        if width and height:
            self._display_width = width
            self._display_height = height

    def _send_phone_input(self, *arguments):
        if not self._adb_path or not self._serial:
            return
        adb_path, serial = self._adb_path, self._serial
        display_id, source = self._input_display_id, self._input_source

        def send():
            try:
                ScreenMirrorManager.send_input(
                    adb_path, serial, *arguments,
                    display_id=display_id, source=source,
                )
            except Exception:
                pass

        threading.Thread(target=send, daemon=True).start()

    def on_key_pressed(self, _controller, keyval, _keycode, state):
        """Forward desktop keyboard keys when the phone view has focus."""
        special_keys = {
            "Return": "ENTER", "KP_Enter": "ENTER", "BackSpace": "DEL",
            "Tab": "TAB", "Escape": "ESCAPE", "space": "SPACE",
            "Delete": "FORWARD_DEL", "Home": "MOVE_HOME", "End": "MOVE_END",
            "Page_Up": "PAGE_UP", "Page_Down": "PAGE_DOWN",
            "Up": "DPAD_UP", "Down": "DPAD_DOWN",
            "Left": "DPAD_LEFT", "Right": "DPAD_RIGHT",
            "Insert": "INSERT", "Menu": "MENU",
        }
        name = Gdk.keyval_name(keyval) or ""
        keycode = special_keys.get(name)
        modifiers = []
        if state & Gdk.ModifierType.CONTROL_MASK:
            modifiers.append("KEYCODE_CTRL_LEFT")
        if state & Gdk.ModifierType.ALT_MASK:
            modifiers.append("KEYCODE_ALT_LEFT")
        if state & Gdk.ModifierType.SHIFT_MASK:
            modifiers.append("KEYCODE_SHIFT_LEFT")
        if modifiers:
            character = chr(Gdk.keyval_to_unicode(keyval)) if Gdk.keyval_to_unicode(keyval) else ""
            base = keycode
            if not base and character.isascii() and character.isalnum():
                base = f"KEYCODE_{character.upper()}"
            elif not base:
                base = {
                    "c": "KEYCODE_C", "v": "KEYCODE_V", "x": "KEYCODE_X",
                    "z": "KEYCODE_Z", "a": "KEYCODE_A", "s": "KEYCODE_S",
                    "f": "KEYCODE_F", "p": "KEYCODE_P", "w": "KEYCODE_W",
                }.get(name.lower())
            if base:
                self._send_phone_input("keycombination", *modifiers, f"KEYCODE_{base.removeprefix('KEYCODE_')}")
                return True
        if keycode:
            self._send_phone_input("keyevent", keycode)
            return True

        character = chr(Gdk.keyval_to_unicode(keyval)) if Gdk.keyval_to_unicode(keyval) else ""
        if character and character.isprintable():
            # Android's input command uses %s as its space escape.
            escaped = character.replace(" ", "%s")
            self._send_phone_input("text", escaped)
            return True
        return False

    def on_scroll(self, controller, delta_x, delta_y):
        """Send mouse-wheel motion to the Android display under the pointer."""
        if not self._adb_path or not self._serial or self.stream_mode != "adb":
            return False
        event = controller.get_current_event()
        position = event.get_position() if event else None
        if not position:
            return False
        if len(position) == 3:
            valid, x, y = position
            if not valid:
                return False
        else:
            x, y = position
        point = self._screen_coordinates(x, y)
        if not point:
            return False
        args = ["scroll", *point]
        if delta_y:
            args.extend(("--axis", f"VSCROLL,{-delta_y:.2f}"))
        if delta_x:
            args.extend(("--axis", f"HSCROLL,{-delta_x:.2f}"))
        self._send_phone_input(*args)
        return True

    def on_navigation(self, _button, key):
        self._send_phone_input("keyevent", key)

    def on_screen_click(self, _gesture, _presses, x, y):
        if self._drag_moved:
            self._drag_moved = False
            return
        point = self._screen_coordinates(x, y)
        if point:
            self.picture.grab_focus()
            self._send_phone_input("tap", *point)

    def on_drag_begin(self, _gesture, x, y):
        self._drag_start = (x, y, self._screen_coordinates(x, y))
        self._drag_moved = False

    def on_drag_end(self, _gesture, offset_x, offset_y):
        if not self._drag_start or not self._drag_start[2]:
            return
        start_x, start_y = self._drag_start[2]
        end = self._screen_coordinates(
            self._drag_start[0] + offset_x,
            self._drag_start[1] + offset_y,
        )
        if end and (abs(offset_x) + abs(offset_y) > 12):
            self._drag_moved = True
            self._send_phone_input(
                "swipe", start_x, start_y, end[0], end[1], 350,
            )
        self._drag_start = None

    def stop_stream(self):
        self._stream_epoch += 1
        if self.stream_process and self.stream_process.poll() is None:
            self.stream_process.terminate()
        self.stream_process = None
        if self.desktop_process and self.desktop_process.poll() is None:
            self.desktop_process.terminate()
        self.desktop_process = None
        if self.stream_socket:
            try:
                self.stream_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.stream_socket.close()
            except OSError:
                pass
        self.stream_socket = None
        self.stream_mode = None
        self._set_navigation_enabled(False)

    def on_popout(self, _button):
        if self.popout:
            self.popout.present()
            return
        self.picture.unparent()
        placeholder = Gtk.Label(label="Phone screen is open in a separate window.")
        placeholder.set_wrap(True)
        placeholder.set_margin_top(24)
        placeholder.set_margin_bottom(24)
        self.picture_parent.append(placeholder)
        popout = Gtk.Window(
            application=self.window.get_application(),
            title="OneGuyOS Connect · Phone screen",
        )
        popout.set_default_size(480, 840)
        popout.set_child(self.picture)
        popout.connect("close-request", self.on_popout_closed, placeholder)
        self.popout = popout
        popout.present()

    def on_popout_closed(self, popout, placeholder):
        self.picture.unparent()
        popout.set_child(None)
        self.picture_parent.remove(placeholder)
        self.picture_parent.append(self.picture)
        self.popout = None
        return False
