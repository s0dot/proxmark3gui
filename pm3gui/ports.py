"""Serial-port enumeration for the Proxmark3 GUI.

Dependency-free: on Windows we read the registry (``winreg`` is stdlib) and
optionally enrich the result with a best-effort WMI query via PowerShell to
flag ports that look like a Proxmark3.  On POSIX we glob the usual /dev nodes.
"""
from __future__ import annotations

import glob
import json
import subprocess
import sys
import time

# USB VID/PID combos used by Proxmark3 devices (mirrors the upstream `pm3` script)
_PM3_SIGNATURES = (
    ("VID_9AC4&PID_4B8F", "Proxmark3 RDV4"),
    ("VID_2D2D&PID_504D", "Proxmark3 (generic)"),
    ("VID_10C4&PID_EA60", "Proxmark3 BT add-on (HC-06 dongle)"),
)

_CACHE = {"ts": 0.0, "data": None}
_CACHE_TTL = 2.0  # seconds


def list_ports(use_cache: bool = True):
    """Return a list of ``{device, description, is_pm3}`` dicts."""
    now = time.time()
    if use_cache and _CACHE["data"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["data"]

    if sys.platform.startswith("win"):
        data = _list_windows()
    else:
        data = _list_posix()

    _CACHE["ts"] = now
    _CACHE["data"] = data
    return data


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
def _list_windows():
    ports = {}

    # 1) Authoritative list of existing COM ports from the registry.
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        i = 0
        while True:
            try:
                _name, value, _type = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            ports[value] = {"device": value, "description": "Serial port", "is_pm3": False}
        winreg.CloseKey(key)
    except OSError:
        pass

    # 2) Enrich with friendly names / PM3 detection via WMI (best effort).
    for entry in _wmi_serialports():
        dev = entry.get("DeviceID")
        if not dev:
            continue
        pnp = (entry.get("PNPDeviceID") or "").upper()
        desc = entry.get("Description") or entry.get("Name") or "Serial port"
        is_pm3 = False
        for sig, label in _PM3_SIGNATURES:
            if sig in pnp:
                is_pm3 = True
                desc = label
                break
        ports[dev] = {"device": dev, "description": desc, "is_pm3": is_pm3}

    out = sorted(ports.values(), key=lambda p: (not p["is_pm3"], _com_sort_key(p["device"])))
    return out


def _wmi_serialports():
    """Query Win32_SerialPort via PowerShell. Returns [] on any failure."""
    ps = (
        "Get-CimInstance -ClassName Win32_SerialPort | "
        "Select-Object DeviceID,Description,PNPDeviceID,Name | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=6,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return data if isinstance(data, list) else []


def _com_sort_key(name: str):
    # Sort COM3, COM4, COM10 numerically rather than lexically.
    digits = "".join(ch for ch in name if ch.isdigit())
    return (name[:3].upper(), int(digits) if digits else 0)


# --------------------------------------------------------------------------- #
# POSIX (Linux / macOS) -- so the server also runs there
# --------------------------------------------------------------------------- #
def _list_posix():
    patterns = ["/dev/ttyACM*", "/dev/ttyUSB*", "/dev/tty.usbmodem*", "/dev/tty.usbserial*", "/dev/cu.*"]
    found = []
    for pat in patterns:
        for dev in glob.glob(pat):
            found.append({"device": dev, "description": "Serial port", "is_pm3": "ACM" in dev})
    seen, out = set(), []
    for f in found:
        if f["device"] not in seen:
            seen.add(f["device"])
            out.append(f)
    return out


if __name__ == "__main__":
    for p in list_ports(use_cache=False):
        flag = "  <-- looks like Proxmark3" if p["is_pm3"] else ""
        print(f'{p["device"]:10} {p["description"]}{flag}')
