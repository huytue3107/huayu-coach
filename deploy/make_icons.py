#!/usr/bin/env python3
"""Generates the PWA icons in app/static/manifest/ with the Python standard library only (zlib + struct).

    python deploy/make_icons.py

The mark is a white ring with a gap and an orange dot in the gap: "the way back". Colours match app/static/icon.svg.
  icon-192.png, icon-512.png   rounded corners, transparent outside (purpose "any")
  icon-maskable-512.png        full-bleed square, drawing kept inside the central 80% safe zone (purpose "maskable")
The files are committed, so this script only needs to run again if the design changes.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

NAVY = (0x14, 0x23, 0x3C)
WHITE = (0xFF, 0xFF, 0xFF)
ORANGE = (0xF5, 0x9D, 0x52)
OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "manifest"


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _mix(base: tuple[float, float, float], color: tuple[int, int, int], a: float) -> tuple[float, float, float]:
    return tuple(b + (c - b) * a for b, c in zip(base, color))  # type: ignore[return-value]


def render(size: int, rounded: bool) -> bytes:
    """RGBA bytes, one anti-aliased pixel at a time (distance-based coverage)."""
    c = size / 2
    ring_r, ring_w = 0.27 * size, 0.085 * size
    dot_r = 0.07 * size
    gap_angle = math.radians(-45)  # top right, where icon.svg has its orange dot
    gap_half = ring_r * math.radians(25)  # half width of the gap, in pixels along the ring
    dot_x, dot_y = c + ring_r * math.cos(gap_angle), c + ring_r * math.sin(gap_angle)
    corner = 0.22 * size
    out = bytearray()
    for y in range(size):
        out.append(0)  # PNG filter type 0 for this scanline
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            alpha = 1.0
            if rounded:  # signed distance to a rounded square, negative inside
                qx, qy = abs(px - c) - (c - corner), abs(py - c) - (c - corner)
                d = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - corner
                alpha = _clamp(0.5 - d)
            rgb: tuple[float, float, float] = tuple(float(v) for v in NAVY)  # type: ignore[assignment]
            dx, dy = px - c, py - c
            dist = math.hypot(dx, dy)
            ring = _clamp(0.5 - (abs(dist - ring_r) - ring_w / 2))
            if ring > 0:
                delta = math.atan2(dy, dx) - gap_angle
                delta = abs((delta + math.pi) % (2 * math.pi) - math.pi)  # wrapped angular distance
                ring *= _clamp(0.5 + (delta * ring_r - gap_half))  # cut the gap out of the ring
                rgb = _mix(rgb, WHITE, ring)
            dot = _clamp(dot_r - math.hypot(px - dot_x, py - dot_y) + 0.5)
            if dot > 0:
                rgb = _mix(rgb, ORANGE, dot)
            out += bytes((round(rgb[0]), round(rgb[1]), round(rgb[2]), round(alpha * 255)))
    return bytes(out)


def png(size: int, rounded: bool) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA, no interlace
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(render(size, rounded), 9)) + chunk(b"IEND", b"")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, rounded in (("icon-192.png", 192, True), ("icon-512.png", 512, True), ("icon-maskable-512.png", 512, False)):
        (OUT / name).write_bytes(png(size, rounded))
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()
