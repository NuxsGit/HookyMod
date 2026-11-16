"""Toutes les cartes d'un modèle : reconnaissance du rôle et câblage Blender.

Le défaut d'origine : l'export ne sortait qu'**une** carte (l'albédo), donc dans
Blender l'épée arrivait avec sa couleur mais sans normale, sans rugosité, sans
métal. Ces tests vérifient que :

* chaque suffixe du jeu est reconnu (``_Alb``, ``_Nrm``, ``_Rgh``, ``_Mtl``,
  ``_AO``, ``_Emi``) et qu'un nom inconnu n'est **pas** rangé au hasard ;
* les cartes candidates partent dans l'export, plus ``TEXTURES.md`` ;
  les associations non vérifiées restent clairement signalées ;
* le glTF porte les textures là où glTF les attend (baseColor, normal,
  metallicRoughness avec rugosité en G et métal en B, occlusion, émission) ;
* l'OMT d'OBJ (`MTL`) référence l'albédo et la normale.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookycreator.project import Project  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402
from hookymod import blender, texturemaps, util  # noqa: E402
from hookymod.mesh import MeshData, export_obj  # noqa: E402

MODEL = "Model/Heros.bfres"


def _grey_png(value: int, size: int = 8) -> bytes:
    pixels = bytes((value, value, value, 255)) * (size * size)
    return util.png_bytes(size, size, pixels)


class ClassifyTests(unittest.TestCase):
    def test_known_suffixes(self) -> None:
        cases = {
            "Mt_Weapon_Sword_Example_Alb.bntx.zs": "albedo",
            "Mt_Hair_Npc_Example_Nrm.0.bntx.zs": "normal",
            "CeTexte_Mt_Hair_Npc_Rgh.bntx.zs": "roughness",
            "Mt_Weapon_Sword_Example_Mtl.bntx.zs": "metallic",
            "Mt_Weapon_Sword_Example_AO.bntx.zs": "occlusion",
            "Mt_Weapon_Sword_Example_Emi.bntx.zs": "emissive",
            "Mt_Blade_Spc.bntx.zs": "specular",
            "Mt_Blade_Dif.bntx.zs": "albedo",
            "Creature_Alpha_Alb.png": "albedo",
        }
        for name, role in cases.items():
            self.assertEqual(texturemaps.classify(name), role, name)

    def test_last_token_wins(self) -> None:
        """« Mt_ » veut dire *material*, pas *metallic*."""
        self.assertEqual(texturemaps.classify("Mt_Body_Nrm.bntx"), "normal")
        self.assertEqual(texturemaps.classify("Mt_Body_Alb.bntx"), "albedo")

    def test_unknown_suffix_is_not_guessed(self) -> None:
        self.assertEqual(texturemaps.classify("Mt_Blade_Weird.bntx"), "autre")
        self.assertEqual(texturemaps.classify("sans_suffixe.bntx"), "autre")

    def test_base_name_groups_the_maps_of_one_asset(self) -> None:
        self.assertEqual(texturemaps.base_name("Mt_Epee_Alb.bntx.zs"),
                         "mt_epee")
        self.assertEqual(texturemaps.base_name("Mt_Epee_Nrm.bntx.zs"),
                         "mt_epee")

    def test_group_orders_the_roles(self) -> None:
        grouped = texturemaps.group(["X_Nrm.png", "X_Alb.png", "X_Rgh.png"])
        self.assertEqual(list(grouped), ["albedo", "normal", "roughness"])


class ComposeTests(unittest.TestCase):
    def test_roughness_goes_to_green_and_metal_to_blue(self) -> None:
        composed = texturemaps.compose_metal_roughness(_grey_png(64), _grey_png(255))
        self.assertTrue(composed.startswith(b"\x89PNG"))
        # on relit l'image produite
        from hookycreator.pngread import decode_image

        width, height, pixels = decode_image(composed)
        self.assertEqual((width, height), (8, 8))
        self.assertAlmostEqual(pixels[1], 64, delta=2)     # G = rugosité
        self.assertAlmostEqual(pixels[2], 255, delta=2)    # B = métal

    def test_missing_metal_is_zero(self) -> None:
        composed = texturemaps.compose_metal_roughness(_grey_png(200), None)
        from hookycreator.pngread import decode_image

        _w, _h, pixels = decode_image(composed)
        self.assertAlmostEqual(pixels[2], 0, delta=2)

    def test_missing_roughness_is_matte(self) -> None:
        composed = texturemaps.compose_metal_roughness(None, _grey_png(255))
        from hookycreator.pngread import decode_image

        _w, _h, pixels = decode_image(composed)
        self.assertAlmostEqual(pixels[1], 255, delta=2)

    def test_nothing_to_compose(self) -> None:
        self.assertIsNone(texturemaps.compose_metal_roughness(None, None))


def _make_project(tmp: Path) -> Project:
    root = tmp / "dump"
    (root / "Model").mkdir(parents=True, exist_ok=True)
    (root / "Model" / "Heros.bfres").write_bytes(synth.make_bfres())
    for suffix in ("Alb", "Nrm", "Rgh", "Mtl", "AO", "Emi"):
        (root / "Model" / f"Heros_{suffix}.bntx").write_bytes(
            synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8)))
    project = Project(root, tmp / "work")
    project.scan()
    return project


class ExportMapsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.project = _make_project(self.tmp)
        self.folder = self.tmp / "export"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_every_map_is_exported(self) -> None:
        result = self.project.export_maps_for(MODEL, self.folder)
        roles = set(result["maps"])
        self.assertEqual(roles, {"albedo", "normal", "roughness", "metallic",
                                 "occlusion", "emissive", "metalrough"})
        for name in result["maps"].values():
            self.assertTrue((self.folder / name).is_file(), name)

    def test_manifest_explains_where_each_map_goes(self) -> None:
        self.project.export_maps_for(MODEL, self.folder)
        text = (self.folder / "TEXTURES.md").read_text(encoding="utf-8")
        self.assertIn("Base Color", text)
        self.assertIn("Normal Map", text)
        self.assertIn("Non-Color", text)
        self.assertIn("association heuristique", text)
        self.assertIn("branchement manuel", text)
        self.assertIn("absentes,", text)

    def test_export_does_not_generate_a_shader_script(self) -> None:
        # Les anciens scripts raccordaient les cartes à tous les matériaux,
        # ce qui peut présenter des associations incorrectes comme certaines.
        self.project.export_maps_for(MODEL, self.folder)
        self.assertFalse((self.folder / "assembler_shaders.py").exists())

    def test_export_model_reports_the_maps_by_role(self) -> None:
        result = self.project.export_model(MODEL)
        labels = {item["label"] for item in result["maps"]}
        self.assertTrue({"Alb", "Nrm"} <= labels)
        self.assertGreaterEqual(result["textures"], 6)
        self.assertIn("TEXTURES.md", result["files"])

    def test_unknown_suffix_is_exported_anyway(self) -> None:
        """Une carte au suffixe inconnu n'est pas jetée : elle est exportée et
        classée « autre », sinon le moddeur croit qu'elle n'existe pas."""
        (self.tmp / "dump" / "Model" / "Heros_Weird.bntx").write_bytes(
            synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8)))
        project = _make_project(self.tmp)      # même dossier : rescan
        result = project.export_maps_for(MODEL, self.tmp / "export2")
        self.assertIn("autre", result["maps"])
        self.assertTrue((self.tmp / "export2" / result["maps"]["autre"]).is_file())


class GltfWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.folder = self.tmp / "bundle"
        self.folder.mkdir()
        for role, name in (("albedo", "Heros_Alb.png"),
                           ("normal", "Heros_Nrm.png"),
                           ("metalrough", "Heros_MetalRough.png"),
                           ("occlusion", "Heros_AO.png"),
                           ("emissive", "Heros_Emi.png")):
            (self.folder / name).write_bytes(_grey_png(128))
        self.mesh = MeshData(name="tri", positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                                                    (0.0, 1.0, 0.0)],
                             faces=[(0, 1, 2)],
                             uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                             material="Mt_Heros")
        self.mesh.ensure_normals()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _document(self, maps: dict[str, str]) -> dict:
        written = blender.export_bundle(
            [self.mesh], self.folder, "heros",
            textures={"Mt_Heros": str(self.folder / "Heros_Alb.png")},
            maps=maps)
        return json.loads(Path(written["gltf"]).read_text())

    def test_all_maps_are_wired_in_the_material(self) -> None:
        document = self._document({
            "albedo": "Heros_Alb.png", "normal": "Heros_Nrm.png",
            "metalrough": "Heros_MetalRough.png", "occlusion": "Heros_AO.png",
            "emissive": "Heros_Emi.png"})
        material = document["materials"][0]
        pbr = material["pbrMetallicRoughness"]
        self.assertIn("baseColorTexture", pbr)
        self.assertIn("metallicRoughnessTexture", pbr)
        self.assertEqual(pbr["metallicFactor"], 1.0)
        self.assertEqual(pbr["roughnessFactor"], 1.0)
        self.assertIn("normalTexture", material)
        self.assertIn("occlusionTexture", material)
        self.assertIn("emissiveTexture", material)
        self.assertEqual(material["emissiveFactor"], [1.0, 1.0, 1.0])

    def test_each_map_becomes_one_image(self) -> None:
        document = self._document({"normal": "Heros_Nrm.png",
                                   "metalrough": "Heros_MetalRough.png"})
        uris = sorted(image["uri"] for image in document["images"])
        self.assertEqual(uris, ["Heros_Alb.png", "Heros_MetalRough.png",
                                "Heros_Nrm.png"])
        self.assertEqual(len(document["textures"]), len(uris))

    def test_without_maps_the_material_stays_simple(self) -> None:
        document = self._document({})
        material = document["materials"][0]
        self.assertNotIn("normalTexture", material)
        self.assertEqual(material["pbrMetallicRoughness"]["metallicFactor"], 0.0)

    def test_mtl_references_albedo_and_normal(self) -> None:
        written = export_obj([self.mesh], self.folder / "heros.obj",
                             albedo_map={"Mt_Heros": "Heros_Alb.png"},
                             normal_map="Heros_Nrm.png")
        mtl = Path(written).with_suffix(".mtl").read_text(encoding="utf-8")
        self.assertIn("map_Kd Heros_Alb.png", mtl)
        self.assertIn("map_Bump Heros_Nrm.png", mtl)


class StateRoleTests(unittest.TestCase):
    def test_assets_carry_their_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            roles = {asset["name"]: asset.get("role")
                     for asset in (asset.as_json() for asset in project.assets
                                   if asset.kind == "texture")}
            self.assertEqual(roles["Heros_Alb.bntx"], "albedo")
            self.assertEqual(roles["Heros_Nrm.bntx"], "normal")

    def test_state_route_exposes_the_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_project(Path(tmp)).root
            server = HookyCreatorServer(root, Path(tmp) / "work2",
                                        host="127.0.0.1", port=0)
            body, _ = server.api("GET", "/api/state", {}, {})
            assets = json.loads(body)["project"]["assets"]
            textures = [asset for asset in assets if asset["kind"] == "texture"]
            self.assertTrue(all(asset.get("role") for asset in textures))


if __name__ == "__main__":
    unittest.main()
