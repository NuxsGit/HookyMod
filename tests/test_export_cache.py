"""Le cache d'export accélère les répétitions sans figer des fichiers périmés."""
from __future__ import annotations

import os
import struct
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import patch

from hookycreator import project as project_mod
from hookymod import blender, util
from tests import synth
from tests.test_ensembles_blend import _make_project


class ExportCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = _make_project(Path(self.tmp.name), animations=False)
        self.rel = "Model/Heros.bfres"

    def archive(self, result):
        token = urllib.parse.parse_qs(urllib.parse.urlparse(result["download_url"]).query)["id"][0]
        return zipfile.ZipFile(self.project.export_download(token))

    def replace_source(self, rel, data):
        source = self.project.root / rel
        before = source.stat()
        replacement = source.with_name(source.name + ".temp")
        replacement.write_bytes(data)
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, source)
        self.assertEqual((source.stat().st_size, source.stat().st_mtime_ns),
                         (before.st_size, before.st_mtime_ns))

    def test_cached_archive_and_folder_are_exact_and_freshly_delivered(self):
        with patch.object(blender, "export_bundle", wraps=blender.export_bundle) as build:
            first = self.project.export_model(self.rel, archive=True)
            folder = Path(first["absolute_folder"])
            with self.archive(first) as zipped:
                original = {name: zipped.read(name) for name in zipped.namelist()}
                self.assertEqual(zipped.testzip(), None)
                for info in zipped.infolist():
                    if info.filename.endswith(".png"):
                        self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            self.assertGreater(self.project._export_cache_bytes, 0)
            # Ne pas réutiliser le dossier utilisateur, qui peut avoir été modifié.
            (folder / "Heros.gltf").write_bytes(b"corrupted by user")
            (folder / "Heros.bin").unlink()
            (folder / "unrelated.txt").write_text("keep", encoding="utf-8")
            second = self.project.export_model(self.rel, archive=True)
            self.assertEqual(build.call_count, 1, "un export identique est réutilisé")
            self.assertNotEqual(first["download_url"], second["download_url"])
            self.assertIn("unrelated.txt", second["stale_files"])
            with self.archive(second) as zipped:
                self.assertEqual({name: zipped.read(name) for name in zipped.namelist()}, original)
                self.assertEqual(zipped.testzip(), None)
            for name, data in original.items():
                self.assertEqual((folder / Path(name).name).read_bytes(), data)
            self.assertEqual((folder / "unrelated.txt").read_text(encoding="utf-8"), "keep")
            # La modification des métadonnées renvoyées ne modifie pas le cache.
            second["maps"].clear()
            third = self.project.export_model(self.rel, archive=True)
            self.assertTrue(third["maps"])
            self.assertEqual(build.call_count, 1)

    def test_replaced_texture_and_model_invalidate_even_with_same_size_and_mtime(self):
        with patch.object(blender, "export_bundle", wraps=blender.export_bundle) as build:
            first = self.project.export_model(self.rel, archive=True)
            with self.archive(first) as zipped:
                old_map = zipped.read("Heros/Heros_Alb.png")
                old_binary = zipped.read("Heros/Heros.bin")
            pixels = bytes((255, 9, 44, 255)) * 64
            self.replace_source("Model/Heros_Alb.bntx", synth.make_bntx(8, 8, pixels))
            second = self.project.export_model(self.rel, archive=True)
            self.assertEqual(build.call_count, 2)
            with self.archive(second) as zipped:
                new_map = zipped.read("Heros/Heros_Alb.png")
            self.assertNotEqual(new_map, old_map)
            self.assertEqual(util.decode_png(new_map)[2][:4], bytes((255, 9, 44, 255)))
            modified = bytearray(synth.make_bfres())
            struct.pack_into("<f", modified, 0x800 + 8 + 12, 2.0)
            self.replace_source(self.rel, modified)
            third = self.project.export_model(self.rel, archive=True)
            self.assertEqual(build.call_count, 3)
            with self.archive(third) as zipped:
                self.assertNotEqual(zipped.read("Heros/Heros.bin"), old_binary)

    def test_incomplete_export_is_not_cached_and_can_recover(self):
        original = self.project.texture_png
        def fail_once(rel, index=0, *args, **kwargs):
            if rel.endswith("Heros_Alb.bntx"):
                raise ValueError("problème temporaire")
            return original(rel, index, *args, **kwargs)
        with patch.object(blender, "export_bundle", wraps=blender.export_bundle) as build:
            with patch.object(self.project, "texture_png", side_effect=fail_once):
                first = self.project.export_model(self.rel, archive=True)
            self.assertTrue(any("problème temporaire" in warning for warning in first["warnings"]))
            self.assertEqual(self.project._export_cache_bytes, 0)
            second = self.project.export_model(self.rel, archive=True)
            self.assertEqual(build.call_count, 2)
            self.assertGreater(second["textures"], first["textures"])

    def test_bounded_cache_clear_and_scan_invalidate_bundles(self):
        with patch.object(project_mod, "EXPORT_CACHE_BYTES", 1):
            result = self.project.export_model(self.rel, archive=True)
            self.assertEqual(self.project._export_cache_bytes, 0)
            with self.archive(result) as zipped:
                self.assertIsNone(zipped.testzip())
        self.project.export_model(self.rel, archive=True)
        self.assertGreater(self.project._export_cache_bytes, 0)
        self.project.clear_cache()
        self.assertEqual(self.project._export_cache_bytes, 0)
        self.project.export_model(self.rel, archive=True)
        self.project.scan()
        self.assertEqual(self.project._export_cache_bytes, 0)



if __name__ == "__main__":
    unittest.main()
