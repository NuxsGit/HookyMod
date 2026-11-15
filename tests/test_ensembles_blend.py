"""Vue Ensembles, association de textures et performance de la liste."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookycreator.project import Project, _make_sampler  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402
from hookymod.mesh import MeshData  # noqa: E402
from hookymod.viewer import build_render_data  # noqa: E402


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

    def test_preview_route_accepts_the_texture_switch(self) -> None:
        for tex in ("0", "1"):
            body, kind = self.server.api(
                "GET", "/api/model/preview",
                {"path": ["Model/Heros.bfres"], "w": ["96"], "h": ["72"],
                 "tex": [tex]}, {})
            self.assertEqual(kind, "image/png")
            self.assertTrue(body.startswith(b"\x89PNG"))



if __name__ == "__main__":
    unittest.main()
