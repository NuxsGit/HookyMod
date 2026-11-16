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

    def test_invalid_faces_are_refused_not_dropped(self):
        for face in ((0, 1, 7), (0, 1), (-1, 0, 1), (0, 1, 1.0)):
            with self.subTest(face=face), self.assertRaisesRegex(HookyError, "sans retirer de faces"):
                triangulated(MeshData(positions=[(0, 0, 0), (1, 0, 0)], faces=[face]))

    def test_degenerate_face_is_retained(self):
        mesh = MeshData(positions=[(0, 0, 0)] * 3, faces=[(0, 0, 1)])
        self.assertEqual(triangulated(mesh), [(0, 0, 1)])


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

    def test_uv_are_written_as_is_for_gltf(self):
        """glTF place (0,0) en **haut** à gauche : les UV du jeu s'écrivent tel quel.

        Les retourner ici donnait une texture à l'envers dans Blender (l'importeur
        glTF de Blender applique déjà sa propre conversion).
        """
        written = blender.export_bundle([_quad()], self.folder, "papillon",
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        index = document["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]
        view = document["bufferViews"][document["accessors"][index]["bufferView"]]
        binary = (self.folder / "papillon.bin").read_bytes()
        chunk = binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]
        uvs = struct.unpack("<8f", chunk)
        self.assertEqual(uvs[0:2], (0.0, 0.0))
        self.assertEqual(uvs[4:6], (0.0, 1.0))
        self.assertEqual(uvs[6:8], (1.0, 1.0))

    def test_wide_mesh_uses_32_bit_indices(self):
        mesh = MeshData(positions=[(float(i), 0.0, 0.0) for i in range(70000)],
                        faces=[(69000, 69001, 69002)])
        written = blender.export_bundle([mesh], self.folder, "grand",
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        self.assertEqual(document["accessors"][-1]["componentType"], 5125)

    def test_reserved_uint16_restart_index_is_promoted(self):
        mesh = MeshData(positions=[(0.0, 0.0, 0.0)] * 65536, faces=[(0, 1, 65535)])
        written = blender.export_bundle([mesh], self.folder, "limite", formats=("gltf",))
        document = json.loads(written["gltf"].read_text())
        primitive = document["meshes"][0]["primitives"][0]
        accessor = document["accessors"][primitive["indices"]]
        self.assertEqual(accessor["componentType"], 5125)
        offset = document["bufferViews"][accessor["bufferView"]]["byteOffset"]
        self.assertEqual(struct.unpack_from("<3I", written["bin"].read_bytes(), offset), (0, 1, 65535))

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

    def test_obj_and_gltf_preserve_all_triangles_and_split_polygons_identically(self):
        first, second = _quad("first"), _quad("second")
        first.faces = [(0, 1, 2), (0, 0, 1), (0, 1, 2, 3)]
        second.faces = [(3, 2, 1)]
        written = blender.export_bundle([first, second], self.folder, "faces")
        obj_faces = [tuple(int(vertex.split("/")[0]) - 1 for vertex in line.split()[1:])
                     for line in written["obj"].read_text().splitlines() if line.startswith("f ")]
        self.assertEqual(obj_faces, [(0, 1, 2), (0, 0, 1), (0, 1, 2), (0, 2, 3), (7, 6, 5)])
        document = json.loads(written["gltf"].read_text())
        binary = written["bin"].read_bytes()
        gltf_faces = []
        for gl_mesh in document["meshes"]:
            accessor = document["accessors"][gl_mesh["primitives"][0]["indices"]]
            offset = document["bufferViews"][accessor["bufferView"]]["byteOffset"]
            indices = struct.unpack_from(f"<{accessor['count']}H", binary, offset)
            gltf_faces.append([tuple(indices[i:i + 3]) for i in range(0, len(indices), 3)])
        self.assertEqual(gltf_faces, [[(0, 1, 2), (0, 0, 1), (0, 1, 2), (0, 2, 3)], [(3, 2, 1)]])
        self.assertIn("triangles dégénérés", written["note"].read_text())

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

    def test_invalid_face_refuses_each_format_without_writing_geometry(self):
        for formats in (("gltf",), ("obj",), ("gltf", "obj")):
            for face in ((0, 1, 9), (0, 1), (-1, 0, 1)):
                with self.subTest(formats=formats, face=face), tempfile.TemporaryDirectory() as tmp:
                    invalid = _quad("invalide")
                    invalid.faces = [face]
                    with self.assertRaisesRegex(HookyError, "sans retirer de faces"):
                        blender.export_bundle([_quad(), invalid], Path(tmp), "test", formats=formats)
                    self.assertFalse((Path(tmp) / "test.gltf").exists())
                    self.assertFalse((Path(tmp) / "test.bin").exists())
                    self.assertFalse((Path(tmp) / "test.obj").exists())

    def test_faces_without_positions_are_not_silently_dropped(self):
        bad = MeshData(name="sans sommets", faces=[(0, 1, 2)])
        with self.assertRaisesRegex(HookyError, "sans sommets"):
            blender.export_bundle([_quad(), bad], Path(tempfile.mkdtemp()))

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

    def test_cli_obj_export_preserves_seams_and_does_not_invent_normals(self):
        import contextlib
        import io
        from hookymod import cli, modelio

        source_bytes = (b"mtllib absent.mtl\no Plan\nusemtl Surface\n"
                        b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\n"
                        b"vt 0 0\nvt 1 0\nvt 0 1\nvt 1 1\nvt .5 .5\n"
                        b"f 1/1 2/2 3/3\nf 2/5 4/4 3/3\n")
        expected = modelio.read_model("forme.obj", source_bytes).meshes[0]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, output = folder / "forme.obj", folder / "sortie"
            source.write_bytes(source_bytes)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["export-model", str(source), "-o", str(output),
                                           "--no-textures"]), 0)
            report = (output / "RAPPORT_MODELE.txt").read_text(encoding="utf-8")
            self.assertIn("MTL et textures source non importées", report)
            gltf = json.loads((output / "forme.gltf").read_text(encoding="utf-8"))
            self.assertNotIn("NORMAL", gltf["meshes"][0]["primitives"][0]["attributes"])
            decoded = (modelio.read_model("forme.gltf", (output / "forme.gltf").read_bytes(),
                                          {"forme.bin": (output / "forme.bin").read_bytes()}),
                       modelio.read_model("forme.obj", (output / "forme.obj").read_bytes()))
            for result in decoded:
                mesh = result.meshes[0]
                self.assertEqual(mesh.positions, expected.positions)
                self.assertEqual(mesh.uvs, expected.uvs)
                self.assertEqual(mesh.faces, expected.faces)
                self.assertEqual(mesh.material, "Surface")
                self.assertIsNone(mesh.normals)

    def test_cli_obj_export_refuses_invalid_and_oversized_input(self):
        import contextlib
        import io
        from unittest.mock import patch
        from hookymod import cli, modelio

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, output = folder / "forme.obj", folder / "sortie"
            source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 4\n")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(cli.main(["export-model", str(source), "-o", str(output)]), 0)
            self.assertFalse(output.exists())
            source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
            with patch.object(modelio, "MAX_BYTES", 16), contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(cli.main(["export-model", str(source), "-o", str(output)]), 0)
            self.assertFalse(output.exists())

    def test_cli_does_not_bind_normal_or_ambiguous_images_as_albedo(self):
        import contextlib
        import io
        from hookymod import cli, util

        cases = ((["forme_Nrm.png"], None, "Aucun albédo identifié"),
                 (["forme_Weird.png"], None, "Aucun albédo identifié"),
                 (["forme_Alb.png", "forme_Nrm.png"], "forme_Alb.png", "heuristique"),
                 (["forme_Alb.png", "forme_BaseColor.png"], None, "Plusieurs matériaux ou albédos"),
                 (["forme_#_Alb.png"], "forme___Alb.png", "heuristique"))
        for names, expected, message in cases:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, output = root / "forme.obj", root / "sortie"
                source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl Surface\nf 1 2 3\n")
                for index, name in enumerate(names):
                    (root / name).write_bytes(util.png_bytes(1, 1, bytes((index, 0, 0, 255))))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["export-model", str(source), "-o", str(output)]), 0)
                doc = json.loads((output / "forme.gltf").read_text(encoding="utf-8"))
                pbr = doc["materials"][0]["pbrMetallicRoughness"]
                if expected is None:
                    self.assertNotIn("baseColorTexture", pbr)
                    self.assertNotIn("map_Kd", (output / "forme.mtl").read_text(encoding="utf-8"))
                else:
                    texture = doc["textures"][pbr["baseColorTexture"]["index"]]
                    self.assertEqual(doc["images"][texture["source"]]["uri"], expected)
                    self.assertIn(f"map_Kd {expected}", (output / "forme.mtl").read_text(encoding="utf-8"))
                    self.assertTrue((output / expected).is_file())
                report = (output / "RAPPORT_MODELE.txt").read_text(encoding="utf-8")
                self.assertIn(message, report)

    def test_cli_ignores_ambiguous_filenames_and_multi_image_containers(self):
        import contextlib
        import io
        from hookymod import cli, util
        from tests.test_bulk import _multi_texture_bntx

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "forme.obj", root / "sortie"
            source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl Surface\nf 1 2 3\n")
            png = util.png_bytes(1, 1, bytes((2, 3, 4, 255)))
            (root / "forme_Alb.png").write_bytes(png)
            (root / "forme_Alb.bntx").write_bytes(_multi_texture_bntx(["une", "deux"]))
            (root / "forme_Dif.bntx").write_bytes(_multi_texture_bntx(["une", "deux"]))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["export-model", str(source), "-o", str(output)]), 0)
            doc = json.loads((output / "forme.gltf").read_text(encoding="utf-8"))
            self.assertNotIn("baseColorTexture", doc["materials"][0]["pbrMetallicRoughness"])
            report = (output / "RAPPORT_MODELE.txt").read_text(encoding="utf-8")
            self.assertIn("candidates ignorées", report)
            self.assertIn("sans image unique", report)
            self.assertEqual((root / "forme_Alb.png").read_bytes(), png)
            self.assertFalse((output / "forme_Alb.png").exists())
            self.assertFalse((output / "forme_Dif.png").exists())

    def test_cli_preserves_obj_source_and_does_not_follow_png_output_links(self):
        import contextlib
        import io
        from hookymod import cli, util

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "forme.obj"
            original = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
            source.write_bytes(original)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(cli.main(["export-model", str(source), "-o", str(root)]), 0)
            self.assertEqual(source.read_bytes(), original)
            png = util.png_bytes(1, 1, bytes((8, 9, 10, 255)))
            (root / "forme_Alb.png").write_bytes(png)
            output = root / "sortie"
            output.mkdir()
            victim = root / "important.txt"
            victim.write_bytes(b"ne pas modifier")
            link = output / "forme_Alb.png"
            try:
                link.symlink_to(victim)
            except (OSError, NotImplementedError):
                self.skipTest("liens symboliques indisponibles")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["export-model", str(source), "-o", str(output)]), 0)
            self.assertEqual(victim.read_bytes(), b"ne pas modifier")
            self.assertFalse(link.is_symlink())
            self.assertEqual(link.read_bytes(), png)

    def test_cli_single_format_and_invalid_options(self):
        import contextlib
        import io
        from hookymod import cli
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "forme.obj"
            source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
            for fmt in ("gltf", "obj"):
                output = folder / fmt
                with self.subTest(fmt=fmt), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["export-model", str(source), "-o", str(output),
                                               "--formats", fmt, "--no-textures"]), 0)
                    self.assertTrue((output / f"forme.{fmt}").is_file())
                    report = (output / "RAPPORT_MODELE.txt").read_text(encoding="utf-8")
                    self.assertIn("textures désactivé", report)
            for options in (("--formats", "unsupported"), ("--scale", "nan"), ("--model", "-1")):
                output = folder / ("bad-" + options[0][2:])
                with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                    self.assertNotEqual(cli.main(["export-model", str(source), "-o", str(output), *options]), 0)
                    self.assertFalse(output.exists())


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
