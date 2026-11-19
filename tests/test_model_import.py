"""Injection limitée mais réelle d'OBJ/glTF, sans altérer les sources."""
from __future__ import annotations

import base64
import json
import struct
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from hookycreator import model_import
from hookycreator.project import Project, ProjectError
from hookycreator.server import HookyCreatorServer
from hookymod import bfres, bfres_import, bfres_safe
from hookymod.mesh import MeshData
from tests import synth
from tests.test_modelio import fixture, glb
from tests.test_bfres_v10 import DataDrivenTests, make_v10

OBJ = b"v 0 0 0\nv 2 0 0\nv 0 3 0\nf 1 2 3\n"


def payload(*files, target="Model/Original.bfres", **extra):
    return {"files": [{"name": name, "data": base64.b64encode(data).decode()} for name, data in files],
            "target": target, "fit": False, **extra}


class ModelImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source"
        (self.root / "Model").mkdir(parents=True)
        self.source = self.root / "Model" / "Original.bfres"
        original = bytearray(synth.make_bfres())
        # Fixture du layout historique exploité pour l'écriture ; le véritable
        # layout 10.x est toujours en lecture seule.
        struct.pack_into("<I", original, 8, 0x00090002)
        self.original = bytes(original)
        self.source.write_bytes(self.original)
        self.project = Project(self.root, Path(self.tmp.name) / "work")
        self.project.scan()

    def assert_written(self, expected, faces=((0, 1, 2),)):
        copy = self.project.work / "assets" / "Model" / "Original.bfres"
        self.assertEqual(self.source.read_bytes(), self.original)
        mesh = bfres.meshes_from_file(bfres.BfresFile.parse(copy.read_bytes()))[0]
        self.assertEqual(mesh.positions, expected)
        self.assertEqual(mesh.faces, list(faces))

    def test_inspect_is_read_only_and_warns_about_untriangulated_obj(self):
        answer = model_import.inspect_model(payload(("forme.obj", OBJ)))
        self.assertEqual((answer["vertices"], answer["faces"]), (3, 1))
        self.assertTrue(answer["can_import"])
        self.assertTrue(answer["preview"].startswith("data:image/png;base64,"))
        self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())
        obj = b"v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n"
        answer = model_import.inspect_model(payload(("quad.obj", obj)))
        self.assertFalse(answer["can_import"])
        self.assertTrue(any("Triangule" in note for note in answer["warnings"]))

    def test_own_obj_and_gltf_export_round_trip_without_invented_normals(self):
        exported = self.project.export_model("Model/Original.bfres", textures=False)
        folder = Path(exported["absolute_folder"])
        obj = (folder / "Original.obj").read_bytes()
        document = json.loads((folder / "Original.gltf").read_text())
        self.assertNotIn(b"\nvn ", obj)
        self.assertNotIn("NORMAL", document["meshes"][0]["primitives"][0]["attributes"])
        self.assertTrue(any("normales absentes" in warning.lower() for warning in exported["warnings"]))
        model_import.import_model(self.project, payload(("Original.obj", obj)))
        self.assert_written([(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)])
        model_import.import_model(self.project, payload(
            ("Original.gltf", (folder / "Original.gltf").read_bytes()),
            ("Original.bin", (folder / "Original.bin").read_bytes())))
        self.assert_written([(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)])

    def test_obj_actually_changes_only_the_working_copy(self):
        result = model_import.import_model(self.project, payload(("forme.obj", OBJ)))
        self.assertEqual(result["faces"], 1)
        self.assertTrue(any("textures de la cible" in note for note in result["notes"]))
        self.assert_written([(0., 0., 0.), (2., 0., 0.), (0., 3., 0.)])
        self.assertIn("Model/Original.bfres", self.project.edited_files())
        self.assertTrue((self.project.work / "assets/Model/Original.bfres").is_file())
        self.assertFalse((self.project.root / "mods").exists())

    def test_gltf_external_bin_and_glb_actually_change_geometry(self):
        doc, data = fixture()
        attrs = doc["meshes"][0]["primitives"][0]["attributes"]
        del attrs["NORMAL"]
        del attrs["TEXCOORD_0"]
        result = model_import.import_model(self.project, payload(
            ("forme.gltf", json.dumps(doc).encode()), ("geometrie.bin", data)))
        self.assertEqual(result["vertices"], 3)
        self.assert_written([(2., 3., 4.), (0., 3., 4.), (2., 6., 4.)], faces=((0, 2, 1),))
        model_import.import_model(self.project, payload(("forme.glb", glb(doc, data))))
        self.assert_written([(2., 3., 4.), (0., 3., 4.), (2., 6., 4.)], faces=((0, 2, 1),))

    def test_refuses_partial_unknown_or_unsafe_imports_without_a_working_copy(self):
        cases = [payload(("bad.obj", OBJ.replace(b"f 1 2 3", b"f 1 2 4"))),
                 payload(("../escape.obj", OBJ)),
                 payload(("file.obj", OBJ), target="../../outside.bfres"),
                 payload(("file.obj", OBJ), ("another.obj", OBJ)),
                 payload(("file.obj", OBJ + b"vt 0 0\n" + b"f 1/1 2/1 3/1\n")),
                 payload(("file.obj", OBJ + b"vn 0 0 1\n" + b"f 1//1 2//1 3//1\n")),
                 payload(("file.obj", OBJ), scale=float("nan")),
                 payload(("file.obj", OBJ), axis="arbitrary")]
        for request in cases:
            with self.subTest(request=str(request)[:80]), self.assertRaises(ProjectError):
                model_import.import_model(self.project, request)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())

    def test_source_normals_and_uvs_are_not_silently_discarded(self):
        with_normals = b"v 0 0 0\nv 2 0 0\nv 0 3 0\nvn 0 0 1\nf 1//1 2//1 3//1\n"
        with_uvs = b"v 0 0 0\nv 2 0 0\nv 0 3 0\nvt 0 0\nvt 1 0\nvt 0 1\nf 1/1 2/2 3/3\n"
        for data, reason in ((with_normals, "normales"), (with_uvs, "UV")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ProjectError, reason):
                model_import.import_model(self.project, payload(("source.obj", data)))
            self.assertEqual(self.source.read_bytes(), self.original)
            self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())

        # Les appelants internes ne peuvent pas contourner le refus des couleurs.
        colored = MeshData(positions=[(0., 0., 0.), (2., 0., 0.), (0., 3., 0.)],
                           faces=[(0, 1, 2)], colors=[(1., 0., 0.)] * 3)
        with self.assertRaisesRegex(bfres_safe.HookyError, "couleurs"):
            bfres_safe.rewrite(self.original, [colored])

    def test_extra_gltf_attributes_can_preview_but_not_inject(self):
        doc, data = fixture()
        attrs = doc["meshes"][0]["primitives"][0]["attributes"]
        del attrs["NORMAL"]
        del attrs["TEXCOORD_0"]
        # Un accessor VEC3 de couleurs valide, que la cible ne peut conserver.
        attrs["COLOR_0"] = attrs["POSITION"]
        request = payload(("source.gltf", json.dumps(doc).encode()), ("geometrie.bin", data))
        inspected = model_import.inspect_model(request)
        self.assertTrue(inspected["preview"])
        self.assertFalse(inspected["can_import"])
        self.assertTrue(any("COLOR_0" in note and "refusée" in note for note in inspected["warnings"]))
        with self.assertRaisesRegex(ProjectError, "Attributs source non conservables"):
            model_import.import_model(self.project, request)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())

    def test_zero_area_triangle_is_rejected_even_after_quantization(self):
        degenerate = (
            b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 1 2\n",  # indices répétés
            b"v 0 0 0\nv 1 0 0\nv 2 0 0\nf 1 2 3\n",  # points alignés
            b"v 0 0 0\nv 1 0 0\nvt 0 0\nvt 1 0\nvt 0 1\nf 1/1 1/2 2/3\n",  # couture
        )
        for obj in degenerate:
            with self.subTest(obj=obj), self.assertRaisesRegex(ProjectError, "dégénér"):
                model_import.import_model(self.project, payload(("source.obj", obj)))
        # Non nul en double précision, mais deux sommets se confondent en float32.
        collapsed = MeshData(positions=[(1., 0., 0.), (1. + 1e-8, 0., 0.), (1., 1e-8, 0.)],
                             faces=[(0, 1, 2)])
        with self.assertRaisesRegex(bfres_safe.HookyError, "quantification"):
            bfres_safe.rewrite(self.original, [collapsed])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())

    def test_empty_mesh_does_not_offset_following_triangles(self):
        empty = MeshData(name="sans faces", positions=[(99., 99., 99.)])
        triangle = MeshData(name="triangle", positions=[(0., 0., 0.), (2., 0., 0.), (0., 3., 0.)],
                            faces=[(0, 1, 2)])
        self.assertEqual(bfres_import._triangles([empty, triangle]), [(0, 1, 2)])
        data, _ = bfres_safe.rewrite(self.original, [empty, triangle])
        mesh = bfres.meshes_from_file(bfres.BfresFile.parse(data))[0]
        self.assertEqual(mesh.faces, [(0, 1, 2)])
        self.assertEqual(mesh.positions[:3], triangle.positions)
        # La réécriture ne doit pas ajouter des normales synthétiques à la
        # source en mémoire : elle doit rester réutilisable telle quelle.
        self.assertIsNone(triangle.normals)
        bfres_safe.rewrite(self.original, [triangle])
        self.assertIsNone(triangle.normals)

    def test_multiple_source_materials_are_rejected_during_inspection_and_import(self):
        obj = OBJ + b"usemtl Second\nf 1 2 3\n"
        request = payload(("materials.obj", obj))
        inspected = model_import.inspect_model(request)
        self.assertFalse(inspected["can_import"])
        self.assertTrue(any("matériaux" in note for note in inspected["warnings"]))
        with self.assertRaisesRegex(ProjectError, "matériaux"):
            model_import.import_model(self.project, request)
        self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())

    def test_duplicate_gltf_material_names_do_not_hide_distinct_slots(self):
        doc, data = fixture()
        del doc["meshes"][0]["primitives"][0]["attributes"]["NORMAL"]
        del doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]
        other = json.loads(json.dumps(doc["meshes"][0]["primitives"][0]))
        other["material"] = 1
        doc["meshes"][0]["primitives"].append(other)
        doc["materials"].append({"name": "Surface"})
        request = payload(("scene.gltf", json.dumps(doc).encode()), ("geometrie.bin", data))
        self.assertFalse(model_import.inspect_model(request)["can_import"])
        with self.assertRaisesRegex(ProjectError, "matériaux"):
            model_import.import_model(self.project, request)

    def test_heuristic_bfres10_is_refused_instead_of_claiming_success(self):
        data = DataDrivenTests()._grid()
        self.source.write_bytes(make_v10(*data))
        with self.assertRaisesRegex(ProjectError, "10.x"):
            model_import.import_model(self.project, payload(("file.obj", OBJ)))
        self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())

    def test_refuses_known_unwritten_target_structures_and_any_v10(self):
        for offset, fmt, value, reason in (
                (8, "I", 0x000A0002, "10.x"),
                (8, "I", 0x00080002, "9.x"),
                (0x1cc, "Q", 0x490, "Squelette"),
                (0x2b4, "Q", 0x490, "englobants"),
                (0x2bc, "Q", 0x490, "englobants"),
                (0x2d4, "H", 1, "Squelette"),
                (0x2d8, "B", 1, "morph")):
            target = bytearray(self.original)
            struct.pack_into("<" + fmt, target, offset, value)
            self.source.write_bytes(target)
            with self.subTest(offset=offset), self.assertRaisesRegex(ProjectError, reason):
                model_import.import_model(self.project, payload(("file.obj", OBJ)))
            self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())

    def test_quads_are_not_injected_as_an_unverified_triangle_fan(self):
        obj = b"v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n"
        with self.assertRaisesRegex(ProjectError, "triangule"):
            model_import.import_model(self.project, payload(("file.obj", obj)))
        self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())

    def test_http_import_refuses_concurrent_request_before_reading_body(self):
        server = HookyCreatorServer(self.root, self.project.work, host="127.0.0.1", port=0).start()
        self.addCleanup(server.stop)
        server.model_import_slot.acquire()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/model/import",
                                             data=b"{}", headers={"Content-Type": "application/json"},
                                             method="POST")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(error.exception.code, 429)
        finally:
            server.model_import_slot.release()
        self.assertFalse((self.project.work / "assets/Model/Original.bfres").exists())

    def test_http_export_then_import_cannot_write_the_original(self):
        server = HookyCreatorServer(self.root, self.project.work, host="127.0.0.1", port=0).start()
        self.addCleanup(server.stop)
        request = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/model/import",
                                         data=json.dumps(payload(("file.obj", OBJ))).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.load(response)
        self.assertEqual(answer["vertices"], 3)
        self.assert_written([(0., 0., 0.), (2., 0., 0.), (0., 3., 0.)])
        bad = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/model/import",
                                     data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(bad, timeout=10)
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
