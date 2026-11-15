"""Les chemins rapides conservent chaque pixel (avec et sans Pillow)."""
from __future__ import annotations

import random
import struct
import unittest
import zlib
from unittest.mock import patch

from hookycreator import pngread
from hookymod import texturemaps, util


def chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xffffffff))


def filtered_png(width: int, rows: list[bytes], channels: int, filters: list[int]) -> bytes:
    """Construit à la main tous les filtres PNG à partir des pixels attendus."""
    previous = bytes(width * channels)
    raw = bytearray()
    for row, kind in zip(rows, filters):
        raw.append(kind)
        for index, value in enumerate(row):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            prediction = (0 if kind == 0 else left if kind == 1 else up if kind == 2 else
                          (left + up) // 2 if kind == 3 else pngread._paeth(left, up, upper_left))
            raw.append((value - prediction) & 255)
        previous = row
    header = struct.pack(">IIBBBBB", width, len(rows), 8, 6 if channels == 4 else 2, 0, 0, 0)
    return (pngread.PNG_MAGIC + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class FastPngTests(unittest.TestCase):
    def test_all_filters_rgba_and_rgb_match_source_bytes_without_pillow(self):
        rng = random.Random(927)
        for channels in (3, 4):
            for width in (1, 3, 17):
                rows = [bytes(rng.randrange(256) for _ in range(width * channels)) for _ in range(5)]
                png = filtered_png(width, rows, channels, [0, 1, 2, 3, 4])
                expected = (b"".join(rows) if channels == 4 else b"".join(
                    bytes((*row[i:i + 3], 255)) for row in rows for i in range(0, len(row), 3)))
                with self.subTest(channels=channels, width=width), \
                     patch.object(pngread, "_pil_decode", return_value=None):
                    self.assertEqual(pngread.decode_image(png), (width, 5, expected))
                    self.assertEqual(util.decode_png(png), (width, 5, expected))

    def test_random_unfiltered_rgba_with_transparency(self):
        rng = random.Random(82)
        for width, height in ((1, 1), (8, 5), (129, 73)):
            pixels = rng.randbytes(width * height * 4)
            png = util.png_bytes(width, height, pixels)
            with self.subTest(width=width, height=height):
                self.assertEqual(pngread._png_decode(png), (width, height, pixels))
                self.assertEqual(pngread.decode_image(png), (width, height, pixels))


class FastCompositionTests(unittest.TestCase):
    def test_every_channel_and_nearest_neighbour_for_different_sizes(self):
        rng = random.Random(881)
        for rw, rh, mw, mh in ((1, 1, 1, 1), (7, 5, 7, 5), (7, 5, 3, 2),
                               (2, 3, 9, 8), (13, 1, 1, 9), (1, 9, 11, 1)):
            rough = rng.randbytes(rw * rh * 4)
            metal = rng.randbytes(mw * mh * 4)
            rough_png = util.png_bytes(rw, rh, rough)
            metal_png = util.png_bytes(mw, mh, metal)
            for have_rough, have_metal in ((True, True), (True, False), (False, True)):
                width, height = (rw, rh) if have_rough else (mw, mh)
                expected = bytearray()
                for y in range(height):
                    for x in range(width):
                        green = rough[(y * rw + x) * 4] if have_rough else 255
                        sx, sy = x * mw // width, y * mh // height
                        blue = metal[(sy * mw + sx) * 4] if have_metal else 0
                        expected.extend((255, green, blue, 255))
                with self.subTest(sizes=(rw, rh, mw, mh), rough=have_rough, metal=have_metal), \
                     patch.object(pngread, "_pil_decode", return_value=None):
                    output = texturemaps.compose_metal_roughness(
                        rough_png if have_rough else None, metal_png if have_metal else None)
                    self.assertEqual(pngread.decode_image(output), (width, height, bytes(expected)))
                    self.assertEqual(util.decode_png(output), (width, height, bytes(expected)))


if __name__ == "__main__":
    unittest.main()
