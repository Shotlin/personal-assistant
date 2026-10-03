"""What Sani has learned about where controls are, per app or site.

A control found by SIGHT once (a button the accessibility tree does not expose)
is remembered by what it is, not just where it was: its label and role, its
position as a share of the window, the window size it was seen at, and a tiny
fingerprint of how it looked. A later request reuses the spot only when the
window is about the same size AND the fingerprint still matches the pixels
there; the click is then checked like any other, and a spot that stops working
is marked bad and never trusted again.

Nothing here keeps a screenshot, or any text typed in a box. Only labels, roles,
relative positions, sizes and a 64-bit hash. The file is local and clearable.
"""

from __future__ import annotations

import io
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[a-z0-9]+")
#: Windows within this fraction of the remembered size count as "the same layout".
SIZE_TOLERANCE = 0.04
#: Fingerprint bits allowed to differ (of 64) before a spot is considered changed.
MAX_HASH_DISTANCE = 10
#: A spot that has failed more often than it has worked stops being offered.


def norm_label(label: str) -> str:
    return " ".join(_WORD.findall(label.lower()))


@dataclass(frozen=True)
class Spot:
    scope: str
    label: str
    role: str
    rel_x: float  # centre, 0..1 of the window
    rel_y: float
    win_w: int
    win_h: int
    fingerprint: str
    ok: int
    failed: int
    source: str

    def usable_for(self, win_w: int, win_h: int) -> bool:
        if self.failed > self.ok:
            return False
        return (
            abs(win_w - self.win_w) <= self.win_w * SIZE_TOLERANCE
            and abs(win_h - self.win_h) <= self.win_h * SIZE_TOLERANCE
        )


def fingerprint_of(png: bytes, cx: float, cy: float, half: int = 24) -> str:
    """64-bit average hash of the pixels around (cx, cy); "" when it cannot be made."""
    try:
        from PIL import Image
    except ImportError:  # the memory still works, just without the pixel check
        return ""
    try:
        image = Image.open(io.BytesIO(png)).convert("L")
        box = (
            max(0, int(cx) - half), max(0, int(cy) - half),
            min(image.width, int(cx) + half), min(image.height, int(cy) + half),
        )
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return ""
        small = image.crop(box).resize((8, 8))
        pixels = list(small.getdata())
    except Exception:  # noqa: BLE001 -- an unreadable image just means no fingerprint
        return ""
    mean = sum(pixels) / len(pixels)
    bits = "".join("1" if p >= mean else "0" for p in pixels)
    return f"{int(bits, 2):016x}"


def distance(a: str, b: str) -> int:
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def scope_for(app_name: str, address: str = "") -> str:
    """``site:host`` for a browser page, else ``app:name``."""
    host = ""
    if address:
        text = address.strip().lower()
        text = re.sub(r"^[a-z]+://", "", text)
        host = text.split("/")[0].split("?")[0]
        if "." not in host or " " in host:
            host = ""
    return f"site:{host}" if host else f"app:{norm_label(app_name) or 'unknown'}"


class UiMemory:
    """Small SQLite file next to the Sani data; safe to delete at any time."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5)

    def _init(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS spots (
                    scope TEXT NOT NULL, key TEXT NOT NULL, label TEXT NOT NULL,
                    role TEXT NOT NULL, rel_x REAL NOT NULL, rel_y REAL NOT NULL,
                    win_w INTEGER NOT NULL, win_h INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    ok INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'vision', last_seen REAL NOT NULL,
                    PRIMARY KEY (scope, key, win_w, win_h)
                )"""
            )

    def remember(
        self, scope: str, label: str, *, role: str, rel_x: float, rel_y: float,
        win_w: int, win_h: int, fingerprint: str, source: str = "vision",
    ) -> None:
        key = norm_label(label)
        if not key or not 0.0 <= rel_x <= 1.0 or not 0.0 <= rel_y <= 1.0:
            return
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO spots (scope, key, label, role, rel_x, rel_y, win_w, win_h,
                       fingerprint, ok, failed, source, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,1,0,?,?)
                   ON CONFLICT(scope, key, win_w, win_h) DO UPDATE SET
                       rel_x=excluded.rel_x, rel_y=excluded.rel_y,
                       fingerprint=excluded.fingerprint, ok=ok+1, last_seen=excluded.last_seen,
                       label=excluded.label, role=excluded.role""",
                (scope, key, label[:80], role[:30], rel_x, rel_y, win_w, win_h,
                 fingerprint, source, time.time()),
            )

    def recall(self, scope: str, label: str, win_w: int, win_h: int) -> list[Spot]:
        key = norm_label(label)
        with self._lock, self._connect() as db:
            rows = db.execute(
                """SELECT scope, label, role, rel_x, rel_y, win_w, win_h, fingerprint,
                          ok, failed, source FROM spots WHERE scope=? AND key=?
                   ORDER BY ok - failed DESC, last_seen DESC""",
                (scope, key),
            ).fetchall()
        spots = [Spot(*row) for row in rows]
        return [s for s in spots if s.usable_for(win_w, win_h)]

    def mark(self, spot: Spot, *, worked: bool) -> None:
        column = "ok" if worked else "failed"
        with self._lock, self._connect() as db:
            db.execute(
                f"UPDATE spots SET {column}={column}+1, last_seen=? "  # noqa: S608 -- fixed names
                "WHERE scope=? AND key=? AND win_w=? AND win_h=?",
                (time.time(), spot.scope, norm_label(spot.label), spot.win_w, spot.win_h),
            )

    def entries(self, scope: str | None = None) -> list[Spot]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT scope, label, role, rel_x, rel_y, win_w, win_h, fingerprint, ok, failed,"
                " source FROM spots " + ("WHERE scope=? " if scope else "") + "ORDER BY scope, label",
                (scope,) if scope else (),
            ).fetchall()
        return [Spot(*row) for row in rows]

    def clear(self, scope: str | None = None) -> int:
        with self._lock, self._connect() as db:
            cur = db.execute(
                "DELETE FROM spots" + (" WHERE scope=?" if scope else ""),
                (scope,) if scope else (),
            )
            return cur.rowcount


__all__ = ["Spot", "UiMemory", "distance", "fingerprint_of", "norm_label", "scope_for"]
