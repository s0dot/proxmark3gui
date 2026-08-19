#!/usr/bin/env python3
"""High-level Proxmark3 operations — the device workflows, server-side.

Each op takes an injected ``run(cmd) -> list[str]`` (executes one CLI command,
returns its output lines) and an optional ``log(msg)`` progress callback, and
returns a structured :class:`OpResult`. Because ``run`` is injected, every op
is unit-tested with a fake runner that replays real hardware transcripts
(``tests/test_pm3ops.py``) — no device or browser required.

This is where the safety-critical logic lives (moved out of the browser). The
Gen4 GDM / USCUID clone in particular encodes rules learned the hard way:
  * the UID-setting script is ALWAYS the last write (cload after it, under the
    7AFF "cfg block access" mode, wipes the UID);
  * we NEVER write 8500 (disable knock) through the knock, and never revert
    config — that combination permanently bricks the card;
  * after enabling the knock we VERIFY it actually took (over gen1a) and abort
    *before* any data write if it didn't — a failed enable does zero damage.
"""
from __future__ import annotations

import pm3parse as P


# --------------------------------------------------------------------------- #
# Result envelope
# --------------------------------------------------------------------------- #
def _result(op: str) -> dict:
    return {
        "op": op,
        "ok": False,        # did the whole op succeed (incl. verification)?
        "aborted": False,   # did a pre-flight check stop it before writing?
        "phase": "check",   # "check" (dry run) | "execute"
        "error": None,
        "warnings": [],
        "result": {},       # op-specific structured data
        "steps": [],        # [{cmd, raw, ...}] every CLI command run, in order
    }


def _fail(r: dict, msg: str) -> dict:
    r["error"] = msg
    r["aborted"] = True
    return r


class _Ctx:
    """Runs commands, records every one as a step, and streams progress."""

    def __init__(self, run, log, result):
        self._run = run
        self._log = log or (lambda *_: None)
        self._r = result

    def say(self, msg):
        self._log(msg)

    def run(self, cmd, *, quiet=False):
        lines = self._run(cmd) or []
        text = "\n".join(lines)
        self._r["steps"].append({"cmd": cmd, "raw": text})
        return text


# --------------------------------------------------------------------------- #
# Identify — the simple case (shows the pattern)
# --------------------------------------------------------------------------- #
def identify(run, log=None) -> dict:
    r = _result("identify")
    ctx = _Ctx(run, log, r)
    info = P.parse_hf_info(ctx.run("hf mf info"))
    r["result"] = info
    r["ok"] = bool(info.get("uid"))
    if not r["ok"]:
        r["error"] = "no card detected"
    return r


# --------------------------------------------------------------------------- #
# Gen4 GDM / USCUID clone (7-byte capable) — the crown jewel
# --------------------------------------------------------------------------- #
def clone_gdm_uscuid(run, *, dump_base, target_uid, dump_bytes,
                     execute=False, log=None) -> dict:
    """Clone the dump ``dump_base`` (whose UID is ``target_uid``, ``dump_bytes``
    on disk) onto the Gen4 GDM / USCUID card on the antenna.

    ``execute=False`` runs ONLY the read-only pre-flight checks and returns the
    plan (nothing is written). ``execute=True`` re-runs the checks and then
    performs the writes in the verified-safe order. The caller is expected to
    confirm with the user between the two.
    """
    r = _result("clone-gdm-uscuid")
    r["phase"] = "execute" if execute else "check"
    ctx = _Ctx(run, log, r)

    uid = P.norm_hex(target_uid)
    res = r["result"]
    res["target_uid"] = uid

    # ---- input sanity (no device contact yet) ----
    if len(uid) not in (8, 14):
        return _fail(r, f"target UID must be 4 or 7 bytes; got {target_uid!r}")
    if not dump_bytes:
        return _fail(r, f"dump file not found or empty on disk: {dump_base}")
    res["dump_bytes"] = dump_bytes
    res["cload_flag"] = cload_flag = P.size_flag(dump_bytes)

    ctx.say("[=] ───── Gen4 GDM / USCUID pre-flight checks ─────")

    # ---- CHECK 1: a GDM/USCUID card is actually present ----
    info = P.parse_hf_info(ctx.run("hf mf info"))
    if not info.get("uid"):
        return _fail(r, "no card detected (hf mf info returned no UID). Put the magic card flat on the antenna.")
    if not info.get("is_gdm"):
        return _fail(r, "the card on the antenna is NOT a Gen4 GDM / USCUID magic card — this workflow is only for those.")
    res["current_uid"] = info["uid"]
    ctx.say(f"[+] GDM / USCUID card present — current UID {info['uid']}")

    # ---- CHECK 2: read config (magic-auth, then either knock style) ----
    via = "magic-auth"
    cfg = P.parse_gdm_config(ctx.run("hf mf gdmcfg"))
    if not cfg:
        via = "gen1a"
        cfg = P.parse_gdm_config(ctx.run("hf mf gdmcfg --gen1a"))
    if not cfg:
        via = "gdm"
        cfg = P.parse_gdm_config(ctx.run("hf mf gdmcfg --gdm"))
    if not cfg:
        return _fail(r, "couldn't read the GDM config on any channel (magic-auth, gen1a or gdm). The card may be locked — nothing was written.")
    res["config"] = cfg["hex"]
    res["config_via"] = via
    ctx.say(f"[+] Config readable via {via}: {cfg['hex']}")

    # ---- CHECK 3: perso / magic-auth sanity ----
    if len(uid) == 14 and cfg["perso"] not in ("5A", "C3", "A5"):
        return _fail(r, f"perso byte is {cfg['perso']}, not a 7-byte / CL2 mode (need 5A, C3 or A5). Refusing to write a 7-byte UID onto it.")
    if cfg["magic_auth"] != "5A":
        r["warnings"].append(f"magic-auth byte is {cfg['magic_auth']} (expected 5A) — recovery is limited if a write fails.")

    enable_cfg = "7AFF" + cfg["hex"][4:]        # only bytes 0-1 change → 7A FF
    wake = "2" if cfg["wakeup_20_23"] else "4"  # script -t: knock style (byte 2)
    gate_cmd = "hf mf gdmcfg --gdm" if wake == "2" else "hf mf gdmcfg --gen1a"
    res["enable_cfg"] = enable_cfg
    res["wake"] = wake
    res["gate_cmd"] = gate_cmd
    res["already_enabled"] = cfg["cfg_block_access"]
    res["plan"] = [
        "knock already enabled — skip config write" if cfg["cfg_block_access"]
        else f"enable knock:  hf mf gdmsetcfg -d {enable_cfg}",
        f"verify knock:  {gate_cmd}  (must read 7AFF…)",
        f"load data:     hf mf cload {cload_flag} -f {dump_base}",
        f"set UID (LAST): script run hf_mf_uscuid_prog -t {wake} -u {uid}",
        "verify:        hf mf info",
    ]

    # dry run stops here — all checks passed, nothing written
    if not execute:
        r["ok"] = True
        res["ready"] = True
        ctx.say("[+] Pre-flight checks passed — ready to clone.")
        return r

    # ===================== WRITES BEGIN =====================
    # 1 — enable the knock (skip if already 7AFF)
    if cfg["cfg_block_access"]:
        ctx.say("[=] Knock already enabled (7AFF) — skipping config write.")
    else:
        ctx.say("[=] Enabling 40/43 knock…")
        ctx.run(f"hf mf gdmsetcfg -d {enable_cfg}")

    # 2 — CRITICAL GATE: confirm the knock took, over the card's own knock style.
    #     Abort here = nothing else written = zero damage.
    chk = P.parse_gdm_config(ctx.run(gate_cmd))
    if not chk or not chk["hex"].startswith("7AFF"):
        return _fail(r, f"knock did NOT enable ({gate_cmd} → {chk['hex'] if chk else 'no read / error'}). "
                        "Stopped BEFORE touching data — re-seat the card flat on the antenna and try again.")
    ctx.say(f"[+] Knock confirmed — config now {chk['hex']}")

    # 3 — load the data blocks
    ctx.say(f"[=] Loading data (cload {cload_flag})…")
    cl = P.parse_cload(ctx.run(f"hf mf cload {cload_flag} -f {dump_base}"))
    res["blocks_loaded"] = cl["blocks"]
    if not cl["ok"]:
        r["warnings"].append("cload didn't clearly report success — continued to set the UID anyway.")

    # 4 — set the UID via the backdoor script (MUST be the last write)
    ctx.say(f"[=] Setting {'7' if len(uid) == 14 else '4'}-byte UID via backdoor script (last write)…")
    sc = P.parse_uscuid_script(ctx.run(f"script run hf_mf_uscuid_prog -t {wake} -u {uid}"))
    if not sc["ok"]:
        r["warnings"].append("the UID script reported a problem — see the console output.")

    # 5 — verify
    ctx.say("[=] Verifying — reading the card back…")
    vinfo = P.parse_hf_info(ctx.run("hf mf info"))
    got = P.norm_hex(vinfo.get("uid") or "")
    res["verified_uid"] = vinfo.get("uid")
    r["ok"] = got == uid
    if r["ok"]:
        ctx.say(f"[+] Clone verified — UID {uid}. Done. Do NOT run cload again (it would wipe the UID).")
    else:
        r["error"] = f"verify failed — card reads {vinfo.get('uid') or '(none)'}"
        ctx.say(f"[!] {r['error']}. If it reads 0000, re-run ONLY the UID script (never cload after it).")
    return r


# --------------------------------------------------------------------------- #
# Probe magic type — auto-detect what the target card is
# --------------------------------------------------------------------------- #
def probe_magic(run, log=None) -> dict:
    """Read the card on the antenna and report its magic generation (so the UI
    can auto-pick the right write flow instead of the user guessing)."""
    r = _result("probe-magic")
    ctx = _Ctx(run, log, r)
    info = P.parse_hf_info(ctx.run("hf mf info"))
    r["result"] = info
    if not info.get("uid"):
        r["aborted"] = True
        r["error"] = "no card detected (put the target flat on the antenna)"
    elif not info.get("magic_gen"):
        r["ok"] = True
        r["warnings"].append("no magic capability detected — this looks like a normal (non-writable) card")
    else:
        r["ok"] = True
        ctx.say(f"[+] Detected {info.get('magic')} → target type '{info['magic_gen']}' (UID {info.get('uid')})")
    return r


# --------------------------------------------------------------------------- #
# Deep verify — prove the WHOLE copy matches, not just the UID (read-only)
# --------------------------------------------------------------------------- #
def deep_verify_mfc(run, io, *, dump_base, key_base, dump_bytes, log=None) -> dict:
    """Dump the just-written target with the original's keys and compare it to
    the source dump block-by-block. Read-only — a dump can't harm the card."""
    r = _result("deep-verify")
    ctx = _Ctx(run, log, r)
    flag = P.size_flag(dump_bytes).replace("+", "")   # dump has no --1k+
    ctx.say("[=] Deep verify — reading the target back to compare against the original…")
    ctx.run(f"hf mf dump {flag} -k {key_base} -f hf-mf-verify")
    cmp = P.compare_dumps(io.read_dump(dump_base), io.read_dump("hf-mf-verify"))
    r["result"] = cmp
    if not cmp["readable"]:
        r["error"] = "couldn't read the target back (keys may differ, or it isn't fully readable) — the UID check still stands"
        return r
    r["ok"] = cmp["match"]
    if cmp["match"]:
        ctx.say(f"[+] Deep verify PASSED — all {cmp['blocks']} blocks match the original.")
    else:
        r["error"] = f"{len(cmp['diff_blocks'])} block(s) differ from the original"
        ctx.say(f"[!] Deep verify: block(s) {cmp['diff_blocks'][:8]} differ from the original.")
    return r


# --------------------------------------------------------------------------- #
# Rescue — diagnose a stuck magic card (read-only battery, never writes)
# --------------------------------------------------------------------------- #
def rescue_magic(run, log=None) -> dict:
    r = _result("rescue-magic")
    ctx = _Ctx(run, log, r)
    ctx.say("[=] Rescue — probing every backdoor channel (read-only, nothing is written)…")
    ch = {}
    ch["present"] = bool(P.parse_hf_info(ctx.run("hf 14a info")).get("uid"))
    ch["magic_auth"] = P.parse_gdm_config(ctx.run("hf mf gdmcfg")) is not None
    ch["gen1a_gdmcfg"] = P.parse_gdm_config(ctx.run("hf mf gdmcfg --gen1a")) is not None
    ch["gdm_gdmcfg"] = P.parse_gdm_config(ctx.run("hf mf gdmcfg --gdm")) is not None
    ch["gen1a_knock"] = P.parse_raw_ack(ctx.run("hf 14a raw -k -a -b 7 40"))
    ctx.run("hf 14a raw -k -a 43")
    ch["gdm_knock"] = P.parse_raw_ack(ctx.run("hf 14a raw -k -a -b 7 20"))
    ctx.run("hf 14a raw -k -a 23")
    alive = [k for k in ("magic_auth", "gen1a_gdmcfg", "gdm_gdmcfg", "gen1a_knock", "gdm_knock") if ch.get(k)]
    r["result"] = {"channels": ch, "alive": alive}
    r["ok"] = bool(alive)
    if alive:
        ctx.say(f"[+] Reachable via: {', '.join(alive)}. The card is recoverable — a GDM clone can re-enable the knock from here.")
    elif ch["present"]:
        r["error"] = "card answers plain reads but NO backdoor channel responds — likely soft-bricked (config locked every magic channel)."
        ctx.say("[!] " + r["error"])
    else:
        r["error"] = "no card detected — re-seat it flat on the antenna and try again."
        ctx.say("[!] " + r["error"])
    return r


# registry the server dispatches on. Every op is called fn(run, log, params, io).
OPS = {
    "identify": lambda run, log, p, io: identify(run, log=log),
    "probe-magic": lambda run, log, p, io: probe_magic(run, log=log),
    "rescue-magic": lambda run, log, p, io: rescue_magic(run, log=log),
    "clone-gdm-uscuid": lambda run, log, p, io: clone_gdm_uscuid(
        run,
        dump_base=p.get("dump_base"),
        target_uid=p.get("target_uid"),
        dump_bytes=int(p.get("dump_bytes") or 0),
        execute=bool(p.get("execute")),
        log=log,
    ),
    "deep-verify": lambda run, log, p, io: deep_verify_mfc(
        run, io,
        dump_base=p.get("dump_base"),
        key_base=p.get("key_base") or (p.get("dump_base") or "").replace("-dump", "-key"),
        dump_bytes=int(p.get("dump_bytes") or 0),
        log=log,
    ),
}
