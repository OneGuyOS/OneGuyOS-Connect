"""
OneGuyOS Connect - D-Bus Service Interface
Interfaces directly with the underlying connection daemon.
"""

import os
import subprocess
import unicodedata
from typing import Dict, List, Optional, Callable
import dbus
from dbus.mainloop.glib import DBusGMainLoop

DBUS_SERVICE = "org.kde.kdeconnect"
DAEMON_PATH = "/modules/kdeconnect"
DAEMON_IFACE = "org.kde.kdeconnect.daemon"
DEVICE_IFACE = "org.kde.kdeconnect.device"
BATTERY_IFACE = "org.kde.kdeconnect.device.battery"
PING_IFACE = "org.kde.kdeconnect.device.ping"
FIND_IFACE = "org.kde.kdeconnect.device.findmyphone"
SHARE_IFACE = "org.kde.kdeconnect.device.share"
CLIPBOARD_IFACE = "org.kde.kdeconnect.device.clipboard"
SFTP_IFACE = "org.kde.kdeconnect.device.sftp"
LOCK_IFACE = "org.kde.kdeconnect.device.lockdevice"
NOTIFICATIONS_IFACE = "org.kde.kdeconnect.device.notifications"
MPRIS_IFACE = "org.kde.kdeconnect.device.mprisremote"
NOTIFICATION_IFACE = "org.kde.kdeconnect.device.notifications.notification"


def _clean_notification_text(value, limit: int) -> str:
    """Keep phone-provided notification text compact and safe for the dashboard."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(
        ch
        for ch in text
        if ch in "\n\t" or unicodedata.category(ch) not in {"Cc", "Cf", "Cs"}
    )
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


class PhoneNotification:
    def __init__(self, notification_id: str):
        self.id = notification_id
        self.app_name = "Phone"
        self.title = "Notification"
        self.text = ""
        self.icon_path = ""
        self.dismissable = False
        self.reply_id = ""
        self.silent = False

    @property
    def repliable(self) -> bool:
        return bool(self.reply_id)


class DeviceInfo:
    def __init__(self, dev_id: str):
        self.id = dev_id
        self.name: str = dev_id
        self.hardware_model: str = ""
        self.manufacturer: str = ""
        self.device_type: str = "unknown"
        self.is_paired: bool = False
        self.is_reachable: bool = False
        self.battery_charge: int = -1
        self.is_charging: bool = False
        self.sftp_mounted: bool = False
        self.sftp_mount_point: str = ""

    @property
    def display_name(self) -> str:
        """Prefer the live hardware model while keeping the pairing alias intact."""
        return self.hardware_model or self.name

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "device_type": self.device_type,
            "is_paired": self.is_paired,
            "is_reachable": self.is_reachable,
            "battery_charge": self.battery_charge,
            "is_charging": self.is_charging,
            "sftp_mounted": self.sftp_mounted,
            "sftp_mount_point": self.sftp_mount_point,
        }


class ConnectBackend:
    def __init__(self):
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self.callbacks: List[Callable[[], None]] = []
        self._setup_daemon_proxy()

    def _setup_daemon_proxy(self):
        try:
            self.daemon_obj = self.bus.get_object(DBUS_SERVICE, DAEMON_PATH)
            self.daemon_iface = dbus.Interface(self.daemon_obj, DAEMON_IFACE)
        except Exception as e:
            self.daemon_obj = None
            self.daemon_iface = None
            print(f"[ConnectBackend] Error connecting to daemon: {e}")

    def on_change(self, callback: Callable[[], None]):
        self.callbacks.append(callback)

    def notify_change(self):
        for cb in self.callbacks:
            try:
                cb()
            except Exception as e:
                print(f"[ConnectBackend] Callback error: {e}")

    def refresh_discovery(self):
        if not self.daemon_iface:
            self._setup_daemon_proxy()
        if self.daemon_iface:
            try:
                self.daemon_iface.forceOnNetworkChange()
            except Exception as e:
                print(f"[ConnectBackend] forceOnNetworkChange error: {e}")

    def get_devices(self) -> List[DeviceInfo]:
        if not self.daemon_iface:
            self._setup_daemon_proxy()
            if not self.daemon_iface:
                return []

        devices = []
        try:
            dev_ids = list(self.daemon_iface.devices(False, False))
        except Exception as e:
            print(f"[ConnectBackend] devices call error: {e}")
            return []

        for dev_id in dev_ids:
            info = self.get_device_info(str(dev_id))
            if info:
                devices.append(info)

        return devices

    def get_device_info(self, dev_id: str) -> Optional[DeviceInfo]:
        info = DeviceInfo(dev_id)
        dev_path = f"/modules/kdeconnect/devices/{dev_id}"
        try:
            dev_obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            props_iface = dbus.Interface(dev_obj, "org.freedesktop.DBus.Properties")

            try:
                info.name = str(props_iface.Get(DEVICE_IFACE, "name"))
            except Exception:
                pass

            try:
                info.device_type = str(props_iface.Get(DEVICE_IFACE, "type"))
            except Exception:
                pass

            try:
                info.is_paired = bool(props_iface.Get(DEVICE_IFACE, "isPaired"))
            except Exception:
                pass

            try:
                info.is_reachable = bool(props_iface.Get(DEVICE_IFACE, "isReachable"))
            except Exception:
                pass

            # Battery
            try:
                bat_obj = self.bus.get_object(DBUS_SERVICE, f"{dev_path}/battery")
                bat_props = dbus.Interface(bat_obj, "org.freedesktop.DBus.Properties")
                info.battery_charge = int(bat_props.Get(BATTERY_IFACE, "charge"))
                info.is_charging = bool(bat_props.Get(BATTERY_IFACE, "isCharging"))
            except Exception:
                pass

            # SFTP
            try:
                sftp_obj = self.bus.get_object(DBUS_SERVICE, f"{dev_path}/sftp")
                sftp_iface = dbus.Interface(sftp_obj, SFTP_IFACE)
                info.sftp_mounted = bool(sftp_iface.isMounted())
                if info.sftp_mounted:
                    info.sftp_mount_point = str(sftp_iface.mountPoint())
            except Exception:
                pass

            return info
        except Exception as e:
            print(f"[ConnectBackend] Error getting device {dev_id}: {e}")
            return None

    def send_ping(self, dev_id: str, custom_msg: Optional[str] = None) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/ping"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, PING_IFACE)
            if custom_msg:
                iface.sendPing(custom_msg, signature="s")
            else:
                iface.sendPing(signature="")
            return True
        except Exception as e:
            print(f"[ConnectBackend] send_ping error: {e}")
            return False

    def ring_phone(self, dev_id: str) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/findmyphone"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, FIND_IFACE)
            iface.ring()
            return True
        except Exception as e:
            print(f"[ConnectBackend] ring_phone error: {e}")
            return False

    def send_clipboard(self, dev_id: str, text: Optional[str] = None) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/clipboard"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, CLIPBOARD_IFACE)
            if text is not None:
                iface.sendClipboard(text, signature="s")
            else:
                iface.sendClipboard(signature="")
            return True
        except Exception as e:
            print(f"[ConnectBackend] send_clipboard error: {e}")
            return False

    def share_text(self, dev_id: str, text: str) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/share"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, SHARE_IFACE)
            iface.shareText(text)
            return True
        except Exception as e:
            print(f"[ConnectBackend] share_text error: {e}")
            return False

    def share_url(self, dev_id: str, url: str) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/share"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, SHARE_IFACE)
            iface.shareUrl(url)
            return True
        except Exception as e:
            print(f"[ConnectBackend] share_url error: {e}")
            return False

    def share_file(self, dev_id: str, file_path: str) -> bool:
        try:
            if not os.path.exists(file_path):
                return False
            # Use file URL
            abs_path = os.path.abspath(file_path)
            file_url = f"file://{abs_path}"
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/share"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, SHARE_IFACE)
            iface.openFile(file_url)
            return True
        except Exception as e:
            print(f"[ConnectBackend] share_file error: {e}")
            return False

    def mount_sftp(self, dev_id: str) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/sftp"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, SFTP_IFACE)
            res = iface.mountAndWait()
            return bool(res)
        except Exception as e:
            print(f"[ConnectBackend] mount_sftp error: {e}")
            return False

    def browse_sftp(self, dev_id: str) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/sftp"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, SFTP_IFACE)
            res = iface.startBrowsing()
            return bool(res)
        except Exception as e:
            print(f"[ConnectBackend] browse_sftp error: {e}")
            return False

    def sftp_mount_error(self, dev_id: str) -> str:
        """Return KDE Connect's latest phone-side SFTP error, if available."""
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/sftp"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, SFTP_IFACE)
            return str(iface.getMountError())
        except Exception as e:
            print(f"[ConnectBackend] sftp_mount_error error: {e}")
            return ""

    def unmount_sftp(self, dev_id: str) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/sftp"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, SFTP_IFACE)
            iface.unmount()
            return True
        except Exception as e:
            print(f"[ConnectBackend] unmount_sftp error: {e}")
            return False

    def lock_device(self, dev_id: str, lock: bool = True) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/lockdevice"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, LOCK_IFACE)
            iface.setLocked(lock)
            return True
        except Exception as e:
            print(f"[ConnectBackend] lock_device error: {e}")
            return False

    def mute_device(self, dev_id: str, muted: bool = True) -> bool:
        try:
            dev_path = f"/modules/kdeconnect/devices/{dev_id}/remotesystemvolume"
            obj = self.bus.get_object(DBUS_SERVICE, dev_path)
            iface = dbus.Interface(obj, "org.kde.kdeconnect.device.remotesystemvolume")
            # First try sink "default" or empty string
            iface.sendMuted("", muted)
            return True
        except Exception as e:
            print(f"[ConnectBackend] mute_device error: {e}")
            return False

    def get_notifications(self, dev_id: str) -> List[PhoneNotification]:
        """Return the phone notifications currently mirrored by KDE Connect."""
        base_path = f"/modules/kdeconnect/devices/{dev_id}/notifications"
        notifications: List[PhoneNotification] = []
        try:
            manager_obj = self.bus.get_object(DBUS_SERVICE, base_path)
            manager = dbus.Interface(manager_obj, NOTIFICATIONS_IFACE)
            notification_ids = list(manager.activeNotifications())
        except Exception as e:
            print(f"[ConnectBackend] get_notifications error: {e}")
            return []

        for notification_id in notification_ids:
            notification_id = str(notification_id)
            try:
                obj = self.bus.get_object(
                    DBUS_SERVICE, f"{base_path}/{notification_id}"
                )
                props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
                values = props.GetAll(NOTIFICATION_IFACE)
                item = PhoneNotification(notification_id)
                item.app_name = _clean_notification_text(
                    values.get("appName", "Phone"), 60
                ) or "Phone"
                item.title = _clean_notification_text(
                    values.get("title", "Notification"), 100
                ) or "Notification"
                item.text = _clean_notification_text(values.get("text", ""), 320)
                item.icon_path = str(values.get("iconPath", ""))
                item.dismissable = bool(values.get("dismissable", False))
                item.reply_id = str(values.get("replyId", ""))
                item.silent = bool(values.get("silent", False))
                notifications.append(item)
            except Exception as e:
                print(
                    f"[ConnectBackend] notification {notification_id} read error: {e}"
                )
        return notifications

    def dismiss_notification(self, dev_id: str, notification_id: str) -> bool:
        try:
            path = (
                f"/modules/kdeconnect/devices/{dev_id}/notifications/"
                f"{notification_id}"
            )
            obj = self.bus.get_object(DBUS_SERVICE, path)
            dbus.Interface(obj, NOTIFICATION_IFACE).dismiss()
            return True
        except Exception as e:
            print(f"[ConnectBackend] dismiss_notification error: {e}")
            return False

    def reply_to_notification(
        self, dev_id: str, notification_id: str, message: str
    ) -> bool:
        try:
            path = (
                f"/modules/kdeconnect/devices/{dev_id}/notifications/"
                f"{notification_id}"
            )
            obj = self.bus.get_object(DBUS_SERVICE, path)
            dbus.Interface(obj, NOTIFICATION_IFACE).sendReply(message)
            return True
        except Exception as e:
            print(f"[ConnectBackend] reply_to_notification error: {e}")
            return False

