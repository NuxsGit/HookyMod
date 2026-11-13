"""Export en masse : contenu des packs, rapport honnête, CLI sans interface."""

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
from hookycreator import bulk  # noqa: E402
from hookycreator.project import Project  # noqa: E402


def _project() -> Project:
    root = Path(tempfile.mkdtemp())
    (root / "Model").mkdir()
    (root / "Model" / "Bon.bfres").write_bytes(synth.make_bfres())
    (root / "Model" / "Casse.bfres").write_bytes(b"FRES" + b"\x00" * 40)
    (root / "Model" / "A.anim.bfres").write_bytes(b"FRES" + b"FSKA" + b"\x00" * 64)
    (root / "Tex.pack").write_bytes(synth.make_sarc([
        ("A.bntx", synth.make_bntx(32, 32, synth.rgba8_pixels(32, 32))),
        ("note.txt", "ignore".encode()),
    ]))
    project = Project(root)
    project.scan()
    return project


class DiscoveryTests(unittest.TestCase):
    def test_pack_is_opened_and_entries_are_classified(self):
        project = _project()
        items = bulk.discover(project)
        labels = {item.label: item.kind for item in items}
        self.assertEqual(labels["Tex.pack!/A.bntx"], "texture")
        self.assertEqual(labels["Model/Bon.bfres"], "model")
        self.assertNotIn("Tex.pack!/note.txt", labels)

    def test_entry_is_read_through_the_archive(self):
        project = _project()
        self.assertEqual(project.read_asset("Tex.pack!/A.bntx")[:4], b"BNTX")

    def test_unknown_entry_is_reported(self):
        project = _project()
        with self.assertRaises(Exception):
            project.read_asset("Tex.pack!/absent.bntx")

    def test_animations_are_recognised_by_name(self):
        project = _project()
        kinds = {item.label: item.kind for item in bulk.discover(project)}
        self.assertEqual(kinds["Model/A.anim.bfres"], "animation")


class ExportTests(unittest.TestCase):
    def test_report_counts_and_files(self):
        project = _project()
        out = project.root / "export"
        report = bulk.export_all(project, out)
        self.assertEqual(report["error"], 0)
        self.assertGreaterEqual(report["ok"], 2)
        self.assertTrue((out / "rapport.json").is_file())
        self.assertTrue(any((out / "textures").rglob("*.png")))
        self.assertTrue(any((out / "models").rglob("*.gltf")))
        self.assertTrue(any((out / "models").rglob("*.obj")))

    def test_broken_asset_is_skipped_with_a_reason(self):
        project = _project()
        report = bulk.export_all(project, project.root / "export")
        broken = [i for i in report["items"] if "Casse" in i["label"]]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0]["status"], "skip")
        self.assertTrue(broken[0]["reason"])

    def test_animations_are_skipped_not_faked(self):
        project = _project()
        report = bulk.export_all(project, project.root / "export",
                                 kinds=("texture", "model", "animation"))
        animation = [i for i in report["items"] if i["kind"] == "animation"]
        self.assertEqual(len(animation), 1)
        self.assertEqual(animation[0]["status"], "skip")
        self.assertIn("animation", animation[0]["reason"].lower())

    def test_limit_stops_early(self):
        project = _project()
        report = bulk.export_all(project, project.root / "export", limit=1)
        self.assertEqual(report["started"], 1)

    def test_progress_is_reported(self):
        project = _project()
        seen = []
        bulk.export_all(project, project.root / "export",
                        progress=lambda done, total, label: seen.append((done, total)))
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], seen[-1][1])      # le dernier appel est complet

    def test_manifest_is_valid_json(self):
        project = _project()
        out = project.root / "export"
        bulk.export_all(project, out)
        report = json.loads((out / "rapport.json").read_text())
        self.assertIn("items", report)
        self.assertEqual(len(report["items"]), report["started"])


class CliTests(unittest.TestCase):
    def test_export_all_command(self):
        project = _project()
        from hookycreator.__main__ import main
        out = project.root / "cli_export"
        code = main(["export-all", str(project.root), "-o", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue((out / "rapport.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
