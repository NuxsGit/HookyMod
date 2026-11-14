"""Les aperçus et exports doivent utiliser les modifications, pas le dump d'origine."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import zstandard

from hookycreator import blend
from hookycreator.project import Project, ProjectError
from hookymod import bntx
from hookymod.util import decode_png, png_bytes
from tests import synth
from tests.test_hookycreator_blend import triangle_blend


class WorkingCopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "dump"
        (self.root / "Tex").mkdir(parents=True)
        (self.root / "Model").mkdir()
        self.model = self.root / "Model" / "Test.bfres"
        self.model.write_bytes(synth.make_bfres())
        self.original = synth.make_bntx(32, 32, synth.rgba8_pixels(32, 32))
        (self.root / "Tex" / "Mt_Test_Alb.bntx").write_bytes(self.original)
        compressed = zstandard.ZstdCompressor().compress(self.original)
        (self.root / "Tex" / "Other.bntx.zs").write_bytes(compressed)
        self.project = Project(self.root, Path(self.tmp.name) / "work")
        self.project.scan()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def solid(r, g, b):
        return png_bytes(32, 32, bytes((r, g, b, 255)) * (32 * 32))

    def test_texture_replacement_updates_preview_sampler_and_export_without_touching_source(self):
        rel = "Tex/Mt_Test_Alb.bntx"
        old = self.project.texture_png(rel)
        old_texture_revision = self.project.texture_info(rel)["revision"]
        old_model_revision = self.project.model_info("Model/Test.bfres")["revision"]
        old_project_revision = self.project.state()["image_revision"]
        before = self.project.thumbnail_key("Model/Test.bfres", 160, 120, textured=True)
        old_sample = self.project.albedo_samplers("Model/Test.bfres")[""](0.5, 0.5)
        self.project.import_texture(rel, 0, self.solid(255, 0, 0))
        self.assertNotEqual(old_project_revision, self.project.state()["image_revision"])
        self.assertNotEqual(old_texture_revision, self.project.texture_info(rel)["revision"])
        self.assertNotEqual(old_model_revision, self.project.model_info("Model/Test.bfres")["revision"])
        current = self.project.texture_png(rel)
        self.assertNotEqual(old, current)
        self.assertEqual(decode_png(current)[2][:4], bytes((255, 0, 0, 255)))
        self.assertNotEqual(before, self.project.thumbnail_key("Model/Test.bfres", 160, 120,
                                                               textured=True))
        sample = self.project.albedo_samplers("Model/Test.bfres")[""](0.5, 0.5)
        self.assertNotEqual(old_sample, sample)
        self.assertEqual(sample, (1.0, 0.0, 0.0))
        exported = self.project.export_textures_for("Model/Test.bfres", self.project.work / "test")
        self.assertEqual(decode_png(Path(exported["Mt_Test_Alb"]).read_bytes())[2][:4],
                         bytes((255, 0, 0, 255)))
        self.assertEqual((self.root / rel).read_bytes(), self.original)
        self.assertIn(rel, self.project.state()["edited"])
        # Une deuxième édition part de la copie courante, sans toucher au dump.
        self.project.import_texture(rel, 0, self.solid(0, 255, 0))
        self.assertEqual(decode_png(self.project.texture_png(rel))[2][:4], bytes((0, 255, 0, 255)))
        self.assertEqual((self.root / rel).read_bytes(), self.original)

    def test_image_revision_changes_with_settings_and_between_server_sessions(self):
        first = self.project.state()["image_revision"]
        self.project.update_settings({"supersample": 1})
        second = self.project.state()["image_revision"]
        other = Project(self.root, self.project.work)
        other.scan()
        self.assertNotEqual(first, second)
        self.assertNotEqual(other.state()["image_revision"], second)

    def test_compressed_replacement_remains_compressed_and_is_visible(self):
        rel = "Tex/Other.bntx.zs"
        source = (self.root / rel).read_bytes()
        self.project.import_texture(rel, 0, self.solid(0, 0, 255))
        edited = (self.project.work / "assets" / rel).read_bytes()
        self.assertTrue(edited.startswith(b"\x28\xb5\x2f\xfd"))
        self.assertEqual(bntx.BntxFile.parse(zstandard.ZstdDecompressor().decompress(edited))
                         .textures[0].decode()[2][:4], bytes((0, 0, 255, 255)))
        self.assertEqual(decode_png(self.project.texture_png(rel))[2][:4], bytes((0, 0, 255, 255)))
        self.assertEqual((self.root / rel).read_bytes(), source)

    def test_copy_with_same_size_and_timestamp_still_has_a_distinct_cache_key(self):
        rel = "Tex/Mt_Test_Alb.bntx"
        first = self.project.texture_png(rel)
        src = self.root / rel
        edited = self.project.work / "assets" / rel
        edited.parent.mkdir(parents=True)
        edited.write_bytes(synth.make_bntx(32, 32, bytes((255, 0, 0, 255)) * 1024))
        stamp = src.stat().st_mtime_ns
        os.utime(edited, ns=(stamp, stamp))
        self.assertEqual(src.stat().st_size, edited.stat().st_size)
        self.assertNotEqual(first, self.project.texture_png(rel))

    def test_blend_translation_updates_model_and_state_but_not_original(self):
        rel = "Model/Test.bfres"
        original = self.model.read_bytes()
        before = self.project.model_info(rel)
        self.project.state_payload()  # remplir le cache d'état avant l'écriture
        blend.save_upload(self.project, "triangle.blend", triangle_blend())
        blend.translate(self.project, "work:triangle.blend", rel, axis="keep", fit=False)
        after = self.project.model_info(rel)
        self.assertNotEqual(before["bounds"], after["bounds"])
        self.assertEqual(after["bounds"], [2.0, 2.0, 0.0])
        self.assertEqual(self.model.read_bytes(), original)
        self.assertIn(rel.encode(), self.project.state_payload())
        self.assertIn(rel, self.project.state()["edited"])

    def test_mob_copies_current_assets_with_the_right_compression_suffix(self):
        rel = "Tex/Mt_Test_Alb.bntx"
        self.project.import_texture(rel, 0, self.solid(255, 0, 0))
        mob = self.project.create_mob("Copie", "Model/Test.bfres", [rel])
        model, texture = mob["files"]
        self.assertTrue(model["to"].endswith(".bfres"))
        self.assertTrue(texture["to"].endswith(".bntx"))
        directory = self.project.work / "mobs" / "Copie"
        self.assertEqual((directory / texture["to"]).read_bytes(),
                         (self.project.work / "assets" / rel).read_bytes())
        self.assertTrue(mob["todo"])  # la fiche n'est pas un nouvel acteur jouable

    def test_nested_custom_work_is_not_reindexed_as_source_assets(self):
        work = self.root / "scratch" / "custom-work"
        other = self.root / "scratch" / "custom-work-sibling" / "Other.bntx"
        other.parent.mkdir(parents=True)
        other.write_bytes(self.original)
        project = Project(self.root, work)
        project.scan()
        rel = "Tex/Mt_Test_Alb.bntx"
        project.import_texture(rel, 0, self.solid(255, 0, 0))
        project.create_mob("Copie", "Model/Test.bfres", [rel])
        project.scan()
        paths = [asset.rel for asset in project.assets]
        self.assertIn("scratch/custom-work-sibling/Other.bntx", paths)
        self.assertFalse(any(path.startswith("scratch/custom-work/") for path in paths))
        self.assertEqual(project.edited_files(), [rel])  # séparateurs portables, même sous Windows

    def test_work_folder_cannot_be_the_source_root(self):
        with self.assertRaises(ProjectError):
            Project(self.root, self.root)

    def test_edited_assets_root_cannot_be_a_symlink_outside_work(self):
        outside = self.root / "Other"
        outside.mkdir()
        alias = self.project.work / "assets"
        try:
            alias.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("création de liens symboliques indisponible")
        with self.assertRaises(ProjectError):
            self.project.import_texture("Tex/Mt_Test_Alb.bntx", 0, self.solid(255, 0, 0))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((self.root / "Tex" / "Mt_Test_Alb.bntx").read_bytes(), self.original)

    def test_archive_path_and_edited_destination_cannot_escape_project(self):
        sibling = self.root.with_name("dump-autre")
        sibling.mkdir()
        (sibling / "secret.pack").write_bytes(b"secret")
        with self.assertRaises(ProjectError):
            self.project.open_level("../dump-autre/secret.pack")
        # Même un chemin absolu d'asset valide ne doit jamais devenir une
        # destination absolue qui écrase le fichier du dump.
        self.project.import_texture(str(self.root / "Tex" / "Mt_Test_Alb.bntx"), 0,
                                    self.solid(255, 0, 0))
        self.assertEqual((self.root / "Tex" / "Mt_Test_Alb.bntx").read_bytes(), self.original)
        self.assertTrue((self.project.work / "assets" / "Tex" / "Mt_Test_Alb.bntx").is_file())


if __name__ == "__main__":
    unittest.main()
