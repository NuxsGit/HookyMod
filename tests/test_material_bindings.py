"""Les cartes référencées sont reliées par matériau, jamais à tous les objets."""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hookycreator import cache
from hookycreator.project import Asset, Project
from hookymod import util
from tests import synth
from tests.test_bfres_v10_struct import structured_model


class MaterialBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source"
        (self.root / "Model").mkdir(parents=True)
        (self.root / "Tex").mkdir()
        (self.root / "Model/Objet.bfres").write_bytes(structured_model())
        self.colors = {"Tex_A_Alb": (250, 0, 0, 255), "Tex_A_Nrm": (128, 128, 255, 255),
                       "Tex_B_Alb": (0, 220, 0, 255), "Tex_B_Rgh": (160, 160, 160, 255)}
        for name, color in self.colors.items():
            (self.root / "Tex" / f"{name}.bntx").write_bytes(synth.make_bntx(4, 4, bytes(color) * 16))
        self.project = Project(self.root, Path(self.tmp.name) / "work")
        self.project.scan()

    def export(self):
        result = self.project.export_model("Model/Objet.bfres", archive=True)
        folder = Path(result["absolute_folder"])
        doc = json.loads((folder / "Objet.gltf").read_text())
        return result, folder, doc

    def test_per_material_gltf_and_obj_and_full_resolution(self):
        result, folder, doc = self.export()
        materials = {m["name"]: m for m in doc["materials"]}
        def image(texture):
            return doc["images"][doc["textures"][texture["index"]]["source"]]["uri"]
        a, b = materials["Mat_A"], materials["Mat_B"]
        self.assertEqual(image(a["pbrMetallicRoughness"]["baseColorTexture"]), "Tex_A_Alb.png")
        self.assertEqual(image(a["normalTexture"]), "Tex_A_Nrm.png")
        self.assertEqual(image(b["pbrMetallicRoughness"]["baseColorTexture"]), "Tex_B_Alb.png")
        self.assertNotIn("normalTexture", b)
        self.assertIn("metallicRoughnessTexture", b["pbrMetallicRoughness"])
        self.assertNotIn("metallicRoughnessTexture", a["pbrMetallicRoughness"])
        for name, color in self.colors.items():
            w, h, pixels = util.decode_png((folder / f"{name}.png").read_bytes())
            self.assertEqual((w, h), (4, 4))
            self.assertEqual(tuple(pixels[:4]), color)
        mtl = (folder / "Objet.mtl").read_text()
        section_a = mtl.split("newmtl Mat_A")[1].split("newmtl")[0]
        section_b = mtl.split("newmtl Mat_B")[1].split("newmtl")[0]
        self.assertIn("map_Kd Tex_A_Alb.png", section_a)
        self.assertIn("map_Bump Tex_A_Nrm.png", section_a)
        self.assertIn("Kd 1.000 1.000 1.000", section_a)
        self.assertIn("map_Kd Tex_B_Alb.png", section_b)
        self.assertNotIn("map_Bump Tex_A_Nrm.png", section_b)
        self.assertTrue(result["download_url"])
        with zipfile.ZipFile(self.project.export_download(result["download_url"].split("=")[1])) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(json.loads(archive.read("Objet/Objet.gltf"))["materials"], doc["materials"])
        info = self.project.model_info("Model/Objet.bfres")
        self.assertEqual(info["texture_match"], "références du matériau")
        self.assertEqual({(item["material"], item["name"]) for item in info["textures"]},
                         {("Mat_A", "Tex_A_Alb"), ("Mat_A", "Tex_A_Nrm"),
                          ("Mat_B", "Tex_B_Alb"), ("Mat_B", "Tex_B_Rgh")})
        samplers = self.project.albedo_samplers("Model/Objet.bfres")
        self.assertNotIn("", samplers)
        self.assertEqual(samplers["Mat_A"](.5, .5), (250 / 255, 0., 0.))
        self.assertEqual(samplers["Mat_B"](.5, .5), (0., 220 / 255, 0.))

    def test_ambiguous_duplicate_and_missing_maps_do_not_bind_to_wrong_material(self):
        (self.root / "Other").mkdir()
        (self.root / "Other/Tex_A_Alb.bntx").write_bytes((self.root / "Tex/Tex_A_Alb.bntx").read_bytes())
        (self.root / "Tex/Tex_B_Alb.bntx").unlink()
        self.project.scan()
        result, _folder, doc = self.export()
        mats = {m["name"]: m for m in doc["materials"]}
        self.assertNotIn("baseColorTexture", mats["Mat_A"]["pbrMetallicRoughness"])
        self.assertNotIn("baseColorTexture", mats["Mat_B"]["pbrMetallicRoughness"])
        self.assertTrue(any("plusieurs fichiers" in warning for warning in result["warnings"]))
        self.assertTrue(any("absente" in warning for warning in result["warnings"]))

    def test_multi_texture_container_uses_matching_internal_index(self):
        asset = next(a for a in self.project.assets if a.stem == "Tex_A_Alb")
        fake = SimpleNamespace(textures=[SimpleNamespace(index=0, name="Autre"),
                                         SimpleNamespace(index=1, name="Tex_A_Alb")])
        with patch.object(self.project, "texture_file", return_value=fake):
            resolved, warnings = self.project._material_reference_sources({"Mat_A": ["Tex_A_Alb"]})
        self.assertEqual(resolved["Mat_A"]["albedo"], [(asset, 1, "Tex_A_Alb")])
        self.assertEqual(warnings, [])

    def test_reference_filenames_are_portable_and_hash_collisions_do_not_overwrite(self):
        assets = [Asset(f"Tex/{index}.bntx", "texture", 0) for index in range(5)]
        suffix = cache.digest(f"{assets[2].rel}:0")[:8]
        references = [(assets[0], 0, f"X--{suffix}"), (assets[1], 0, "X"),
                      (assets[2], 0, "X"), (assets[3], 0, "../../escape"),
                      (assets[4], 0, "CON")]
        resolved = {"Mat_A": {"albedo": references}}
        pixels = {asset.rel: util.png_bytes(2, 2, bytes((index, 0, 0, 255)) * 4)
                  for index, asset in enumerate(assets)}
        folder = Path(self.tmp.name) / "portable"
        folder.mkdir()
        with patch.object(self.project, "_material_reference_sources", return_value=(resolved, [])), \
                patch.object(self.project, "texture_png", side_effect=lambda rel, index: pixels[rel]):
            result = self.project._export_referenced_maps("Model/Objet.bfres", folder, {}, 128)
        files = result["files"]
        self.assertEqual(len(files), len(assets))
        self.assertEqual(len({item["file"].casefold() for item in files}), len(assets))
        self.assertIn(f"X--{suffix}-2.png", [item["file"] for item in files])
        self.assertIn("_CON.png", [item["file"] for item in files])
        for item in files:
            path = folder / item["file"]
            self.assertEqual(path.resolve().parent, folder.resolve())
            self.assertEqual(path.read_bytes(), pixels[item["source"]])
        self.assertFalse((folder.parent / "escape.png").exists())

    def test_selection_export_uses_same_per_material_bindings(self):
        import io
        with zipfile.ZipFile(io.BytesIO(self.project.export_selection([
                {"kind": "model", "rel": "Model/Objet.bfres"}]))) as archive:
            doc = json.loads(archive.read("modeles/Objet/Objet.gltf"))
            mats = {m["name"]: m for m in doc["materials"]}
            self.assertIn("normalTexture", mats["Mat_A"])
            self.assertNotIn("normalTexture", mats["Mat_B"])


if __name__ == "__main__":
    unittest.main()
