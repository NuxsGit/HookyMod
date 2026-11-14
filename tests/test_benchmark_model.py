"""Mesure d'ouverture reproductible sans écrire dans le dump."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hookymod import native
from tests import synth

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "benchmark_model.py"
COMPARE_SCRIPT = ROOT / "tools" / "compare_model.py"


class ModelBenchmarkTests(unittest.TestCase):
    def test_measures_synthetic_model_without_touching_project(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dump"
            (root / "Model").mkdir(parents=True)
            model = root / "Model" / "Test.bfres"
            content = synth.make_bfres()
            model.write_bytes(content)
            proc = subprocess.run([sys.executable, str(SCRIPT), str(root), "Model/Test.bfres",
                                   "--width", "64", "--height", "64"],
                                  capture_output=True, text=True, encoding="utf-8", timeout=60,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("cache HookyCreator vierge", proc.stdout)
            self.assertIn("Objectif de 3 s côté serveur", proc.stdout)
            self.assertEqual(model.read_bytes(), content)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["Model"])

    def test_measures_v10_with_model_dictionary(self):
        from tests.test_bfres_v10 import DataDrivenTests, make_v10

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dump"
            (root / "Model").mkdir(parents=True)
            (root / "Model" / "Test.bfres").write_bytes(make_v10(*DataDrivenTests()._grid()))
            proc = subprocess.run([sys.executable, str(SCRIPT), str(root), "Model/Test.bfres",
                                   "--width", "64", "--height", "64"],
                                  capture_output=True, text=True, encoding="utf-8", timeout=60,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("242 triangles", proc.stdout)

    @unittest.skipUnless(native.available(), "moteur Rust non compilé")
    def test_compares_model_preview_and_exports_in_isolated_caches(self):
        from tests.test_bfres_v10 import DataDrivenTests, make_v10

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dump"
            (root / "Model").mkdir(parents=True)
            model = root / "Model" / "Test.bfres"
            content = make_v10(*DataDrivenTests()._grid())
            model.write_bytes(content)
            proc = subprocess.run([sys.executable, str(COMPARE_SCRIPT), str(root),
                                   "Model/Test.bfres", "--width", "64", "--height", "64"],
                                  capture_output=True, text=True, encoding="utf-8", timeout=60,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("positions, indices, UV et normales : identique", proc.stdout)
            self.assertIn("aperçu PNG : identique", proc.stdout)
            self.assertIn("exports géométriques OBJ/glTF : identique", proc.stdout)
            self.assertEqual(model.read_bytes(), content)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["Model"])

    def test_comparison_requires_the_native_library(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = subprocess.run([sys.executable, str(COMPARE_SCRIPT), folder, "any.bfres"],
                                  capture_output=True, text=True, encoding="utf-8", timeout=60,
                                  env={**os.environ, "HOOKYMOD_DISABLE_RUST": "1",
                                       "PYTHONIOENCODING": "utf-8"})
            self.assertEqual(proc.returncode, 2)
            self.assertIn("Comparaison impossible", proc.stderr)

    def test_rejects_a_model_outside_the_project(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dump"
            root.mkdir()
            proc = subprocess.run([sys.executable, str(SCRIPT), str(root), "../autre.bfres"],
                                  capture_output=True, text=True, encoding="utf-8", timeout=60,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("modèle doit se trouver", proc.stderr)


if __name__ == "__main__":
    unittest.main()
