"""Local Android screen mirroring helpers backed by Android screenrecord and ADB."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile


CONFIG_PATH = os.path.expanduser(
    "~/.config/oneguyos-connect/screen_mirror_devices.json"
)
PROFILE_PATH = os.path.expanduser(
    "~/.config/oneguyos-connect/device_model_profiles.json"
)


class ScreenMirrorManager:
    PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-linux.zip"
    def __init__(self, config_path=CONFIG_PATH):
        self.config_path = config_path
        try:
            with open(config_path, encoding="utf-8") as config_file:
                mappings = json.load(config_file)
            self.mappings = mappings if isinstance(mappings, dict) else {}
        except (OSError, ValueError):
            self.mappings = {}
        try:
            with open(PROFILE_PATH, encoding="utf-8") as profile_file:
                profiles = json.load(profile_file)
            self.model_profiles = profiles if isinstance(profiles, dict) else {}
        except (OSError, ValueError):
            self.model_profiles = {}

    def remember_profile(self, device_id, manufacturer, model):
        """Persist a verified phone model so its label survives temporary ADB loss."""
        self.model_profiles[device_id] = {
            "manufacturer": manufacturer,
            "model": model,
        }
        os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
        temporary_path = f"{PROFILE_PATH}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as profile_file:
            json.dump(self.model_profiles, profile_file, indent=2)
            profile_file.write("\n")
        os.replace(temporary_path, PROFILE_PATH)

    @staticmethod
    def _tool_path(name):
        return shutil.which(name) or os.path.expanduser(
            f"~/.local/bin/{name}"
        )

    def discover(self):
        adb = self._tool_path("adb")
        if not adb or not os.path.isfile(adb):
            return {"error": "Android platform tools (adb) are not installed."}

        try:
            result = subprocess.run(
                [adb, "devices", "-l"], capture_output=True, text=True,
                timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"error": f"Could not check Android devices: {error}"}
        if result.returncode:
            return {"error": result.stderr.strip() or "ADB could not list devices."}

        devices, unauthorized, offline = [], [], []
        for line in result.stdout.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 2:
                continue
            serial, state = fields[:2]
            if state == "device":
                props = dict(
                    field.split(":", 1) for field in fields[2:] if ":" in field
                )
                model = props.get("model", "").replace("_", " ")
                try:
                    maker_result = subprocess.run(
                        [adb, "-s", serial, "shell", "getprop",
                         "ro.product.manufacturer"],
                        capture_output=True, text=True, timeout=5, check=False,
                    )
                    manufacturer = maker_result.stdout.strip().lower()
                except (OSError, subprocess.TimeoutExpired):
                    manufacturer = ""
                devices.append({
                    "serial": serial,
                    "model": model,
                    "manufacturer": manufacturer,
                    "label": model or serial,
                })
            elif state == "unauthorized":
                unauthorized.append(serial)
            elif state == "offline":
                offline.append(serial)
        return {
            "adb": adb, "devices": devices,
            "unauthorized": unauthorized, "offline": offline,
        }

    @staticmethod
    def profile(adb_path, serial):
        """Read a connected Android device's identity and current display profile."""
        def shell_value(*arguments):
            try:
                result = subprocess.run(
                    [adb_path, "-s", serial, "shell", *arguments],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                return result.stdout.strip() if result.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                return ""

        manufacturer = shell_value("getprop", "ro.product.manufacturer")
        model = shell_value("getprop", "ro.product.model")
        device = shell_value("getprop", "ro.product.device")
        width, height = ScreenMirrorManager._display_size(adb_path, serial)
        return {
            "serial": serial,
            "manufacturer": manufacturer,
            "model": model,
            "device": device,
            "width": width,
            "height": height,
        }

    @staticmethod
    def desktop_mode_capability(adb_path, serial):
        """Return the supported vendor desktop mode, if the phone exposes it."""
        def shell(*arguments):
            try:
                result = subprocess.run(
                    [adb_path, "-s", serial, "shell", *arguments],
                    capture_output=True, text=True, timeout=8, check=False,
                )
                return result.stdout.strip() if result.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                return ""

        manufacturer = shell("getprop", "ro.product.manufacturer").lower()
        if "samsung" in manufacturer:
            status = shell("dumpsys", "statusbar")
            tile = "com.sec.android.app.launcher/com.honeyspace.dexservice.DesktopModeTile"
            if tile in status:
                return "DeX"
        elif "motorola" in manufacturer:
            package_path = shell("cmd", "package", "path", "com.motorola.mobiledesktop")
            if package_path.startswith("package:"):
                return "Smart Connect"
        return None

    @staticmethod
    def external_displays(adb_path, serial):
        """Return physical external displays and Android IDs usable for capture."""
        def shell(*arguments):
            try:
                result = subprocess.run(
                    [adb_path, "-s", serial, "shell", *arguments],
                    capture_output=True, text=True, timeout=8, check=False,
                )
                return result.stdout if result.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                return ""

        surfaceflinger = shell("dumpsys", "SurfaceFlinger", "--display-id")
        display_dump = shell("dumpsys", "display")
        display_info = {}
        sections = display_dump.split("DisplayDeviceInfo{")[1:]
        for section in sections:
            unique = re.search(r'uniqueId="([^"]+)"', section)
            size = re.search(r",\s*(\d+) x (\d+),", section)
            logical = re.search(r"mDisplayId=(\d+)", section)
            if not unique:
                continue
            physical = re.search(r"local:(\d+)", unique.group(1))
            display_info[physical.group(1) if physical else unique.group(1)] = {
                "width": int(size.group(1)) if size else None,
                "height": int(size.group(2)) if size else None,
                "logical_id": int(logical.group(1)) if logical else None,
            }

        displays = []
        for line in surfaceflinger.splitlines():
            match = re.search(
                r'Display (\d+) \(HWC display (\d+)\):.*displayName="([^"]*)"',
                line,
            )
            if not match or match.group(2) == "0":
                continue
            physical_id, _hwc_id, name = match.groups()
            info = display_info.get(physical_id, {})
            displays.append({
                "physical_id": physical_id,
                "logical_id": info.get("logical_id"),
                "name": name or "External display",
                "width": info.get("width"),
                "height": info.get("height"),
            })
        return displays

    @classmethod
    def ensure_tools(cls):
        """Install Google's official ADB tools into the user's home folder."""
        if cls._tool_path("adb"):
            return {"adb": cls._tool_path("adb")}
        if not __import__("platform").machine().lower() in ("x86_64", "amd64"):
            raise RuntimeError("Automatic setup currently supports 64-bit Intel/AMD Linux.")

        opt_dir = os.path.expanduser("~/.local/opt/oneguyos-mirroring")
        bin_dir = os.path.expanduser("~/.local/bin")
        os.makedirs(opt_dir, exist_ok=True)
        os.makedirs(bin_dir, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="oneguyos-mirror-") as temp_dir:
            tools_zip = os.path.join(temp_dir, "platform-tools.zip")
            with __import__("urllib.request", fromlist=["urlopen"]).urlopen(
                cls.PLATFORM_TOOLS_URL, timeout=30
            ) as response, open(tools_zip, "wb") as destination:
                shutil.copyfileobj(response, destination)
            tools_dir = os.path.join(opt_dir, "platform-tools")
            os.makedirs(tools_dir, exist_ok=True)
            with zipfile.ZipFile(tools_zip) as archive:
                for name in archive.namelist():
                    target = os.path.realpath(os.path.join(opt_dir, name))
                    if not target.startswith(os.path.realpath(opt_dir) + os.sep):
                        raise ValueError("The Android tools archive contains an unsafe path.")
                    archive.extract(name, opt_dir)

        adb_bin = os.path.join(opt_dir, "platform-tools", "adb")
        if not os.path.isfile(adb_bin):
            raise RuntimeError("The official Android tools archive did not contain adb.")
        os.chmod(adb_bin, os.stat(adb_bin).st_mode | 0o111)
        link = os.path.join(bin_dir, "adb")
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(adb_bin, link)
        return {"adb": adb_bin}

    @staticmethod
    def start_stream(adb_path, serial, display=None):
        """Stream raw H.264 from Android screenrecord over ADB without root."""
        # Preserve the phone's actual aspect ratio. A fixed 720x1280 frame
        # cropped the taller Moto display (720x1612) and some Samsung screens.
        width = display.get("width") if display else None
        height = display.get("height") if display else None
        if not width or not height:
            width, height = ScreenMirrorManager._display_size(adb_path, serial)
        long_edge = 1600
        scale = min(1.0, long_edge / max(width, height))
        capture_width = max(2, int(width * scale) // 2 * 2)
        capture_height = max(2, int(height * scale) // 2 * 2)
        command = [adb_path, "-s", serial, "exec-out", "screenrecord"]
        if display:
            command.extend(("--display-id", display["physical_id"]))
        command.extend((
            "--output-format=h264", "--size",
            f"{capture_width}x{capture_height}",
            "--bit-rate", "2500000", "--time-limit", "0", "-",
        ))
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            bufsize=0,
        )

    @staticmethod
    def _display_size(adb_path, serial):
        """Return Android's logical display size, falling back to a portrait default."""
        try:
            result = subprocess.run(
                [adb_path, "-s", serial, "shell", "wm", "size"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            for line in result.stdout.splitlines():
                match = re.search(r"(\d+)x(\d+)", line)
                if match:
                    return tuple(map(int, match.groups()))
        except (OSError, subprocess.TimeoutExpired):
            pass
        return (720, 1280)

    @staticmethod
    def send_input(adb_path, serial, *arguments, display_id=None, source=None):
        """Send one Android input command without blocking the GTK event loop."""
        command = [adb_path, "-s", serial, "shell", "input"]
        if source:
            command.append(source)
        if display_id is not None:
            command.extend(("-d", str(display_id)))
        command.extend(map(str, arguments))
        return subprocess.run(
            command,
            capture_output=True, text=True, timeout=5, check=False,
        )

    @staticmethod
    def _normalized(value):
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    def resolve(self, kde_device, adb_devices):
        # Exact model matches win. A USB serial already assigned to a different
        # KDE phone must never be borrowed just because its vendor matches.
        kde_name = self._normalized(kde_device.name)
        expected_maker = None
        if "galaxy" in kde_name or "samsung" in kde_name:
            expected_maker = "samsung"
        elif "moto" in kde_name or "motorola" in kde_name:
            expected_maker = "motorola"

        matches = [
            adb_device for adb_device in adb_devices
            if self._normalized(adb_device.get("model", ""))
            and (
                self._normalized(adb_device["model"]) in kde_name
                or kde_name in self._normalized(adb_device["model"])
            )
        ]
        if len(matches) == 1:
            target = matches[0]
            if self.mappings.get(kde_device.id) != target["serial"]:
                self.save_mapping(kde_device.id, target["serial"])
            return target
        if matches:
            # The same Android phone can appear twice when USB and Wireless
            # debugging are both enabled. Reuse only this phone's saved serial
            # when it is among the exact model matches.
            saved_serial = self.mappings.get(kde_device.id)
            saved_matches = [
                adb_device for adb_device in matches
                if adb_device["serial"] == saved_serial
            ]
            if len(saved_matches) == 1:
                return saved_matches[0]
            return None
        if expected_maker:
            saved_serial = self.mappings.get(kde_device.id)
            saved_match = next(
                (
                    adb_device for adb_device in adb_devices
                    if adb_device["serial"] == saved_serial
                    and expected_maker in self._normalized(
                        adb_device.get("manufacturer", "")
                    )
                ),
                None,
            )
            if saved_match:
                return saved_match
            claimed_elsewhere = {
                serial for device_id, serial in self.mappings.items()
                if device_id != kde_device.id
            }
            maker_matches = [
                adb_device for adb_device in adb_devices
                if expected_maker in self._normalized(
                    adb_device.get("manufacturer", "")
                ) and adb_device["serial"] not in claimed_elsewhere
            ]
            if len(maker_matches) == 1:
                target = maker_matches[0]
                self.save_mapping(kde_device.id, target["serial"])
                return target
            return None

        serial = self.mappings.get(kde_device.id)
        if any(
            device_id != kde_device.id and mapped_serial == serial
            for device_id, mapped_serial in self.mappings.items()
        ):
            return None
        for adb_device in adb_devices:
            if adb_device["serial"] == serial:
                return adb_device
        return None

    def save_mapping(self, kde_device_id, serial):
        self.mappings[kde_device_id] = serial
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        temporary_path = f"{self.config_path}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as config_file:
            json.dump(self.mappings, config_file, indent=2)
            config_file.write("\n")
        os.replace(temporary_path, self.config_path)

