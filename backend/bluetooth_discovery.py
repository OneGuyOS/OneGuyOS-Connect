"""Read-only discovery of nearby Android phones through BlueZ."""

import re
import subprocess
import threading


_DEVICE = re.compile(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+?)\s*$")
_PHONE_WORDS = (
    "android", "motorola", "motorola edge", "moto ", "galaxy", "samsung",
    "s25", "pixel", "oneplus", "xiaomi", "redmi", "oppo", "vivo", "huawei",
    "honor", "nokia", "sony", "nothing phone", "asus rog", "zenfone",
)
_PHONE_WORDS = tuple(word.casefold() for word in _PHONE_WORDS)


def parse_devices(output):
    """Return named likely phones from bluetoothctl output, keyed by address."""
    found = {}
    for line in output.splitlines():
        match = _DEVICE.search(line.strip())
        if not match:
            continue
        address, name = match.groups()
        name = re.sub(r"\x1b\[[0-9;]*m", "", name).strip()
        lowered = name.casefold()
        if name and any(word in lowered for word in _PHONE_WORDS):
            found[address.upper()] = name
    return found


class BluetoothPhoneDiscovery:
    """Periodically scan without pairing; callback receives the complete snapshot."""

    def __init__(self, callback, interval=75, scan_seconds=8):
        self.callback = callback
        self.interval = interval
        self.scan_seconds = scan_seconds
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                result = subprocess.run(
                    ["bluetoothctl", "--timeout", str(self.scan_seconds), "scan", "on"],
                    capture_output=True, text=True, timeout=self.scan_seconds + 5,
                    check=False,
                )
                output = result.stdout + "\n" + result.stderr
                devices = parse_devices(output)
                # BlueZ often reports discoveries only in its device cache.
                listed = subprocess.run(
                    ["bluetoothctl", "devices"], capture_output=True,
                    text=True, timeout=4, check=False,
                )
                devices.update(parse_devices(listed.stdout + "\n" + listed.stderr))
                self.callback(devices)
            except (OSError, subprocess.TimeoutExpired):
                self.callback({})
            self._stop.wait(self.interval)
