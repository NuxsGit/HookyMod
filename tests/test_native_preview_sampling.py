"""Parité de la table d'albédo Rust et du chemin Python des aperçus."""
from __future__ import annotations

import random
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from hookycreator.pngread import decode_image
from hookycreator.project import Project, _make_sampler, _sampler_table_python
from hookymod import native
from tests import synth


class SamplerFallbackTests(unittest.TestCase):
    def test_python_fallback_keeps_transparent_and_small_images(self):
        pixels = bytes((255, 0, 0, 255, 0, 0, 255, 0))
        with mock.patch.object(native, "available", return_value=False):
            sample = _make_sampler((2, 1, pixels), size=2)
        self.assertEqual(sample(0, 0), (1.0, 0.0, 0.0))
        self.assertEqual(sample(1, 0), (180 / 255,) * 3)
        self.assertEqual(sample(-1, 3), (1.0, 0.0, 0.0))

    def test_albedo_uses_decoded_pixels_without_png_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dump"
            (root / "Model").mkdir(parents=True)
            (root / "Tex").mkdir()
            (root / "Model" / "Test.bfres").write_bytes(synth.make_bfres())
            (root / "Tex" / "Mt_Test_Alb.bntx").write_bytes(
                synth.make_bntx(32, 24, synth.rgba8_pixels(32, 24)))
            project = Project(root, Path(folder) / "work")
            project.scan()
            rel = "Model/Test.bfres"
            asset = project.texture_assets_for(rel)[0]
            old_image = decode_image(project.texture_png(asset.rel, 0))
            with mock.patch.object(native, "available", return_value=False):
                expected = _make_sampler(old_image)
            with mock.patch.object(project, "texture_png", side_effect=AssertionError("PNG inutile")):
                sampler = project.albedo_samplers(rel)[""]
            for u, v in ((0, 0), (0.1, 0.9), (0.5, 0.5), (1, 1)):
                self.assertEqual(sampler(u, v), expected(u, v))


@unittest.skipUnless(native.available(), "moteur Rust non compilé")
class NativeSamplerTests(unittest.TestCase):
    def test_random_dimensions_alpha_and_uneven_cells_match_python(self):
        rng = random.Random(80431)
        for width, height in ((1, 1), (2, 1), (3, 7), (17, 19), (47, 49),
                              (48, 48), (61, 33), (96, 97), (257, 13)):
            for size in (1, 2, 7, 48, 63):
                for alpha in ("random", "zero", "opaque"):
                    rgba = bytearray(rng.randbytes(width * height * 4))
                    if alpha != "random":
                        rgba[3::4] = bytes([0 if alpha == "zero" else 255]) * (width * height)
                    expected = _sampler_table_python(width, height, rgba, size)
                    with self.subTest(width=width, height=height, size=size, alpha=alpha):
                        self.assertEqual(native.preview_sample_table(rgba, width, height, size),
                                         expected)

    def test_invalid_inputs_refuse_without_allocating(self):
        for args in ((b"", 1, 1, 1), (b"\0" * 4, 0, 1, 1),
                     (b"\0" * 4, 1, 1, 0), (b"\0" * 4, 1, 1, 257),
                     (b"\0" * 4, -1, 1, 1), (b"\0" * 4, 2**32, 1, 1),
                     (b"\0" * 4, True, 1, 1), (memoryview(b"\0" * 4), 1, 1, 1)):
            with self.subTest(args=args):
                self.assertIsNone(native.preview_sample_table(*args))

    def test_parallel_calls_do_not_share_buffers(self):
        def check(seed):
            rgba = random.Random(seed).randbytes(47 * 55 * 4)
            return (native.preview_sample_table(rgba, 47, 55, 48),
                    _sampler_table_python(47, 55, rgba, 48))
        with ThreadPoolExecutor(max_workers=8) as pool:
            for actual, expected in pool.map(check, range(64)):
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
