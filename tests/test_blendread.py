"""Lecteur de fichiers .blend (hookymod.blendread)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth_blend  # noqa: E402

from hookymod import blendread  # noqa: E402


class ClassicLayoutTests(unittest.TestCase):
    """mvert / mpoly / mloop — Blender 2.5 à 3.6."""

    def setUp(self) -> None:
        self.data = synth_blend.make_blend([synth_blend.cube(2.0)], version="300")

    def test_cube_is_read_with_its_quads_triangulated(self) -> None:
        result = blendread.read_blend(self.data)
        self.assertEqual(len(result.objects), 1)
        self.assertEqual(result.objects[0].name, "Cube")
        self.assertEqual(len(result.meshes), 1)
        # 6 quads -> 12 triangles
        self.assertEqual(result.faces, 12)
        self.assertEqual(result.vertices, 8)

    def test_axis_conversion_turns_blender_z_into_zelda_y(self) -> None:
        result = blendread.read_blend(self.data, axis="y_up")
        low = min(point[1] for point in result.meshes[0].positions)
        self.assertAlmostEqual(low, -1.0, places=5)
        kept = blendread.read_blend(self.data, axis="keep")
        # Blender : (x, y, z) -> Zelda : (x, z, -y)
        source = kept.meshes[0].positions[0]
        self.assertEqual(result.meshes[0].positions[0],
                         (round(source[0], 6), round(source[2], 6), round(-source[1], 6)))

    def test_object_matrix_is_applied(self) -> None:
        matrix = (1.0, 0.0, 0.0, 10.0,
                  0.0, 1.0, 0.0, 0.0,
                  0.0, 0.0, 1.0, 0.0,
                  0.0, 0.0, 0.0, 1.0)
        obj = dict(synth_blend.cube(2.0), matrix=matrix)
        result = blendread.read_blend(synth_blend.make_blend([obj]), axis="keep")
        self.assertAlmostEqual(result.meshes[0].positions[0][0], 9.0, places=5)

    def test_scale_option_multiplies_coordinates(self) -> None:
        result = blendread.read_blend(self.data, axis="keep", scale=3.0)
        self.assertAlmostEqual(result.meshes[0].positions[0][0], -3.0, places=5)

    def test_material_name_is_kept(self) -> None:
        obj = dict(synth_blend.cube(1.0), material="M_Bois")
        result = blendread.read_blend(synth_blend.make_blend([obj], materials=("M_Bois",)))
        self.assertEqual(result.materials, ["M_Bois"])

    def test_two_objects_are_both_read(self) -> None:
        first = dict(synth_blend.cube(1.0), name="Tete")
        second = dict(synth_blend.cube(1.0), name="Queue")
        result = blendread.read_blend(synth_blend.make_blend([first, second]))
        self.assertEqual([obj.name for obj in result.objects], ["Tete", "Queue"])
        self.assertEqual(result.faces, 24)

    def test_empty_object_is_reported_not_fatal(self) -> None:
        empty = {"name": "Vide", "positions": [], "faces": []}
        result = blendread.read_blend(
            synth_blend.make_blend([synth_blend.cube(1.0), empty]))
        self.assertEqual(len(result.objects), 2)
        self.assertEqual(result.objects[1].faces if hasattr(result.objects[1], "faces") else 0, 0)
        self.assertTrue(result.objects[1].reason)
        self.assertEqual(result.faces, 12)

    def test_only_filter_keeps_one_object(self) -> None:
        first = dict(synth_blend.cube(1.0), name="Tete")
        second = dict(synth_blend.cube(1.0), name="Queue")
        result = blendread.read_blend(synth_blend.make_blend([first, second]), only="tete")
        self.assertEqual(len(result.meshes), 1)
        self.assertEqual(result.objects[0].name, "Tete")

    def test_json_report_is_complete(self) -> None:
        payload = blendread.read_blend(self.data).as_json()
        self.assertEqual(payload["version"], "3.00")
        self.assertEqual(payload["faces"], 12)
        self.assertEqual(payload["meshes"], 1)
        self.assertIn("objects", payload)


class ModernLayoutTests(unittest.TestCase):
    """Attributs CustomData + face_offset_indices — Blender 4.1+."""

    def test_modern_file_is_read_the_same_way(self) -> None:
        data = synth_blend.make_blend([synth_blend.cube(2.0)], modern=True, version="401")
        result = blendread.read_blend(data)
        self.assertEqual(result.faces, 12)
        self.assertEqual(result.vertices, 8)
        self.assertEqual(result.version, "4.01")
        self.assertAlmostEqual(max(abs(p[0]) for p in result.meshes[0].positions), 1.0,
                              places=5)

    def test_modern_and_classic_agree(self) -> None:
        classic = blendread.read_blend(synth_blend.make_blend([synth_blend.cube(2.0)]))
        modern = blendread.read_blend(
            synth_blend.make_blend([synth_blend.cube(2.0)], modern=True, version="401"))
        self.assertEqual(classic.faces, modern.faces)
        for a, b in zip(sorted(classic.meshes[0].positions), sorted(modern.meshes[0].positions)):
            for left, right in zip(a, b):
                self.assertAlmostEqual(left, right, places=5)


class RobustnessTests(unittest.TestCase):
    def test_zstd_compressed_file_is_inflated(self) -> None:
        data = synth_blend.make_blend([synth_blend.cube(1.0)], compress=True)
        self.assertTrue(data.startswith(b"\x28\xb5\x2f\xfd"))
        result = blendread.read_blend(data)
        self.assertEqual(result.faces, 12)

    def test_garbage_is_refused_with_a_clear_message(self) -> None:
        with self.assertRaises(blendread.BlendError) as caught:
            blendread.read_blend(b"pas un blend du tout")
        self.assertIn(".blend", str(caught.exception))

    def test_truncated_file_is_refused(self) -> None:
        data = synth_blend.make_blend([synth_blend.cube(1.0)])[:200]
        with self.assertRaises(blendread.BlendError):
            blendread.read_blend(data)

    def test_unknown_axis_is_refused(self) -> None:
        data = synth_blend.make_blend([synth_blend.cube(1.0)])
        with self.assertRaises(blendread.BlendError):
            blendread.read_blend(data, axis="penche")

    def test_is_blend_recognises_both_forms(self) -> None:
        self.assertTrue(blendread.is_blend(synth_blend.make_blend([synth_blend.cube(1.0)])))
        self.assertFalse(blendread.is_blend(b"\x89PNG\r\n\x1a\n"))

    def test_file_without_geometry_says_why(self) -> None:
        only_empty = [{"name": "Vide1", "positions": [], "faces": []}]
        with self.assertRaises(blendread.BlendError) as caught:
            blendread.read_blend(synth_blend.make_blend(only_empty))
        self.assertIn("Aucune géométrie", str(caught.exception))

    def test_degnerate_faces_are_dropped_with_a_warning(self) -> None:
        obj = {"name": "Bancal", "positions": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                                               (0.0, 1.0, 0.0)],
               "faces": [(0, 0, 1), (0, 1, 2), (0, 1, 99)]}
        result = blendread.read_blend(synth_blend.make_blend([obj]))
        self.assertEqual(result.faces, 1)
        self.assertTrue(any("écartée" in note for note in result.warnings))


if __name__ == "__main__":               # pragma: no cover
    unittest.main()
