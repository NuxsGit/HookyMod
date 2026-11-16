"""Tegra T239 / « Orin » : BNTX 0.4.x, disposition des pixels, robustesse.

Les fichiers de test sont synthétiques : aucune donnée de jeu n'est utilisée.
Les deux dispositions sont vérifiées contre les formules des TRM Tegra :

* X1  : ``((x%64)/32)*256 + ((y%8)/2)*64 + ((x%32)/16)*32 + (y%2)*16 + (x%16)``
* Orin: ``((x%64)/32)*256 + ((y%8)/4)*128 + ((x%32)/16)*64 + (y%4)*16 + (x%16)``
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import bntx, bntx_encode, util  # noqa: E402

bntx_mod = bntx


class SwizzleLayoutTests(unittest.TestCase):
    def test_gob_offsets_match_tegra_tables(self):
        x1 = [bntx.gob_offset(0, y) for y in range(8)]
        orin = [bntx.gob_offset(0, y, True) for y in range(8)]
        self.assertEqual(x1, [0, 16, 64, 80, 128, 144, 192, 208])
        self.assertEqual(orin, [0, 16, 32, 48, 128, 144, 160, 176])
        # le second secteur de 32 octets (x = 32..63) reste à 256 octets
        self.assertEqual(bntx.gob_offset(32, 0), 256)
        self.assertEqual(bntx.gob_offset(32, 0, True), 256)

    def test_swizzle_roundtrip_both_layouts(self):
        for width, height, bpp in ((64, 64, 4), (32, 32, 16), (17, 9, 4), (8, 8, 1)):
            linear = bytes((i * 7 + 3) & 0xFF for i in range(width * height * bpp))
            for orin in (False, True):
                packed = bntx.swizzle(width, height, 1, 1, bpp, 0, 4, linear, orin=orin)
                restored = bntx.deswizzle(width, height, 1, 1, bpp, 0, 4, packed,
                                          orin=orin)
                self.assertEqual(restored, linear,
                                 f"{width}x{height} bpp={bpp} orin={orin}")

    def test_layouts_are_actually_different(self):
        width, height, bpp = 64, 64, 4
        linear = bytes(range(256)) * (width * height * bpp // 256)
        x1 = bntx.swizzle(width, height, 1, 1, bpp, 0, 4, linear, orin=False)
        orin = bntx.swizzle(width, height, 1, 1, bpp, 0, 4, linear, orin=True)
        self.assertNotEqual(x1, orin)
        # relire un fichier Orin comme du X1 casse l'image (et inversement)
        self.assertNotEqual(bntx.deswizzle(width, height, 1, 1, bpp, 0, 4, orin,
                                           orin=False), linear)
        self.assertNotEqual(bntx.deswizzle(width, height, 1, 1, bpp, 0, 4, x1,
                                           orin=True), linear)

    def test_address_helper_is_consistent_with_deswizzle(self):
        # chaque octet doit être lu une seule fois : aucune collision d'adresse
        width, height, bpp = 16, 8, 4
        seen = set()
        for y in range(height):
            for x in range(width):
                seen.add(bntx.get_addr_block_linear(x, y, width, bpp, 0, 1, True))
        self.assertEqual(len(seen), width * height)


class OrinBntxTests(unittest.TestCase):
    def _orin_texture(self, size: int = 32, format_name: str = "RGBA8") -> tuple:
        rgba = synth.rgba8_pixels(size, size, seed=size)
        data, _ = bntx_encode.png_to_bntx(util.png_bytes(size, size, rgba),
                                          format_name=format_name, orin=True)
        return data, rgba

    def test_version_selects_orin_layout(self):
        data, rgba = self._orin_texture()
        file = bntx.BntxFile.parse(data)
        self.assertEqual(file.version, 0x00040100)
        self.assertGreaterEqual(file.version, bntx.BntxFile.ORIN_VERSION)
        self.assertTrue(file.orin)
        self.assertTrue(all(t.orin for t in file.textures))
        self.assertEqual(file.textures[0].decode(0)[2], rgba)

    def test_default_stays_x1(self):
        rgba = synth.rgba8_pixels(16, 16, seed=1)
        data, _ = bntx_encode.png_to_bntx(util.png_bytes(16, 16, rgba),
                                          format_name="RGBA8")
        file = bntx.BntxFile.parse(data)
        self.assertEqual(file.version, 0x00030000)
        self.assertFalse(file.orin)
        self.assertEqual(file.textures[0].swizzle_label, "linéaire")

    def test_32_bit_name_and_mip_pointers_in_synthetic_0_4_1_1(self):
        rgba = synth.rgba8_pixels(16, 16, seed=14)
        data = bytearray(bntx_encode.build_bntx(
            "Example.bntx", 16, 16, rgba, format_name="BC4", mips=5,
            version=0x00040101, texture_name="Example_Alb", orin=True))
        data[0x20:0x24] = b"Ounc"
        brti = struct.unpack_from("<Q", data, 0x58)[0]
        info = brti + 0x10
        name_ptr = struct.unpack_from("<Q", data, info + 76)[0]
        mip_ptr = struct.unpack_from("<Q", data, info + 92)[0]
        # The 32-bit variants occupy the upper half of the old pointer slots.
        struct.pack_into("<Q", data, info + 76, name_ptr << 32)
        struct.pack_into("<Q", data, info + 92, mip_ptr << 32)

        file = bntx.BntxFile.parse(bytes(data))
        self.assertEqual(file.version_label, "0.4.1.1")
        self.assertTrue(file.orin)
        self.assertEqual(file.name, "Example.bntx")
        texture = file.textures[0]
        self.assertEqual(texture.name, "Example_Alb")
        self.assertEqual(texture.format_name, "BC4_UNORM")
        self.assertEqual(texture.mip_count, 5)
        self.assertEqual(texture.mip_offsets[0], 0)
        self.assertEqual(texture.mip_offsets, sorted(texture.mip_offsets))
        for level in range(5):
            width, height, pixels = texture.decode(level)
            self.assertEqual((width, height), (max(1, 16 >> level),) * 2)
            self.assertEqual(len(pixels), width * height * 4)

    def test_forced_layout_overrides_version(self):
        data, rgba = self._orin_texture(32, "BC1")
        forced_x1 = bntx.BntxFile.parse(data, orin=False)
        self.assertFalse(forced_x1.textures[0].orin)
        # forcer la mauvaise disposition ne doit pas planter, juste donner autre chose
        self.assertEqual(len(forced_x1.textures[0].decode(0)[2]), 32 * 32 * 4)
        self.assertTrue(bntx.BntxFile.parse(data, orin=True).textures[0].orin)

    def test_info_reports_version_and_swizzle(self):
        data, _ = self._orin_texture(16, "BC3")
        text = bntx.BntxFile.parse(data).info()
        self.assertIn("Version : 0.4.1.0", text)
        self.assertIn("Orin", text)

    def test_replace_texture_keeps_orin_layout(self):
        template, _ = self._orin_texture(32, "BC3")
        new = synth.rgba8_pixels(32, 32, seed=77)
        data, report = bntx_encode.png_to_bntx(util.png_bytes(32, 32, new),
                                               template=template)
        self.assertEqual(len(data), len(template))
        file = bntx.BntxFile.parse(data)
        self.assertTrue(file.textures[0].orin)
        decoded = file.textures[0].decode(0)[2]
        error = sum(abs(a - b) for a, b in zip(decoded, new)) / len(new)
        self.assertLess(error, 10.0)

    def test_pointer_table_recovery_when_header_moves(self):
        data, rgba = self._orin_texture(16)
        broken = bytearray(data)
        struct.pack_into("<Q", broken, 0x28, 0x00FFFF00)   # table de pointeurs absurde
        file = bntx.BntxFile.parse(bytes(broken))
        self.assertEqual(len(file.textures), 1)
        self.assertTrue(any("balayage" in w for w in file.warnings))
        self.assertEqual(file.textures[0].decode(0)[2], rgba)

    def test_unreadable_file_reports_a_clear_error(self):
        with self.assertRaises(bntx.FormatError) as ctx:
            bntx.BntxFile.parse(b"BNTX" + b"\x00" * 0x100)
        message = str(ctx.exception)
        self.assertIn("BNTX", message)
        self.assertIn("bom", message.lower())

    def test_bad_magic_is_explicit(self):
        with self.assertRaises(bntx.FormatError) as ctx:
            bntx.BntxFile.parse(b"FRES" + b"\x00" * 0x40)
        self.assertIn("BNTX", str(ctx.exception))


class NameTableTests(unittest.TestCase):
    """Le bloc ``_STR`` écrit par l'encodeur doit être relu par le parseur."""

    def test_built_file_exposes_its_name_and_texture_name(self):
        data, _ = bntx_encode.png_to_bntx(
            util.png_bytes(16, 16, synth.rgba8_pixels(16, 16, seed=2)),
            format_name="BC1", name="Example.bftex", orin=True)
        file = bntx_mod.BntxFile.parse(data)
        self.assertEqual(file.name, "Example.bftex")
        self.assertEqual(file.textures[0].name, "Example")

    def test_default_name_is_derived_from_the_texture(self):
        data, _ = bntx_encode.png_to_bntx(
            util.png_bytes(8, 8, synth.rgba8_pixels(8, 8, seed=3)),
            format_name="RGBA8")
        file = bntx_mod.BntxFile.parse(data)
        self.assertEqual(file.name, "texture.bntx")
        self.assertEqual(file.textures[0].name, "texture")

    def test_texture_label_follows_the_file_name(self):
        data, _ = bntx_encode.png_to_bntx(
            util.png_bytes(8, 8, synth.rgba8_pixels(8, 8, seed=4)),
            format_name="RGBA8", name="Tete.bntx")
        file = bntx_mod.BntxFile.parse(data)
        self.assertEqual(file.name, "Tete.bntx")
        self.assertEqual(file.textures[0].name, "Tete")

    def test_replace_texture_keeps_names(self):
        original, _ = bntx_encode.png_to_bntx(
            util.png_bytes(16, 16, synth.rgba8_pixels(16, 16, seed=5)),
            format_name="RGBA8", name="Oeil.bntx")
        before = bntx_mod.BntxFile.parse(original)
        replaced, _ = bntx_encode.replace_texture(
            original, synth.rgba8_pixels(16, 16, seed=6), 16, 16)
        after = bntx_mod.BntxFile.parse(replaced)
        self.assertEqual(after.name, before.name)
        self.assertEqual(after.textures[0].name, before.textures[0].name)
        self.assertEqual(len(replaced), len(original))


if __name__ == "__main__":
    unittest.main(verbosity=2)
