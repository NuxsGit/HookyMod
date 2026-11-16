"""Pose statique des pièces rigides : fixtures synthétiques, sans fichier de jeu."""
from __future__ import annotations

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

from hookymod import bfres_v10, blender
from tests.test_bfres_v10_struct import structured_model


def rigid_model() -> bytes:
    data = bytearray(structured_model())
    # FSKL v10 : deux os à échelle unitaire, avec un enfant tourné de 90°.
    struct.pack_into("<Q", data, 0x218, 0x1500)
    data[0x1500:0x1504] = b"FSKL"
    struct.pack_into("<I", data, 0x1504, 0x1000)  # Euler XYZ
    struct.pack_into("<QQQ", data, 0x1510, 0x1580, 0x1700, 0)
    struct.pack_into("<HHH", data, 0x1538, 2, 0, 2)
    struct.pack_into("<HH", data, 0x1700, 0, 1)
    for i, (parent, angle, trans) in enumerate(((-1, 0., (5., 0., 0.)),
                                                 (0, math.pi / 2, (0., 2., 0.)))):
        at = 0x1580 + 0x58 * i
        struct.pack_into("<Hhhh", data, at + 0x20, i, parent, -1, i)
        struct.pack_into("<I", data, at + 0x2c, 0x1000)
        struct.pack_into("<3f4f3f", data, at + 0x30,
                         1., 1., 1., 0., 0., angle, 1., *trans)

    # Forme A : zéro influence, os directement dans FSHP.
    struct.pack_into("<H", data, 0x400 + 0x54, 1)
    for j in range(4):
        struct.pack_into("<I", data, 0x2100 + j * 20 + 12, 511)  # normale +X

    # Forme B : une influence, indice de matrice par sommet (_i0 uint8).
    data[0x400 + 0x60 + 0x5a] = 1
    vertex = 0x300 + 0x58
    data[vertex + 0x54] = 1
    data[vertex + 0x4c] = 4
    struct.pack_into("<I", data, 0xb20, 4 * 24)
    struct.pack_into("<I", data, 0xb28, 24)
    struct.pack_into("<H", data, 0x1480, 3)
    data[0x1482:0x1485] = b"_i0"
    struct.pack_into("<QII", data, 0xa70, 0x1480, 0x0203, 20)
    points = ((1., 3.), (1., 2.), (0., 3.), (0., 2.))
    for j, (x, y) in enumerate(points):
        at = 0x2200 + j * 24
        struct.pack_into("<3fI2eB", data, at, x, y, .125, 511, 0., 0., (0, 1, 1, 0)[j])
    return bytes(data)


def accessor(document, binary: bytes, index: int):
    item = document["accessors"][index]
    view = document["bufferViews"][item["bufferView"]]
    offset = view.get("byteOffset", 0) + item.get("byteOffset", 0)
    width = {"SCALAR": 1, "VEC3": 3}[item["type"]]
    fmt = {5126: "f", 5123: "H", 5125: "I"}[item["componentType"]]
    return struct.unpack_from(f"<{item['count'] * width}{fmt}", binary, offset)


class RigidPoseTests(unittest.TestCase):
    def assert_vec(self, actual, expected):
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b, places=5)

    def test_hierarchy_rigid_indices_and_normals_are_placed_without_losing_faces(self):
        meshes, report = bfres_v10.parse(rigid_model())
        self.assertEqual([m.faces for m in meshes], [[(0, 1, 2)], [(1, 2, 3)]])
        self.assert_vec(meshes[0].positions[0], (5., 2., .125))
        self.assert_vec(meshes[0].positions[1], (5., 3., .125))
        self.assert_vec(meshes[0].normals[0], (0., 1., 0.))
        # L'index 0 utilise la racine, 1 utilise l'enfant.
        self.assert_vec(meshes[1].positions[0], (6., 3., .125))
        self.assert_vec(meshes[1].positions[1], (3., 3., .125))
        self.assert_vec(meshes[1].normals[1], (0., 1., 0.))
        self.assertTrue(any("2 forme(s) rigide(s) replacée(s)" in w for w in report.warnings))
        self.assertTrue(any("animation non exportés" in w for w in report.warnings))

    def test_gltf_and_obj_export_the_same_placed_geometry_and_triangles(self):
        meshes, report = bfres_v10.parse(rigid_model())
        with tempfile.TemporaryDirectory() as tmp:
            files = blender.export_bundle(meshes, tmp, "rigide", warnings=report.warnings)
            doc = json.loads(files["gltf"].read_text())
            binary = files["bin"].read_bytes()
            obj = files["obj"].read_text()
            self.assertEqual(len([line for line in obj.splitlines() if line.startswith("f ")]), 2)
            for index, mesh in enumerate(meshes):
                prim = doc["meshes"][index]["primitives"][0]
                pos = accessor(doc, binary, prim["attributes"]["POSITION"])
                normals = accessor(doc, binary, prim["attributes"]["NORMAL"])
                indices = accessor(doc, binary, prim["indices"])
                for actual, expected in zip(pos, (v for p in mesh.positions for v in p)):
                    self.assertAlmostEqual(actual, expected, places=5)
                for actual, expected in zip(normals, (v for p in mesh.normals for v in p)):
                    self.assertAlmostEqual(actual, expected, places=5)
                self.assertEqual(indices, tuple(v for f in mesh.faces for v in f))
            self.assertIn("replacée(s)", files["report"].read_text())

    def test_unsupported_or_damaged_skeleton_never_moves_only_part_of_a_shape(self):
        for label, patch in (
            ("cycle", lambda d: struct.pack_into("<h", d, 0x1580 + 0x22, 1)),
            ("scale", lambda d: struct.pack_into("<f", d, 0x1580 + 0x30, 2.)),
            ("mapping", lambda d: struct.pack_into("<H", d, 0x1702, 99)),
            ("billboard", lambda d: struct.pack_into("<I", d, 0x1580 + 0x2c, 0x11000)),
            ("nonfinite", lambda d: struct.pack_into("<f", d, 0x1580 + 0x4c, float("nan"))),
            ("index", lambda d: d.__setitem__(0x2200 + 24 + 20, 5)),
        ):
            data = bytearray(rigid_model())
            patch(data)
            with self.subTest(label=label):
                meshes, report = bfres_v10.parse(bytes(data))
                self.assertEqual([m.faces for m in meshes], [[(0, 1, 2)], [(1, 2, 3)]])
                self.assertTrue(any("non replacée(s)" in w for w in report.warnings))
                # Pour un indice invalide seule la forme B échoue ; les autres
                # variantes invalides empêchent tout placement de forme rigide.
                if label != "index":
                    self.assert_vec(meshes[0].positions[0], (0., 0., .125))
                self.assert_vec(meshes[1].positions[1], (1., 2., .125))

    def test_quaternion_rotation_and_invalid_inverse_matrix(self):
        from hookymod.bfres_v10_skeleton import SkeletonError, read

        data = bytearray(rigid_model())
        # Même transformation de l'enfant, enregistrée comme quaternion.
        struct.pack_into("<I", data, 0x1504, 0)
        for index in range(2):
            struct.pack_into("<I", data, 0x1580 + 0x58 * index + 0x2c, 0)
        struct.pack_into("<4f", data, 0x1580 + 0x58 + 0x3c,
                         0., 0., math.sin(math.pi / 4), math.cos(math.pi / 4))
        meshes, report = bfres_v10.parse(bytes(data))
        self.assert_vec(meshes[0].positions[1], (5., 3., .125))
        self.assertTrue(any("replacée(s)" in w for w in report.warnings))

        # La matrice inverse de la racine doit valider sa pose, même si une
        # translation gigantesque rendrait un seuil relatif global trop lâche.
        struct.pack_into("<HH", data, 0x153a, 1, 1)
        struct.pack_into("<Q", data, 0x1520, 0x1740)
        struct.pack_into("<hh", data, 0x1580 + 0x24, 0, -1)
        struct.pack_into("<12f", data, 0x1740, 1., 0., 0., -5., 0., 1., 0., 0., 0., 0., 1., 0.)
        self.assertEqual(len(read(bytes(data), 0x1500).matrices), 2)
        struct.pack_into("<f", data, 0x1740, 0.)
        struct.pack_into("<f", data, 0x174c, 1e6)
        with self.assertRaisesRegex(SkeletonError, "incompatibles"):
            read(bytes(data), 0x1500)

    def test_shape_without_skeleton_warns_instead_of_assuming_identity(self):
        data = bytearray(rigid_model())
        struct.pack_into("<Q", data, 0x218, 0)
        meshes, report = bfres_v10.parse(bytes(data))
        self.assert_vec(meshes[1].positions[1], (1., 2., .125))
        self.assertTrue(any("squelette absent" in w for w in report.warnings))


if __name__ == "__main__":
    unittest.main()
