"""HookyCreator : projet, textures, modèles, mobs, export de mod et API.

Tout est fabriqué à la volée (``tests/synth.py``) : aucun asset du jeu n'est
nécessaire. Les tests vérifient aussi les refus — un éditeur ne doit jamais
écrire un fichier faux sans le dire.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402
from hookycreator.project import Project, ProjectError  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402
from hookymod import util  # noqa: E402


def _gradient_png(width: int = 64, height: int = 64) -> bytes:
    raw = bytearray()
    for y in range(height):
        for x in range(width):
            raw += bytes((x * 4 % 256, y * 4 % 256, 120, 255))
    return util.png_bytes(width, height, bytes(raw))


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        (cls.root / "Tex").mkdir()
        (cls.root / "Model").mkdir()
        (cls.root / "Tex" / "A.bntx").write_bytes(
            synth.make_bntx(32, 32, synth.rgba8_pixels(32, 32)))
        (cls.root / "Model" / "B.bfres").write_bytes(synth.make_bfres())
        (cls.root / "Tex" / "note.txt").write_text("ignoré")
        cls.project = Project(cls.root)

    def test_scan_classifies_assets(self):
        state = self.project.scan()
        self.assertEqual(state["textures"], 1)
        self.assertEqual(state["models"], 1)
        self.assertEqual(state["count"], 2)

    def test_animation_and_skeleton_files_are_not_counted_as_models(self):
        """Les fichiers BFRES auxiliaires connus ne sont pas proposés comme maillages."""
        root = Path(tempfile.mkdtemp())
        (root / "Model").mkdir()
        names = ("B_Animation.anim.bfres", "B_Animation.ANIMATION.BFRES.ZS",
                 "B_Animation.Skeleton.bfres.zs", "C.camera.bfres", "D.light.bfres.zs")
        (root / "Model" / "B.bfres").write_bytes(synth.make_bfres())
        for name in names:
            (root / "Model" / name).write_bytes(b"FRES" + bytes(0x40))
        # Un mot ordinaire dans le nom d'un modèle ne suffit pas pour l'exclure.
        (root / "Model" / "Camera_Prop.Camera_Prop.bfres").write_bytes(synth.make_bfres())
        project = Project(root)
        state = project.scan()
        self.assertEqual(state["models"], 2)
        self.assertEqual(state["animations"], 2)
        self.assertEqual(state["auxiliary"], 3)
        kinds = {asset.rel: asset.kind for asset in project.assets}
        for name in names[:2]:
            self.assertEqual(kinds[f"Model/{name}"], "animation")
        for name in names[2:]:
            self.assertEqual(kinds[f"Model/{name}"], "auxiliary")
        self.assertEqual(kinds["Model/Camera_Prop.Camera_Prop.bfres"], "model")
        animations = project.animations_for("Model/B.bfres")
        self.assertEqual([asset.rel for asset in animations], sorted(f"Model/{n}" for n in names[:2]))
        self.assertIn("B_Animation.ANIMATION", [asset.stem for asset in animations])

        from hookycreator import bulk
        items = {item.label: item.kind for item in bulk.discover(project)}
        for name in names:
            self.assertEqual(items[f"Model/{name}"], kinds[f"Model/{name}"])

    def test_paths_outside_the_project_are_refused(self):
        with self.assertRaises(ProjectError):
            self.project.asset("../secret.txt")

    def test_unknown_asset_is_reported(self):
        with self.assertRaises(ProjectError):
            self.project.texture_info("Tex/manquant.bntx")

    def test_texture_info_and_png(self):
        info = self.project.texture_info("Tex/A.bntx")
        self.assertEqual(info["count"], 1)
        self.assertEqual(info["textures"][0]["width"], 32)
        png = self.project.texture_png("Tex/A.bntx", 0)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_texture_import_keeps_format_and_size(self):
        png = _gradient_png(32, 32)
        report = self.project.import_texture("Tex/A.bntx", 0, png)
        self.assertEqual(report["format"], "R8_G8_B8_A8_UNORM")   # format du BNTX synthétique
        written = self.project.work / "assets" / "Tex" / "A.bntx"
        self.assertTrue(written.is_file())
        info = Project(self.root).texture_info("Tex/A.bntx")
        self.assertEqual(info["textures"][0]["width"], 32)

    def test_texture_import_refuses_unsupported_format(self):
        # ASTC ne peut pas être encodé : on veut une erreur, pas un fichier faux.
        from hookymod import bntx_encode
        if bntx_encode.can_encode_format(0x3206):
            self.skipTest("format inattendu")
        with self.assertRaises(Exception):
            bntx_encode.encode_pixels(bytes(32 * 32 * 4), 32, 32, 0x3206)

    def test_model_info_and_export(self):
        info = self.project.model_info("Model/B.bfres")
        self.assertGreaterEqual(info["meshes"], 1)
        result = self.project.export_model("Model/B.bfres", textures=False)
        folder = self.root / result["folder"]
        self.assertTrue((folder / "B.gltf").is_file())
        self.assertIn("B.obj", result["files"])


class MobAndModTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Tex").mkdir()
        (self.root / "Model").mkdir()
        (self.root / "Tex" / "Butterfly_Alb.bntx").write_bytes(
            synth.make_bntx(32, 32, synth.rgba8_pixels(32, 32)))
        (self.root / "Model" / "Butterfly.bfres").write_bytes(synth.make_bfres())
        self.project = Project(self.root)
        self.project.scan()

    def test_create_mob_copies_assets_and_records_todo(self):
        mob = self.project.create_mob("Papillon Doré", "Model/Butterfly.bfres",
                                      ["Tex/Butterfly_Alb.bntx"])
        self.assertEqual(mob["slug"], "Papillon_Doré")
        self.assertEqual(len(mob["files"]), 2)
        for entry in mob["files"]:
            self.assertTrue((self.project.work / "mobs" / mob["slug"] / entry["to"]).is_file())
        self.assertTrue(mob["todo"])
        self.assertEqual(len(self.project.list_mobs()), 1)

    def test_mob_name_is_validated(self):
        with self.assertRaises(ProjectError):
            self.project.create_mob("   ", "", [])

    def test_build_mod_writes_romfs_tree(self):
        mob = self.project.create_mob("Test", "Model/Butterfly.bfres", [])
        self.project.import_texture("Tex/Butterfly_Alb.bntx", 0, _gradient_png(32, 32))
        result = self.project.build_mod("MonMod")
        target = self.root / "mods" / "MonMod"
        self.assertTrue((target / "romfs" / "Tex" / "Butterfly_Alb.bntx").is_file())
        model_entry = next(f for f in mob["files"] if f["kind"] == "model")
        self.assertTrue((target / "romfs" / "Model" / model_entry["to"]).is_file())
        manifest = json.loads((target / "hookycreator.json").read_text())
        self.assertEqual(manifest["mobs"], 1)
        self.assertGreaterEqual(result["files"], 2)


class PngReaderTests(unittest.TestCase):
    def test_decodes_our_own_png(self):
        from hookycreator.pngread import decode_image
        width, height, rgba = decode_image(_gradient_png(8, 8))
        self.assertEqual((width, height), (8, 8))
        self.assertEqual(len(rgba), 8 * 8 * 4)
        self.assertEqual(rgba[0:4], bytes((0, 0, 120, 255)))

    def test_garbage_is_refused(self):
        from hookycreator.pngread import decode_image
        with self.assertRaises(ValueError):
            decode_image(b"pas une image")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        (cls.root / "Tex").mkdir()
        (cls.root / "Tex" / "A.bntx").write_bytes(
            synth.make_bntx(32, 32, synth.rgba8_pixels(32, 32)))
        cls.server = HookyCreatorServer(cls.root, port=0).start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def _get(self, route: str):
        with urllib.request.urlopen(self.base + route) as response:
            return response.status, response.read(), response.headers.get("Content-Type")

    def test_index_is_served(self):
        status, body, kind = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"HookyCreator", body)
        self.assertIn("text/html", kind)

    def test_state_lists_assets(self):
        status, body, _ = self._get("/api/state")
        self.assertEqual(status, 200)
        payload = json.loads(body)["project"]
        self.assertEqual(len(payload["assets"]), 1)

    def test_texture_png(self):
        status, body, kind = self._get("/api/texture/png?path=Tex/A.bntx&index=0")
        self.assertEqual(status, 200)
        self.assertEqual(kind, "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_unknown_asset_returns_json_error(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._get("/api/texture/info?path=Tex/nope.bntx")
        self.assertEqual(context.exception.code, 400)
        payload = json.loads(context.exception.read())
        self.assertIn("introuvable", payload["error"])

    def test_import_texture_through_api(self):
        payload = json.dumps({
            "path": "Tex/A.bntx", "index": 0,
            "image": base64.b64encode(_gradient_png(32, 32)).decode(),
        }).encode()
        request = urllib.request.Request(self.base + "/api/texture/import", data=payload,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            report = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertIn("wrote", report)

    def test_api_refuses_paths_outside_the_project(self):
        with self.assertRaises(urllib.error.HTTPError):
            self._get("/api/texture/info?path=../etc/passwd")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class BulkClassificationTests(unittest.TestCase):
    """Le classement décide de ce que la grille montre : il doit être explicite."""

    def _classify(self, data: bytes, name: str) -> str:
        from hookycreator.bulk import _classify

        return _classify(data, name)

    def test_texture_is_recognised_by_magic(self) -> None:
        self.assertEqual(self._classify(b"BNTX" + bytes(8), "peu_importe.bin"), "texture")

    def test_auxiliary_names_are_never_models(self) -> None:
        for name in ("Enemy_Spike.anim.bfres.zs", "Enemy_Stalfos_Animation.anim.bfres.zs"):
            self.assertEqual(self._classify(b"FRES" + bytes(64), name), "animation", name)
        for name in ("Enemy_Stalfos_Animation.Skeleton.bfres.zs", "Dm_Zora_0000.camera.bfres.zs"):
            self.assertEqual(self._classify(b"FRES" + bytes(64), name), "auxiliary", name)

    def test_bfres_without_animation_name_is_a_model(self) -> None:
        self.assertEqual(self._classify(b"FRES" + bytes(64), "Enemy_Wolfos.bfres.zs"), "model")

    def test_unknown_content_falls_back_on_extension(self) -> None:
        self.assertEqual(self._classify(b"????", "Modele.bfres"), "model")
        self.assertEqual(self._classify(b"????", "lecture.txt"), "other")
