#!/usr/bin/env python3
"""Proxmark3 GUI -- local web server.

A tiny, dependency-free (Python stdlib only) HTTP server that wraps the
Proxmark3 command-line client and serves a browser UI.  Console output is
streamed to the browser with long-polling.

Run:  python pm3gui/server.py   (then a browser opens automatically)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ports as ports_mod          # noqa: E402
import pm3_client as pm3           # noqa: E402

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SENTINEL_RE = re.compile(re.escape(pm3.SENTINEL_PREFIX) + r"(\d+)")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


# --------------------------------------------------------------------------- #
# Application state
# --------------------------------------------------------------------------- #
class AppState:
    def __init__(self):
        self.cv = threading.Condition(threading.RLock())
        self.events = deque(maxlen=12000)
        self.seq = 0
        self.client = None
        self.connected = False
        self.mode = "offline"      # offline | live | demo
        self.port = None
        self.client_path = None
        self.runtime_dir = None
        self.cmd_seq = 0
        self.busy = False

    # event log ---------------------------------------------------------- #
    def add_event(self, kind, **data):
        with self.cv:
            self.seq += 1
            ev = {"seq": self.seq, "kind": kind}
            ev.update(data)
            self.events.append(ev)
            self.cv.notify_all()
            return self.seq

    def snapshot(self):
        with self.cv:
            return {
                "connected": self.connected,
                "mode": self.mode,
                "port": self.port,
                "busy": self.busy,
                "client_path": self.client_path,
            }

    # client lifecycle --------------------------------------------------- #
    def connect(self, port, client_path=None, runtime_dir=None, demo=False, baud=None):
        with self.cv:
            if self.connected:
                raise RuntimeError("already connected")

        resolved = None if demo else pm3.find_client(client_path)
        use_demo = demo or resolved is None or port == "__demo__"

        rt = None
        if use_demo:
            client = pm3.MockClient(port if port and port != "__demo__" else "DEMO",
                                    self._on_line)
            mode, shown_port, cpath = "demo", (port if port != "__demo__" else "DEMO"), None
        else:
            rt = runtime_dir or pm3.find_runtime_dir(resolved)
            client = pm3.RealClient(resolved, port, self._on_line, baud=baud, runtime_dir=rt)
            if not client.wait_started():
                try:
                    client.close()
                except Exception:
                    pass
                msg = ("Client exited immediately — its runtime DLLs "
                       "(e.g. Qt5Core.dll, libwinpthread-1.dll, libgcc_s_seh-1.dll) could not "
                       "be loaded. Open Settings (gear icon) and set 'Runtime folder' to your "
                       "ProxSpace ...\\msys2\\mingw64\\bin folder.")
                if rt:
                    msg += f"  (auto-tried: {rt})"
                raise RuntimeError(msg)
            mode, shown_port, cpath = "live", port, resolved

        with self.cv:
            self.client = client
            self.connected = True
            self.mode = mode
            self.port = shown_port
            self.client_path = cpath
            self.runtime_dir = rt
            self.busy = False
        self.add_event("status", **self.snapshot())
        self.add_event("output", text=f"[+] Connected ({mode.upper()} mode) on {shown_port}",
                       stream="sys")
        if mode == "live" and rt:
            self.add_event("output", text=f"[=] runtime DLLs from: {rt}", stream="sys")
        return self.snapshot()

    def disconnect(self):
        with self.cv:
            client = self.client
            self.client = None
            self.connected = False
            self.mode = "offline"
            self.port = None
            self.busy = False
        if client:
            try:
                client.close()
            except Exception:
                pass
        self.add_event("output", text="[=] Disconnected", stream="sys")
        self.add_event("status", **self.snapshot())

    def dispatch(self, cmd):
        cmd = (cmd or "").strip()
        if not cmd:
            return None
        with self.cv:
            if not self.client or not self.connected:
                raise RuntimeError("not connected")
            self.cmd_seq += 1
            s = self.cmd_seq
            self.busy = True
        self.add_event("output", text="pm3 --> " + cmd, stream="cmd")

        low = cmd.lower()
        if low in ("quit", "exit", "q"):
            client = self.client
            if client:
                client.send(cmd)
            # tear the session down ourselves
            threading.Timer(0.2, self.disconnect).start()
            return s

        client = self.client
        client.send(cmd)
        client.send(f"rem {pm3.SENTINEL_PREFIX}{s}")
        self.add_event("status", **self.snapshot())
        return s

    # client reader callback -------------------------------------------- #
    def _on_line(self, line):
        if line is None:
            with self.cv:
                was = self.connected
                self.connected = False
                self.client = None
                self.mode = "offline"
                self.busy = False
            if was:
                self.add_event("output", text="[!] Session ended", stream="sys")
                self.add_event("status", **self.snapshot())
            return

        m = _SENTINEL_RE.search(strip_ansi(line))
        if m:
            with self.cv:
                self.busy = False
            self.add_event("done", cmd_seq=int(m.group(1)))
            self.add_event("status", **self.snapshot())
            return
        self.add_event("output", text=line)


STATE = AppState()


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pm3gui/1.0"

    def log_message(self, *_a):  # silence default stderr logging
        pass

    # helpers ------------------------------------------------------------ #
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except (ValueError, OSError):
            return {}

    # routing ------------------------------------------------------------ #
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        try:
            if path == "/api/ports":
                return self._json(self._ports_payload())
            if path == "/api/status":
                return self._json(STATE.snapshot())
            if path == "/api/output":
                return self._handle_output(qs)
            return self._static(path)
        except Exception as e:  # never 500 the browser silently
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._read_json()
            if u.path == "/api/connect":
                snap = STATE.connect(
                    body.get("port"),
                    client_path=body.get("client_path") or None,
                    runtime_dir=body.get("runtime_dir") or None,
                    demo=bool(body.get("demo")),
                    baud=body.get("baud") or None,
                )
                return self._json({"ok": True, "status": snap})
            if u.path == "/api/disconnect":
                STATE.disconnect()
                return self._json({"ok": True, "status": STATE.snapshot()})
            if u.path == "/api/command":
                s = STATE.dispatch(body.get("cmd", ""))
                return self._json({"ok": True, "cmd_seq": s, "status": STATE.snapshot()})
            return self._json({"error": "unknown endpoint"}, 404)
        except RuntimeError as e:
            return self._json({"error": str(e)}, 409)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # endpoint impls ----------------------------------------------------- #
    def _ports_payload(self):
        found = pm3.find_client()
        rt = pm3.find_runtime_dir(found) if found else None
        return {
            "ports": ports_mod.list_ports(),
            "client_found": found is not None,
            "client_path": found,
            "clients": pm3.discover_clients(),
            "runtime_dir": rt,
            "is_windows": sys.platform.startswith("win"),
        }

    def _handle_output(self, qs):
        try:
            since = int(qs.get("since", ["0"])[0])
        except ValueError:
            since = 0
        try:
            wait = float(qs.get("wait", ["10"])[0])
        except ValueError:
            wait = 10.0
        wait = max(0.0, min(wait, 25.0))  # 0 = return at once (lets the page settle)
        deadline = time.time() + wait
        with STATE.cv:
            while True:
                evs = [e for e in STATE.events if e["seq"] > since]
                if evs or wait <= 0:
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                STATE.cv.wait(timeout=remaining)
            last = STATE.seq
            status = STATE.snapshot()
        return self._json({"events": evs, "last": last, "status": status})

    # static files ------------------------------------------------------- #
    def _static(self, path):
        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        ctypes = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as fh:
            data = fh.read()
        self._send(200, data, ctypes.get(ext, "application/octet-stream"))


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def _suppress_windows_error_dialogs():
    """Stop Windows from popping 'DLL not found' / crash message boxes for the
    client subprocess (it inherits this process's error mode). We detect and
    report such failures ourselves instead."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except Exception:
        pass


def main():
    _suppress_windows_error_dialogs()
    ap = argparse.ArgumentParser(description="Proxmark3 GUI web server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    httpd = None
    port = args.port
    for attempt in range(20):
        try:
            httpd = ThreadingHTTPServer((args.host, port), Handler)
            break
        except OSError:
            port += 1
    if httpd is None:
        print("[!!] Could not bind a port", file=sys.stderr)
        sys.exit(1)

    httpd.daemon_threads = True
    url = f"http://{args.host}:{port}/"
    client = pm3.find_client()
    print("=" * 58)
    print("  Proxmark3 GUI")
    print("=" * 58)
    print(f"  Serving at : {url}")
    print(f"  pm3 client : {client if client else 'not found -> DEMO mode available'}")
    print("  Press Ctrl+C to stop.")
    print("=" * 58)

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[=] Shutting down...")
    finally:
        STATE.disconnect()
        httpd.shutdown()


if __name__ == "__main__":
    main()
