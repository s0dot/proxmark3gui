"""Proxmark3 client backends.

``RealClient``  -- drives the compiled ``proxmark3`` binary as a subprocess,
                   feeding commands on stdin and streaming stdout back.
``MockClient``  -- a faithful offline simulator used when no binary is found,
                   so the GUI is fully usable without hardware.

Both expose the same tiny interface::

    client.send(command_line)      # queue a command (newline added for you)
    client.close()                 # stop the session

and call ``on_output(line)`` for every line produced, with a final
``on_output(None)`` when the session ends.
"""
from __future__ import annotations

import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time

SENTINEL_PREFIX = "__PM3GUI_DONE__"

_CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


# --------------------------------------------------------------------------- #
# Locating the compiled client
# --------------------------------------------------------------------------- #
def find_client(extra=None):
    """Return a path to the proxmark3 client binary, or ``None``."""
    here = os.path.dirname(os.path.abspath(__file__))
    workdir = os.path.dirname(here)  # ...\pm3GUI
    names = ["proxmark3.exe", "proxmark3"] if sys.platform.startswith("win") else ["proxmark3"]

    roots = []
    if extra:
        roots.append(extra if os.path.isdir(extra) else os.path.dirname(extra))
        # also allow `extra` to be the binary itself
        if os.path.isfile(extra):
            return extra
    roots += [
        os.path.join(here, "pm3client"),   # bundled Windows client (shipped in the repo)
        os.path.join(workdir, "proxmark3-master", "client"),
        os.path.join(workdir, "proxmark3-master", "client", "build"),
        os.path.join(workdir, "proxmark3-master"),
        workdir,
    ]
    for root in roots:
        for name in names:
            cand = os.path.join(root, name)
            if os.path.isfile(cand):
                return cand
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def discover_clients():
    """Return a de-duplicated list of all proxmark3 client executables found in
    common locations (bundled tree, Downloads/Desktop packages, PATH), so the
    user can pick the right one instead of typing a path."""
    names = ["proxmark3.exe"] if sys.platform.startswith("win") else ["proxmark3"]
    here = os.path.dirname(os.path.abspath(__file__))
    workdir = os.path.dirname(here)
    found, seen = [], set()

    def add(p):
        if p and os.path.isfile(p):
            ap = os.path.abspath(p)
            if ap.lower() not in seen:
                seen.add(ap.lower())
                found.append(ap)

    roots = [os.path.join(here, "pm3client"),   # bundled Windows client (shipped in the repo)
             os.path.join(workdir, "proxmark3-master", "client"),
             os.path.join(workdir, "proxmark3-master", "client", "build"),
             os.path.join(workdir, "proxmark3-master"), workdir]
    for root in roots:
        for n in names:
            add(os.path.join(root, n))

    home = os.path.expanduser("~")
    for base in (os.path.join(home, "Downloads"), os.path.join(home, "Desktop"),
                 os.path.join(home, "Documents")):
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for name in entries:
            d = os.path.join(base, name)
            if os.path.isdir(d):
                for n in names:
                    add(os.path.join(d, "client", n))
                    add(os.path.join(d, n))

    for n in names:
        w = shutil.which(n)
        if w:
            add(w)
    return found


# --------------------------------------------------------------------------- #
# External companion apps (e.g. the Chameleon Ultra GUI)
# --------------------------------------------------------------------------- #
_ext_procs = {}


def find_chameleon_gui():
    """Locate the bundled Chameleon Ultra GUI executable, or None."""
    names = ["chameleonultragui.exe"] if sys.platform.startswith("win") else ["chameleonultragui"]
    here = os.path.dirname(os.path.abspath(__file__))
    workdir = os.path.dirname(here)  # project root
    roots = [os.path.join(workdir, "chameleon"),
             os.path.join(here, "chameleon"),
             workdir]
    for root in roots:
        for n in names:
            cand = os.path.join(root, n)
            if os.path.isfile(cand):
                return cand
    return None


def launch_external(name, exe):
    """Launch a detached external GUI app. Returns 'launched' or 'already running'."""
    p = _ext_procs.get(name)
    if p is not None and p.poll() is None:
        return "already running"
    flags = 0
    if sys.platform.startswith("win"):
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [exe],
        cwd=os.path.dirname(exe) or None,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _ext_procs[name] = proc
    return "launched"


# --------------------------------------------------------------------------- #
# Locating the MinGW / Qt runtime DLLs (ProxSpace builds)
#
# A ProxSpace-built proxmark3.exe depends on DLLs such as libwinpthread-1.dll,
# libgcc_s_seh-1.dll, libstdc++-6.dll and (for Qt builds) Qt5Core.dll. These
# live in ProxSpace's ...\msys2\mingw64\bin and are only on PATH inside the
# ProxSpace shell. We locate that folder so we can add it to the client's PATH.
# --------------------------------------------------------------------------- #
_RUNTIME_SENTINEL = "libwinpthread-1.dll"  # present in every MinGW build


def _dir_has_runtime(d):
    if not d or not os.path.isdir(d):
        return False
    try:
        names = {n.lower() for n in os.listdir(d)}
    except OSError:
        return False
    return _RUNTIME_SENTINEL in names


def _proxspace_like_roots():
    """A handful of likely parent folders that may contain a ProxSpace install."""
    roots = []
    home = os.path.expanduser("~")
    bases = [home, os.path.join(home, "Desktop"), os.path.join(home, "Documents"),
             os.path.join(home, "Downloads")]
    for drive in "CDEFG":
        dr = drive + ":\\"
        if os.path.isdir(dr):
            bases.append(dr)
    for base in bases:
        try:
            for name in os.listdir(base):
                low = name.lower()
                if "proxspace" in low or "msys" in low:
                    roots.append(os.path.join(base, name))
        except OSError:
            pass
    return roots


def find_runtime_dir(client_path=None, hint=None):
    """Return a folder containing the MinGW/Qt runtime DLLs, or None.

    Search order: explicit hint, next to the client exe, walking up the client's
    path for a msys2/mingw64 tree, then common ProxSpace/MSYS2 install spots.
    """
    if not sys.platform.startswith("win"):
        return None

    cands = []
    if hint:
        cands += [hint,
                  os.path.join(hint, "msys2", "mingw64", "bin"),
                  os.path.join(hint, "mingw64", "bin")]
    if client_path:
        cdir = os.path.dirname(os.path.abspath(client_path))
        cands.append(cdir)  # DLLs may sit next to the exe ...
        cands += [os.path.join(cdir, "libs"),   # ... or in a libs/ or lib/ subfolder
                  os.path.join(cdir, "lib")]    #     (e.g. proxmarkbuilds.org packages)
        p = cdir
        for _ in range(7):  # walk up a ProxSpace tree
            cands += [os.path.join(p, "msys2", "mingw64", "bin"),
                      os.path.join(p, "mingw64", "bin")]
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
    for root in _proxspace_like_roots():
        cands += [os.path.join(root, "msys2", "mingw64", "bin"),
                  os.path.join(root, "mingw64", "bin")]
    cands += [r"C:\msys64\mingw64\bin", r"C:\ProxSpace\msys2\mingw64\bin"]

    seen = set()
    for d in cands:
        d = os.path.normpath(d)
        key = d.lower()
        if key in seen:
            continue
        seen.add(key)
        if _dir_has_runtime(d):
            return d
    return None


def find_qt_platform_dir(runtime_dir=None, client_dir=None):
    """Return the folder that holds the Qt platform plugin ``qwindows.dll``.

    A Qt client won't even start without it. Packages put it either directly in
    the runtime folder (proxmarkbuilds.org) or in a ``platforms`` subfolder
    (standard Qt deployment); we handle both."""
    if not sys.platform.startswith("win"):
        return None
    cands = []
    for base in (runtime_dir, client_dir):
        if base:
            cands += [base, os.path.join(base, "platforms")]
    for d in cands:
        if d and os.path.isfile(os.path.join(d, "qwindows.dll")):
            return d
    return None


# --------------------------------------------------------------------------- #
# Real client
# --------------------------------------------------------------------------- #
class RealClient:
    mode = "live"

    def __init__(self, client_path, port, on_output, baud=None, runtime_dir=None):
        self.on_output = on_output
        cdir = os.path.dirname(os.path.abspath(client_path))
        args = [client_path, "-p", port, "-f"]
        if baud:
            args += ["-b", str(baud)]

        # Make the MinGW/Qt runtime DLLs resolvable for the child process by
        # prepending the runtime folder (and the client's own dir) to PATH.
        env = os.environ.copy()
        extra = [d for d in (runtime_dir, cdir) if d and os.path.isdir(d)]
        if extra:
            env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
        # Qt clients need their platform plugin (qwindows.dll) to initialize.
        qt_dir = find_qt_platform_dir(runtime_dir, cdir)
        if qt_dir and "QT_QPA_PLATFORM_PLUGIN_PATH" not in env:
            env["QT_QPA_PLATFORM_PLUGIN_PATH"] = qt_dir

        self.proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_CREATE_NO_WINDOW,
            cwd=cdir or None,
            env=env,
        )
        self._alive = True
        self.got_output = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def wait_started(self, timeout=1.5):
        """Decide whether the client actually launched.

        Returns False only when the process dies *without printing anything* —
        the signature of a DLL/load failure (the loader fails before main()).
        A client that prints its banner counts as started, even if it then exits
        because no device is attached, so a missing-device exit is not misreported
        as a missing-DLL error."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.got_output:
                return True
            if self.proc.poll() is not None:
                return self.got_output
            time.sleep(0.03)
        return self.is_alive() or self.got_output

    def is_alive(self):
        return self._alive and self.proc.poll() is None

    def _read_loop(self):
        try:
            for line in self.proc.stdout:
                self.got_output = True
                self.on_output(line.rstrip("\r\n"))
        except (ValueError, OSError):
            pass
        self._alive = False
        self.on_output(None)

    def send(self, cmd):
        if not self._alive:
            return
        try:
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            self._alive = False

    def close(self):
        if not self._alive:
            return
        self._alive = False
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()


# --------------------------------------------------------------------------- #
# Mock client (offline demo)
# --------------------------------------------------------------------------- #
class MockClient:
    mode = "demo"

    def __init__(self, port, on_output):
        self.on_output = on_output
        self.port = port
        self._q = queue.Queue()
        self._alive = True
        self.state = {"lf_id": None, "lf_type": None, "hf_uid": None}
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        self._emit_lines(_banner(port), fast=True)

    # public API ----------------------------------------------------------- #
    def send(self, cmd):
        if self._alive:
            self._q.put(cmd)

    def close(self):
        self._alive = False
        self._q.put(None)

    # internals ------------------------------------------------------------ #
    def _run(self):
        while True:
            cmd = self._q.get()
            if cmd is None or not self._alive:
                break
            self._handle(cmd)
        self.on_output(None)

    def _emit_lines(self, lines, fast=False):
        for ln in lines:
            if not self._alive:
                return
            self.on_output(ln)
            if not fast:
                time.sleep(random.uniform(0.012, 0.05))

    def _handle(self, cmd):
        c = cmd.strip()
        low = c.lower()

        if low.startswith("rem "):  # sentinel / remark -> echo it back like the real client
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            self.on_output(f"[+] {ts} remark: {c[4:].strip()}")
            return
        if low in ("quit", "exit", "q"):
            self._alive = False
            return

        lines = self._respond(low, c)
        self._emit_lines(lines)

    # ------------------------------------------------------------------ #
    # Canned responses.  These mirror the look of real Proxmark3 output.
    # ------------------------------------------------------------------ #
    def _respond(self, low, raw):
        for prefix, fn in _DISPATCH:
            if low == prefix or low.startswith(prefix + " ") or low == prefix.strip():
                return fn(self, raw)
        # bare category help
        if low in ("hw", "lf", "hf", "data", "analyse"):
            return _category_help(low)
        return [
            f"[#] (demo) no canned output for '{raw}'.",
            "[?] Connect a real Proxmark3 + compiled client for live results.",
        ]


# ---- mock response builders ---------------------------------------------- #
def _banner(port):
    return [
        f"[=] Session log will be written",
        f"[+] loaded preferences",
        f"[=] Using UART port {port}",
        f"[=] Communicating with PM3 over USB-CDC",
        "",
        "  \x1b[34m8888888b.  888b     d888  .d8888b.\x1b[0m",
        "  \x1b[34m888   Y88b 8888b   d8888 d88P  Y88b\x1b[0m",
        "  \x1b[34m888    888 88888b.d88888      .d88P\x1b[0m",
        "  \x1b[34m888   d88P 888Y88888P888     8888\"\x1b[0m",
        "  \x1b[34m8888888P\"  888 Y888P 888      \"Y8b.\x1b[0m",
        "  \x1b[34m888        888  Y8P  888 888    888\x1b[0m   [ \x1b[33mDEMO MODE\x1b[0m ]",
        "  \x1b[34m888        888   \"   888 Y88b  d88P\x1b[0m",
        "  \x1b[34m888        888       888  \"Y8888P\"\x1b[0m",
        "",
        "  [ Proxmark3 RFID instrument ]",
        "",
        "    MCU....... AT91SAM7S512 Rev B",
        "    Memory.... 512 KB ( 47% used )",
        "",
        "    Client.... Iceman/master/v4.x (GUI demo)",
        "    Bootrom... Iceman/master/v4.x",
        "    OS........ Iceman/master/v4.x",
        "",
        "[+] Running in DEMO MODE -- responses are simulated.",
        "",
    ]


def _hw_version(self, raw):
    return [
        "[=] --- Proxmark3 Firmware ----------------------",
        "[+]  Client..... Iceman/master/v4.x (GUI demo)",
        "[+]  Bootrom.... Iceman/master/v4.x",
        "[+]  OS........ Iceman/master/v4.x",
        "[=]  Target.... RDV4",
        "[=] --- Hardware --------------------------------",
        "[=]  MCU....... AT91SAM7S512 Rev B",
        "[=]  Memory.... 512 KB",
    ]


def _hw_status(self, raw):
    return [
        "[=] --- Memory ----------------------------------",
        "[=]   BIGBUF_SIZE.......... 40000",
        "[=]   Available memory.... 40000",
        "[=] --- Tag Field -------------------------------",
        "[=]   HF field.... disabled",
        "[=]   LF field.... disabled",
        "[=] --- Operating Modes -------------------------",
        "[=]   Slow clock........... 30000 Hz",
        "[+]   USB.... connected",
        "[+] Client/Device... time in sync",
    ]


def _hw_tune(self, raw):
    lf = 30 + random.uniform(-3, 6)
    lf134 = lf * 0.8
    hf = 40 + random.uniform(-3, 6)
    return [
        "[=] Measuring antenna characteristics, please wait...",
        "[=]  ............",
        f"[+] LF antenna: {lf:5.2f} V - 125.00 kHz",
        f"[+] LF antenna: {lf134:5.2f} V - 134.00 kHz",
        f"[+] LF optimal: {lf + 0.3:5.2f} V - 125.00 kHz",
        "[+] LF antenna is " + ("\x1b[32mOK\x1b[0m" if lf > 20 else "\x1b[31mLOW\x1b[0m"),
        f"[+] HF antenna: {hf:5.2f} V - 13.56 MHz",
        "[+] HF antenna is " + ("\x1b[32mOK\x1b[0m" if hf > 20 else "\x1b[31mLOW\x1b[0m"),
        "[=] Displaying LF tuning graph...",
    ]


def _lf_search(self, raw):
    self.state["lf_id"] = "0F0368568B"
    self.state["lf_type"] = "EM410x"
    return [
        "[=] NOTE: some demods output possible binary",
        "[=] checking for known tags...",
        "",
        "[+] \x1b[32mEM 410x ID 0F0368568B\x1b[0m",
        "[+] EM410x ( RF/64 )",
        "[=] -------- Possible de-scramble patterns ---------",
        "[+] Unique TAG ID....... F0C06A1AD1",
        "[+] HoneyWell IdentKey",
        "[+]     DEZ 8.......... 06641547",
        "[+]     DEZ 10.......... 0057153675",
        "[=] ------------------------------------------------",
        "",
        "[+] Valid \x1b[32mEM410x\x1b[0m ID found!",
        "",
        "[+] Couldn't identify a chip in HF, try `hf search`",
    ]


def _hf_search(self, raw):
    self.state["hf_uid"] = "04 7A 8C B2 19 50 80"
    return [
        "[=] Checking for known tags...",
        "",
        "[+]  UID: 04 7A 8C B2 19 50 80",
        "[+] ATQA: 00 44",
        "[+]  SAK: 00 [2]",
        "[+] Possible types:",
        "[+]    MIFARE Ultralight EV1 48bytes (MF0UL1101)",
        "",
        "[+] Valid \x1b[32mISO 14443-A\x1b[0m tag found",
    ]


def _hf_14a_info(self, raw):
    self.state["hf_uid"] = "04 7A 8C B2 19 50 80"
    return [
        "[=] --- ISO14443-A Information ---------------------",
        "[+]  UID: 04 7A 8C B2 19 50 80",
        "[+] ATQA: 00 44",
        "[+]  SAK: 00 [2]",
        "[=]  MANUFACTURER: NXP Semiconductors Germany",
        "[+] Possible types:",
        "[+]    MIFARE Ultralight EV1 48bytes (MF0UL1101)",
        "[=] proprietary non iso14443-4 card found, RATS not supported",
        "[?] Hint: try `hf mfu info`",
    ]


def _lf_em_reader(self, raw):
    self.state["lf_id"] = "0F0368568B"
    self.state["lf_type"] = "EM410x"
    return [
        "[+] EM 410x ID 0F0368568B",
        "[+] EM410x ( RF/64 )",
        "[=]  DEZ 10.......... 0057153675",
    ]


def _lf_hid_read(self, raw):
    return [
        "[+] \x1b[32mHID\x1b[0m H10301 26-bit",
        "[+]  bin: 1010000010..",
        "[+]  FC: 123  CN: 4567",
        "[+]  Raw: 2004263f88",
    ]


def _lf_t55_detect(self, raw):
    return [
        "[+] Chip type......... T55x7",
        "[+] Modulation....... ASK",
        "[+] Bit rate......... 5 - RF/64",
        "[+] Inverted......... No",
        "[+] Offset........... 33",
        "[+] Seq. terminator.. Yes",
        "[+] Block0........... 00148040",
        "[+] Downlink mode.... default/fixed bit length",
        "[+] Password set..... No",
    ]


def _lf_clone(self, raw):
    return [
        "[=] Preparing to clone to T55x7 tag...",
        "[=] Writing block 0...",
        "[=] Writing block 1...",
        "[=] Writing block 2...",
        "[+] \x1b[32mDone!\x1b[0m",
        "[?] Hint: try `lf search` to verify",
    ]


def _hf_mf_info(self, raw):
    return [
        "[=] --- ISO14443-A Information ---------------------",
        "[+]  UID: 9A 5C 2E F1",
        "[+] ATQA: 00 04",
        "[+]  SAK: 08 [2]",
        "[+] Possible types: MIFARE Classic 1K",
        "[=] --- Fingerprint ------------------------------",
        "[+] Magic capabilities... Gen 1a",
        "[+] Prng detection....... weak",
        "[?] Hint: try `hf mf autopwn`",
    ]


def _hf_mf_autopwn(self, raw):
    out = [
        "[=] Loading default keys dictionary...",
        "[+] target sector  0 key A using known key... 0xFFFFFFFFFFFF",
        "[=] running nested attack...",
        "[+] found 32 / 32 keys",
        "[=] Reading sectors with found keys...",
    ]
    for s in range(0, 16, 3):
        out.append(f"[+]   sector {s:2}... \x1b[32mok\x1b[0m")
    out += [
        "[+] \x1b[32mTransfer to emulator memory complete\x1b[0m",
        "[+] Saved 1024 bytes to binary file hf-mf-9A5C2EF1-dump.bin",
    ]
    return out


def _hf_mf_dump(self, raw):
    return [
        "[=] Dumping all blocks using found keys...",
        "[+] read 64 / 64 blocks",
        "[+] Saved 1024 bytes to binary file hf-mf-9A5C2EF1-dump.bin",
        "[+] Saved to json file hf-mf-9A5C2EF1-dump.json",
    ]


def _hf_iclass_info(self, raw):
    return [
        "[=] --- iCLASS / Picopass ------------------------",
        "[+]    CSN: 9B 84 4D 00 FB FF 12 E0",
        "[+] Config: 12 FF FF FF 7F 1F FF 3C",
        "[+]  AA1...: secured page,  block 6 readable",
        "[=]   Card type: iCLASS Legacy",
    ]


def _hf_iclass_dump(self, raw):
    return [
        "[=] Reading tag memory...",
        "[+] read 0x12 blocks",
        "[+] Saved 288 bytes to binary file hf-iclass-9B844D00-dump.bin",
    ]


def _auto(self, raw):
    return (
        ["[=] lf search", ""]
        + _lf_search(self, raw)
        + ["", "[=] hf search", ""]
        + _hf_search(self, raw)
    )


def _category_help(cat):
    tables = {
        "hw": [("status", "Show runtime status"), ("version", "Show version info"),
               ("tune", "Measure antenna tuning"), ("ping", "Test connection")],
        "lf": [("search", "Read and identify a LF tag"), ("em", "{ EM susbsystem }"),
               ("hid", "{ HID prox }"), ("t55xx", "{ T55xx / T5577 }"), ("indala", "{ Indala }")],
        "hf": [("search", "Read and identify a HF tag"), ("14a", "{ ISO 14443A }"),
               ("mf", "{ MIFARE Classic }"), ("iclass", "{ iCLASS / Picopass }"), ("mfu", "{ Ultralight }")],
        "data": [("plot", "Show graph window"), ("save", "Save trace buffer"), ("clear", "Clear buffer")],
        "analyse": [("lcr", "LRC over bytes"), ("crc", "CRC over bytes")],
    }
    rows = tables.get(cat, [])
    out = [f"[=] --- {cat} commands " + "-" * 28]
    for name, desc in rows:
        out.append(f"[=]   {name:10} {desc}")
    return out


_DISPATCH = [
    ("hw version", _hw_version),
    ("hw status", _hw_status),
    ("hw tune", _hw_tune),
    ("hw ping", lambda s, r: ["[+] PM3 Ping... \x1b[32mok\x1b[0m"]),
    ("auto", _auto),
    ("lf search", _lf_search),
    ("lf em 410x reader", _lf_em_reader),
    ("lf em 410x read", _lf_em_reader),
    ("lf em 410x clone", _lf_clone),
    ("lf hid read", _lf_hid_read),
    ("lf hid reader", _lf_hid_read),
    ("lf hid clone", _lf_clone),
    ("lf indala clone", _lf_clone),
    ("lf t55xx detect", _lf_t55_detect),
    ("lf t55xx wipe", lambda s, r: ["[=] Wiping T55x7 tag...", "[+] \x1b[32mDone!\x1b[0m"]),
    ("hf search", _hf_search),
    ("hf 14a info", _hf_14a_info),
    ("hf 14a reader", _hf_14a_info),
    ("hf mf info", _hf_mf_info),
    ("hf mf autopwn", _hf_mf_autopwn),
    ("hf mf dump", _hf_mf_dump),
    ("hf iclass info", _hf_iclass_info),
    ("hf iclass dump", _hf_iclass_dump),
]
