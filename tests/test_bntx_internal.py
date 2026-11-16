"""Décodeurs BCn intégrés (utilisés quand ``texture2ddecoder`` est absent).

Ces tests tournent volontairement **sans** le module optionnel : c'est le cas
des installations Windows où ``pip install texture2ddecoder`` n'a pas été
lancé, et c'est là que les erreurs de disposition des blocs se voyaient.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import bntx, bntx_encode, util  # noqa: E402


def _mean_error(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(b))


class InternalDecoderTests(unittest.TestCase):
    def setUp(self):
        self._saved = bntx._t2d
        bntx._t2d = None          # force les décodeurs Python

    def tearDown(self):
        bntx._t2d = self._saved

    def _roundtrip(self, fmt: str, size: int = 32, orin: bool = True):
        rgba = synth.rgba8_pixels(size, size, seed=13)
        data, _ = bntx_encode.png_to_bntx(util.png_bytes(size, size, rgba),
                                          format_name=fmt, orin=orin)
        texture = bntx.BntxFile.parse(data).textures[0]
        return rgba, texture.decode(0)[2]

    def test_bc1_is_close_to_source(self):
        rgba, decoded = self._roundtrip("BC1")
        self.assertLess(_mean_error(decoded, rgba), 10.0)

    def test_bc3_is_close_to_source(self):
        """Régression : les moitiés alpha/couleur des blocs sont entrelacées."""
        rgba, decoded = self._roundtrip("BC3")
        self.assertLess(_mean_error(decoded, rgba), 10.0)

    def test_bc3_keeps_transparency(self):
        rgba = bytearray(synth.rgba8_pixels(16, 16, seed=21))
        for i in range(3, len(rgba), 4):      # moitié de la texture transparente
            rgba[i] = 0 if (i // 4) % 2 else 255
        data, _ = bntx_encode.png_to_bntx(util.png_bytes(16, 16, bytes(rgba)),
                                          format_name="BC3", orin=True)
        decoded = bntx.BntxFile.parse(data).textures[0].decode(0)[2]
        alphas = decoded[3::4]
        self.assertLess(min(alphas), 40)
        self.assertGreater(max(alphas), 215)

    def test_bc3_internal_matches_texture2ddecoder(self):
        if self._saved is None:
            self.skipTest("texture2ddecoder absent")
        rgba = synth.rgba8_pixels(32, 32, seed=31)
        data, _ = bntx_encode.png_to_bntx(util.png_bytes(32, 32, rgba),
                                          format_name="BC3", orin=True)
        texture = bntx.BntxFile.parse(data).textures[0]
        internal = texture.decode(0)[2]
        bntx._t2d = self._saved
        reference = texture.decode(0)[2]
        self.assertLess(_mean_error(internal, reference), 2.0)

    def test_bc2_alpha_half_is_deinterleaved(self):
        """BC2 = 8 octets d'alpha puis 8 octets de couleur, bloc par bloc."""
        rgba = synth.rgba8_pixels(8, 8, seed=41)
        bc1, _ = bntx_encode.png_to_bntx(util.png_bytes(8, 8, rgba),
                                         format_name="BC1", orin=True)
        texture = bntx.BntxFile.parse(bc1).textures[0]
        color_blocks = texture.data
        # reconstruit un flux BC2 : alpha opaque (8 octets) + bloc BC1
        bc2 = b"".join(b"\xff" * 8 + color_blocks[i * 8:i * 8 + 8]
                       for i in range(len(color_blocks) // 8))
        decoded = bntx._decode_bcn("BC2", bc2, 8, 8, 4, 4)
        expected = bntx._decode_bcn("BC1", color_blocks, 8, 8, 4, 4)
        self.assertEqual(_mean_error(decoded, expected), 0.0)
        self.assertEqual(set(decoded[3::4]), {255})

    def test_bc4_and_bc5_keep_their_channels(self):
        rgba = synth.rgba8_pixels(16, 16, seed=51)
        for fmt in ("BC4", "BC5"):
            data, _ = bntx_encode.png_to_bntx(util.png_bytes(16, 16, rgba),
                                              format_name=fmt, orin=True)
            decoded = bntx.BntxFile.parse(data).textures[0].decode(0)[2]
            self.assertEqual(len(decoded), 16 * 16 * 4)
            self.assertEqual(set(decoded[3::4]), {255})
        # BC5 conserve deux canaux distincts
        data, _ = bntx_encode.png_to_bntx(util.png_bytes(16, 16, rgba),
                                          format_name="BC5", orin=True)
        decoded = bntx.BntxFile.parse(data).textures[0].decode(0)[2]
        self.assertNotEqual(decoded[0::4], decoded[1::4])

    def test_orin_and_x1_layouts_both_decode(self):
        for orin in (True, False):
            rgba, decoded = self._roundtrip("BC1", size=16, orin=orin)
            self.assertLess(_mean_error(decoded, rgba), 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
