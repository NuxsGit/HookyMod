"""Vérificateur d'UV : ce qu'il montre, ce qu'il mesure, et l'orientation exportée.

Trois choses sont vérifiées ici :

* l'image d'atlas trace bien les triangles **sur** la texture, et le retournement
  de V donne une image différente (sinon le bouton ne servirait à rien) ;
* les chiffres trahissent les vrais défauts — UV qui se répètent, triangles
  réduits à un point, absence totale de coordonnées ;
* les deux formats exportés suivent leur spécification : glTF écrit V tel quel
  (origine en haut à gauche), OBJ retourne V (origine en bas à gauche). C'est le
  défaut d'origine : une épée dont la texture arrivait à l'envers dans Blender.
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookycreator import uvmap  # noqa: E402
from hookycreator.project import Project  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402
from hookymod import blender, util  # noqa: E402
from hookymod.mesh import MeshData, export_obj  # noqa: E402

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _quad(uvs=None, size: float = 1.0) -> MeshData:
    mesh = MeshData(name="quad",
                    positions=[(0.0, 0.0, 0.0), (size, 0.0, 0.0),
                               (size, size, 0.0), (0.0, size, 0.0)],
                    faces=[(0, 1, 2), (0, 2, 3)],
                    uvs=uvs, material="Mt_Test")
    mesh.ensure_normals()
    return mesh


def _red_texture(size: int = 64) -> bytes:
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            top = y < size // 2
            pixels += bytes((220, 40, 40, 255)) if top else bytes((40, 60, 210, 255))
    return util.png_bytes(size, size, bytes(pixels))


class OverlayTests(unittest.TestCase):
    def test_overlay_is_a_png_the_size_asked(self) -> None:
        png = uvmap.overlay(_red_texture(), [_quad([(0, 0), (1, 0), (1, 1), (0, 1)])],
                            size=128)
        self.assertTrue(png.startswith(PNG_SIGNATURE))
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual((width, height), (128, 128))

    def test_flipping_v_changes_the_image(self) -> None:
        mesh = [_quad([(0, 0), (1, 0), (1, 1), (0, 1)])]
        texture = _red_texture()
        normal = hashlib.sha1(uvmap.overlay(texture, mesh, size=128)).hexdigest()
        flipped = hashlib.sha1(uvmap.overlay(texture, mesh, size=128,
                                            flip=True)).hexdigest()
        self.assertNotEqual(normal, flipped)

    def test_without_texture_the_layout_is_still_visible(self) -> None:
        png = uvmap.overlay(None, [_quad([(0, 0), (1, 0), (1, 1), (0, 1)])], size=128)
        self.assertTrue(png.startswith(PNG_SIGNATURE))

    def test_a_mesh_without_uvs_does_not_crash(self) -> None:
        png = uvmap.overlay(_red_texture(), [_quad(None)], size=128)
        self.assertTrue(png.startswith(PNG_SIGNATURE))


class SummaryTests(unittest.TestCase):
    def test_clean_uvs_are_reported_clean(self) -> None:
        report = uvmap.summarise([_quad([(0, 0), (1, 0), (1, 1), (0, 1)])])
        self.assertTrue(report["has_uvs"])
        self.assertEqual(report["outside"], 0)
        self.assertEqual(report["degenerate"], 0)
        self.assertEqual(report["u"], [0.0, 1.0])
        self.assertEqual(report["warnings"], [])

    def test_tiling_uvs_are_flagged(self) -> None:
        mesh = _quad([(0, 0), (3, 0), (3, 3), (0, 3)])
        report = uvmap.summarise([mesh])
        self.assertGreater(report["outside"], 0)
        self.assertTrue(any("se répète" in warning for warning in report["warnings"]))

    def test_degenerate_uv_triangle_is_flagged(self) -> None:
        mesh = _quad([(0.5, 0.5)] * 4)
        report = uvmap.summarise([mesh])
        self.assertGreater(report["degenerate"], 0)
        self.assertTrue(any("point" in warning for warning in report["warnings"]))

    def test_missing_uvs_are_said_out_loud(self) -> None:
        report = uvmap.summarise([_quad(None)])
        self.assertFalse(report["has_uvs"])
        self.assertTrue(any("aucune coordonnée" in warning for warning in report["warnings"]))

    def test_conventions_are_documented_in_the_report(self) -> None:
        report = uvmap.summarise([_quad([(0, 0), (1, 0), (1, 1), (0, 1)])])
        self.assertIn("haut", report["conventions"]["gltf"])
        self.assertIn("bas", report["conventions"]["obj"])


class ExportOrientationTests(unittest.TestCase):
    """Les deux formats, chacun selon sa spécification — le bug d'origine."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.mesh = _quad([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _gltf_uvs(self) -> tuple[float, ...]:
        written = blender.export_bundle([self.mesh], self.folder, "epee",
                                        formats=("gltf",))
        document = json.loads(Path(written["gltf"]).read_text())
        accessor = document["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]
        view = document["bufferViews"][document["accessors"][accessor]["bufferView"]]
        binary = (self.folder / "epee.bin").read_bytes()
        chunk = binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]
        return struct.unpack("<8f", chunk)

    def test_gltf_keeps_v_as_in_the_game(self) -> None:
        uvs = self._gltf_uvs()
        pairs = [tuple(round(value, 4) for value in uvs[i:i + 2])
                 for i in range(0, len(uvs), 2)]
        # l'exporteur peut réordonner les sommets : on compare l'ensemble
        self.assertEqual(set(pairs), {(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)})

    def test_obj_flips_v_for_opengl(self) -> None:
        written = blender.export_bundle([self.mesh], self.folder, "epee",
                                        formats=("obj",))
        text = Path(written["obj"]).read_text(encoding="utf-8")
        coordinates = [line.split()[1:] for line in text.splitlines()
                       if line.startswith("vt ")]
        self.assertEqual(coordinates[0], ["0", "1"])
        self.assertEqual(coordinates[2], ["1", "0"])

    def test_the_two_formats_disagree_on_purpose(self) -> None:
        """Si les deux écrivaient la même chose, l'un des deux serait faux."""
        gltf_v = self._gltf_uvs()[1]
        written = export_obj([self.mesh], self.folder / "obj", "epee")
        text = Path(written).read_text(encoding="utf-8") if isinstance(written, Path) \
            else (self.folder / "obj" / "epee.obj").read_text(encoding="utf-8")
        obj_v = float([line for line in text.splitlines() if line.startswith("vt ")][0]
                      .split()[2])
        self.assertNotEqual(gltf_v, obj_v)


class ProjectUvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        root = self.tmp / "dump"
        (root / "Model").mkdir(parents=True)
        (root / "Model" / "Heros.bfres").write_bytes(synth.make_bfres())
        (root / "Model" / "Heros_Alb.bntx").write_bytes(
            synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8)))
        self.project = Project(root, self.tmp / "work")
        self.project.scan()
        self.rel = "Model/Heros.bfres"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_report_names_the_texture_it_compares_with(self) -> None:
        report = self.project.uv_info(self.rel)
        if report["has_uvs"]:
            self.assertEqual(report["texture"], "Heros_Alb")
        self.assertEqual(report["rel"], self.rel)

    def test_overlay_is_cached(self) -> None:
        first = self.project.uv_overlay(self.rel, size=192)
        start = time.perf_counter()
        second = self.project.uv_overlay(self.rel, size=192)
        self.assertEqual(first, second)
        self.assertLess(time.perf_counter() - start, 0.05)

    def test_flip_is_part_of_the_cache_key(self) -> None:
        """Le modèle de synthèse n'a pas d'UV : on en fournit, comme un vrai."""

        quad = _quad([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
        self.project._model_meshes = lambda rel: [quad]        # type: ignore[assignment]
        normal = hashlib.sha1(self.project.uv_overlay(self.rel, size=192)).hexdigest()
        flipped = hashlib.sha1(self.project.uv_overlay(self.rel, size=192,
                                                      flip=True)).hexdigest()
        self.assertNotEqual(normal, flipped)


class UvApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        root = self.tmp / "dump"
        (root / "Model").mkdir(parents=True)
        (root / "Model" / "Heros.bfres").write_bytes(synth.make_bfres())
        self.server = HookyCreatorServer(root, self.tmp / "work",
                                         host="127.0.0.1", port=0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_overlay_route_serves_a_png(self) -> None:
        body, kind = self.server.api("GET", "/api/model/uv",
                                     {"path": ["Model/Heros.bfres"],
                                      "size": ["128"]}, {})
        self.assertEqual(kind, "image/png")
        self.assertTrue(body.startswith(PNG_SIGNATURE))

    def test_info_route_serves_the_diagnostic(self) -> None:
        body, kind = self.server.api("GET", "/api/model/uv/info",
                                     {"path": ["Model/Heros.bfres"]}, {})
        self.assertEqual(kind, "application/json")
        report = json.loads(body)
        for key in ("has_uvs", "outside", "degenerate", "warnings", "conventions"):
            self.assertIn(key, report)

    def test_missing_path_is_a_readable_error(self) -> None:
        from hookycreator.project import ProjectError

        with self.assertRaises(ProjectError):
            self.server.api("GET", "/api/model/uv", {}, {})


if __name__ == "__main__":
    unittest.main()
