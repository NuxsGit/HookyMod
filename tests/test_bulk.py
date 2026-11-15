"""Export en masse : contenu des packs, rapport honnête, CLI sans interface."""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402
from hookycreator import bulk  # noqa: E402
from hookycreator.project import Project, ProjectError  # noqa: E402
from tests import test_bfres_v10 as v10_fixtures  # noqa: E402


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

    def test_nested_compressed_model_and_texture_can_be_exported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "dump"
            root.mkdir()
            model = bytearray(v10_fixtures.make_v10(*v10_fixtures.DataDrivenTests()._grid()))
            struct.pack_into("<Q", model, 0x30, 0)
            model = bytes(model)
            texture = synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8))
            dictionary = synth.train_dict([b"asset %d" % i * 40 for i in range(64)], 1024)
            (root / "Pack").mkdir()
            dictionary_pack = root / "Pack" / "ZsDic.pack.zs"
            dictionary_pack.write_bytes(synth.compress(synth.make_sarc([("zs.zsdic", dictionary)])))
            compressed_model = synth.compress(model, dictionary)
            inner = synth.make_sarc([
                ("Model/Valide.bfres.zs", compressed_model),
                ("Tex/Valide.bntx.zs", synth.compress(texture)),
            ])
            (root / "Assets.pack").write_bytes(synth.make_sarc([("Packs/Inner.pack", inner)]))
            project = Project(root, Path(temp) / "work")
            project.scan()
            prefix = "Assets.pack!/Packs/Inner.pack!/"
            self.assertEqual(project.read_entry(prefix + "Model/Valide.bfres.zs"), compressed_model)
            self.assertEqual(project.read_asset(prefix + "Model/Valide.bfres.zs"), model)
            self.assertEqual(project.read_asset(prefix + "Tex/Valide.bntx.zs"), texture)
            report = bulk.export_all(project, Path(temp) / "out")
            self.assertEqual((report["ok"], report["error"], report["skip"]), (2, 0, 0))
            self.assertTrue(any((Path(temp) / "out" / "models").rglob("*.gltf")))
            self.assertTrue(any((Path(temp) / "out" / "models").rglob("*.obj")))
            self.assertTrue(any((Path(temp) / "out" / "textures").rglob("*.png")))

            # Ancien séparateur « / » entre deux archives : le garder lisible.
            (root / "Legacy.pack").write_bytes(synth.make_sarc([("Inner.pack", inner)]))
            self.assertEqual(project.read_asset("Legacy.pack!/Inner.pack/Model/Valide.bfres.zs"),
                             model)

            dictionary_pack.unlink()
            without_dict = Project(root, Path(temp) / "work-without-dict")
            with self.assertRaisesRegex(ProjectError, "dictionnaire zstd"):
                without_dict.read_asset(prefix + "Model/Valide.bfres.zs")

    def test_truncated_zstd_in_a_pack_is_reported_without_returning_partial_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "dump"
            (root / "Pack").mkdir(parents=True)
            model = v10_fixtures.make_v10(*v10_fixtures.DataDrivenTests()._grid())
            dictionary = synth.train_dict([b"asset %d" % i * 40 for i in range(64)], 1024)
            (root / "Pack" / "ZsDic.pack.zs").write_bytes(
                synth.compress(synth.make_sarc([("zs.zsdic", dictionary)])))
            plain_frame = synth.compress(model)
            dict_frame = synth.compress(model, dictionary)
            (root / "Assets.pack").write_bytes(synth.make_sarc([
                ("Model/Tronque.bfres.zs", plain_frame[:-4]),
                ("Model/Tronque_Avec_Dict.bfres.zs", dict_frame[:-4]),
            ]))
            project = Project(root, Path(temp) / "work")
            project.scan()
            for name, raw in (("Tronque.bfres.zs", plain_frame[:-4]),
                              ("Tronque_Avec_Dict.bfres.zs", dict_frame[:-4])):
                with self.subTest(name=name):
                    rel = f"Assets.pack!/Model/{name}"
                    self.assertEqual(project.read_entry(rel), raw)
                    with self.assertRaisesRegex(ProjectError, "zstd") as caught:
                        project.read_asset(rel)
                    self.assertIn(name, str(caught.exception))
            report = bulk.export_all(project, Path(temp) / "out")
            self.assertEqual(report["ok"], 0)
            self.assertEqual(report["error"], 2)
            self.assertTrue(all("zstd" in item["reason"].lower() for item in report["items"]))

    def test_auxiliary_bfres_in_packs_are_only_reported_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "dump"
            root.mkdir()
            auxiliary = b"FRES" + b"FSKA" + bytes(64)
            inner = synth.make_sarc([("A.Skeleton.bfres.zs", synth.compress(auxiliary))])
            (root / "Assets.pack").write_bytes(synth.make_sarc([
                ("A.AnIm.BFRES", auxiliary),
                ("Inner.pack", inner),
                ("Camera_Prop.Camera_Prop.bfres", synth.make_bfres()),
            ]))
            project = Project(root, Path(temp) / "work")
            project.scan()
            kinds = {item.label: item.kind for item in bulk.discover(project)}
            self.assertEqual(kinds["Assets.pack!/A.AnIm.BFRES"], "animation")
            self.assertEqual(kinds["Assets.pack!/Inner.pack!/A.Skeleton.bfres.zs"], "auxiliary")
            self.assertEqual(kinds["Assets.pack!/Camera_Prop.Camera_Prop.bfres"], "model")

            default = bulk.export_all(project, Path(temp) / "out-default")
            self.assertFalse(any(item["kind"] in ("animation", "auxiliary")
                                 for item in default["items"]))
            report = bulk.export_all(project, Path(temp) / "out-aux",
                                     kinds=("animation", "auxiliary"))
            self.assertEqual(report["started"], 2)
            self.assertEqual(report["skip"], 2)
            self.assertTrue(all("auxiliaire" in item["reason"] for item in report["items"]))


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
