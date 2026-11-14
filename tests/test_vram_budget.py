"""Réglages et budget mémoire des aperçus (aucun asset du jeu requis)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hookymod.mesh import MeshData

from hookycreator import vram
from hookycreator.project import Project, ProjectError
from hookycreator.settings import (Settings, SettingsError, VRAM_DEFAULT_GB,
                                   VRAM_MAX_GB, VRAM_MIN_GB)

from tests import synth


class _Counted:
    """Compte sans matérialiser : les tests de budget restent instantanés."""

    def __init__(self, count: int) -> None:
        self._count = count

    def __len__(self) -> int:
        return self._count


class FakeMesh:
    """Maillage « virtuel » de N sommets et F faces (aucune mémoire allouée)."""

    def __init__(self, faces: int, vertices: int = 3) -> None:
        self.faces = _Counted(faces)
        self.positions = _Counted(vertices)


def small_mesh(faces: int = 10) -> MeshData:
    """Un maillage minuscule mais valide, avec ``faces`` triangles."""
    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    return MeshData(name="test", positions=positions, faces=[(0, 1, 2)] * faces)


class SettingsTests(unittest.TestCase):
    def test_defaults_follow_the_6gb_rule(self) -> None:
        settings = Settings()
        self.assertEqual(settings.vram_gb, VRAM_DEFAULT_GB)
        self.assertEqual(settings.budget_bytes, int(6 * 1024 ** 3))
        self.assertEqual(VRAM_MAX_GB, 25.0)

    def test_values_are_clamped_to_the_allowed_range(self) -> None:
        self.assertEqual(Settings.from_json({"vram_gb": 99}).vram_gb, VRAM_MAX_GB)
        self.assertEqual(Settings.from_json({"vram_gb": -3}).vram_gb, VRAM_MIN_GB)
        self.assertEqual(Settings.from_json({"supersample": 12}).supersample, 4)
        self.assertEqual(Settings.from_json({"supersample": 0}).supersample, 1)

    def test_zero_or_empty_max_faces_means_automatic(self) -> None:
        for payload in ({"max_faces": 0}, {"max_faces": ""}, {"max_faces": "auto"},
                        {"max_faces": None}):
            self.assertIsNone(Settings.from_json(payload).max_faces, payload)

    def test_bad_types_are_refused_not_guessed(self) -> None:
        with self.assertRaises(SettingsError):
            Settings.from_json({"vram_gb": "beaucoup"})
        with self.assertRaises(SettingsError):
            Settings.from_json({"cull": "oui"})
        with self.assertRaises(SettingsError):
            Settings.from_json(["pas", "un", "objet"])

    def test_budget_bytes_follow_the_choice(self) -> None:
        self.assertEqual(Settings.from_json({"vram_gb": 25}).budget_bytes,
                         int(25 * 1024 ** 3))
        self.assertEqual(Settings.from_json({"vram_gb": 6}).budget_bytes,
                         int(6 * 1024 ** 3))

    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            Settings(vram_gb=12.5, supersample=3, cull=False, max_faces=50_000).save(path)
            loaded = Settings.load(path)
            self.assertEqual(loaded.vram_gb, 12.5)
            self.assertEqual(loaded.supersample, 3)
            self.assertFalse(loaded.cull)
            self.assertEqual(loaded.max_faces, 50_000)

    def test_broken_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{ pas du json")
            self.assertEqual(Settings.load(path).vram_gb, VRAM_DEFAULT_GB)
            path.write_text(json.dumps({"vram_gb": "n'importe quoi"}))
            self.assertEqual(Settings.load(path).vram_gb, VRAM_DEFAULT_GB)


class PlanTests(unittest.TestCase):
    def test_small_model_fits_without_degradation(self) -> None:
        plan = vram.plan(Settings(), [small_mesh()], 640, 480)
        self.assertTrue(plan.fits)
        self.assertEqual(plan.notes, [])
        self.assertEqual(plan.supersample, 2)

    def test_tiny_budget_lowers_antialiasing_first(self) -> None:
        settings = Settings(vram_gb=1.0, supersample=4)
        # canevas énorme : à ×4 le seul tampon (couleur + profondeur) dépasse 1 Go
        plan = vram.plan(settings, [small_mesh()], 3600, 3600)
        self.assertTrue(plan.fits)
        self.assertEqual(plan.supersample, 1, "l'anticrénelage doit baisser d'abord")
        self.assertTrue(any("anticrénelage" in note for note in plan.notes))

    def test_faces_are_limited_when_geometry_is_too_big(self) -> None:
        heavy = FakeMesh(faces=12_000_000, vertices=6_000_000)   # ≈ 1,9 Go de géométrie
        plan = vram.plan(Settings(vram_gb=1.0), [heavy], 640, 480)
        self.assertTrue(plan.fits)
        self.assertIsNotNone(plan.face_limit)
        self.assertLess(plan.face_limit, 12_000_000)
        self.assertTrue(any("faces limitées" in note for note in plan.notes))

    def test_refusal_is_explicit_when_adaptation_is_off(self) -> None:
        heavy = FakeMesh(faces=12_000_000, vertices=6_000_000)
        settings = Settings(vram_gb=1.0, adaptive=False)
        with self.assertRaises(vram.BudgetError) as caught:
            vram.plan(settings, [heavy], 640, 480)
        self.assertIn("budget", str(caught.exception))
        self.assertGreater(caught.exception.needed, caught.exception.budget)

    def test_impossible_budget_says_so(self) -> None:
        # même vide, un canevas de 12 000 px de côté ne rentre pas dans 1 Go
        with self.assertRaises(vram.BudgetError):
            vram.plan(Settings(vram_gb=1.0), [small_mesh()], 12_000, 12_000)

    def test_explicit_face_limit_is_respected(self) -> None:
        plan = vram.plan(Settings(), [small_mesh(5000)], 640, 480, face_limit=1000)
        self.assertEqual(plan.face_limit, 1000)
        self.assertLessEqual(plan.total_bytes, Settings().budget_bytes)

    def test_trim_keeps_a_coherent_subset(self) -> None:
        mesh = small_mesh(100)
        kept = vram.trim([mesh], 40)
        self.assertEqual(sum(len(m.faces) for m in kept), 40)
        for part in kept:
            self.assertEqual(len(part.positions), 3,)
            for face in part.faces:
                self.assertTrue(all(0 <= i < len(part.positions) for i in face))

    def test_trim_without_limit_returns_the_same_meshes(self) -> None:
        mesh = small_mesh(5)
        self.assertEqual(vram.trim([mesh], None), [mesh])


class ProjectSettingsTests(unittest.TestCase):
    def test_update_settings_clamps_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dump"
            root.mkdir()
            project = Project(root, Path(tmp) / "work")
            project.update_settings({"vram_gb": 40, "supersample": 9})
            self.assertEqual(project.settings.vram_gb, VRAM_MAX_GB)
            self.assertEqual(project.settings.supersample, 4)
            saved = json.loads((project.work / "settings.json").read_text())
            self.assertEqual(saved["vram_gb"], VRAM_MAX_GB)

    def test_update_settings_refuses_garbage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dump"
            root.mkdir()
            project = Project(root, Path(tmp) / "work")
            with self.assertRaises(ProjectError):
                project.update_settings({"vram_gb": "énorme"})

    def test_preview_plan_needs_a_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dump"
            (root / "Model").mkdir(parents=True)
            (root / "Model" / "Petit.bfres").write_bytes(synth.make_bfres())
            project = Project(root, Path(tmp) / "work")
            project.scan()
            plan = project.preview_plan("Model/Petit.bfres", 320, 240)
            self.assertTrue(plan["fits"])
            self.assertGreater(plan["frame_bytes"], 0)

    def test_preview_respects_a_face_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dump"
            (root / "Model").mkdir(parents=True)
            (root / "Model" / "Petit.bfres").write_bytes(synth.make_bfres())
            project = Project(root, Path(tmp) / "work")
            project.scan()
            png = project.model_preview("Model/Petit.bfres", 160, 120, face_limit=600)
            self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":               # pragma: no cover
    unittest.main()


class CullingTests(unittest.TestCase):
    def test_faces_outside_the_screen_are_dropped(self) -> None:
        from hookymod.viewer import order_visible_faces

        faces = [(0, 1, 2)]
        projected = [(500.0, 500.0, 0.0), (520.0, 500.0, 0.0), (500.0, 520.0, 0.0)]
        kept = order_visible_faces(faces, projected, 10, cull=False, min_area=0.0,
                                   screen=(100, 100))
        self.assertEqual(kept, [])
        without = order_visible_faces(faces, projected, 10, cull=False, min_area=0.0)
        self.assertEqual(len(without), 1, "sans culling, la face reste candidate")

    def test_render_without_frustum_still_works(self) -> None:
        from hookymod import render

        png = render.render_png([small_mesh(3)], 96, 72, frustum=False)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_render_accepts_both_culling_flags(self) -> None:
        from hookymod import render

        png = render.render_png([small_mesh(3)], 96, 72, cull=False, frustum=True)
        self.assertTrue(png.startswith(b"\x89PNG"))
