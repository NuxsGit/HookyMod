"""Décodage complet, rendu sans traînées, cache de géométrie et export Blender.

Chaque test correspond à un défaut réel constaté sur les fichiers du jeu :

* une épée exportée **en morceaux** (segmentation limitée à des blocs de
  0x1000 octets) ;
* de longues traînées horizontales à l'écran (interpolation extrapolée hors
  d'un triangle presque plat) ;
* un glTF sans ``.bin`` et sans texture ;
* un clic sur un modèle qui redécodait tout le fichier.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookycreator import geo as geo_mod  # noqa: E402
from hookycreator.project import Project  # noqa: E402
from hookymod import blender, bfres_v10, render  # noqa: E402
from hookymod.mesh import MeshData  # noqa: E402


def _triangle_mesh(z: float = 0.0, size: float = 1.0) -> MeshData:
    mesh = MeshData(name="tri", positions=[(0.0, 0.0, z), (size, 0.0, z),
                                           (0.0, size, z)],
                    faces=[(0, 1, 2)])
    mesh.ensure_normals()
    return mesh


class SegmentationTests(unittest.TestCase):
    def test_run_starting_mid_block_is_found(self) -> None:
        """Un sous-maillage qui commence hors alignement ne doit plus être perdu."""
        words = array("H", [0]) * 0
        payload = [65535] * 21                      # remplissage avant le début
        payload += [0, 1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 8]   # 4 triangles valides
        payload += [999] * 15                       # remplissage après
        words = array("H", payload)
        runs = bfres_v10._segment_indices(words, [64])
        self.assertEqual(len(runs), 1)
        shape_index, start, triangles = runs[0]
        self.assertEqual(shape_index, 0)
        self.assertEqual(start, 21)                 # pas un multiple de 3 ni de 0x1000
        self.assertEqual(len(triangles), 4)

    def test_gap_inside_a_submesh_is_tolerated_by_merging(self) -> None:
        first = (0, 3, [(0, 1, 2), (2, 3, 4)])
        second = (0, 12, [(5, 6, 7)])
        merged = bfres_v10._merge_runs([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0][2]), 3)

    def test_different_shapes_are_not_merged(self) -> None:
        merged = bfres_v10._merge_runs([(0, 0, [(0, 1, 2)]), (1, 9, [(3, 4, 5)])])
        self.assertEqual(len(merged), 2)


class OutlierTests(unittest.TestCase):
    def test_far_vertex_triangle_is_dropped(self) -> None:
        positions = [(0.0, 0.0, 0.0), (0.1, 0.0, 0.0), (0.0, 0.1, 0.0),
                     (0.1, 0.1, 0.0), (50.0, 50.0, 50.0)]
        triangles = [(0, 1, 2), (1, 3, 2), (0, 1, 4)]
        kept = bfres_v10._drop_outliers(positions, triangles)
        self.assertEqual(kept, [(0, 1, 2), (1, 3, 2)])

    def test_robust_diagonal_ignores_outlier(self) -> None:
        import math
        cloud = [(math.sin(i) * 0.5, math.cos(i) * 0.5, 0.0) for i in range(400)]
        cloud.append((1000.0, 1000.0, 1000.0))
        self.assertLess(bfres_v10._diagonal(cloud), 3.0)


class StreakTests(unittest.TestCase):
    """Le rastériseur ne doit rien dessiner hors du triangle projeté."""

    def _runs(self, png: bytes, width: int, height: int) -> list[int]:
        from hookymod.util import png_bytes  # noqa: F401  (l'encodeur est testé)
        from PIL import Image

        image = Image.open(io.BytesIO(png)).convert("RGB")
        pixels = image.load()
        background = (0x14, 0x16, 0x1C)
        runs = []
        for y in range(height):
            longest = 0
            current = 0
            for x in range(width):
                if pixels[x, y] != background:
                    current += 1
                    longest = max(longest, current)
                else:
                    current = 0
            runs.append(longest)
        return runs

    def test_nearly_flat_triangle_stays_inside_its_box(self) -> None:
        # triangle de 20 cm de haut, 1 m de large, vu de face : 2 lignes max
        mesh = MeshData(name="plat",
                        positions=[(0.0, 0.0, 0.0), (1.0, 0.01, 0.0), (0.0, 0.02, 0.0)],
                        faces=[(0, 1, 2)])
        mesh.ensure_normals()
        png = render.render_png([mesh], 200, 150, yaw=0.0, pitch=0.0)
        runs = self._runs(png, 200, 150)
        # la projection occupe au plus la largeur du modèle à l'écran (~140 px) :
        # aucune ligne ne doit dépasser largement ce cadre
        self.assertLessEqual(max(runs), 160)

    def test_vertical_sliver_does_not_paint_a_band(self) -> None:
        mesh = MeshData(name="sliver",
                        positions=[(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.01, 0.5, 0.0)],
                        faces=[(0, 1, 2)])
        mesh.ensure_normals()
        png = render.render_png([mesh], 160, 160, yaw=0.0, pitch=0.0, cull=False)
        runs = self._runs(png, 160, 160)
        self.assertLessEqual(max(runs), 40)


class GeometryCacheTests(unittest.TestCase):
    def test_roundtrip_keeps_geometry(self) -> None:
        mesh = MeshData(name="part", material="Mt_Test",
                        positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                        faces=[(0, 1, 2)],
                        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                        normals=[(0.0, 0.0, 1.0)] * 3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.geo"
            size = geo_mod.write(path, [mesh], {"model": "Test"})
            self.assertGreater(size, 0)
            back, meta = geo_mod.read(path)
            self.assertEqual(meta["model"], "Test")
            self.assertEqual(len(back), 1)
            self.assertEqual(back[0].name, "part")
            self.assertEqual(back[0].material, "Mt_Test")
            self.assertEqual(back[0].faces, [(0, 1, 2)])
            self.assertAlmostEqual(back[0].positions[1][0], 1.0, places=6)
            self.assertAlmostEqual(back[0].uvs[2][1], 1.0, places=6)

    def test_corrupted_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.geo"
            path.write_bytes(b"HOOKYGEO1\x00\x00\x00\x05nope")
            self.assertIsNone(geo_mod.read(path))


class ProjectFastPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "dump"
        (root / "Model").mkdir(parents=True)
        (root / "Model" / "Modele.bfres").write_bytes(synth.make_bfres())
        self.project = Project(root, Path(self._tmp.name) / "work")
        self.project.scan()
        self.project.update_settings({"cache_mb": 0})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_second_open_uses_the_disk_cache(self) -> None:
        rel = "Model/Modele.bfres"
        first = self.project.model_info(rel)
        self.assertEqual(self.project.geo_hits, 0)
        self.project._cache.clear()
        self.project._mesh_bytes = 0
        second = self.project.model_info(rel)
        self.assertEqual(self.project.geo_hits, 1)
        self.assertEqual(first["faces"], second["faces"])
        self.assertEqual(first["vertices"], second["vertices"])

    def test_geometry_cache_is_written_next_to_the_work_dir(self) -> None:
        self.project.model_info("Model/Modele.bfres")
        stats = self.project.geo_stats()
        self.assertEqual(stats["entries"], 1)
        self.assertGreater(stats["bytes"], 0)

    def test_clearing_the_cache_removes_geometry(self) -> None:
        self.project.model_info("Model/Modele.bfres")
        self.project.clear_cache()
        self.assertEqual(self.project.geo_stats()["entries"], 0)


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        folder = Path(self._tmp.name)
        self.out = folder / "export"
        self.meshes = [_triangle_mesh(), _triangle_mesh(z=0.5, size=0.5)]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_gltf_bin_is_written_and_listed(self) -> None:
        written = blender.export_bundle(self.meshes, self.out, "modele")
        self.assertIn("bin", written)
        self.assertTrue(written["bin"].is_file())
        document = json.loads(written["gltf"].read_text())
        self.assertEqual(document["buffers"][0]["uri"], "modele.bin")
        self.assertEqual(document["buffers"][0]["byteLength"],
                         written["bin"].stat().st_size)

    def test_texture_is_linked_even_with_another_name(self) -> None:
        png = self.out / "Weapon_Sword_Kokiri_Alb.png"
        self.out.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        written = blender.export_bundle(
            self.meshes, self.out, "epee",
            textures={"Mt_Blade": str(png)})
        document = json.loads(written["gltf"].read_text())
        self.assertEqual(len(document.get("images", [])), 1)
        material = document["materials"][0]
        self.assertIn("baseColorTexture",
                      material["pbrMetallicRoughness"])

    def test_obj_and_mtl_are_fast_and_complete(self) -> None:
        import time

        big = MeshData(name="gros", positions=[], faces=[])
        for index in range(4000):
            big.positions += [(float(index), 0.0, 0.0), (float(index), 1.0, 0.0),
                              (float(index), 0.0, 1.0)]
        big.faces = [(i * 3, i * 3 + 1, i * 3 + 2) for i in range(4000)]
        started = time.perf_counter()
        path = blender.export_bundle([big], self.out, "gros")["obj"]
        elapsed = time.perf_counter() - started
        text = path.read_text()
        self.assertEqual(text.count("\nv "), 12000)
        self.assertEqual(text.count("\nf "), 4000)
        self.assertLess(elapsed, 3.0)
        self.assertTrue(path.with_suffix(".mtl").is_file())


class SelectionZipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "dump"
        (root / "Model").mkdir(parents=True)
        (root / "Model" / "Modele.bfres").write_bytes(synth.make_bfres())
        self.project = Project(root, Path(self._tmp.name) / "work")
        self.project.scan()
        self.project.update_settings({"cache_mb": 0})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_zip_contains_the_bin_file(self) -> None:
        blob = self.project.export_selection([{"kind": "model",
                                               "rel": "Model/Modele.bfres"}])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = archive.namelist()
        self.assertTrue(any(name.endswith(".gltf") for name in names))
        self.assertTrue(any(name.endswith(".bin") for name in names),
                        f"le .bin manque du ZIP : {names}")
        self.assertTrue(any(name.endswith("LISEZMOI.txt") for name in names))


if __name__ == "__main__":
    unittest.main()
