# Proxmark3 GUI

A lightweight, browser-based GUI for the [Iceman Proxmark3](https://github.com/RfidResearchGroup/proxmark3)
command-line client. It runs a tiny local web server (Python **standard library only** —
no `pip install` needed) that wraps the `proxmark3` client: pick a serial port, connect,
and drive common RFID workflows with buttons while watching live console output.

> **Platform: Windows x64.** A complete Iceman client (`proxmark3.exe` + its Qt/MinGW
> runtime) is **bundled** in `pm3gui/pm3client/`, so you just clone and run — no compiler,
> no `pip`, no extra downloads. (The bundled binary is Windows-64-bit only.)

![stack](https://img.shields.io/badge/stack-Python%20stdlib%20%2B%20HTML%2FJS-blue)
![platform](https://img.shields.io/badge/client-Windows%20x64%20(bundled)-success)

## Quick start

Requires **Python 3** (uses only the standard library). Then double-click
**`Start-PM3-GUI.bat`**, or:

```powershell
python pm3gui\server.py
```

A browser opens at <http://127.0.0.1:8765>. Stop the server with `Ctrl+C`.

```
Options:
  --port N        port to serve on (default 8765, auto-increments if busy)
  --host ADDR     bind address (default 127.0.0.1)
  --no-browser    don't auto-open a browser
```

## Live mode vs. Demo mode

| Mode | When | Behaviour |
|------|------|-----------|
| **LIVE** | bundled client + a real Proxmark3 on a COM port | spawns `proxmark3 -p <port> -f`, pipes commands to it, streams real output |
| **DEMO** | you pick *“Demo device”* (or no client present) | responses are simulated so you can explore the UI without hardware |

A coloured **LIVE / DEMO** badge in the top bar always tells you which one you're in.
The GUI auto-selects the bundled `pm3gui/pm3client/proxmark3.exe` and wires up its runtime
(adds `libs\` to `PATH` and sets `QT_QPA_PLATFORM_PLUGIN_PATH`) automatically.

> **To go live:** plug in your Proxmark3, hit refresh ⟳, pick the `★`-tagged COM port,
> and **Connect**. Use the **⚙ Settings** gear only if you want to point at a *different*
> `proxmark3.exe`.

> ⚠️ **Client ↔ firmware must match.** The Iceman client refuses to talk to a Proxmark3
> running different firmware (`Capabilities structure version … not the same`). Flash your
> device to the bundled build first with **`pm3gui/pm3client/pm3-flash-all`** (or the
> matching firmware images `bootrom.elf` / `fullimage.elf` in that folder).

### Choosing the client (⚙ Settings)

Open the **gear icon** in the top bar to pick which `proxmark3.exe` to drive:

* The GUI **auto-discovers** clients in the bundled tree, your `Downloads`/`Desktop`
  packages (e.g. a `proxmarkbuilds.org` build), and `PATH` — pick one from the dropdown,
  or type a full path.
* It also handles the **Windows runtime** automatically: it adds the build's `libs\`
  (MinGW + Qt DLLs) to the client's `PATH` and points `QT_QPA_PLATFORM_PLUGIN_PATH` at the
  Qt platform plugin (`qwindows.dll`) — the same thing the package's own `setup.bat` does.
  If a build keeps its DLLs elsewhere, set **Runtime folder** manually.

> **Client ↔ firmware must match.** The Iceman client refuses to talk to a Proxmark3 running
> different firmware (`Capabilities structure version … not the same`). Flash the device with
> the firmware from the *same* package (e.g. `pm3-flash-all.bat`) before connecting.

## What's in the box

* **Dashboard** — one-click *Auto detect*, plus `hw status` / `hw version` / `hw tune` / `hw ping`.
* **Identify** — LF search, HF search, NFC (ISO-14443A) info; parsed UID/ATQA/SAK/ID chips.
* **LF Clone** — read EM410x / HID / Indala / T55xx, then write to a blank **T5577**
  (auto-fills the ID it just read).
* **HF Dump** — MIFARE Classic `info` / `autopwn` / `dump`, and iCLASS `info` / `dump`.
* **Console** — full command line with history (↑/↓), ANSI colours, autoscroll. Type *any*
  pm3 command here — the panels are just shortcuts.

## How it talks to the client

```
browser  ──HTTP──►  server.py  ──stdin pipe──►  proxmark3 -p COMx -f
   ▲                    │                              │
   └── long-poll  ◄─────┴────────── stdout lines ◄─────┘
```

Each command is followed by a hidden `rem __PM3GUI_DONE__<n>` sentinel; when the client
echoes it back, the GUI knows the command finished (and hides the marker).

## Files

```
Start-PM3-GUI.bat        launcher (Windows)
pm3gui/
  server.py              HTTP server, long-poll stream, command dispatch
  pm3_client.py          RealClient (subprocess) + MockClient (demo) + client/runtime discovery
  ports.py               serial-port enumeration (winreg + WMI, no deps)
  web/  index.html style.css app.js
  pm3client/             ← BUNDLED Iceman Windows-x64 client (~192 MB)
    proxmark3.exe        the client the GUI drives by default
    libs/                Qt + MinGW runtime DLLs (incl. qwindows.dll)
    bootrom.elf fullimage.elf   firmware images (for flashing to match)
    dictionaries/ resources/ luascripts/ ...
```

## Notes & limits

* **Windows x64 only** (because of the bundled client). The server itself is pure Python and
  also runs on Linux/macOS, but you'd need to supply a native client for those and point at
  it via **⚙ Settings → Client**.
* The repo is large (~190 MB) because it ships the full client runtime so it works on a
  fresh clone with no setup. The DLLs are committed as plain binaries (no Git LFS, to avoid
  LFS bandwidth limits breaking clones).
* The upstream `proxmark3-master/` source is **git-ignored** by default (not needed to run
  the GUI). Remove that line in `.gitignore` if you want the source in the repo too.
* Long-running/interactive device commands (e.g. continuous `sim`) are best driven from the
  console; use **Disconnect** to hard-stop a session.
