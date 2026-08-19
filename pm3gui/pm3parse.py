#!/usr/bin/env python3
"""Structured parsers for Proxmark3 (Iceman) console output.

Pure functions — no I/O, no device, no globals. Everything here turns the
human-readable text the CLI prints into typed Python data, so the rest of the
GUI never has to scrape console text again. Because they're pure, they're
covered by golden-transcript tests in ``tests/test_pm3parse.py`` using real
output captured from actual hardware, which means a firmware wording change
can't silently break a workflow — a test fails first.
"""
from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s or "")


def norm_hex(s: str) -> str:
    """Uppercase hex digits only (spaces/punctuation removed)."""
    return re.sub(r"[^0-9A-Fa-f]", "", s or "").upper()


# --------------------------------------------------------------------------- #
# Generic field parsers
# --------------------------------------------------------------------------- #
def parse_uid(text: str):
    """The `UID: xx xx …` value as a spaced hex string, or None."""
    m = re.search(r"\bUID:\s*([0-9A-Fa-f][0-9A-Fa-f ]{4,})", strip_ansi(text))
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1).strip())


def parse_hf_info(text: str) -> dict:
    """`hf mf info` / `hf 14a info` → structured facts."""
    t = strip_ansi(text)
    out = {
        "uid": parse_uid(t),
        "atqa": None,
        "sak": None,
        "type": None,
        "magic": None,       # e.g. "Gen 4 GDM / USCUID ( Magic Auth )"
        "is_gdm": bool(re.search(r"Gen\s*4\s*GDM|USCUID", t, re.I)),
        "prng": None,
        "error": error_signature(t),
    }
    m = re.search(r"\bATQA:\s*([0-9A-Fa-f]{2}\s*[0-9A-Fa-f]{2})", t)
    if m:
        out["atqa"] = re.sub(r"\s+", " ", m.group(1).strip())
    m = re.search(r"\bSAK:\s*([0-9A-Fa-f]{2})", t)
    if m:
        out["sak"] = m.group(1).upper()
    m = re.search(r"Magic capabilities\W*\s*(.+?)\s*$", t, re.M)
    if m:
        out["magic"] = m.group(1).strip()
    m = re.search(r"Possible types?:\s*(.+?)\s*$", t, re.M)
    if m:
        out["type"] = m.group(1).strip()
    # last word on the Prng line — handles both "Prng....... weak" and
    # "Prng detection....... weak" (don't capture the word "detection")
    m = re.search(r"Prng\b[^\n]*?(\w+)\s*$", t, re.M)
    if m:
        out["prng"] = m.group(1).lower()
    out["magic_gen"] = magic_gen_slug(out["magic"] or t)
    return out


def magic_gen_slug(magic_text):
    """Map a `Magic capabilities...` string to the GUI's magic-type slug
    (matches the HF-copy dropdown), or None for a non-magic / unknown card."""
    s = strip_ansi(magic_text or "")
    if re.search(r"GDM|USCUID", s, re.I):
        return "gdm"
    if re.search(r"Gen\s*4\s*GTU|Ultimate\s*Magic|\bUMC\b", s, re.I):
        return "gen4"
    if re.search(r"Gen\s*3", s, re.I):
        return "gen3"
    if re.search(r"Gen\s*2|CUID|DirectWrite", s, re.I):
        return "gen2"
    if re.search(r"Gen\s*1", s, re.I):     # gen1a / gen1b both use cload
        return "gen1a"
    return None


def parse_raw_ack(text: str) -> bool:
    """True if a `hf 14a raw` magic-wakeup frame was ACKed by the tag (0a)."""
    return bool(re.search(r"(?:^|[^0-9A-Fa-f])0a(?:[^0-9A-Fa-f]|$)", strip_ansi(text), re.I))


def compare_dumps(a, b) -> dict:
    """Compare two MIFARE dump byte-strings, 16 bytes per block. Used by deep
    verify to prove the whole copy matches the original, not just the UID."""
    if not a or not b:
        return {"readable": False, "match": False, "diff_blocks": [], "blocks": 0}
    n = min(len(a), len(b)) // 16
    diffs = [i for i in range(n) if a[i * 16:(i + 1) * 16] != b[i * 16:(i + 1) * 16]]
    return {
        "readable": True,
        "match": len(a) == len(b) and not diffs,
        "diff_blocks": diffs,
        "blocks": n,
        "len_a": len(a),
        "len_b": len(b),
    }


# --------------------------------------------------------------------------- #
# Gen4 GDM / USCUID config block
# --------------------------------------------------------------------------- #
def parse_gdm_config(text):
    """`hf mf gdmcfg[ --gen1a]` → decoded 16-byte config, or None if unreadable
    (e.g. "Auth error" / "wupC1 error"). Byte offsets verified against the
    Iceman client's ``parse_gdm_cfg`` (cmdhfmf.c)."""
    t = strip_ansi(text)
    m = re.search(r"\b([0-9A-Fa-f]{32})\b", t)
    if not m:
        return None
    h = m.group(1).upper()
    b = [h[i:i + 2] for i in range(0, 32, 2)]
    return {
        "hex": h,
        "bytes": b,
        # bytes 0-1: 85 00 = wakeup disabled; anything else = enabled
        "wakeup_enabled": not (b[0] == "85" and b[1] == "00"),
        # 7A FF specifically = enabled *with* "GDM cfg block access" (remaps block 0)
        "cfg_block_access": b[0] == "7A" and b[1] == "FF",
        # byte 2: 85 = 20/23 knock style, else 40/43
        "wakeup_20_23": b[2] == "85",
        "perso": b[9],            # 5A/C3/A5 = CL2 (7-byte) capable
        "magic_auth": b[11],      # 5A = magic-auth channel enabled
        "sak": b[15],
    }


# --------------------------------------------------------------------------- #
# Write / load result parsers
# --------------------------------------------------------------------------- #
def parse_write_result(text: str) -> dict:
    """Result of a write-ish command. NOTE: `ack` (the "Write ( ok )" line) is
    only the tag acknowledging the frame — it is NOT proof the value stuck, so
    callers must still read back and compare. `fail` is a hard failure."""
    t = strip_ansi(text)
    return {
        "ack": bool(re.search(r"Write\s*\(\s*ok\s*\)", t, re.I)),
        "fail": bool(re.search(r"Write\s*\(\s*fail\s*\)", t, re.I))
        or bool(re.search(r"\bAuth error\b", t, re.I)),
    }


def parse_cload(text: str) -> dict:
    """`hf mf cload` / restore → {ok, blocks}."""
    t = strip_ansi(text)
    m = re.search(r"(?:Card\s+)?loaded\s+(\d+)\s+blocks", t, re.I)
    return {
        "ok": bool(re.search(r"\bDone!?\b", t, re.I)) or m is not None,
        "blocks": int(m.group(1)) if m else None,
    }


def parse_uscuid_script(text: str) -> dict:
    """`script run hf_mf_uscuid_prog` → {uid, ok}. The script prints
    `UID | xx xx …` for the value it programmed and errors via `ERROR:` /
    `did not ACK`."""
    t = strip_ansi(text)
    m = re.search(r"\bUID\s*\|\s*([0-9A-Fa-f ]{6,})", t)
    bad = bool(re.search(r"\bERROR:|did not ACK", t, re.I))
    return {
        "uid": re.sub(r"\s+", " ", m.group(1).strip()) if m else None,
        "ok": not bad,
    }


# --------------------------------------------------------------------------- #
# Error signatures
# --------------------------------------------------------------------------- #
_ERRORS = [
    (r"\bAuth error\b", "auth-error"),
    (r"\bwupC1 error\b", "gen1a-knock-fail"),
    (r"\bwupGDM1 error\b", "gdm-knock-fail"),
    (r"\bwupGDM2 error\b", "gdm-knock-fail"),
    (r"No card|iso14443a? card select failed|can'?t select card", "no-card"),
]


def error_signature(text: str):
    """Short slug for a recognised device error, or None."""
    t = strip_ansi(text)
    for pat, name in _ERRORS:
        if re.search(pat, t, re.I):
            return name
    return None


# convenience: bytes -> size flag used by cload/gload/restore
def size_flag(dump_bytes: int) -> str:
    return {320: "--mini", 1024: "--1k", 1152: "--1k+",
            2048: "--2k", 4096: "--4k"}.get(dump_bytes, "--1k")
