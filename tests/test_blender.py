"""Chaîne « modèle → Blender » : glTF 2.0, OBJ+MTL, textures branchées.

Ces tests n'ont besoin d'aucun asset du jeu : ils fabriquent de petits
maillages et vérifient ce que Blender recevra réellement (structure du glTF,
tableaux d'indICES, UV retournées, matériaux, absence de fausse géométrie).
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hookymod import blender  # noqa: E402
from hookymod.mesh import MeshData, triangulated  # noqa: E402
from hookymod.util import HookyError  # noqa: E402


def _quad(name: str = "Aile", material: str = "Mt_Aile") -> MeshData:
    return MeshData(
        name=name,
        positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0)],
        faces=[(0, 1, 2), (1, 3, 2)],
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)],
        normals=[(0.0, 0.0, 1.0)] * 4,
        material=material,
    )


class TriangulationTests(unittest.TestCase):
    def test_quad_fan(self):
        mesh = MeshData(positions=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
                        faces=[(0, 1, 2, 3)])
        self.assertEqual(triangulated(mesh), [(0, 1, 2), (0, 2, 3)])

    def test_out_of_range_indices_are_dropped(self):
        mesh = MeshData(positions=[(0, 0, 0), (1, 0, 0)], faces=[(0, 1, 7), (0, 1)])
        self.assertEqual(triangulated(mesh), [])

    def test_degenerate_face_is_dropped(self):
        mesh = MeshData(positions=[(0, 0, 0)] * 3, faces=[(0, 0, 1)])
        self.assertEqual(triangulated(mesh), [])


class GltfTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_gltf_is_valid_and_matches_the_mesh(self):
        written = blender.export_bundle([_quad()], self.folder, "papillon",
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        self.assertEqual(document["asset"]["version"], "2.0")
        self.assertEqual(len(document["meshes"]), 1)
        primitive = document["meshes"][0]["primitives"][0]
        self.assertIn("POSITION", primitive["attributes"])
        self.assertIn("NORMAL", primitive["attributes"])
        self.assertIn("TEXCOORD_0", primitive["attributes"])
        self.assertEqual(primitive["mode"], 4)                  # triangles
        accessors = document["accessors"]
        self.assertEqual(accessors[primitive["attributes"]["POSITION"]]["count"], 4)
        self.assertEqual(accessors[primitive["indices"]]["count"], 6)

    def test_binary_payload_size_matches_declaration(self):
        written = blender.export_bundle([_quad()], self.folder, "papillon",
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        binary = (self.folder / "papillon.bin").read_bytes()
        self.assertEqual(len(binary), document["buffers"][0]["byteLength"])
        self.assertEqual(len(binary) % 4, 0)

    def test_positions_and_bounds_are_consistent(self):
        written = blender.export_bundle([_quad()], self.folder, "papillon",
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        accessor = document["accessors"][0]
        self.assertEqual(accessor["min"], [0.0, 0.0, 0.0])
        self.assertEqual(accessor["max"], [1.0, 1.0, 0.0])
        view = document["bufferViews"][accessor["bufferView"]]
        binary = (self.folder / "papillon.bin").read_bytes()
        chunk = binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]
        first = struct.unpack("<3f", chunk[:12])
        self.assertEqual(first, (0.0, 0.0, 0.0))

    def test_uv_are_flipped_for_gltf(self):
        written = blender.export_bundle([_quad()], self.folder, "papillon",
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        index = document["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]
        view = document["bufferViews"][document["accessors"][index]["bufferView"]]
        binary = (self.folder / "papillon.bin").read_bytes()
        chunk = binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]
        uvs = struct.unpack("<8f", chunk)
        # (0,0) → (0,1) · (0,1) → (0,0) : glTF compte le V vers le haut
        self.assertEqual(uvs[0:2], (0.0, 1.0))
        self.assertEqual(uvs[4:6], (0.0, 0.0))
        self.assertEqual(uvs[6:8], (1.0, 0.0))

    def test_wide_mesh_uses_32_bit_indices(self):
        mesh = MeshData(positions=[(float(i), 0.0, 0.0) for i in range(70000)],
                        faces=[(69000, 69001, 69002)])
        written = blender.export_bundle([mesh], self.folder, "grand",
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        self.assertEqual(document["accessors"][-1]["componentType"], 5125)

    def test_texture_is_copied_and_linked(self):
        png = self.folder / "Mt_Aile_Alb.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        written = blender.export_bundle([_quad()], self.folder / "sortie",
                                        "papillon",
                                        textures={"Mt_Aile": str(png)},
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        self.assertEqual(document["images"][0]["uri"], "Mt_Aile_Alb.png")
        material = document["materials"][0]
        self.assertIn("baseColorTexture",
                      material["pbrMetallicRoughness"])
        self.assertTrue((self.folder / "sortie" / "Mt_Aile_Alb.png").is_file())

    def test_missing_texture_is_ignored_not_faked(self):
        written = blender.export_bundle([_quad()], self.folder, "papillon",
                                        textures={"Mt_Aile": "/nulle/part.png"},
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        self.assertNotIn("images", document)
        self.assertNotIn("baseColorTexture",
                         document["materials"][0]["pbrMetallicRoughness"])


class ObjTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_obj_and_mtl_are_written(self):
        written = blender.export_bundle([_quad()], self.folder, "papillon")
        obj = Path(written["obj"]).read_text().splitlines()
        mtl = (self.folder / "papillon.mtl").read_text()
        self.assertTrue(any(line.startswith("mtllib papillon.mtl") for line in obj))
        self.assertTrue(any(line.startswith("usemtl Mt_Aile") for line in obj))
        self.assertEqual(sum(1 for line in obj if line.startswith("v ")), 4)
        self.assertEqual(sum(1 for line in obj if line.startswith("f ")), 2)
        self.assertIn("newmtl Mt_Aile", mtl)

    def test_scale_is_applied(self):
        written = blender.export_bundle([_quad()], self.folder, "petit", scale=0.5)
        obj = Path(written["obj"]).read_text().splitlines()
        vertices = [line.rstrip() for line in obj if line.startswith("v ")]
        self.assertIn("v 0.5 0.5 0", vertices)

    def test_import_note_is_written(self):
        written = blender.export_bundle([_quad()], self.folder, "papillon")
        note = Path(written["note"]).read_text()
        self.assertIn("papillon.gltf", note)
        self.assertIn("Blender", note)


class RefusalTests(unittest.TestCase):
    """Rien ne doit partir vers Blender si la géométrie est vide."""

    def test_empty_meshes_raise(self):
        with self.assertRaises(HookyError):
            blender.export_bundle([MeshData(name="vide")], Path(tempfile.mkdtemp()))

    def test_mesh_without_faces_raises(self):
        mesh = MeshData(name="sans face", positions=[(0, 0, 0), (1, 0, 0)])
        with self.assertRaises(HookyError):
            blender.export_bundle([mesh], Path(tempfile.mkdtemp()))

    def test_mismatched_uv_are_dropped(self):
        mesh = _quad()
        mesh.uvs = [(0.0, 0.0)]
        written = blender.export_bundle([mesh], Path(tempfile.mkdtemp()), "papillon",
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        primitive = document["meshes"][0]["primitives"][0]
        self.assertNotIn("TEXCOORD_0", primitive["attributes"])


class CliTests(unittest.TestCase):
    def test_blender_command_on_obj(self):
        import contextlib
        import io
        from hookymod import cli
        folder = Path(tempfile.mkdtemp())
        source = folder / "aile.obj"
        source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\n"
                          "f 1/1 2/2 3/3\n", encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["blender", str(source), "-o", str(folder / "sortie")])
        self.assertEqual(code, 0)
        self.assertTrue((folder / "sortie" / "aile.gltf").is_file())
        self.assertTrue((folder / "sortie" / "aile.obj").is_file())
        self.assertIn("Blender", buffer.getvalue())


class DictionaryDiscoveryTests(unittest.TestCase):
    def test_dictionary_next_to_the_file_is_found(self):
        from hookymod import zs
        folder = Path(tempfile.mkdtemp())
        (folder / "ZsDic.pack.zs").write_bytes(b"peu importe")
        deep = folder / "Tex"
        deep.mkdir()
        asset = deep / "Mt_Body_Animal_Butterfly_Alb.bntx.zs"
        asset.touch()
        found = zs.find_dictionaries_near(asset)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "ZsDic.pack.zs")

    def test_no_dictionary_anywhere(self):
        from hookymod import zs
        folder = Path(tempfile.mkdtemp()) / "a" / "b"
        folder.mkdir(parents=True)
        asset = folder / "x.bntx.zs"
        asset.touch()
        self.assertEqual(zs.find_dictionaries_near(asset), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
