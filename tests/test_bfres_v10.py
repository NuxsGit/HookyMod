"""BFRES 10.x : lecture de la géométrie (mesures + garde-fous).

Le format 10.x n'est pas documenté : ces tests vérifient surtout que le module
**refuse** ce qu'il ne comprend pas, et que les détecteurs (pas d'enregistrement,
découpage des sous-maillages, cohérence) se comportent comme attendu.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from hookymod import bfres, bfres_v10, zs  # noqa: E402

MODEL = ROOT / "Animal_Butterfly.Animal_Butterfly.bfres.zs"
DICTS = ROOT / "ZsDic.pack.zs"


def _synthetic_vertices(positions, stride: int = 24) -> bytes:
    out = bytearray()
    for x, y, z in positions:
        record = bytearray(stride)
        struct.pack_into("<3e", record, 0, x, y, z)
        struct.pack_into("<2H", record, 16, int((x * 1000) % 65535), int((y * 1000) % 65535))
        out += record
    return bytes(out)


class StrideDetectionTests(unittest.TestCase):
    def test_stride_24_is_found(self):
        import math
        payload = _synthetic_vertices([(math.sin(i) * 0.1, math.cos(i) * 0.1, 0.0) for i in range(300)])
        self.assertEqual(bfres_v10._detect_stride(payload, 0, len(payload)), 24)

    def test_stride_32_is_found(self):
        import math
        payload = _synthetic_vertices(
            [(math.sin(i) * 0.1, math.cos(i * 1.7) * 0.2, math.sin(i * 2.3) * 0.3)
             for i in range(300)], stride=32)
        self.assertEqual(bfres_v10._detect_stride(payload, 0, len(payload)), 32)

    def test_noise_falls_back(self):
        noise = bytes((i * 37 + 11) % 251 for i in range(2048))
        self.assertEqual(bfres_v10._detect_stride(noise, 0, len(noise), 24), 24)


class IndexGroupTests(unittest.TestCase):
    def test_padding_ends_a_submesh(self):
        data = bytearray(0x2000)
        # sous-maillage 0 : 3 triangles valides puis du remplissage
        struct.pack_into("<9H", data, 0, 0, 1, 2, 2, 3, 4, 4, 5, 6)
        struct.pack_into("<3H", data, 18, 0, 0, 0)
        # sous-maillage 1 (bloc aligné sur 0x1000) : 2 triangles
        struct.pack_into("<6H", data, 0x1000, 1, 2, 3, 4, 5, 6)
        groups = bfres_v10._index_triangles(bytes(data), 0, len(data), 10)
        self.assertEqual([len(g) for g in groups], [3, 2])
        self.assertEqual(groups[0][0], (0, 1, 2))

    def test_out_of_range_indices_stop_the_group(self):
        data = bytearray(0x1000)
        struct.pack_into("<6H", data, 0, 0, 1, 2, 3, 4, 999)
        groups = bfres_v10._index_triangles(bytes(data), 0, len(data), 10)
        self.assertEqual([len(g) for g in groups], [1])


class CoherenceTests(unittest.TestCase):
    def test_tight_surface_scores_low(self):
        positions = [(i * 0.01, 0.0, 0.0) for i in range(100)]
        triangles = [(i, i + 1, i + 2) for i in range(97)]
        self.assertLess(bfres_v10._coherence(positions, triangles), 0.2)

    def test_garbage_scores_high(self):
        import math
        positions = [(math.sin(i * 12.9898) * 3, math.cos(i * 78.233) * 3,
                      math.sin(i * 37.719) * 3) for i in range(100)]
        triangles = [(i, (i * 7 + 3) % 100, (i * 13 + 11) % 100) for i in range(97)]
        self.assertGreater(bfres_v10._coherence(positions, triangles), 0.5)


class RefusalTests(unittest.TestCase):
    def test_not_a_bfres(self):
        self.assertIsNone(bfres_v10.parse(b"PLZ0" + bytes(0x200)))

    def test_other_versions_are_ignored(self):
        import synth
        data = bytearray(synth.make_bfres())
        struct.pack_into("<I", data, 0x08, 0x00090000)      # version 9
        self.assertIsNone(bfres_v10.parse(bytes(data)))

    def test_truncated_v10_is_refused(self):
        data = bytearray(0x300)
        data[0:4] = b"FRES"
        struct.pack_into("<I", data, 0x08, 0x000A0400)
        self.assertIsNone(bfres_v10.parse(bytes(data)))


@unittest.skipUnless(MODEL.is_file() and DICTS.is_file() and zs.zstd_available(),
                     "modèle ou dictionnaires absents")
class RealV10Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store = zs.ZstdDicts()
        store.load_file(DICTS)
        cls.data = store.decompress(MODEL.read_bytes())

    def test_geometry_is_found_and_validated(self):
        result = bfres_v10.parse(self.data)
        self.assertIsNotNone(result)
        meshes, report = result
        self.assertEqual(report.major, 10)
        self.assertEqual(report.model_name, "Animal_Butterfly")
        self.assertEqual(report.buffer_base, 0x4000)
        self.assertEqual(report.stride, 24)
        self.assertEqual(len(meshes), 3)
        self.assertEqual(sum(len(m.faces) for m in meshes), 1302)

    def test_uv_are_accepted_only_when_plausible(self):
        meshes, report = bfres_v10.parse(self.data)
        self.assertTrue(report.uv)
        self.assertEqual(len(meshes[0].uvs), len(meshes[0].positions))

    def test_bfresfile_exposes_the_geometry(self):
        file = bfres.BfresFile.parse(self.data)
        self.assertEqual(len(file.models), 1)
        meshes = bfres.meshes_from_file(file)
        self.assertEqual(sum(len(m.faces) for m in meshes), 1302)
        lo, hi = meshes[0].bounds()
        self.assertAlmostEqual(lo[0], -hi[0], places=3)     # ailes symétriques


if __name__ == "__main__":
    unittest.main(verbosity=2)
