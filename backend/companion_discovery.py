"""Discover OneGuyOS Connect Android companions advertising on the local Wi-Fi."""

import os
import sys
import threading

_vendor = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend", "vendor")
if os.path.isdir(_vendor) and _vendor not in sys.path:
    sys.path.insert(0, _vendor)

from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf


SERVICE_TYPE = "_ogconnect._tcp.local."


class _Listener(ServiceListener):
    def __init__(self, zeroconf, callback):
        self.zeroconf = zeroconf
        self.callback = callback
        self._names = {}
        self._lock = threading.Lock()

    def add_service(self, _zeroconf, _service_type, name):
        info = self.zeroconf.get_service_info(SERVICE_TYPE, name, timeout=2500)
        if not info:
            return
        properties = info.properties or {}
        device_id = properties.get(b"id", b"").decode("utf-8", "replace")
        addresses = info.parsed_addresses(IPVersion.V4Only)
        if not device_id or not addresses:
            return
        profile = {
            "id": device_id,
            "name": properties.get(b"model", b"Android phone").decode("utf-8", "replace"),
            "manufacturer": properties.get(b"manufacturer", b"").decode("utf-8", "replace"),
            "address": addresses[0],
            "port": info.port,
        }
        with self._lock:
            self._names[name] = device_id
        self.callback(profile, True)

    def remove_service(self, _zeroconf, _service_type, name):
        with self._lock:
            device_id = self._names.pop(name, None)
        if device_id:
            self.callback({"id": device_id}, False)

    def update_service(self, zeroconf, service_type, name):
        self.add_service(zeroconf, service_type, name)


class CompanionDiscovery:
    """Small owner for a background mDNS browser; callback may run off the GTK thread."""

    def __init__(self, callback):
        self._callback = callback
        self._zeroconf = None
        self._browser = None

    def start(self):
        if self._browser:
            return
        self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        listener = _Listener(self._zeroconf, self._callback)
        self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, listener)

    def stop(self):
        if self._browser:
            self._browser.cancel()
            self._browser = None
        if self._zeroconf:
            self._zeroconf.close()
            self._zeroconf = None
