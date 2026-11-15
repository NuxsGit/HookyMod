"""Livraison d'un export individuel : fichiers réels, ZIP isolé et HTTP."""
from __future__ import annotations

import concurrent.futures
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402
from test_ensembles_blend import _make_project  # noqa: E402
from hookycreator.project import Asset, Project, ProjectError  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402
from hookymod import blendfile  # noqa: E402


class ExportDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.project = _make_project(self.directory, animations=False)
        self.rel = "Model/Heros.bfres"

    def archive(self, result: dict) -> zipfile.ZipFile:
        token = urllib.parse.parse_qs(urllib.parse.urlparse(result["download_url"]).query)["id"][0]
        return zipfile.ZipFile(self.project.export_download(token))

    def test_complete_archive_contains_only_new_files_and_working_gltf_refs(self) -> None:
        folder = self.project.work / "export" / "Heros"
        folder.mkdir(parents=True)
        (folder / "old.png").write_bytes(b"old")
        (folder / "Heros.blend").write_bytes(b"old blend")
        result = self.project.export_model(self.rel, archive=True)
        self.assertTrue(Path(result["absolute_folder"]).is_absolute())
        self.assertEqual(result["textures"], len(result["maps"]))
        self.assertIn("old.png", result["stale_files"])
        self.assertIn("Heros.blend", result["stale_files"])
        self.assertTrue((folder / "old.png").is_file(), "ne jamais supprimer un fichier utilisateur")
        with self.archive(result) as zipped:
            names = zipped.namelist()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual(set(names), {f"Heros/{name}" for name in result["files"]})
            self.assertNotIn("Heros/old.png", names)
            self.assertNotIn("Heros/Heros.blend", names)
            doc = json.loads(zipped.read("Heros/Heros.gltf"))
            binary = doc["buffers"][0]
            self.assertEqual(binary["uri"], "Heros.bin")
            self.assertEqual(len(zipped.read("Heros/Heros.bin")), binary["byteLength"])
            self.assertIn(b"\nv ", b"\n" + zipped.read("Heros/Heros.obj"))
            for image in doc.get("images", []):
                self.assertTrue(zipped.read("Heros/" + image["uri"]).startswith(b"\x89PNG\r\n\x1a\n"))
            for item in result["maps"]:
                self.assertTrue(zipped.read("Heros/" + item["file"]).startswith(b"\x89PNG"))

    def test_disabled_textures_and_absent_blender_do_not_claim_stale_files(self) -> None:
        folder = self.project.work / "export" / "Heros"
        folder.mkdir(parents=True)
        (folder / "Heros.blend").write_bytes(b"previous")
        (folder / "Heros_Alb.png").write_bytes(b"previous")
        with patch.object(blendfile, "find_blender", return_value=None):
            result = self.project.export_blend(self.rel, textures=False, archive=True)
        self.assertEqual(result["method"], "script")
        self.assertEqual(result["blend"], "")
        self.assertEqual(result["textures"], 0)
        self.assertEqual(result["maps"], [])
        self.assertNotIn("model-export-", result["command"])
        self.assertIn(str(folder), result["command"])
        with self.archive(result) as zipped:
            names = zipped.namelist()
            self.assertIn("Heros/creer_blend.py", names)
            self.assertNotIn("Heros/Heros.blend", names)
            self.assertFalse(any(name.endswith(".png") for name in names))
            self.assertNotIn("assembler_shaders.py", zipped.read("Heros/TEXTURES.md").decode())
            doc = json.loads(zipped.read("Heros/Heros.gltf"))
            self.assertFalse(doc.get("images"))

    def test_concurrent_exports_do_not_share_textures_or_archives(self) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(self.project.export_model, self.rel, flag, archive=True)
                       for flag in (True, False, True, False)]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(len({r["download_url"] for r in results}), 4)
        for flag, result in zip((True, False, True, False), results):
            with self.archive(result) as zipped:
                names = zipped.namelist()
                self.assertEqual(any(name.endswith(".png") for name in names), flag)
                self.assertEqual(set(names), {f"Heros/{name}" for name in result["files"]})

    def test_homonymous_models_and_selection_entries_do_not_collide(self) -> None:
        other = self.project.root / "Elsewhere" / "Heros.bfres"
        other.parent.mkdir()
        other.write_bytes(synth.make_bfres())
        self.project.scan()
        one = self.project.export_model(self.rel, archive=True)
        two = self.project.export_model("Elsewhere/Heros.bfres", archive=True)
        self.assertNotEqual(one["absolute_folder"].casefold(), two["absolute_folder"].casefold())
        zipped = self.project.export_selection([
            {"kind": "model", "rel": self.rel},
            {"kind": "model", "rel": "Elsewhere/Heros.bfres"},
            {"kind": "model", "rel": self.rel},
        ])
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual(len([name for name in names if name.endswith(".gltf")]), 2)

    def test_multiple_maps_with_same_stem_are_distinct_and_invalid_one_skipped(self) -> None:
        assets = [Asset("A/Heros_Alb.0.bntx", "texture", 1),
                  Asset("B/Heros_Alb.1.bntx", "texture", 1),
                  Asset("C/Heros_Alb.2.bntx", "texture", 1)]
        def png(rel: str, _index: int) -> bytes:
            if rel.startswith("A/"):
                raise ProjectError("unsupported")
            return (b"first" if rel.startswith("B/") else b"second")
        folder = self.directory / "maps"
        with patch.object(self.project, "texture_maps_for", return_value={"albedo": assets}), \
             patch.object(self.project, "texture_png", side_effect=png):
            result = self.project.export_maps_for(self.rel, folder)
            index = self.project._albedo_index(self.rel, folder, result["files"])
        self.assertEqual(result["textures"], 2)
        self.assertEqual(len({item["file"].casefold() for item in result["files"]}), 2)
        self.assertEqual({(folder / item["file"]).read_bytes() for item in result["files"]},
                         {b"first", b"second"})
        self.assertIn("unsupported", " ".join(result["warnings"]))
        self.assertTrue(index)

    def test_download_tokens_are_confined_and_old_temp_archives_pruned(self) -> None:
        directory = self.project.work / "cache" / "downloads"
        directory.mkdir()
        old = directory / ("a" * 32 + ".zip")
        old.write_bytes(b"temporary")
        os.utime(old, (time.time() - 8 * 86400,) * 2)
        unrelated = directory / "user.zip"
        unrelated.write_bytes(b"untouched")
        result = self.project.export_model(self.rel, archive=True)
        self.assertFalse(old.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(self.archive(result).close() is None)
        for token in ("../user", "a" * 31, "A" * 32, "0" * 32):
            with self.subTest(token=token), self.assertRaises(ProjectError):
                self.project.export_download(token)


class AssetChecksTests(unittest.TestCase):
    def test_real_decode_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp), animations=False)
            self.assertTrue(project.check_asset("Model/Heros.bfres", "model")["valid"])
            self.assertTrue(project.check_asset("Model/Heros_Alb.bntx", "texture")["valid"])
            broken = project.root / "Model" / "Broken.bntx"
            broken.write_bytes(b"not bntx")
            self.assertFalse(project.check_asset("Model/Broken.bntx", "texture")["valid"])
            self.assertFalse(project.check_asset("Model/Heros.bfres", "texture")["valid"])
            self.assertFalse(project.check_asset("../outside.bfres", "model")["valid"])

    def test_changed_file_must_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp), animations=False)
            with patch.object(project, "_version", side_effect=[(10, 1, False), (11, 2, False)]), \
                 patch.object(project, "texture_png", return_value=b"png"):
                result = project.check_asset("Model/Heros_Alb.bntx", "texture")
            self.assertFalse(result["valid"])
            self.assertTrue(result["retry"])


class HttpExportTests(unittest.TestCase):
    def test_http_download_returns_archive_and_rejects_unknown_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            p = _make_project(directory, animations=False)
            server = HookyCreatorServer(p.root, p.work, host="127.0.0.1", port=0).start()
            try:
                origin = f"http://127.0.0.1:{server.port}"
                req = urllib.request.Request(origin + "/api/model/export", method="POST",
                    data=json.dumps({"path": "Model/Heros.bfres"}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as response:
                    result = json.load(response)
                with urllib.request.urlopen(origin + result["download_url"], timeout=30) as response:
                    self.assertEqual(response.headers.get_content_type(), "application/zip")
                    self.assertIn("attachment", response.headers["Content-Disposition"])
                    data = response.read()
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    self.assertIn("Heros/Heros.gltf", archive.namelist())
                    self.assertIn("Heros/Heros.bin", archive.namelist())
                for token in ("../outside", "0" * 32, "invalid"):
                    with self.subTest(token=token), self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(origin + "/api/export/download?id=" +
                                               urllib.parse.quote(token), timeout=5)
                    self.assertEqual(error.exception.code, 404)
                with urllib.request.urlopen(origin + "/api/asset/check?kind=model&path=" +
                                           urllib.parse.quote("Model/Heros.bfres"), timeout=30) as response:
                    self.assertTrue(json.load(response)["valid"])
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
