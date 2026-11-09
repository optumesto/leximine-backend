# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Generate leximine.ico for the desktop shortcut.

Pure stdlib -- no Pillow. A Vista-or-later .ico may contain a PNG directly,
so this rasterises the artwork, encodes a PNG by hand (zlib + CRC32 are both
in the standard library) and wraps it in a one-entry icon directory.

    python scripts/make_icon.py

Re-run only if you want to change the artwork; the .ico is committed.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 256
SUPERSAMPLE = 4  # 4x4 samples per pixel, for smooth edges

BACKGROUND = (21, 21, 28, 255)
EDGE = (58, 58, 72, 255)
GEM = (74, 237, 217, 255)
GEM_LIGHT = (164, 250, 238, 255)
METER = (90, 200, 190, 255)


def rounded_rect(x: float, y: float, left: float, top: float,
                 right: float, bottom: float, radius: float) -> bool:
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def diamond(x: float, y: float, cx: float, cy: float,
            half_w: float, half_h: float) -> bool:
    return abs(x - cx) / half_w + abs(y - cy) / half_h <= 1.0


def sample(x: float, y: float) -> tuple[int, int, int, int]:
    """Colour of the artwork at one point, or transparent outside it."""
    if not rounded_rect(x, y, 8, 8, SIZE - 8, SIZE - 8, 52):
        return (0, 0, 0, 0)
    if not rounded_rect(x, y, 12, 12, SIZE - 12, SIZE - 12, 48):
        return EDGE

    # A gem, because the thing you are earning is usually an item.
    if diamond(x, y, 128, 100, 58, 64):
        # Upper-left facet catches the light.
        if (x - 128) + (y - 100) < -26:
            return GEM_LIGHT
        return GEM

    # Three bars below it: the level meter, which is what tells you the
    # microphone is actually hearing something.
    for index, (centre, height) in enumerate(((94, 20), (128, 36), (162, 26))):
        if abs(x - centre) <= 11 and abs(y - 198) <= height / 2:
            return METER
    return BACKGROUND


def render() -> list[bytes]:
    rows: list[bytes] = []
    step = 1.0 / SUPERSAMPLE
    offset = step / 2.0
    for py in range(SIZE):
        row = bytearray()
        for px in range(SIZE):
            red = green = blue = alpha = 0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    r, g, b, a = sample(px + sx * step + offset,
                                        py + sy * step + offset)
                    # Premultiply so transparent samples do not drag the
                    # colour of an edge pixel toward black.
                    red += r * a
                    green += g * a
                    blue += b * a
                    alpha += a
            if alpha == 0:
                row += bytes(4)
            else:
                row += bytes((red // alpha, green // alpha, blue // alpha,
                              alpha // (SUPERSAMPLE * SUPERSAMPLE)))
        rows.append(bytes(row))
    return rows


def encode_png(rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def encode_ico(png: bytes) -> bytes:
    # 0 in the width/height byte means 256.
    directory = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 6 + 16)
    return directory + entry + png


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    png = encode_png(render())

    ico = root / "leximine.ico"
    ico.write_bytes(encode_ico(png))
    print(f"wrote {ico} ({ico.stat().st_size:,} bytes)")

    # The Minecraft launcher wants a data: URI for a custom profile icon, so
    # the same artwork is emitted as a plain PNG for install_mod.ps1 to
    # base64 into launcher_profiles.json.
    plain = root / "leximine.png"
    plain.write_bytes(png)
    print(f"wrote {plain} ({plain.stat().st_size:,} bytes)")

    # install_mod.ps1 reads this by name from the MOD repo's root, so both the
    # directory and the filename have to match what the rename left behind --
    # this was still writing linguacraft.png into lingua-craft-mod, which is a
    # name and a folder that no longer exist, so the launcher profile has been
    # going in without its icon.
    for name in ("leximine", "leximine-mod", "lingua-craft-mod"):
        sibling = root.parent / name
        if sibling.is_dir():
            copy = sibling / "leximine.png"
            copy.write_bytes(png)
            print(f"wrote {copy} ({copy.stat().st_size:,} bytes)")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
