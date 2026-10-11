"""Find and connect to the Android companion's local screen stream."""

import ipaddress
import os
import select
import socket
import struct
import sys
import time


_VENDOR = os.path.join(os.path.dirname(__file__), "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

import ifaddr


DISCOVERY_PORT = 43139
DISCOVERY_QUERY = b"OGCONNECT_DISCOVER_V1"
DISCOVERY_RESPONSE = "OGCONNECT/1"
MAX_FRAME_SIZE = 12_000_000


def _local_ipv4_broadcasts():
    broadcasts = set()
    local_addresses = set()
    probe_addresses = set()
    for adapter in ifaddr.get_adapters():
        for address in adapter.ips:
            if not isinstance(address.ip, str) or address.is_IPv6:
                continue
            local_addresses.add(address.ip)
            try:
                network = ipaddress.ip_interface(
                    f"{address.ip}/{address.network_prefix}"
                ).network
            except (ValueError, TypeError):
                continue
            if network.prefixlen >= 8 and not address.ip.startswith("127."):
                broadcasts.add(str(network.broadcast_address))
                # Some phone hotspots drop directed broadcasts. Probe modest
                # directly-connected subnets by unicast as a fallback.
                if network.num_addresses <= 1024:
                    probe_addresses.update(str(host) for host in network.hosts())
    probe_addresses.difference_update(local_addresses)
    return broadcasts, local_addresses, probe_addresses


def discover_active_streams(timeout=2.5):
    """Return phones actively sharing their screen on a directly connected LAN."""
    broadcasts, local_addresses, probe_addresses = _local_ipv4_broadcasts()
    if not broadcasts and not probe_addresses:
        return []

    found = {}
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as discovery:
        discovery.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        discovery.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        discovery.bind(("", 0))
        discovery.setblocking(False)
        unsent = set(probe_addresses)
        next_query = 0.0
        last_response = None
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_query:
                for broadcast in broadcasts:
                    try:
                        discovery.sendto(DISCOVERY_QUERY, (broadcast, DISCOVERY_PORT))
                    except OSError:
                        continue
                # Blocking sends can spend seconds resolving neighbors on a
                # hotspot. Queue accepted probes quickly and retry pressure.
                for address in tuple(unsent):
                    try:
                        discovery.sendto(DISCOVERY_QUERY, (address, DISCOVERY_PORT))
                        unsent.remove(address)
                    except OSError:
                        break
                next_query = now + 0.1
            readable, _, _ = select.select(
                [discovery], [], [], min(0.05, max(0, deadline - now))
            )
            if not readable:
                if found and last_response is not None:
                    if time.monotonic() - last_response >= 0.5:
                        break
                continue
            while True:
                try:
                    payload, source = discovery.recvfrom(512)
                except BlockingIOError:
                    break
                if source[0] in local_addresses:
                    continue
                fields = payload.decode("utf-8", "replace").split("|", 4)
                if len(fields) != 5 or fields[0] != DISCOVERY_RESPONSE:
                    continue
                _version, device_id, manufacturer, model, port_text = fields
                try:
                    port = int(port_text)
                except ValueError:
                    continue
                if not device_id or not 1 <= port <= 65535:
                    continue
                found[device_id] = {
                    "id": device_id,
                    "manufacturer": manufacturer,
                    "model": model,
                    "address": source[0],
                    "port": port,
                }
                last_response = time.monotonic()
    return list(found.values())


def connect_screen_stream(profile, pairing_code, timeout=8):
    """Pair with a phone stream and return its open socket."""
    code = str(pairing_code).strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError("Enter the six-digit code shown on the phone.")
    connection = socket.create_connection(
        (profile["address"], profile["port"]), timeout=timeout
    )
    connection.settimeout(timeout)
    try:
        connection.sendall(f"PIN {code}\n".encode("ascii"))
        response = bytearray()
        while not response.endswith(b"\n") and len(response) <= 64:
            part = connection.recv(1)
            if not part:
                raise ConnectionError("The phone closed the pairing connection.")
            response.extend(part)
        if bytes(response).strip() != b"OK":
            raise PermissionError("The phone rejected that pairing code.")
        connection.settimeout(None)
        return connection
    except Exception:
        connection.close()
        raise


def receive_frame(connection):
    """Read one length-prefixed JPEG frame from a paired phone."""
    header = _receive_exactly(connection, 4)
    size = struct.unpack(">I", header)[0]
    if not 0 < size <= MAX_FRAME_SIZE:
        raise ValueError(f"Invalid phone frame size: {size}")
    return _receive_exactly(connection, size)


def _receive_exactly(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Phone stopped sharing its screen.")
        data.extend(chunk)
    return bytes(data)
