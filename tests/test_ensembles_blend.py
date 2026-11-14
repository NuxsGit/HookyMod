"""Vue « Ensembles » (modèle + textures + animations) et export ``.blend``.

Deux promesses à tenir, donc deux séries de tests :

* **un objet vu en entier** — le modèle assemblé, coloré par son albédo, ses
  textures et ses animations retrouvées par leur nom, et une liste qui s'affiche
  sans décoder un seul fichier (elle doit rester instantanée sur un gros dump) ;
* **un ``.blend`` honnête** — soit Blender l'écrit, soit on fournit le script et
  la commande exacte, jamais un fichier vide ni un message trompeur.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookycreator.project import Project, _make_sampler  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402
from hookymod import blendfile  # noqa: E402
from hookymod.mesh import MeshData  # noqa: E402
from hookymod.viewer import build_render_data  # noqa: E402


def _blender_stub(test: unittest.TestCase, directory: Path, body: str) -> Path:
    """Lance un faux Blender Python avec les vrais arguments, même sous Windows.

    Une ligne ``#!`` exécutable ne fonctionne pas avec CreateProcess sur
    Windows. On intercepte uniquement le lancement, puis on démarre réellement
    l'interpréteur Python avec le script et les arguments construits par l'app.
    """
    stub = directory / "blender stub.py"
    stub.write_text(body + "\n", encoding="utf-8")
    real_run = subprocess.run

    def launch(command, *args, **kwargs):
        test.assertEqual(command[0], str(stub))
        test.assertEqual(command[1:4], ["--background", "--factory-startup", "--python"])
        test.assertEqual(command[5], "--")
        return real_run([sys.executable, str(stub), *command[1:]], *args, **kwargs)

    test.enterContext(patch.object(blendfile.subprocess, "run", side_effect=launch))
    return stub


def _red_blue_image() -> tuple[int, int, bytes]:
    """4 × 4 : moitié gauche rouge, moitié droite bleue."""
    pixels = bytearray()
    for _y in range(4):
        for x in range(4):
            pixels += bytes((255, 0, 0, 255)) if x < 2 else bytes((0, 0, 255, 255))
    return 4, 4, bytes(pixels)


def _quad_with_uvs(left: float, right: float) -> MeshData:
    mesh = MeshData(name="quad",
                    positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                               (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
                    faces=[(0, 1, 2), (0, 2, 3)],
                    uvs=[(left, 0.5), (right, 0.5), (right, 0.5), (left, 0.5)],
                    material="Mt_Test")
    mesh.ensure_normals()
    return mesh


class SamplerTests(unittest.TestCase):
    def test_sampler_reads_the_right_half(self) -> None:
        sample = _make_sampler(_red_blue_image())
        red = sample(0.1, 0.5)
        blue = sample(0.9, 0.5)
        self.assertGreater(red[0], 0.8)
        self.assertLess(red[2], 0.2)
        self.assertGreater(blue[2], 0.8)
        self.assertLess(blue[0], 0.2)

    def test_sampler_clamps_outside_coordinates(self) -> None:
        sample = _make_sampler(_red_blue_image())
        self.assertEqual(sample(-3.0, 8.0), sample(0.0, 0.99))

    def test_transparent_pixels_do_not_blacken_the_colour(self) -> None:
        pixels = bytes((0, 200, 0, 0)) * 16           # tout transparent
        sample = _make_sampler((4, 4, pixels))
        self.assertGreater(sample(0.5, 0.5)[1], 0.5)  # repli neutre, pas du noir

    def test_faces_take_their_colour_from_the_texture(self) -> None:
        sample = _make_sampler(_red_blue_image())
        data = build_render_data([_quad_with_uvs(0.1, 0.1)], textures={"": sample})
        self.assertGreater(data.colors[0][0], 0.8)     # rouge, pas la palette
        self.assertLess(data.colors[0][2], 0.2)

        other = build_render_data([_quad_with_uvs(0.9, 0.9)], textures={"": sample})
        self.assertGreater(other.colors[0][2], 0.8)    # bleu

    def test_without_texture_the_palette_is_untouched(self) -> None:
        plain = build_render_data([_quad_with_uvs(0.1, 0.1)])
        textured = build_render_data([_quad_with_uvs(0.1, 0.1)],
                                     textures={"": _make_sampler(_red_blue_image())})
        self.assertNotEqual(tuple(plain.colors[0]), tuple(textured.colors[0]))


def _make_project(tmp: Path, animations: bool = True) -> Project:
    root = tmp / "dump"
    (root / "Model").mkdir(parents=True)
    (root / "Model" / "Heros.bfres").write_bytes(synth.make_bfres())
    (root / "Model" / "Heros_Alb.bntx").write_bytes(
        synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8)))
    (root / "Model" / "Heros_Nrm.bntx").write_bytes(
        synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8, seed=3)))
    if animations:
        (root / "Model" / "Heros.anim.bfres").write_bytes(synth.make_bfres())
    project = Project(root, tmp / "work")
    project.scan()
    return project


class EnsembleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.project = _make_project(self.tmp)
        self.rel = "Model/Heros.bfres"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ensemble_reunites_model_textures_and_animations(self) -> None:
        rows = self.project.ensembles()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["rel"], self.rel)
        self.assertEqual(row["textures"], 1)          # la normale est écartée
        self.assertEqual(row["animations"], 1)
        self.assertTrue(row["complete"])
        self.assertTrue(row["textured"])

    def test_model_info_lists_the_companions(self) -> None:
        info = self.project.model_info(self.rel)
        # toutes les cartes du modèle, chacune avec son rôle (albédo + normale)
        roles = {item["name"]: item["role"] for item in info["textures"]}
        self.assertEqual(roles, {"Heros_Alb": "albedo", "Heros_Nrm": "normal"})
        self.assertEqual([item["name"] for item in info["animations"]],
                         ["Heros.anim"])

    def test_animations_are_matched_by_name_not_by_neighbour(self) -> None:
        (self.tmp / "dump" / "Model" / "Autre.bfres").write_bytes(synth.make_bfres())
        (self.tmp / "dump" / "Model" / "Autre.anim.bfres").write_bytes(synth.make_bfres())
        self.project.scan()
        names = lambda rel: [asset.rel for asset in self.project.animations_for(rel)]
        self.assertEqual(names(self.rel), ["Model/Heros.anim.bfres"])
        self.assertEqual(names("Model/Autre.bfres"), ["Model/Autre.anim.bfres"])

    def test_textured_preview_has_its_own_cache_entry(self) -> None:
        plain = self.project.model_preview(self.rel, 160, 120, textured=False)
        painted = self.project.model_preview(self.rel, 160, 120, textured=True)
        self.assertTrue(plain.startswith(b"\x89PNG"))
        self.assertTrue(painted.startswith(b"\x89PNG"))
        self.assertTrue(self.project.thumbnail_cached(self.rel, 160, 120, textured=False))
        self.assertTrue(self.project.thumbnail_cached(self.rel, 160, 120, textured=True))
        self.assertNotEqual(
            self.project.thumbnail_key(self.rel, 160, 120, textured=False),
            self.project.thumbnail_key(self.rel, 160, 120, textured=True))

    def test_settings_can_turn_textured_previews_off(self) -> None:
        self.project.update_settings({"textured_previews": False})
        info = self.project.model_info(self.rel)
        self.assertFalse(info["textured"])
        # l'albédo reste listé : on l'éteint à l'affichage, on ne le cache pas
        self.assertIn("albedo", {item["role"] for item in info["textures"]})

    def test_albedo_is_shared_by_every_material(self) -> None:
        samplers = self.project.albedo_samplers(self.rel)
        self.assertIn("", samplers)                 # repli pour tout matériau
        self.assertIn("Heros_Alb", samplers)
        self.assertEqual(len(samplers[""](0.25, 0.75)), 3)


class EnsembleScaleTests(unittest.TestCase):
    def test_listing_a_big_dump_stays_instant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dump"
            (root / "Model").mkdir(parents=True)
            (root / "Texture").mkdir(parents=True)
            blob = synth.make_bfres()
            for index in range(150):
                (root / "Model" / f"Mob_{index:03d}.bfres").write_bytes(blob)
                (root / "Texture" / f"Mob_{index:03d}_Alb.bntx").write_bytes(
                    synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8)))
            project = Project(root, Path(tmp) / "work")
            project.scan()
            start = time.perf_counter()
            rows = project.ensembles()
            elapsed = time.perf_counter() - start
            self.assertEqual(len(rows), 150)
            self.assertEqual(rows[0]["textures"], 1)
            self.assertLess(elapsed, 0.5, f"liste des ensembles trop lente : {elapsed:.2f} s")


class BlendScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _stub(self, body: str) -> Path:
        return _blender_stub(self, self.tmp, body)

    def test_without_blender_the_script_and_command_are_ready(self) -> None:
        folder = self.tmp / "export"
        folder.mkdir()
        (folder / "Heros.gltf").write_text("{}", encoding="utf-8")
        result = blendfile.make_blend(folder, "Heros", blender=None)
        if result["method"] == "blender":            # Blender présent : autre test
            self.skipTest("Blender est installé sur cette machine")
        self.assertEqual(result["method"], "script")
        self.assertFalse((folder / "Heros.blend").exists())
        self.assertTrue((folder / blendfile.SCRIPT_NAME).is_file())
        self.assertIn("blender", result["command"])
        self.assertIn("Heros.blend", result["command"])
        self.assertIn("Réglages", result["message"])

    def test_script_is_runnable_on_its_own(self) -> None:
        folder = self.tmp / "export"
        folder.mkdir()
        (folder / "Heros.gltf").write_text("{}", encoding="utf-8")
        blendfile.write_script(folder, "Heros.gltf", "Heros.blend")
        script = (folder / blendfile.SCRIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("import_scene.gltf", script)
        self.assertIn("save_as_mainfile", script)
        self.assertIn("pack_all", script)            # textures embarquées
        self.assertIn("Heros.gltf", script)          # repli sans argument

    def test_the_script_compiles(self) -> None:
        """Une faute de frappe dans le script se verrait seulement chez l'utilisateur."""
        compile(blendfile.IMPORT_SCRIPT, blendfile.SCRIPT_NAME, "exec")

    def test_a_working_blender_produces_a_real_blend(self) -> None:
        folder = self.tmp / "export"
        folder.mkdir()
        (folder / "Heros.gltf").write_text("{}", encoding="utf-8")
        stub = self._stub("import sys\n"
                          "target = sys.argv[-1]\n"
                          "open(target, 'wb').write(b'BLENDER-v300')\n"
                          "print('HOOKY_OK', target)")
        result = blendfile.make_blend(folder, "Heros", blender=stub)
        self.assertEqual(result["method"], "blender")
        self.assertEqual(Path(result["blend"]).read_bytes(), b"BLENDER-v300")
        self.assertIn("Ko", result["message"])

    def test_a_blender_that_writes_nothing_is_reported(self) -> None:
        folder = self.tmp / "export"
        folder.mkdir()
        (folder / "Heros.gltf").write_text("{}", encoding="utf-8")
        stub = self._stub("print('je ne fais rien')")
        result = blendfile.make_blend(folder, "Heros", blender=stub)
        self.assertEqual(result["method"], "script")
        self.assertIsNone(result["blend"])
        self.assertIn("sans écrire", result["message"])

    def test_a_failing_blender_falls_back_with_its_output(self) -> None:
        folder = self.tmp / "export"
        folder.mkdir()
        (folder / "Heros.gltf").write_text("{}", encoding="utf-8")
        stub = self._stub("import sys\nprint('erreur de test')\nsys.exit(3)")
        result = blendfile.make_blend(folder, "Heros", blender=stub)
        self.assertEqual(result["method"], "script")
        self.assertIn("code 3", result["message"])
        self.assertIn("erreur de test", result["output"])

    def test_stale_blend_is_not_mistaken_for_a_fresh_one(self) -> None:
        folder = self.tmp / "export"
        folder.mkdir()
        (folder / "Heros.gltf").write_text("{}", encoding="utf-8")
        (folder / "Heros.blend").write_bytes(b"vieux fichier")
        stub = self._stub("print('rien')")
        result = blendfile.make_blend(folder, "Heros", blender=stub)
        self.assertEqual(result["method"], "script")
        self.assertFalse((folder / "Heros.blend").exists())

    def test_find_blender_accepts_an_explicit_file(self) -> None:
        stub = self._stub("pass")
        self.assertEqual(blendfile.find_blender(str(stub)), stub)
        self.assertIsNone(blendfile.find_blender(str(self.tmp / "absent")))


class BlendExportProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.project = _make_project(self.tmp, animations=False)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_export_contains_geometry_textures_and_the_script(self) -> None:
        result = self.project.export_blend("Model/Heros.bfres")
        folder = self.tmp / "work" / "export" / "Heros"
        self.assertTrue((folder / "Heros.gltf").is_file())
        self.assertTrue((folder / "Heros.bin").is_file())
        self.assertTrue((folder / blendfile.SCRIPT_NAME).is_file())
        self.assertTrue(any(path.suffix == ".png" for path in folder.iterdir()))
        self.assertGreaterEqual(result["faces"], 1)
        self.assertGreaterEqual(result["textures"], 1)
        self.assertIn(result["method"], {"script", "blender"})
        if result["method"] == "script":
            self.assertEqual(result["blend"], "")
            self.assertIn("blender", result["command"])

    def test_configured_blender_is_used(self) -> None:
        stub = _blender_stub(self, self.tmp,
                             "import sys\nopen(sys.argv[-1], 'wb').write(b'BLENDER')")
        self.project.update_settings({"blender_path": str(stub)})
        result = self.project.export_blend("Model/Heros.bfres")
        self.assertEqual(result["method"], "blender")
        self.assertTrue(Path(result["blend"]).is_file())
        self.assertEqual(Path(result["blend"]).read_bytes(), b"BLENDER")


class EnsembleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.server = HookyCreatorServer(_make_project(self.tmp).root,
                                         self.tmp / "work", host="127.0.0.1", port=0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ensembles_route_lists_the_model_with_its_companions(self) -> None:
        body, kind = self.server.api("GET", "/api/ensembles", {}, {})
        self.assertEqual(kind, "application/json")
        payload = json.loads(body)
        self.assertEqual(len(payload["ensembles"]), 1)
        self.assertEqual(payload["ensembles"][0]["textures"], 1)
        self.assertIn("found", payload["blender"])

    def test_blender_route_reports_the_state_and_the_note(self) -> None:
        body, _ = self.server.api("GET", "/api/blender", {}, {})
        state = json.loads(body)
        self.assertIn("path", state)
        self.assertTrue(state["note"])

    def test_preview_route_accepts_the_texture_switch(self) -> None:
        for tex in ("0", "1"):
            body, kind = self.server.api(
                "GET", "/api/model/preview",
                {"path": ["Model/Heros.bfres"], "w": ["96"], "h": ["72"],
                 "tex": [tex]}, {})
            self.assertEqual(kind, "image/png")
            self.assertTrue(body.startswith(b"\x89PNG"))

    def test_blend_route_answers_with_a_recipe(self) -> None:
        body, kind = self.server.api("POST", "/api/model/blend", {},
                                     {"path": "Model/Heros.bfres"})
        self.assertEqual(kind, "application/json")
        payload = json.loads(body)
        self.assertIn(payload["method"], {"script", "blender"})
        self.assertTrue(payload["message"])
        self.assertTrue(payload["script"])

    def test_selection_route_can_ask_for_blend(self) -> None:
        """Un seul ZIP pour toute la sélection, avec un dossier par modèle."""
        body, kind = self.server.api(
            "POST", "/api/export/selection", {},
            {"items": [{"kind": "model", "rel": "Model/Heros.bfres"}], "blend": True})
        self.assertEqual(kind, "application/zip")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = archive.namelist()
            notes = archive.read("LIRE-MOI.txt").decode("utf-8")
        self.assertTrue(any(name.endswith("creer_blend.py") for name in names))
        # le glTF et son .bin voyagent avec le script : le ZIP reste utilisable
        self.assertTrue(any(name.endswith("Heros.gltf") for name in names))
        self.assertTrue(any(name.endswith("Heros.bin") for name in names))
        self.assertIn("Heros", notes)
        self.assertIn("modèle(s)", notes)

    def test_blend_route_refuses_an_empty_selection(self) -> None:
        from hookycreator.project import ProjectError

        with self.assertRaises(ProjectError):
            self.server.api("POST", "/api/export/selection", {},
                            {"items": [], "blend": True})


if __name__ == "__main__":
    unittest.main()
