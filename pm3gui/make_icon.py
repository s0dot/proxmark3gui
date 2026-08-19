#!/usr/bin/env python3
"""Generate pm3.ico (a 'P3' app icon) with the Python standard library only —
no Pillow. Writes a 256x256 PNG-compressed .ico next to this file. Run once:
    python make_icon.py
"""
import os
import struct
import zlib

W = H = 256
BG = (255, 92, 87, 255)      # brand red  #ff5c57
FG = (11, 14, 20, 255)       # near-black  #0b0e14
PAD, RADIUS = 8, 52

# 5x7 bitmap glyphs
GLYPHS = {
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
}


def _rounded(x, y):
    x0, y0, x1, y1 = PAD, PAD, W - 1 - PAD, H - 1 - PAD
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    rx = min(max(x, x0 + RADIUS), x1 - RADIUS)
    ry = min(max(y, y0 + RADIUS), y1 - RADIUS)
    return (x - rx) ** 2 + (y - ry) ** 2 <= RADIUS ** 2


def _text_mask():
    """Set of (x,y) pixels covered by 'P3', centred."""
    scale = 14
    gw, gh = 5 * scale, 7 * scale
    gap = 18
    total = gw * 2 + gap
    x_start = (W - total) // 2
    y_start = (H - gh) // 2
    pts = set()
    for gi, ch in enumerate("P3"):
        ox = x_start + gi * (gw + gap)
        rows = GLYPHS[ch]
        for ry, row in enumerate(rows):
            for rxi, bit in enumerate(row):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            pts.add((ox + rxi * scale + dx, y_start + ry * scale + dy))
    return pts


def _rgba():
    text = _text_mask()
    buf = bytearray()
    for y in range(H):
        buf.append(0)  # PNG filter: none
        for x in range(W):
            if _rounded(x, y):
                buf.extend(FG if (x, y) in text else BG)
            else:
                buf.extend((0, 0, 0, 0))
    return bytes(buf)


def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _png():
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(_rgba(), 9))
            + _chunk(b"IEND", b""))


def _ico(png):
    # ICONDIR + one ICONDIRENTRY pointing at an embedded PNG
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)  # 0/0 = 256px
    return header + entry + png


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pm3.ico")
    with open(out, "wb") as fh:
        fh.write(_ico(_png()))
    print("wrote", out, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    main()
