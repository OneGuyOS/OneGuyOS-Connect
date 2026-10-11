#!/usr/bin/env python3
"""GTK test viewer for the Android companion's local screen-preview prototype."""

import socket
import struct
import sys
import threading
import time
import ipaddress
from pathlib import Path

import gi

sys.path.insert(0, str(Path(__file__).parent / ".vendor"))
from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf
import ifaddr

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

SERVICE_TYPE = "_ogscreen._tcp.local."
DISCOVERY_PORT = 43139
DISCOVERY_QUERY = b"OGCONNECT_DISCOVER_V1"


def receive_exactly(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Phone stopped sharing the screen")
        data.extend(chunk)
    return bytes(data)


def receive_line(sock):
    data = bytearray()
    while not data.endswith(b"\n"):
        part = sock.recv(1)
        if not part:
            raise ConnectionError("Phone closed the pairing connection")
        data.extend(part)
        if len(data) > 64:
            raise ValueError("Invalid pairing response")
    return bytes(data).strip()


class PhoneListener(ServiceListener):
    def __init__(self, zeroconf, on_phone):
        self.zeroconf = zeroconf
        self.on_phone = on_phone
        self.seen = set()

    def add_service(self, _zeroconf, _type, name):
        info = self.zeroconf.get_service_info(SERVICE_TYPE, name, timeout=3000)
        addresses = info.parsed_addresses(IPVersion.V4Only) if info else []
        if not info or not addresses or name in self.seen:
            return
        self.seen.add(name)
        phone = {
            "name": name,
            "model": info.properties.get(b"model", b"Android phone").decode("utf-8", "replace"),
            "host": addresses[0],
            "port": info.port,
        }
        GLib.idle_add(self.on_phone, phone)

    def remove_service(self, _zeroconf, _type, name):
        self.seen.discard(name)

    def update_service(self, zeroconf, service_type, name):
        self.seen.discard(name)
        self.add_service(zeroconf, service_type, name)


class CompanionPreview(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.oneguyos.connect.preview-prototype")
        self.zeroconf = None
        self.browser = None
        self.phone = None
        self.socket = None
        self.reader_thread = None
        self.picture = None
        self.status = None
        self.connect_button = None
        self.window = None
        self.discovery_stop = threading.Event()

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        self.window = Gtk.ApplicationWindow(application=self, title="OG Connect · Preview test")
        self.window.set_default_size(430, 720)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        layout.set_margin_top(16)
        layout.set_margin_bottom(16)
        layout.set_margin_start(16)
        layout.set_margin_end(16)
        self.status = Gtk.Label(label="Looking for a phone sharing its screen…")
        self.status.set_wrap(True)
        layout.append(self.status)
        self.connect_button = Gtk.Button(label="No sharing phone found yet")
        self.connect_button.set_sensitive(False)
        self.connect_button.connect("clicked", self.on_connect_clicked)
        layout.append(self.connect_button)
        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_vexpand(True)
        layout.append(self.picture)
        self.window.set_child(layout)
        self.window.connect("close-request", self.on_close)
        self.window.present()

        self.zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        listener = PhoneListener(self.zeroconf, self.on_phone_found)
        self.browser = ServiceBrowser(self.zeroconf, SERVICE_TYPE, listener)
        threading.Thread(target=self.discover_broadcast, daemon=True).start()

    def discover_broadcast(self):
        """Find sharing phones when a Wi-Fi AP filters mDNS multicast."""
        broadcasts = set()
        try:
            for adapter in ifaddr.get_adapters():
                for address in adapter.ips:
                    if not isinstance(address.ip, str) or address.is_IPv6:
                        continue
                    try:
                        network = ipaddress.ip_interface(
                            f"{address.ip}/{address.network_prefix}"
                        ).network
                    except (ValueError, TypeError):
                        continue
                    if network.prefixlen < 8 or address.ip.startswith("127."):
                        continue
                    broadcasts.add(str(network.broadcast_address))
        except Exception as error:
            GLib.idle_add(self.status.set_text, f"Could not inspect network interfaces: {error}")
            return

        if not broadcasts:
            GLib.idle_add(self.status.set_text, "No local IPv4 network is available for phone discovery.")
            return

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as discovery:
                discovery.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                discovery.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                discovery.bind(("", DISCOVERY_PORT))
                discovery.settimeout(0.4)
                next_query = 0.0
                while not self.discovery_stop.is_set():
                    now = time.monotonic()
                    if now >= next_query:
                        for broadcast in broadcasts:
                            try:
                                discovery.sendto(DISCOVERY_QUERY, (broadcast, DISCOVERY_PORT))
                            except OSError:
                                pass
                        next_query = now + 3.0
                    try:
                        payload, source = discovery.recvfrom(512)
                    except socket.timeout:
                        continue
                    fields = payload.decode("utf-8", "replace").split("|", 4)
                    if len(fields) != 5 or fields[0] != "OGCONNECT/1":
                        continue
                    _version, device_id, manufacturer, model, port_text = fields
                    try:
                        port = int(port_text)
                        if not 1 <= port <= 65535:
                            continue
                    except ValueError:
                        continue
                    phone = {
                        "name": device_id,
                        "id": device_id,
                        "model": f"{manufacturer} {model}".strip(),
                        "host": source[0],
                        "port": port,
                    }
                    GLib.idle_add(self.on_phone_found, phone)
        except OSError as error:
            GLib.idle_add(self.status.set_text, f"Local network discovery failed: {error}")

    def on_phone_found(self, phone):
        self.phone = phone
        self.status.set_text(f"Found {phone['model']} on the local network.")
        self.connect_button.set_label(f"Connect to {phone['model']}")
        self.connect_button.set_sensitive(True)
        return False

    def on_connect_clicked(self, _button):
        if not self.phone:
            return
        dialog = Gtk.Dialog(title="Pair with phone", transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Connect", Gtk.ResponseType.ACCEPT)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.append(Gtk.Label(label="Enter the six-digit code shown in OneGuyOS Connect on your phone."))
        code = Gtk.Entry()
        code.set_input_purpose(Gtk.InputPurpose.DIGITS)
        code.set_max_length(6)
        code.set_placeholder_text("Six-digit code")
        content.append(code)

        def response(_dialog, result):
            value = code.get_text().strip()
            dialog.destroy()
            if result == Gtk.ResponseType.ACCEPT and len(value) == 6 and value.isdigit():
                self.status.set_text("Connecting to the phone…")
                threading.Thread(target=self.connect_phone, args=(value,), daemon=True).start()

        dialog.connect("response", response)
        dialog.present()

    def connect_phone(self, code):
        try:
            phone_socket = socket.create_connection(
                (self.phone["host"], self.phone["port"]), timeout=8
            )
            phone_socket.sendall(f"PIN {code}\n".encode("ascii"))
            if receive_line(phone_socket) != b"OK":
                phone_socket.close()
                GLib.idle_add(self.status.set_text, "The phone rejected that code. Try again.")
                return
            phone_socket.settimeout(None)
            self.socket = phone_socket
            GLib.idle_add(self.status.set_text, f"Connected to {self.phone['model']}.")
            self.reader_thread = threading.Thread(target=self.read_frames, daemon=True)
            self.reader_thread.start()
        except (OSError, ConnectionError, ValueError) as error:
            GLib.idle_add(self.status.set_text, f"Could not connect: {error}")

    def read_frames(self):
        try:
            while True:
                length = struct.unpack(">I", receive_exactly(self.socket, 4))[0]
                if length <= 0 or length > 12_000_000:
                    raise ValueError(f"Invalid frame size: {length}")
                frame = receive_exactly(self.socket, length)
                GLib.idle_add(self.show_frame, frame)
        except (OSError, ConnectionError, ValueError) as error:
            GLib.idle_add(self.status.set_text, f"Preview ended: {error}")

    def show_frame(self, frame):
        self.picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(frame)))
        return False

    def on_close(self, *_args):
        self.discovery_stop.set()
        if self.socket:
            try:
                self.socket.close()
            except OSError:
                pass
        if self.browser:
            self.browser.cancel()
        if self.zeroconf:
            self.zeroconf.close()
        return False


if __name__ == "__main__":
    CompanionPreview().run(sys.argv)
