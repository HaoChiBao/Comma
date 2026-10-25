"""Writes the ribbon icons (a plain clock-face mark) without any image library.

    python scripts/make_icons.py
"""

import math
import struct
import zlib
from pathlib import Path

BLUE = (37, 99, 235, 255)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)


def pixel(x: float, y: float, size: int) -> tuple:
    c = size / 2
    dx, dy = x - c, y - c
    r = math.hypot(dx, dy)
    outer = size * 0.47
    if r > outer:
        return CLEAR
    stroke = max(1.0, size * 0.07)
    # Clock hands: twelve o'clock and three o'clock.
    if abs(dx) <= stroke / 2 and -outer * 0.62 <= dy <= 0:
        return WHITE
    if abs(dy) <= stroke / 2 and 0 <= dx <= outer * 0.48:
        return WHITE
    if r > outer - stroke:
        return WHITE
    return BLUE


def png(size: int) -> bytes:
    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x + 0.5, y + 0.5, size)) for x in range(size)) for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "assets"
    out.mkdir(exist_ok=True)
    for size in (16, 32, 64, 80, 128):
        (out / f"icon-{size}.png").write_bytes(png(size))
        print(out / f"icon-{size}.png")
