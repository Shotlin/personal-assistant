#!/usr/bin/env python3
"""Generate Sani's 1024px source icon (pure stdlib PNG writer).

A quiet dark rounded square with a white microphone glyph and a small
green ready dot -- matching Sani's UI direction.
"""

import math
import struct
import zlib

SIZE = 1024
CX, CY = SIZE / 2, SIZE / 2


def rounded_square_mask(x: int, y: int) -> float:
    """1 inside the rounded square, 0 outside; smooth edge."""
    radius = SIZE * 0.22
    margin = SIZE * 0.04
    half = SIZE - margin
    qx = max(abs(x - CX) - (half - radius), 0)
    qy = max(abs(y - CY) - (half - radius), 0)
    d = math.hypot(qx, qy) - radius
    # anti-alias over ~2px
    return max(0.0, min(1.0, 0.5 - d / 2.0))


def bg_color(x: int, y: int):
    """Vertical charcoal gradient."""
    t = y / SIZE
    top = (38, 38, 42)
    bottom = (18, 18, 20)
    return tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))


def mic_alpha(x: int, y: int) -> float:
    """White microphone glyph coverage (capsule body + arc + stem + base)."""
    scale = SIZE / 512.0
    cx = CX
    # capsule: center (cx, 190*scale) rx=52 ry=80
    bx, by = cx, 190 * scale
    rx, ry = 52 * scale, 82 * scale
    dx, dy = (x - bx) / rx, (y - by) / ry
    d_capsule = math.hypot(dx, dy) - 1.0

    # arc: circle centered (cx, 300*scale), r=110, stroke 26, only lower half band
    ax, ay = cx, 300 * scale
    r_arc = 108 * scale
    d_arc = abs(math.hypot(x - ax, y - ay) - r_arc) - 13 * scale
    in_arc_band = y > ay  # only below center

    # stem: rect from arc bottom to base
    stem_top = (ay + r_arc) * 0.999
    stem_bottom = 452 * scale
    d_stem = math.inf
    if cx - 13 * scale <= x <= cx + 13 * scale and stem_top <= y <= stem_bottom:
        d_stem = -1.0

    # base bar
    d_base = math.inf
    if cx - 60 * scale <= x <= cx + 60 * scale and 448 * scale <= y <= 470 * scale:
        d_base = -1.0

    candidates = [(d_capsule, 1.0)]
    if in_arc_band:
        candidates.append((d_arc, 1.0))
    candidates.append((d_stem, 1.0))
    candidates.append((d_base, 1.0))
    d = min(c[0] for c in candidates)
    if d >= 0:
        return 0.0
    return max(0.0, min(1.0, -d / 2.0))


def ready_dot_alpha(x: int, y: int) -> float:
    """Small green dot top-left inside the square."""
    scale = SIZE / 512.0
    dx, dy = x - 108 * scale, y - 108 * scale
    r = 20 * scale
    d = math.hypot(dx, dy) - r
    if d >= 0:
        return 0.0
    return max(0.0, min(1.0, -d / 2.0))


def render() -> bytes:
    rows = []
    for y in range(SIZE):
        row = bytearray()
        row.append(0)  # filter: none
        for x in range(SIZE):
            mask = rounded_square_mask(x, y)
            if mask <= 0.0:
                row.extend(b"\x00\x00\x00\x00")
                continue
            r, g, b = bg_color(x, y)
            a = int(255 * mask)

            # mic glyph (white)
            ma = mic_alpha(x, y)
            if ma > 0:
                r = int(r + (245 - r) * ma)
                g = int(g + (245 - g) * ma)
                b = int(b + (247 - b) * ma)

            # green ready dot
            da = ready_dot_alpha(x, y)
            if da > 0:
                r = int(r + (52 - r) * da)
                g = int(g + (199 - g) * da)
                b = int(b + (123 - b) * da)

            row.extend(struct.pack("BBBB", r, g, b, a))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        c += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return c

    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "icon-1024.png"
    with open(out, "wb") as f:
        f.write(render())
    print("icon written")
