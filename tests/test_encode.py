"""Tests de l'encodage PNG → BNTX et de l'import OBJ → BFRES."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import bfres, bfres_import, bntx, bntx_encode, util  # noqa: E402
from hookymod.mesh import MeshData, export_obj, load_obj  # noqa: E402
from hookymod.util import HookyError  # noqa: E402


def _error(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


class BntxEncodeTests(unittest.TestCase):
    def test_rgba8_roundtrip_is_lossless(self):
        for size in (4, 8, 16, 32, 64):
            rgba = synth.rgba8_pixels(size, size, seed=size)
            data, report = bntx_encode.png_to_bntx(util.png_bytes(size, size, rgba),
                                                   format_name="RGBA8")
            texture = bntx.BntxFile.parse(data).textures[0]
            self.assertEqual((texture.width, texture.height), (size, size))
            self.assertEqual(texture.mip_count, bntx_encode.mip_levels_for(size, size))
            self.assertEqual(texture.decode(0)[2], rgba, f"taille {size}")
            self.assertIn("R8_G8_B8_A8", report.format_name)

    def test_compressed_formats_are_close(self):
        rgba = synth.rgba8_pixels(32, 32, seed=5)
        png = util.png_bytes(32, 32, rgba)
        for name, limit in (("BC1", 8.0), ("BC3", 8.0)):
            data, _ = bntx_encode.png_to_bntx(png, format_name=name)
            decoded = bntx.BntxFile.parse(data).textures[0].decode(0)[2]
            self.assertLess(_error(decoded, rgba), limit, name)

    def test_bc4_and_bc5_encode_single_channels(self):
        rgba = bytes([10, 20, 0, 255]) * 16
        data, _ = bntx_encode.png_to_bntx(util.png_bytes(4, 4, rgba), format_name="BC4")
        decoded = bntx.BntxFile.parse(data).textures[0].decode(0)[2]
        self.assertLess(abs(decoded[0] - 10), 48)   # rampe 8 valeurs sur un bloc uniforme
        self.assertEqual(decoded[3], 255)

    def test_replace_keeps_file_size_and_template_format(self):
        rgba = synth.rgba8_pixels(16, 16, seed=2)
        template = synth.make_bntx(16, 16, rgba)
        new = synth.rgba8_pixels(16, 16, seed=9)
        data, report = bntx_encode.png_to_bntx(util.png_bytes(16, 16, new),
                                               template=template)
        self.assertEqual(len(data), len(template))
        texture = bntx.BntxFile.parse(data).textures[0]
        self.assertEqual(texture.decode(0)[2], new)
        self.assertEqual(report.mips, 1)
        self.assertEqual(texture.format_name, "R8_G8_B8_A8_UNORM")

    def test_replace_rejects_size_change(self):
        template = synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8))
        with self.assertRaises(HookyError) as caught:
            bntx_encode.png_to_bntx(util.png_bytes(4, 4, synth.rgba8_pixels(4, 4)),
                                    template=template)
        self.assertIn("Dimensions différentes", str(caught.exception))

    def test_replace_selects_texture_by_name(self):
        template = synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8))
        new = synth.rgba8_pixels(8, 8, seed=4)
        data, report = bntx_encode.png_to_bntx(util.png_bytes(8, 8, new), template,
                                               texture="TexName0")
        self.assertEqual(report.texture, "TexName0")
        self.assertEqual(bntx.BntxFile.parse(data).textures[0].decode(0)[2], new)
        with self.assertRaises(HookyError):
            bntx_encode.png_to_bntx(util.png_bytes(8, 8, new), template,
                                    texture="Inexistante")

    def test_mips_are_generated(self):
        rgba = synth.rgba8_pixels(32, 32, seed=6)
        data, _ = bntx_encode.png_to_bntx(util.png_bytes(32, 32, rgba), format_name="RGBA8")
        texture = bntx.BntxFile.parse(data).textures[0]
        sizes = [texture.decode(level)[:2] for level in range(texture.mip_count)]
        self.assertEqual(sizes[1], (16, 16))
        self.assertEqual(sizes[-1], (1, 1))

    def test_unsupported_format_is_reported(self):
        template = synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8), fmt=0x2001)  # BC7
        with self.assertRaises(HookyError) as caught:
            bntx_encode.png_to_bntx(util.png_bytes(8, 8, synth.rgba8_pixels(8, 8)),
                                    template=template)
        self.assertIn("pas encodable", str(caught.exception))

    def test_bntx_to_png(self):
        rgba = synth.rgba8_pixels(8, 8, seed=3)
        png = bntx_encode.bntx_to_png(synth.make_bntx(8, 8, rgba))
        self.assertTrue(png.startswith(b"\x89PNG"))
        width, height, pixels = util.decode_png(png)
        self.assertEqual((width, height), (8, 8))
        self.assertEqual(pixels, rgba)


class BfresImportTests(unittest.TestCase):
    def setUp(self):
        self.original = synth.make_bfres()
        self.file = bfres.BfresFile.parse(self.original)

    def _mesh(self, positions, faces=None):
        return MeshData(name="edit", positions=positions,
                        faces=faces or [(0, 1, 2)],
                        uvs=[(0.0, 0.0)] * len(positions),
                        normals=[(0.0, 0.0, 1.0)] * len(positions))

    def test_capacity_report(self):
        lines = bfres_import.describe_capacity(self.original)
        self.assertIn("Shape0", lines[0])
        self.assertIn("3 sommet(s)", lines[0])
        self.assertTrue(any("Position" in line for line in lines))

    def test_import_replaces_geometry(self):
        mesh = self._mesh([(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)])
        data, report = bfres_import.import_obj(self.original, [mesh])
        self.assertEqual(len(data), len(self.original))
        parsed = bfres.BfresFile.parse(data).models[0].build_meshes(0)[0]
        self.assertEqual(parsed.positions, mesh.positions)
        self.assertEqual(parsed.faces, [(0, 1, 2)])
        self.assertEqual(report.vertices, 3)
        self.assertIn("Position", " ".join(report.attributes))

    def test_import_from_obj_file(self):
        obj = b"v 1 2 3\nv 4 5 6\nv 7 8 10\nvn 0 0 1\nvn 0 0 1\nvn 0 0 1\nf 1//1 2//2 3//3\n"
        data, _ = bfres_import.import_obj_bytes(self.original, obj)
        mesh = bfres.BfresFile.parse(data).models[0].build_meshes(0)[0]
        self.assertEqual(mesh.positions[0], (1.0, 2.0, 3.0))

    def test_import_refuses_more_vertices(self):
        mesh = self._mesh([(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]
                          + [(float(i), 0., 0.) for i in range(3, 8)],
                          faces=[(0, 1, 2)])
        with self.assertRaises(HookyError) as caught:
            bfres_import.import_obj(self.original, [mesh])
        self.assertIn("impossible d'agrandir", str(caught.exception))

    def test_import_refuses_more_indices(self):
        mesh = self._mesh([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                          faces=[(0, 1, 2), (2, 1, 0)])
        with self.assertRaises(HookyError) as caught:
            bfres_import.import_obj(self.original, [mesh])
        self.assertIn("indices", str(caught.exception))

    def test_import_refuses_strips(self):
        mesh = self._mesh([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)])
        original = bytearray(self.original)
        offsets = bfres.BfresFile.parse(self.original).models[0].shapes[0].meshes[0]
        # primitive écrit juste avant indexFormat
        import struct
        fmt_pos = offsets.index_count_offset - 8
        struct.pack_into("<I", original, fmt_pos, 0x04)   # TRIANGLE_STRIP
        with self.assertRaises(HookyError) as caught:
            bfres_import.import_obj(bytes(original), [mesh])
        self.assertIn("Primitive", str(caught.exception))

    def test_export_import_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "m.obj"
            export_obj(self.file.models[0].build_meshes(0), path)
            mesh = load_obj(path.read_bytes())
        data, _ = bfres_import.import_obj(self.original, [mesh])
        parsed = bfres.BfresFile.parse(data).models[0].build_meshes(0)[0]
        self.assertEqual(parsed.faces, [(0, 1, 2)])


if __name__ == "__main__":
    unittest.main()
