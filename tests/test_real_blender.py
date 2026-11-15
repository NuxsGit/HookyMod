"""Intégration facultative avec un vrai exécutable Blender, jamais un stub."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_ensembles_blend import _make_project  # noqa: E402
from hookymod import blendfile  # noqa: E402

BLENDER = os.environ.get("HOOKY_TEST_BLENDER") or shutil.which("blender")


@unittest.skipUnless(BLENDER, "Blender non installé ; intégration réelle facultative")
class RealBlenderTests(unittest.TestCase):
    def test_export_opens_in_blender_with_mesh_and_packed_images(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hooky-real-blender-") as tmp:
            project = _make_project(Path(tmp), animations=False)
            with patch.object(blendfile, "find_blender", return_value=Path(BLENDER)):
                result = project.export_blend("Model/Heros.bfres", archive=True)
            self.assertEqual(result["method"], "blender", result["message"] + "\n" + result["output"])
            folder = Path(result["absolute_folder"])
            blend = folder / "Heros.blend"
            self.assertTrue(blend.is_file())
            self.assertGreater(blend.stat().st_size, 1024)
            token = result["download_url"].split("=", 1)[1]
            with zipfile.ZipFile(project.export_download(token)) as archive:
                self.assertEqual(archive.read("Heros/Heros.blend"), blend.read_bytes())
                self.assertIn("Heros/Heros_Alb.png", archive.namelist())
            expression = (
                "import bpy, json; "
                "meshes=[o for o in bpy.data.objects if o.type=='MESH']; "
                "images=[i for i in bpy.data.images if i.name.startswith('Heros_')]; "
                "print('HOOKY_VERIFY=' + json.dumps({"
                "'objects':len(meshes), 'faces':sum(len(o.data.polygons) for o in meshes), "
                "'images':[(i.name, bool(i.packed_file)) for i in images]}))"
            )
            completed = subprocess.run(
                [BLENDER, "--background", "--factory-startup", str(blend),
                 "--python-exit-code", "7", "--python-expr", expression],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            line = next((line for line in completed.stdout.splitlines() if line.startswith("HOOKY_VERIFY=")), "")
            self.assertTrue(line, completed.stdout + completed.stderr)
            verification = json.loads(line.partition("=")[2])
            self.assertGreater(verification["objects"], 0)
            self.assertGreater(verification["faces"], 0)
            self.assertEqual(len(verification["images"]), 2)
            self.assertTrue(all(image[1] for image in verification["images"]))


if __name__ == "__main__":
    unittest.main()
