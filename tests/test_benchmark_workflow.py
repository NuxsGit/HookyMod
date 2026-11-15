"""Le benchmark utilise le vrai sidecar HTTP et vérifie le ZIP livré."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import synth

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "benchmark_workflow.py"


class WorkflowBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "dump"
        (self.root / "Model").mkdir(parents=True)
        (self.root / "Model" / "Heros.bfres").write_bytes(synth.make_bfres())
        (self.root / "Model" / "Heros_Alb.bntx").write_bytes(
            synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8)))

    def invoke(self, *args):
        return subprocess.run([sys.executable, str(TOOL), str(self.root), *args], cwd=ROOT,
                              env={**os.environ, "PYTHONUTF8": "1"},
                              capture_output=True, text=True, timeout=120)

    def test_startup_cold_and_warm_export_include_real_zip(self):
        result = self.invoke("Model/Heros.bfres", "--repeat", "2", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["assets"], 2)
        self.assertGreater(report["startup"]["process_to_state_seconds"], 0)
        self.assertEqual(len(report["exports"]), 2)
        for entry in report["exports"]:
            self.assertEqual(entry["model"], "Model/Heros.bfres")
            self.assertGreater(entry["zip_bytes"], 100)
            self.assertEqual(entry["faces"], 1)
            self.assertEqual(entry["maps"], 1)
            self.assertGreaterEqual(entry["total_seconds"], entry["post_seconds"])
        self.assertFalse((self.root / "hookycreator_work").exists())

    @unittest.skipUnless(shutil.which("blender"), "Blender non installé")
    def test_timing_with_real_blender_reports_a_real_blend(self):
        result = self.invoke("Model/Heros.bfres", "--repeat", "1", "--blend", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(result.stdout)["exports"][0]
        self.assertEqual(entry["method"], "blender", entry["message"])
        print(f"Export réel .blend (1 triangle synthétique, HTTP + ZIP) : {entry['total_seconds']:.3f} s")

    def test_invalid_or_outside_model_fails_without_creating_work_in_dump(self):
        for path in ("missing.bfres", "../outside.bfres"):
            with self.subTest(path=path):
                result = self.invoke(path)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Mesure impossible", result.stderr)
        self.assertFalse((self.root / "hookycreator_work").exists())


if __name__ == "__main__":
    unittest.main()
