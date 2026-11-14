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


def make_v10(positions, uvs=None, triangles=None):
    """BFRES 10.4 minimal mais **conforme au format mesuré** : en-tête, FMDL,

    FVTX avec vraie table d'attributs (chaînes préfixées par leur longueur) et
    tampons index/sommets rangés comme dans le jeu.  Sert à verrouiller la
    lecture pilotée par les données, sans dépendre d'un asset Nintendo.
    """
    triangles = triangles or []
    stride = 16
    base = 0x4000
    vertex_start = base + 0x1000
    size = vertex_start + stride * len(positions)
    data = bytearray(size)

    data[0:4] = b"FRES"
    struct.pack_into("<I", data, 0x08, 0x000A0400)
    struct.pack_into("<H", data, 0x0C, 0xFFFE)
    struct.pack_into("<Q", data, 0x28, 0xF0)            # FMDL
    struct.pack_into("<Q", data, 0x170, base)           # base des tampons

    # --- chaînes (longueur u16 puis texte)
    def put_string(offset, text):
        raw = text.encode("ascii")
        struct.pack_into("<H", data, offset, len(raw))
        data[offset + 2:offset + 2 + len(raw)] = raw
        return offset

    name_ref = put_string(0x2A0, "Modele_Test")
    p0_ref = put_string(0x2B0, "_p0")
    u0_ref = put_string(0x2B8, "_u0")

    # --- FMDL
    data[0xF0:0xF4] = b"FMDL"
    struct.pack_into("<Q", data, 0xF0 + 0x08, name_ref)
    struct.pack_into("<Q", data, 0xF0 + 0x20, 0x1B0)    # FVTX

    # --- FVTX (2 attributs : position f32x3, UV u16x2)
    data[0x1B0:0x1B4] = b"FVTX"
    struct.pack_into("<I", data, 0x1B0 + 0x04, 1)
    struct.pack_into("<Q", data, 0x1B0 + 0x08, 0x210)   # table d'attributs
    struct.pack_into("<Q", data, 0x1B0 + 0x18, size)    # fin des données
    struct.pack_into("<I", data, 0x1B0 + 0x48, vertex_start - base)
    struct.pack_into("<I", data, 0x1B0 + 0x4C, 2)       # nombre d'attributs

    # --- table d'attributs : 16 octets par entrée
    struct.pack_into("<I", data, 0x210, p0_ref)
    struct.pack_into("<I", data, 0x210 + 8, 0x1805)     # position 3 x f32
    struct.pack_into("<I", data, 0x210 + 12, 0x00)
    struct.pack_into("<I", data, 0x220, u0_ref)
    struct.pack_into("<I", data, 0x220 + 8, 0x1201)     # UV 2 x u16
    struct.pack_into("<I", data, 0x220 + 12, 0x0C)

    # --- indices (u16, triangles pleins, pas de remplissage)
    offset = base
    for a, b, c in triangles:
        struct.pack_into("<3H", data, offset, a, b, c)
        offset += 6

    # --- sommets
    for index, (x, y, z) in enumerate(positions):
        offset = vertex_start + index * stride
        struct.pack_into("<3f", data, offset, x, y, z)
        u, v = uvs[index] if uvs else (0.0, 0.0)
        struct.pack_into("<2H", data, offset + 0x0C,
                         int(max(0.0, min(1.0, u)) * 65535),
                         int(max(0.0, min(1.0, v)) * 65535))
    return bytes(data)


class DataDrivenTests(unittest.TestCase):
    def _grid(self, size=12):
        """Grille dont la densité de texels varie : le garde-fou UV l'accepte.

        Une grille parfaitement régulière a une aire 3D constante *et* une aire
        UV constante : la corrélation n'y est pas définie, et le module refuse
        alors les UV (comportement voulu, on ne devine pas).
        """
        positions, uvs = [], []
        for y in range(size):
            for x in range(size):
                t = x / (size - 1)
                s = y / (size - 1)
                positions.append((t * t, s, 0.0))
                uvs.append((t * t, s))
        triangles = []
        for y in range(size - 1):
            for x in range(size - 1):
                a = y * size + x
                b = a + 1
                c = a + size
                d = c + 1
                triangles.append((a, b, c))
                triangles.append((b, d, c))
        return positions, uvs, triangles

    def test_attribute_table_is_read(self):
        data = make_v10(*self._grid())
        base = bfres_v10._u64(data, 0x170)
        shapes = bfres_v10.find_shapes(data, base)
        self.assertEqual(len(shapes), 1)
        shape = shapes[0]
        self.assertEqual([a.name for a in shape.attributes], ["_p0", "_u0"])
        self.assertEqual(shape.stride, 16)
        self.assertEqual(shape.vertex_offset, base + 0x1000)
        self.assertEqual(shape.vertex_count, 144)

    def test_geometry_is_decoded_without_any_hardcoded_offset(self):
        positions, uvs, triangles = self._grid()
        data = make_v10(positions, uvs, triangles)
        result = bfres_v10.parse(data)
        self.assertIsNotNone(result)
        meshes, report = result
        self.assertEqual(report.model_name, "Modele_Test")
        self.assertEqual(len(meshes), 1)
        self.assertEqual(len(meshes[0].faces), len(triangles))
        lo, hi = meshes[0].bounds()
        self.assertAlmostEqual(lo[0], 0.0, places=5)
        self.assertAlmostEqual(hi[0], 1.0, places=5)
        self.assertTrue(report.uv)
        self.assertAlmostEqual(meshes[0].uvs[5][0], uvs[5][0], places=3)

    def test_garbage_vertex_buffer_is_refused(self):
        data = bytearray(make_v10(*self._grid()))
        base = struct.unpack_from("<Q", data, 0x170)[0]
        start = base + 0x1000
        for offset in range(start, len(data), 16):
            struct.pack_into("<3f", data, offset, float("nan"), 0.0, 0.0)
        self.assertIsNone(bfres_v10.parse(bytes(data)))


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
        self.assertGreaterEqual(len(meshes), 1)
        self.assertGreaterEqual(sum(len(m.faces) for m in meshes), 682)

    def test_uv_are_accepted_only_when_plausible(self):
        meshes, report = bfres_v10.parse(self.data)
        self.assertTrue(report.uv)
        self.assertEqual(len(meshes[0].uvs), len(meshes[0].positions))

    def test_bfresfile_exposes_the_geometry(self):
        file = bfres.BfresFile.parse(self.data)
        self.assertEqual(len(file.models), 1)
        meshes = bfres.meshes_from_file(file)
        self.assertGreaterEqual(sum(len(m.faces) for m in meshes), 682)
        lo, hi = meshes[0].bounds()
        self.assertAlmostEqual(lo[0], -hi[0], places=3)     # ailes symétriques


if __name__ == "__main__":
    unittest.main(verbosity=2)


class GuardrailTests(unittest.TestCase):
    """Les garde-fous géométriques sont nommés, donc réglables et testables."""

    def test_thresholds_are_module_constants(self) -> None:
        from hookymod import bfres_v10 as module

        for name in ("COHERENCE_MAX", "EDGE_RATIO_MAX", "OVERSIZED_RATIO"):
            self.assertTrue(hasattr(module, name), name)
            self.assertGreater(getattr(module, name), 0.0, name)
        self.assertLessEqual(module.COHERENCE_MAX, 1.0)

    def test_oversized_triangle_is_dropped(self) -> None:
        from hookymod import bfres_v10 as module

        positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (50.0, 0.0, 0.0)]
        good = [(0, 1, 2)]
        # diagonale 1000 : l'arête de 49 unités reste locale, on garde
        self.assertEqual(module._drop_oversized(positions, good, 1000.0), good)
        # diagonale 100 : la même arête traverse la moitié du modèle, on écarte
        self.assertEqual(module._drop_oversized(positions, good, 100.0), [])

    def test_edge_ratio_sees_a_stretched_mesh(self) -> None:
        from hookymod import bfres_v10 as module

        tight = [(0.0, 0.0, 0.0), (0.1, 0.0, 0.0), (0.0, 0.1, 0.0)]
        self.assertLess(module._edge_ratio(tight, [(0, 1, 2)], 1.0), 0.15)
        stretched = [(0.0, 0.0, 0.0), (9.0, 0.0, 0.0), (0.0, 0.1, 0.0)]
        self.assertGreater(module._edge_ratio(stretched, [(0, 1, 2)], 1.0), 0.15)

    def test_runaway_vertex_buffer_stops_at_garbage(self) -> None:
        from hookymod import bfres_v10 as module
        import struct

        blob = bytearray(12 * 200)
        for index in range(200):
            struct.pack_into("<3f", blob, index * 12, 0.1, 0.1, 0.1)
        struct.pack_into("<3f", blob, 150 * 12, 1e6, 1e6, 1e6)  # donnée étrangère
        points = module._decode_positions(bytes(blob), 0, 200, 12, 0, 0x1805)
        # l'échelle de référence vient des 128 premiers sommets : au-delà, le
        # premier sommet aberrant arrête la lecture au lieu de laisser passer
        # un million de « sommets » inventés
        self.assertEqual(len(points), 150)
