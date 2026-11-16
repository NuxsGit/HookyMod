"""Géométrie complète et contrats de l'API de la vue 3D, sans données utilisateur."""
from __future__ import annotations

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hookycreator import model_view
from hookycreator.project import ProjectError
from hookycreator.server import HookyCreatorServer
from hookymod.mesh import MeshData
from hookymod.util import HookyError
from tests import synth
from tests.test_bfres_v10_struct import structured_model


def read_blob(blob):
    assert blob[:4] == b"H3D1"
    header_size, = struct.unpack_from("<I", blob, 4)
    header = json.loads(blob[8:8 + header_size])
    cursor = (8 + header_size + 3) & ~3
    parts = []
    for entry in header["meshes"]:
        n, f = entry["vertices"], entry["triangles"]
        def take(fmt, count):
            nonlocal cursor
            values = struct.unpack_from(f"<{count}{fmt}", blob, cursor)
            cursor += count * 4
            return values
        positions = take("f", n * 3)
        normals = take("f", n * 3) if entry["normals"] else None
        uvs = take("f", n * 2) if entry["uvs"] else None
        indices = take("I", f * 3)
        parts.append((positions, normals, uvs, indices))
    assert cursor == len(blob)
    return header, parts


def mesh():
    return MeshData(name="A", material="Mat_A",
                    positions=[(0., 0., 0.), (2., 0., 0.), (0., 1., 0.)],
                    normals=[(0., 0., 1.)] * 3,
                    uvs=[(0., 0.), (1., 0.), (0., 1.)], faces=[(0, 1, 2)])


class GeometryEncodingTests(unittest.TestCase):
    def test_complete_buffers_and_per_material_binding(self):
        a = mesh()
        b = MeshData(name="B", positions=[(0., 0., 1.), (1., 0., 1.), (0., 1., 1.)],
                     faces=[(2, 1, 0)], material="Mat_B")
        blob = model_view.encode([a, b], {"Mat_A": {"path": "Tex/é.bntx", "index": 1},
                                          "Mat_B": {"path": "Tex/b.bntx", "index": 0}})
        header, parts = read_blob(blob)
        self.assertEqual(header["version"], 1)
        self.assertEqual(header["meshes"][0]["texture"], {"path": "Tex/é.bntx", "index": 1})
        self.assertIsNone(header["meshes"][1]["texture"], "pas de texture sans UV")
        self.assertEqual(parts[0][0], (0., 0., 0., 2., 0., 0., 0., 1., 0.))
        self.assertEqual(parts[0][2], (0., 0., 1., 0., 0., 1.))
        self.assertEqual(parts[0][3], (0, 1, 2))
        self.assertEqual(parts[1][3], (2, 1, 0))
        self.assertEqual(sum(e["triangles"] for e in header["meshes"]), 2)

    def test_invalid_data_and_limits_refuse_instead_of_dropping_triangles(self):
        cases = []
        invalid = mesh(); invalid.faces = [(0, 1, 3)]; cases.append(invalid)
        invalid = mesh(); invalid.faces = [(0, -1, 2)]; cases.append(invalid)
        invalid = mesh(); invalid.faces = [(0, 1)]; cases.append(invalid)
        invalid = mesh(); invalid.normals = [(0., 0., 1.)]; cases.append(invalid)
        invalid = mesh(); invalid.uvs[0] = (math.nan, 0.); cases.append(invalid)
        invalid = mesh(); invalid.positions[0] = (math.inf, 0., 0.); cases.append(invalid)
        invalid = mesh(); invalid.positions[0] = (1e300, 0., 0.); cases.append(invalid)
        for value in cases:
            with self.subTest(mesh=value), self.assertRaises(HookyError):
                model_view.encode([value])
        with patch.object(model_view, "MAX_TRIANGLES", 0), self.assertRaisesRegex(HookyError, "Aucune face"):
            model_view.encode([mesh()])
        with patch.object(model_view, "MAX_BYTES", 16), self.assertRaisesRegex(HookyError, "Aucune face"):
            model_view.encode([mesh()])
        with patch.object(model_view, "MAX_MESHES", 0), self.assertRaises(HookyError):
            model_view.encode([mesh()])


class GeometryApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "source"
        (root / "Model").mkdir(parents=True)
        (root / "Tex").mkdir()
        (root / "Model/Objet.bfres").write_bytes(structured_model())
        for name, color in (("Tex_A_Alb", (255, 0, 0, 255)), ("Tex_B_Alb", (0, 255, 0, 255))):
            (root / "Tex" / f"{name}.bntx").write_bytes(synth.make_bntx(4, 4, bytes(color) * 16))
        self.server = HookyCreatorServer(root, Path(self.tmp.name) / "work", host="127.0.0.1", port=0)
        self.addCleanup(self.server.stop)

    def test_route_serves_all_faces_and_resolved_albedos(self):
        blob, content_type = self.server.api("GET", "/api/model/geometry", {"path": ["Model/Objet.bfres"]}, {})
        self.assertEqual(content_type, "application/octet-stream")
        header, parts = read_blob(blob)
        self.assertEqual([item["triangles"] for item in header["meshes"]], [1, 1])
        self.assertEqual([item["texture"] for item in header["meshes"]], [
            {"path": "Tex/Tex_A_Alb.bntx", "index": 0},
            {"path": "Tex/Tex_B_Alb.bntx", "index": 0}])
        self.assertEqual([part[3] for part in parts], [(0, 1, 2), (1, 2, 3)])

    def test_ambiguous_textures_are_not_guessed_and_bad_paths_are_rejected(self):
        root = self.server.project.root
        (root / "Autre").mkdir()
        (root / "Autre/Tex_A_Alb.bntx").write_bytes((root / "Tex/Tex_A_Alb.bntx").read_bytes())
        self.server.project.scan()
        blob, _ = self.server.api("GET", "/api/model/geometry", {"path": ["Model/Objet.bfres"]}, {})
        header, _ = read_blob(blob)
        self.assertIsNone(header["meshes"][0]["texture"])
        self.assertEqual(header["meshes"][1]["texture"]["path"], "Tex/Tex_B_Alb.bntx")
        with self.assertRaises(ProjectError):
            self.server.api("GET", "/api/model/geometry", {"path": ["../secret"]}, {})
        with self.assertRaises(ProjectError):
            self.server.api("GET", "/api/model/geometry", {}, {})


if __name__ == "__main__":
    unittest.main()
