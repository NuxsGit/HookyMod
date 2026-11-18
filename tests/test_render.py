"""Rendu PNG hors interface (``cli view``) et ordre d'occlusion du viewer.

Ces fonctions sont aussi la seule façon de *voir* un modèle quand Tkinter est
absent (Windows sans tcl/tk, serveur, CI) : elles doivent rester fiables.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import cli, render, util, viewer  # noqa: E402
from hookymod.bfres import BfresFile, meshes_from_file  # noqa: E402
from hookymod.mesh import MeshData, load_obj  # noqa: E402

CUBE_VERTS = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
              (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
CUBE_FACES = [(4, 5, 6), (4, 6, 7), (1, 0, 3), (1, 3, 2), (0, 4, 7), (0, 7, 3),
              (5, 1, 2), (5, 2, 6), (3, 7, 6), (3, 6, 2), (0, 1, 5), (0, 5, 4)]


def _cube() -> MeshData:
    return MeshData(positions=CUBE_VERTS, faces=CUBE_FACES)


def _overlapping_quads() -> list[MeshData]:
    """Deux quads face caméra : le rouge est devant, le bleu derrière."""
    near = MeshData(positions=[(-1, -1, 2), (1, -1, 2), (1, 1, 2), (-1, 1, 2)],
                    faces=[(0, 1, 2), (0, 2, 3)])
    far = MeshData(positions=[(-0.5, -0.5, 0), (0.5, -0.5, 0),
                              (0.5, 0.5, 0), (-0.5, 0.5, 0)],
                   faces=[(0, 1, 2), (0, 2, 3)])
    near.colors = [(1.0, 0.0, 0.0)] * 4
    far.colors = [(0.0, 0.0, 1.0)] * 4
    return [near, far]


class RenderTests(unittest.TestCase):
    def test_cube_covers_pixels_and_encodes_png(self):
        width, height, rgba = render.render_rgba([_cube()], 160, 120)
        self.assertEqual((width, height), (160, 120))
        self.assertEqual(len(rgba), 160 * 120 * 4)
        background = tuple(render.BACKGROUND[:3])
        covered = sum(1 for i in range(0, len(rgba), 4)
                      if tuple(rgba[i:i + 3]) != background)
        self.assertGreater(covered, 2000)          # le cube occupe la scène
        self.assertLess(covered, 160 * 120)        # sans remplir tout l'écran
        png = render.render_png([_cube()], 160, 120)
        w, h, decoded = util.decode_png(png)
        self.assertEqual((w, h), (160, 120))
        self.assertEqual(len(decoded), 160 * 120 * 4)

    def test_near_face_wins_over_far_face(self):
        """Régression : l'occlusion était inversée (face lointaine devant)."""
        _w, _h, rgba = render.render_rgba(_overlapping_quads(), 101, 101,
                                          yaw=0.0, pitch=0.0)
        r, g, b = rgba[(50 * 101 + 50) * 4:(50 * 101 + 50) * 4 + 3]
        self.assertGreater(r, g)
        self.assertGreater(r, b)

    def test_painter_order_goes_far_to_near(self):
        near = MeshData(positions=[(-1, -1, 2), (1, -1, 2), (1, 1, 2), (-1, 1, 2)],
                        faces=[(0, 1, 2), (0, 2, 3)])
        far = MeshData(positions=[(-0.5, -0.5, 0), (0.5, -0.5, 0),
                                  (0.5, 0.5, 0), (-0.5, 0.5, 0)],
                       faces=[(0, 1, 2), (0, 2, 3)])
        data = viewer.build_render_data([near, far])
        center, radius = viewer.fit_view(data.positions)
        scale = viewer.view_scale(200, 200, radius, 1.0)
        projector = viewer.Projector(data.positions, center, 0.0, 0.0, scale,
                                     100, 100)
        order = viewer.order_visible_faces(data.faces, projector, 10, cull=True)
        means = []
        for index in order:
            a, b, c = data.faces[index]
            means.append((projector(a)[2] + projector(b)[2] + projector(c)[2]) / 3)
        self.assertEqual(means, sorted(means))

    def test_back_faces_are_culled(self):
        data = viewer.build_render_data([_cube()])
        center, radius = viewer.fit_view(data.positions)
        scale = viewer.view_scale(200, 200, radius, 1.0)
        projector = viewer.Projector(data.positions, center, 0.6, -0.3, scale,
                                     100, 100)
        culled = viewer.order_visible_faces(data.faces, projector, 100, cull=True)
        every = viewer.order_visible_faces(data.faces, projector, 100, cull=False)
        self.assertLess(len(culled), len(every))
        self.assertEqual(len(culled), 6)      # 3 faces × 2 triangles

    def test_supersampling_keeps_the_requested_size(self):
        width, height, rgba = render.render_rgba([_cube()], 40, 30, supersample=3)
        self.assertEqual((width, height), (40, 30))
        self.assertEqual(len(rgba), 40 * 30 * 4)

    def test_turntable_width_is_views_times_width(self):
        png = render.turntable_png([_cube()], views=3, width=64, height=48)
        w, h, _rgba = util.decode_png(png)
        self.assertEqual((w, h), (192, 48))

    def test_empty_mesh_is_rejected(self):
        with self.assertRaises(Exception):
            render.render_png([MeshData()], 32, 32)

    def test_light_vector_is_shared_with_the_viewer(self):
        self.assertEqual(render.light_vector(), viewer.LIGHT)


class ViewCommandTests(unittest.TestCase):
    def test_bfres_is_rendered_to_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modele.bfres"
            path.write_bytes(synth.make_bfres())
            out = Path(tmp) / "apercu.png"
            self.assertEqual(cli.main(["view", str(path), "-o", str(out)]), 0)
            self.assertTrue(out.exists())
            width, height, _rgba = util.decode_png(out.read_bytes())
            self.assertEqual((width, height), (960, 720))

    def test_obj_is_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tri.obj"
            path.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
            out = Path(tmp) / "tri.png"
            self.assertEqual(cli.main(["view", str(path), "-o", str(out),
                                       "--size", "120x90"]), 0)
            self.assertEqual(util.decode_png(out.read_bytes())[:2], (120, 90))

    def test_turntable_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cube.obj"
            path.write_text("v -1 -1 -1\nv 1 -1 -1\nv 1 1 -1\nv -1 1 -1\n"
                            "v -1 -1 1\nv 1 -1 1\nv 1 1 1\nv -1 1 1\n"
                            "f 5 6 7\nf 5 7 8\nf 2 1 4\nf 2 4 3\nf 1 5 8\n"
                            "f 1 8 4\nf 6 2 3\nf 6 3 7\nf 4 8 7\nf 4 7 3\n"
                            "f 1 2 6\nf 1 6 5\n")
            out = Path(tmp) / "planche.png"
            self.assertEqual(cli.main(["view", str(path), "--views", "2",
                                       "--size", "80x64", "-o", str(out)]), 0)
            self.assertEqual(util.decode_png(out.read_bytes())[:2], (160, 64))

    def test_non_model_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text("bonjour")
            import contextlib
            import io
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["view", str(path)])
            self.assertEqual(code, 1)
            self.assertIn("modèle", stderr.getvalue())

    def test_size_parser(self):
        self.assertEqual(cli._parse_size("800x600"), (800, 600))
        self.assertEqual(cli._parse_size("800X600"), (800, 600))
        self.assertEqual(cli._parse_size("800, 600"), (800, 600))
        self.assertEqual(cli._parse_size("10x10"), (64, 64))
        for invalid in ("grand", "800x600x400", "800x600x", "800xx600"):
            with self.subTest(invalid=invalid), self.assertRaises(util.HookyError):
                cli._parse_size(invalid)


class BfresMeshHelperTests(unittest.TestCase):
    def test_meshes_from_file_uses_the_requested_lod(self):
        file = BfresFile.parse(synth.make_bfres())
        meshes = meshes_from_file(file, lod=0)
        self.assertTrue(meshes)
        self.assertTrue(all(mesh.faces for mesh in meshes))

    def test_meshes_from_file_rejects_empty_model(self):
        file = BfresFile()
        with self.assertRaises(Exception):
            meshes_from_file(file)

    def test_obj_loader_is_unchanged(self):
        mesh = load_obj(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        self.assertEqual(len(mesh.faces), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class CullFlagTests(unittest.TestCase):
    def test_back_faces_follow_the_cull_flag(self):
        """La face tournée vers l'arrière doit dépendre du drapeau `cull`."""
        mesh = MeshData(name="dos", positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                                               (0.0, 0.0, 1.0)],
                        faces=[(0, 1, 2)])
        with self.assertRaises(util.HookyError):
            render.render_png([mesh], 64, 64, yaw=0.0, pitch=0.0, cull=True)
        png = render.render_png([mesh], 64, 64, yaw=0.0, pitch=0.0, cull=False)
        self.assertTrue(png.startswith(b"\x89PNG"))
