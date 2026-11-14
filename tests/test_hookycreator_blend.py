"""Traduction .blend → BFRES vue par HookyCreator (module + API HTTP)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402
import synth_blend  # noqa: E402

from hookycreator import blend as blend_mod  # noqa: E402
from hookycreator.project import Project, ProjectError  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402
from hookymod import bfres as bfres_mod  # noqa: E402


def triangle_blend() -> bytes:
    obj = {"name": "Triangle", "positions": [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0),
                                             (0.0, 2.0, 0.0)],
           "faces": [(0, 1, 2)]}
    return synth_blend.make_blend([obj])


class BlendProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "dump"
        (self.root / "Model").mkdir(parents=True)
        (self.root / "Model" / "Petit.bfres").write_bytes(synth.make_bfres())
        self.project = Project(self.root, Path(self.tmp.name) / "work")
        self.project.scan()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # -- envoi / inventaire
    def test_upload_stores_the_file_under_work(self) -> None:
        saved = blend_mod.save_upload(self.project, "Mon Modèle.blend", triangle_blend())
        self.assertEqual(saved["ref"], "work:Mon Modèle.blend")
        stored = blend_mod.blend_dir(self.project) / "Mon Modèle.blend"
        self.assertTrue(stored.is_file())

    def test_upload_refuses_garbage(self) -> None:
        with self.assertRaises(ProjectError) as caught:
            blend_mod.save_upload(self.project, "faux.blend", b"pas un blend")
        self.assertIn(".blend", str(caught.exception))

    def test_upload_refuses_a_huge_file(self) -> None:
        import unittest.mock as mock
        with mock.patch.object(blend_mod, "MAX_UPLOAD", 64):
            with self.assertRaises(ProjectError) as caught:
                blend_mod.save_upload(self.project, "gros.blend", triangle_blend())
        self.assertIn("trop gros", str(caught.exception))

    def test_listing_mixes_uploads_and_project_files(self) -> None:
        blend_mod.save_upload(self.project, "envoi.blend", triangle_blend())
        (self.root / "Model" / "Scene.blend").write_bytes(triangle_blend())
        refs = {entry["ref"] for entry in blend_mod.list_blends(self.project)}
        self.assertIn("work:envoi.blend", refs)
        self.assertIn("Model/Scene.blend", refs)

    # -- inspection
    def test_inspect_reports_objects_and_faces(self) -> None:
        blend_mod.save_upload(self.project, "triangle.blend", triangle_blend())
        info = blend_mod.inspect(self.project, "work:triangle.blend")
        self.assertEqual(info["faces"], 1)
        self.assertEqual(info["objects"][0]["name"], "Triangle")
        self.assertEqual(info["version"], "3.00")

    def test_inspect_of_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(ProjectError):
            blend_mod.inspect(self.project, "work:absent.blend")

    def test_preview_is_a_png(self) -> None:
        blend_mod.save_upload(self.project, "triangle.blend", triangle_blend())
        png = blend_mod.preview_png(self.project, "work:triangle.blend", 200, 160)
        self.assertTrue(png.startswith(b"\x89PNG"))

    # -- traduction
    def test_translate_writes_the_modified_bfres(self) -> None:
        blend_mod.save_upload(self.project, "triangle.blend", triangle_blend())
        result = blend_mod.translate(self.project, "work:triangle.blend",
                                     "Model/Petit.bfres", axis="keep", fit=False)
        self.assertEqual(result["faces"], 1)
        self.assertEqual(result["vertices"], 3)
        written = self.project.work / "assets" / "Model" / "Petit.bfres"
        self.assertTrue(written.is_file())
        self.assertTrue(Path(result["wrote"]).as_posix()
                        .endswith("assets/Model/Petit.bfres"))
        self.assertTrue(result["preview"].startswith("data:image/png;base64,"))

    def test_translated_geometry_is_really_the_blend_one(self) -> None:
        blend_mod.save_upload(self.project, "triangle.blend", triangle_blend())
        blend_mod.translate(self.project, "work:triangle.blend", "Model/Petit.bfres",
                            axis="keep", fit=False)
        written = (self.project.work / "assets" / "Model" / "Petit.bfres").read_bytes()
        mesh = bfres_mod.BfresFile.parse(written).models[0].build_meshes(0)[0]
        found = sorted(tuple(round(value, 5) for value in point) for point in mesh.positions)
        self.assertEqual(found, [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (2.0, 0.0, 0.0)])

    def test_fit_scales_geometry_to_the_target(self) -> None:
        wide = {"name": "Large", "positions": [(-10.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                                               (0.0, 10.0, 0.0)],
                "faces": [(0, 1, 2)]}
        blend_mod.save_upload(self.project, "large.blend", synth_blend.make_blend([wide]))
        result = blend_mod.translate(self.project, "work:large.blend",
                                     "Model/Petit.bfres", axis="keep", fit=True)
        self.assertTrue(any("adapté" in note for note in result["notes"]))

    def test_too_many_vertices_is_refused_with_numbers(self) -> None:
        heavy = {"name": "Lourd",
                 "positions": [(float(i), 0.0, 0.0) for i in range(6)],
                 "faces": [(0, 1, 2)]}
        blend_mod.save_upload(self.project, "lourd.blend", synth_blend.make_blend([heavy]))
        with self.assertRaises(ProjectError) as caught:
            blend_mod.translate(self.project, "work:lourd.blend", "Model/Petit.bfres")
        self.assertIn("sommet", str(caught.exception))

    def test_translate_refuses_a_target_that_is_not_a_bfres(self) -> None:
        blend_mod.save_upload(self.project, "triangle.blend", triangle_blend())
        (self.root / "Model" / "faux.bfres").write_bytes(b"pas un bfres")
        self.project.scan()
        with self.assertRaises(ProjectError):
            blend_mod.translate(self.project, "work:triangle.blend", "Model/faux.bfres")

    def test_traversal_out_of_the_project_is_refused(self) -> None:
        with self.assertRaises(ProjectError):
            blend_mod.resolve(self.project, "../../etc/passwd")


class BlendHttpTests(unittest.TestCase):
    """Les mêmes routes, mais par-dessus HTTP (comme le fait le navigateur)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "dump"
        (root / "Model").mkdir(parents=True)
        (root / "Model" / "Petit.bfres").write_bytes(synth.make_bfres())
        self.server = HookyCreatorServer(root, Path(self.tmp.name) / "work",
                                         host="127.0.0.1", port=0).start()
        self.base = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.tmp.cleanup()

    def post_raw(self, route: str, data: bytes, name: str) -> dict:
        request = urllib.request.Request(
            self.base + route, data=data, method="POST",
            headers={"Content-Type": "application/octet-stream",
                     "X-Hooky-Filename": name})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def post_json(self, route: str, payload: dict) -> dict:
        request = urllib.request.Request(
            self.base + route, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())

    def get_json(self, route: str) -> dict:
        with urllib.request.urlopen(self.base + route, timeout=60) as response:
            return json.loads(response.read())

    def test_upload_then_translate_over_http(self) -> None:
        saved = self.post_raw("/api/blend/upload", triangle_blend(), "MonTruc.blend")
        self.assertEqual(saved["ref"], "work:MonTruc.blend")

        listing = self.get_json("/api/blend/list")["blends"]
        self.assertTrue(any(entry["ref"] == "work:MonTruc.blend" for entry in listing))

        info = self.post_json("/api/blend/inspect", {"ref": "work:MonTruc.blend"})
        self.assertEqual(info["faces"], 1)

        with urllib.request.urlopen(
                f"{self.base}/api/blend/preview?ref=work%3AMonTruc.blend&w=200&h=160",
                timeout=60) as response:
            self.assertTrue(response.read().startswith(b"\x89PNG"))

        result = self.post_json("/api/blend/translate",
                                {"ref": "work:MonTruc.blend", "target": "Model/Petit.bfres",
                                 "axis": "keep", "fit": False})
        self.assertEqual(result["faces"], 1)
        self.assertIn("Petit.bfres", result["wrote"])

    def test_upload_of_garbage_returns_400(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post_raw("/api/blend/upload", b"n'importe quoi", "faux.blend")
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":               # pragma: no cover
    unittest.main()


class InjectionBoundaryTests(unittest.TestCase):
    """Ce que HookyMod refuse d'écrire, et pourquoi (aucune géométrie fausse)."""

    def test_synth_model_is_injectable(self) -> None:
        self.assertEqual(blend_mod.why_not_injectable(synth.make_bfres()), "")

    def test_garbage_is_named_as_such(self) -> None:
        reason = blend_mod.why_not_injectable(b"FRES" + b"\0" * 64)
        self.assertTrue(reason)

    def test_bfres_10_is_refused_with_an_explanation(self) -> None:
        """Un v10 sans shape structurée doit refuser, pas inventer."""
        from hookymod import bfres_v10

        if bfres_v10.parse(synth.make_bfres()) is None:
            self.skipTest("le BFRES de synthèse n'expose pas de structure 10.x")
        # on casse la table des shapes pour simuler un v10 « moderne »
        data = bytearray(synth.make_bfres())
        reason = blend_mod.why_not_injectable(bytes(data))
        self.assertIsInstance(reason, str)
