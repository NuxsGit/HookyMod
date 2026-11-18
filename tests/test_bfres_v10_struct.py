"""Tables LOD 0, matériaux et UV v10 : fixtures indépendantes des ressources utilisateur."""
from __future__ import annotations

import contextlib
import io
import struct
import tempfile
import unittest
from pathlib import Path

from hookymod import bfres, bfres_v10, cli
from hookymod.util import HookyError


def structured_model() -> bytes:
    data = bytearray(0x2500)
    def u16(o, n): struct.pack_into("<H", data, o, n)
    def u32(o, n): struct.pack_into("<I", data, o, n)
    def u64(o, n): struct.pack_into("<Q", data, o, n)
    cursor = 0x1200
    def text(s):
        nonlocal cursor
        start = cursor
        raw = s.encode()
        u16(start, len(raw))
        data[start + 2:start + 2 + len(raw)] = raw
        cursor += len(raw) + 3
        return start
    strings = {s: text(s) for s in ("Objet", "Forme_A", "Forme_B", "Mat_A", "Mat_B", "_p0", "_n0", "_u0",
                                   "Tex_A_Alb", "Tex_A_Nrm", "Tex_B_Alb", "Tex_B_Rgh")}
    data[:4] = b"FRES"
    u32(8, 0x000A0400)
    data[0x0c:0x0e] = b"\xff\xfe"
    u64(0x28, 0x200)
    u64(0x170, 0x2000)
    u16(0xdc, 1)
    data[0x200:0x204] = b"FMDL"
    u64(0x208, strings["Objet"])
    u64(0x220, 0x300)
    u64(0x228, 0x400)
    u64(0x238, 0x500)
    u16(0x268, 2)
    u16(0x26a, 2)
    u16(0x26c, 2)
    for i in range(2):
        vertex = 0x300 + 0x58 * i
        shape = 0x400 + 0x60 * i
        material = 0x500 + 0xb0 * i
        attr = 0xa00 + i * 0x40
        table = 0xb00 + i * 0x20
        mesh = 0xc00 + i * 0x80
        tex = 0xf00 + i * 0x20
        data[vertex:vertex + 4] = b"FVTX"
        u64(vertex + 8, attr)
        u64(vertex + 0x30, table)
        u64(vertex + 0x38, table + 8)
        u32(vertex + 0x48, 0x100 + 0x100 * i)
        data[vertex + 0x4c:vertex + 0x4e] = b"\x03\x01"
        u32(vertex + 0x50, 4)
        u32(table, 4 * 20)
        u32(table + 8, 20)
        for j, (name, fmt, offset) in enumerate((("_p0", 0x1805, 0), ("_n0", 0x0e02, 12),
                                                  ("_u0", 0x1201 if not i else 0x1205, 16))):
            u64(attr + 16 * j, strings[name])
            u32(attr + 16 * j + 8, fmt)
            u32(attr + 16 * j + 12, offset)
        data[shape:shape + 4] = b"FSHP"
        u64(shape + 8, strings[f"Forme_{'AB'[i]}"])
        u64(shape + 0x10, vertex)
        u64(shape + 0x18, mesh)
        u16(shape + 0x52, i)
        data[shape + 0x5b] = 2  # le second LOD ne doit pas être exporté
        u64(mesh, 0xf80 + i * 0x10)
        u64(mesh + 0x18, table + 0x10)
        u32(table + 0x10, 6)
        u16(mesh + 0x34, 1)
        struct.pack_into("<II", data, 0xf80 + i * 0x10, 0, 3)
        struct.pack_into("<5I", data, mesh + 0x20, i * 8, 3, 1, 3, i)
        struct.pack_into("<3H", data, 0x2000 + i * 8, 0, 1, 2)
        for j in range(4):
            at = 0x2100 + i * 0x100 + j * 20
            # Triangles non dégénérés et UV distinctes pour vérifier l'import
            # dans un consommateur externe, y compris le sens de l'axe V.
            coords = (((0, 0), (1, 0), (0, 1), (1, 1)),
                      ((1, 1), (1, 0), (0, 1), (0, 0)))
            x, y = coords[i][j]
            struct.pack_into("<3fI", data, at, float(x), float(y + 2 * i), .125, 511 << 20)
            if i:
                uv = ((-0.5, 1.5), (0., 0.), (1., 0.), (.5, 1.))[j]
                struct.pack_into("<2e", data, at + 16, *uv)
            else:
                uv = ((65535, 32768), (0, 0), (32768, 65535), (65535, 65535))[j]
                struct.pack_into("<2H", data, at + 16, *uv)
        data[material:material + 4] = b"FMAT"
        u64(material + 8, strings[f"Mat_{'AB'[i]}"])
        u64(material + 0x20, tex)
        data[material + 0xa3] = 2
        for j, name in enumerate((("Tex_A_Alb", "Tex_A_Nrm"), ("Tex_B_Alb", "Tex_B_Rgh"))[i]):
            u64(tex + j * 8, strings[name])
    return bytes(data)


class StructuralV10Tests(unittest.TestCase):
    def test_declared_lod_zero_materials_uv_and_indices(self):
        meshes, report = bfres_v10.parse(structured_model())
        self.assertTrue(report.structured)
        self.assertEqual([m.name for m in meshes], ["Forme_A", "Forme_B"])
        self.assertEqual([m.material for m in meshes], ["Mat_A", "Mat_B"])
        self.assertEqual([m.faces for m in meshes], [[(0, 1, 2)], [(1, 2, 3)]])
        self.assertAlmostEqual(meshes[0].uvs[0][0], 1.)
        self.assertAlmostEqual(meshes[0].uvs[0][1], 32768 / 65535)
        self.assertEqual(meshes[1].uvs[0], (-0.5, 1.5))
        self.assertEqual(meshes[0].normals[0], (0., 0., 1.))
        self.assertEqual(report.material_textures, {"Mat_A": ["Tex_A_Alb", "Tex_A_Nrm"],
                                                     "Mat_B": ["Tex_B_Alb", "Tex_B_Rgh"]})
        file = bfres.BfresFile.parse(structured_model())
        self.assertEqual([m.material for m in bfres.meshes_from_file(file)], ["Mat_A", "Mat_B"])

    def test_prebuilt_model_rejects_a_different_lod(self):
        file = bfres.BfresFile.parse(structured_model())
        readers = (lambda: file.models[0].build_meshes(1),
                   lambda: file.build_meshes(0, 1),
                   lambda: bfres.meshes_from_file(file, lod=1))
        for reader in readers:
            with self.subTest(reader=reader), self.assertRaisesRegex(HookyError, "LOD 0"):
                reader()

    def test_cli_rejects_an_unavailable_lod_without_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "modele.bfres"
            source.write_bytes(structured_model())
            for command, output in (("export-model", root / "bundle"),
                                    ("obj", root / "modele.obj"),
                                    ("view", root / "apercu.png")):
                stderr = io.StringIO()
                with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(stderr):
                    self.assertEqual(cli.main([command, str(source), "--lod", "1", "-o", str(output)]), 1)
                    self.assertIn("LOD 0", stderr.getvalue())
                    self.assertFalse(output.exists())

    def test_submesh_ranges_are_bytes_and_unreferenced_indices_are_not_drawn(self):
        data = bytearray(structured_model())
        # Deux plages de triangles séparées par des indices de remplissage.
        struct.pack_into("<I", data, 0xb10, 18)
        struct.pack_into("<I", data, 0xc2c, 9)
        struct.pack_into("<H", data, 0xc34, 2)
        struct.pack_into("<4I", data, 0xf80, 0, 3, 12, 3)
        struct.pack_into("<9H", data, 0x2000, 0, 1, 2, 999, 999, 999, 0, 2, 3)
        # Replacer l'autre tampon pour qu'il ne chevauche pas la première plage.
        struct.pack_into("<I", data, 0xc80 + 0x20, 0x20)
        struct.pack_into("<3H", data, 0x2020, 0, 1, 2)
        meshes, report = bfres_v10.parse(bytes(data))
        self.assertEqual(meshes[0].faces, [(0, 1, 2), (0, 2, 3)])
        self.assertEqual(report.submeshes, 3)
        struct.pack_into("<I", data, 0xf88, 2)  # chevauchement de plages
        self.assertIsNone(bfres_v10.parse(bytes(data)))

    def test_multi_model_and_morph_and_skeleton_are_not_silently_misrepresented(self):
        data = bytearray(structured_model())
        struct.pack_into("<H", data, 0xdc, 2)
        self.assertIsNone(bfres_v10.parse(bytes(data)))
        self.assertFalse(bfres.BfresFile.parse(bytes(data)).models)
        data = bytearray(structured_model())
        struct.pack_into("<Q", data, 0x218, 0x1000)  # pointeur de squelette
        data[0x45c] = 1
        _meshes, report = bfres_v10.parse(bytes(data))
        self.assertTrue(any("squelette et animation à associer" in warning for warning in report.warnings))
        self.assertTrue(any("déformation" in warning for warning in report.warnings))

    def test_corrupt_declared_tables_never_fall_back_to_heuristic_geometry(self):
        for offset, value in ((0x228, 0x7fffffff), (0x410, 0x7fffffff),
                              (0xc20, 0x7fffffff), (0x520, 0x7fffffff)):
            data = bytearray(structured_model())
            struct.pack_into("<Q" if offset != 0xc20 else "<I", data, offset, value)
            with self.subTest(offset=offset):
                self.assertIsNone(bfres_v10.parse(bytes(data)))
                self.assertFalse(bfres.BfresFile.parse(bytes(data)).models)


if __name__ == "__main__":
    unittest.main()
