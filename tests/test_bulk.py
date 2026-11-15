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
from hookymod import bntx  # noqa: E402
from tests import test_bfres_v10 as v10_fixtures  # noqa: E402


def _multi_texture_bntx(names: list[str]) -> bytes:
    """BNTX synthétique avec plusieurs BRTI partageant les mêmes pixels."""
    raw = bytearray(synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))
    template = bytes(raw[0x300:0x3A0])
    struct.pack_into("<I", raw, 0x24, len(names))
    struct.pack_into("<I", raw, 0x110, len(names) + 1)
    cursor = 0x118
    positions = []
    for text in ["source.bntx", *names]:
        encoded = text.encode("utf-8")
        positions.append(cursor)
        struct.pack_into("<H", raw, cursor, len(encoded))
        raw[cursor + 2:cursor + 2 + len(encoded)] = encoded
        raw[cursor + 2 + len(encoded)] = 0
        cursor += 2 + len(encoded) + 1
        cursor += cursor % 2
    assert cursor < 0x200
    struct.pack_into("<I", raw, 0x10, positions[0])
    for index, name_pos in enumerate(positions[1:]):
        brti = 0x300 if index == 0 else 0x900 + (index - 1) * 0x100
        if len(raw) < brti + len(template):
            raw.extend(bytes(brti + len(template) - len(raw)))
        raw[brti:brti + len(template)] = template
        struct.pack_into("<Q", raw, brti + 0x5C, name_pos)
        struct.pack_into("<Q", raw, 0x200 + index * 8, brti)
    struct.pack_into("<I", raw, 0x1C, len(raw))
    return bytes(raw)


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
            model_item = next(item for item in report["items"] if item["kind"] == "model")
            self.assertTrue(any("textures non associées" in note for note in model_item["notes"]))
            self.assertTrue((Path(temp) / "out" / model_item["folder"] / "RAPPORT_MODELE.txt").is_file())

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
    def test_flat_model_uses_per_material_maps_and_keeps_warnings(self):
        from tests.test_bfres_v10_struct import structured_model

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            (root / "Model").mkdir(parents=True)
            (root / "Tex").mkdir()
            (root / "Model/Objet.bfres").write_bytes(structured_model())
            for name in ("Tex_A_Alb", "Tex_A_Nrm", "Tex_B_Alb", "Tex_B_Rgh"):
                (root / "Tex" / f"{name}.bntx").write_bytes(
                    synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))
            project = Project(root, Path(temp) / "work")
            project.scan()
            out = Path(temp) / "out"
            report = bulk.export_all(project, out, kinds=("model",))
            self.assertEqual(report["ok"], 1)
            entry = report["items"][0]
            folder = out / entry["folder"]
            doc = json.loads(next(folder.glob("*.gltf")).read_text())
            mats = {m["name"]: m for m in doc["materials"]}
            def name(material):
                tex = material["pbrMetallicRoughness"]["baseColorTexture"]["index"]
                return doc["images"][doc["textures"][tex]["source"]]["uri"]
            self.assertEqual(name(mats["Mat_A"]), "Tex_A_Alb.png")
            self.assertEqual(name(mats["Mat_B"]), "Tex_B_Alb.png")
            self.assertIn("RAPPORT_MODELE.txt", entry["files"])
            self.assertIn("LOD 0", (folder / "RAPPORT_MODELE.txt").read_text())
            self.assertTrue(any("LOD 0" in note for note in entry["notes"]))

    def test_slug_cannot_escape_output_directory(self):
        for name in ("", ".", ".."):
            self.assertEqual(bulk._slug(name), "asset")
        self.assertNotIn("/", bulk._slug("../../image"))
        self.assertNotIn("\\", bulk._slug("..\\image"))
        self.assertTrue(bulk._slug("CON").startswith("_"))
        self.assertTrue(bulk._slug("lpt1.dat").startswith("_"))
        self.assertLessEqual(len(bulk._slug("界" * 100).encode("utf-8")), 180)

    def test_sanitized_and_casefolded_asset_names_never_share_a_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            (root / "A").mkdir(parents=True)
            # Les deux chemins deviennent A_X.bntx / A_X.bfres après nettoyage.
            for relative in ("A/X.bntx", "A_X.bntx"):
                (root / relative).write_bytes(synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))
            for relative in ("A/X.bfres", "A_X.bfres"):
                (root / relative).write_bytes(synth.make_bfres())
            project = Project(root, Path(temp) / "work")
            project.scan()
            out = Path(temp) / "out"
            report = bulk.export_all(project, out)
            self.assertEqual((report["ok"], report["error"]), (4, 0), report)
            for kind in ("texture", "model"):
                entries = [entry for entry in report["items"] if entry["kind"] == kind]
                self.assertEqual(len(entries), 2)
                self.assertEqual(len({entry["folder"].casefold() for entry in entries}), 2)
                self.assertTrue(all((out / entry["folder"]).is_dir() for entry in entries))

    def test_colliding_exports_remain_owned_after_inventory_changes_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            (root / "A").mkdir(parents=True)
            for relative in ("A/X.bntx", "A_X.bntx"):
                (root / relative).write_bytes(synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))
            for relative in ("A/X.bfres", "A_X.bfres"):
                (root / relative).write_bytes(synth.make_bfres())
            work, out = Path(temp) / "work", Path(temp) / "out"
            project = Project(root, work)
            project.scan()
            first = bulk.export_all(project, out)
            by_label = {item["label"]: item for item in first["items"]}
            self.assertEqual(first["ok"], 4, first)
            retained = []
            for label in ("A/X.bntx", "A/X.bfres"):
                folder = out / by_label[label]["folder"]
                output = next(folder.glob("*.png" if label.endswith(".bntx") else "*.gltf"))
                output.write_bytes(b"ancien export a conserver")
                retained.append(output)
                (root / label).unlink()
            project.scan()
            second = bulk.export_all(project, out)
            for item in second["items"]:
                self.assertEqual(item["folder"], by_label[item["label"]]["folder"])
            for path in retained:
                self.assertEqual(path.read_bytes(), b"ancien export a conserver")
            reopened = Project(root, work)
            reopened.scan()
            third = bulk.export_all(reopened, out)
            for item in third["items"]:
                self.assertEqual(item["folder"], by_label[item["label"]]["folder"])
            for path in retained:
                self.assertEqual(path.read_bytes(), b"ancien export a conserver")

    def test_unowned_bulk_folder_and_other_project_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            (root / "X.bfres").write_bytes(synth.make_bfres())
            out = Path(temp) / "out"
            legacy = out / "models" / "X.bfres"
            legacy.mkdir(parents=True)
            previous = legacy / "X.bfres.gltf"
            previous.write_bytes(b"fichier personnel")
            project = Project(root, Path(temp) / "work")
            project.scan()
            first = bulk.export_all(project, out)
            self.assertEqual(first["ok"], 1, first)
            folder = out / first["items"][0]["folder"]
            self.assertNotEqual(folder, legacy)
            self.assertEqual(previous.read_bytes(), b"fichier personnel")
            prior = next(folder.glob("*.gltf"))
            prior.write_bytes(b"export du premier projet")
            other_root = Path(temp) / "other"
            other_root.mkdir()
            (other_root / "X.bfres").write_bytes(synth.make_bfres())
            other = Project(other_root, Path(temp) / "other-work")
            other.scan()
            second = bulk.export_all(other, out)
            self.assertEqual(second["ok"], 1, second)
            self.assertNotEqual(second["items"][0]["folder"].casefold(),
                                first["items"][0]["folder"].casefold())
            self.assertEqual(prior.read_bytes(), b"export du premier projet")
            self.assertEqual(previous.read_bytes(), b"fichier personnel")

    def test_bulk_replaces_symlinks_without_writing_through_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            model = root / "X.bfres"
            model.write_bytes(synth.make_bfres())
            project = Project(root, Path(temp) / "work")
            project.scan()
            out = Path(temp) / "out"
            first = bulk.export_all(project, out, kinds=("model",))
            self.assertEqual(first["ok"], 1, first)
            folder = out / first["items"][0]["folder"]
            gltf = next(folder.glob("*.gltf"))
            victim = Path(temp) / "important.txt"
            victim.write_bytes(b"ne pas modifier")
            gltf.unlink()
            report_path = out / "rapport.json"
            report_path.unlink()
            try:
                gltf.symlink_to(victim)
                report_path.symlink_to(victim)
            except (OSError, NotImplementedError):
                self.skipTest("liens symboliques indisponibles")
            second = bulk.export_all(project, out, kinds=("model",))
            self.assertEqual(second["ok"], 1, second)
            self.assertEqual(victim.read_bytes(), b"ne pas modifier")
            self.assertFalse(gltf.is_symlink())
            self.assertFalse(report_path.is_symlink())
            self.assertEqual(json.loads(gltf.read_text())["asset"]["version"], "2.0")

    def test_internal_texture_names_are_safe_and_unique(self):
        names = ["../../outside", "..\\..\\outside", "meme/nom", "meme\\nom",
                 ".", "..", "CON", "lpt1"]
        raw = _multi_texture_bntx(names)
        self.assertEqual([texture.name for texture in bntx.BntxFile.parse(raw).textures], names)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            original = root / "multi.bntx"
            original.write_bytes(raw)
            project = Project(root, Path(temp) / "work")
            project.scan()
            out = Path(temp) / "out"
            report = bulk.export_all(project, out, kinds=("texture",))
            self.assertEqual((report["ok"], report["error"], report["textures"]), (1, 0, len(names)), report)
            entry = report["items"][0]
            folder = out / entry["folder"]
            self.assertEqual(len(entry["files"]), len(names))
            self.assertEqual(len({name.casefold() for name in entry["files"]}), len(names))
            for name in entry["files"]:
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)
                self.assertTrue((folder / name).is_file())
                self.assertEqual((folder / name).resolve().parent, folder.resolve())
            self.assertIn("meme_nom.png", entry["files"])
            self.assertIn("meme_nom--2.png", entry["files"])
            self.assertIn("_CON.png", entry["files"])
            self.assertIn("_lpt1.png", entry["files"])
            self.assertFalse((Path(temp) / "outside.png").exists())
            self.assertEqual(original.read_bytes(), raw)

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
