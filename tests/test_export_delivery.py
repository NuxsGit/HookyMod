"""Livraison d'un export individuel : fichiers réels, ZIP isolé et HTTP."""
from __future__ import annotations

import concurrent.futures
import io
import json
import os
import struct
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
from hookycreator import cache as cache_mod, export_folders  # noqa: E402
from hookycreator.project import Asset, Project, ProjectError, _model_export_names  # noqa: E402
from hookymod import blender, util  # noqa: E402
from tests.test_bulk import _multi_texture_bntx  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402


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
        first = self.project.export_model(self.rel, archive=True)
        folder = Path(first["absolute_folder"])
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
            report = zipped.read("Heros/RAPPORT_MODELE.txt").decode("utf-8")
            self.assertIn("normales absentes", report)
            self.assertIn("association heuristique", report)

    def test_selection_archive_keeps_model_fidelity_warnings(self) -> None:
        payload = self.project.export_selection([{"kind": "model", "rel": self.rel}])
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            report = zipped.read("modeles/Heros/RAPPORT_MODELE.txt").decode("utf-8")
            self.assertIn("normales absentes", report)
            self.assertIn("association heuristique", report)

    def test_disabled_textures_do_not_claim_stale_files(self) -> None:
        first = self.project.export_model(self.rel, archive=True)
        folder = Path(first["absolute_folder"])
        self.assertTrue((folder / "Heros_Alb.png").is_file())
        result = self.project.export_model(self.rel, textures=False, archive=True)
        self.assertEqual(result["textures"], 0)
        self.assertEqual(result["maps"], [])
        self.assertIn("Heros_Alb.png", result["stale_files"])
        with self.archive(result) as zipped:
            names = zipped.namelist()
            self.assertFalse(any(name.endswith((".png", ".blend")) for name in names))
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

    def test_individual_names_are_portable_and_resist_hash_name_collisions(self) -> None:
        other_rel = "Elsewhere/Heros.bfres"
        suffix = cache_mod.digest(other_rel)[:8]
        spoof_rel = f"Shadow/Heros--{suffix}.bfres"
        for rel in (other_rel, spoof_rel):
            path = self.project.root / rel
            path.parent.mkdir()
            path.write_bytes(synth.make_bfres())
        self.project.scan()
        results = [self.project.export_model(rel, textures=False, archive=True)
                   for rel in (self.rel, other_rel, spoof_rel)]
        folders = [Path(item["absolute_folder"]).name for item in results]
        self.assertEqual(len(set(name.casefold() for name in folders)), 3)
        self.assertEqual(folders[1], f"Heros--{suffix}-2")
        self.assertEqual(folders[2], f"Heros--{suffix}")
        for result, folder in zip(results, folders):
            with self.archive(result) as archive:
                self.assertIn(f"{folder}/{folder}.gltf", archive.namelist())
                self.assertIn(f"{folder}/{folder}.obj", archive.namelist())
        # Les noms ci-dessous ne peuvent pas tous être créés sur Windows :
        # tester aussi directement l'index à partir de chemins non fiables.
        names = _model_export_names([Asset(rel, "model", 0) for rel in (
            "A/CON.bfres", "B/_CON.bfres", "A/a?b.bfres", "B/a*b.bfres", "C/NUL.bfres")])
        self.assertEqual(len({value.casefold() for value in names.values()}), 5)
        self.assertTrue(names["A/CON.bfres"].startswith("_CON--"))
        self.assertEqual(names["C/NUL.bfres"], "_NUL")
        self.assertTrue(all("?" not in value and "*" not in value for value in names.values()))
        with patch.object(cache_mod, "digest", return_value="f" * 64):
            names = _model_export_names([Asset(rel, "model", 0) for rel in (
                "A/X.bfres", "B/X.bfres", "C/X--ffffffff.bfres")])
        self.assertEqual(set(names.values()), {"X--ffffffff", "X--ffffffff-2", "X--ffffffff-3"})

    def test_two_project_instances_share_one_export_destination_safely(self) -> None:
        other = Project(self.project.root, self.project.work)
        other.scan()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.project.export_model, self.rel, True, archive=True)
            second = executor.submit(other.export_model, self.rel, False, archive=True)
            results = [first.result(timeout=30), second.result(timeout=30)]
        self.assertEqual(results[0]["absolute_folder"], results[1]["absolute_folder"])
        self.assertNotEqual(results[0]["download_url"], results[1]["download_url"])
        for flag, result in zip((True, False), results):
            with self.archive(result) as archive:
                self.assertEqual(any(name.endswith(".png") for name in archive.namelist()), flag)
                self.assertIsNone(archive.testzip())

    def test_unowned_existing_folder_is_not_overwritten(self) -> None:
        legacy = self.project.work / "export" / "Heros"
        legacy.mkdir(parents=True)
        old = legacy / "Heros.gltf"
        old.write_bytes(b"export ou fichier personnel anterieur")
        result = self.project.export_model(self.rel, textures=False, archive=True)
        folder = Path(result["absolute_folder"])
        self.assertNotEqual(folder, legacy)
        self.assertEqual(old.read_bytes(), b"export ou fichier personnel anterieur")
        self.assertTrue((folder / export_folders.OWNER_FILE).is_file())
        self.assertNotIn(export_folders.OWNER_FILE, result["files"])
        self.assertNotIn(export_folders.OWNER_FILE, result["stale_files"])
        with self.archive(result) as archive:
            self.assertFalse(any(name.endswith(export_folders.OWNER_FILE) for name in archive.namelist()))

    def test_renamed_export_folder_is_not_reclaimed(self) -> None:
        result = self.project.export_model(self.rel, textures=False)
        folder = Path(result["absolute_folder"])
        renamed = folder.with_name("sauvegarde-personnelle")
        folder.rename(renamed)
        old = renamed / "Heros.gltf"
        old.write_bytes(b"fichier a conserver")
        reopened = Project(self.project.root, self.project.work)
        reopened.scan()
        again = reopened.export_model(self.rel, textures=False)
        self.assertNotEqual(Path(again["absolute_folder"]), renamed)
        self.assertEqual(old.read_bytes(), b"fichier a conserver")

    def test_owners_survive_rescans_restarts_and_removed_models(self) -> None:
        first = self.project.export_model(self.rel, textures=False)
        first_folder = Path(first["absolute_folder"])
        self.assertEqual(first_folder.name, "Heros")
        other_rel = "Elsewhere/Heros.bfres"
        other = self.project.root / other_rel
        other.parent.mkdir()
        other.write_bytes(synth.make_bfres())
        self.project.scan()
        again = self.project.export_model(self.rel, textures=False)
        second = self.project.export_model(other_rel, textures=False)
        second_folder = Path(second["absolute_folder"])
        self.assertEqual(Path(again["absolute_folder"]), first_folder)
        self.assertNotEqual(first_folder.name.casefold(), second_folder.name.casefold())
        old_export = first_folder / "Heros.gltf"
        old_export.write_bytes(b"ne pas remplacer par le modele homonyme")
        (self.project.root / self.rel).unlink()
        self.project.scan()
        self.assertEqual(Path(self.project.export_model(other_rel, textures=False)["absolute_folder"]),
                         second_folder)
        self.assertEqual(old_export.read_bytes(), b"ne pas remplacer par le modele homonyme")
        reopened = Project(self.project.root, self.project.work)
        reopened.scan()
        self.assertEqual(Path(reopened.export_model(other_rel, textures=False)["absolute_folder"]),
                         second_folder)
        self.assertEqual(old_export.read_bytes(), b"ne pas remplacer par le modele homonyme")
        (self.project.root / self.rel).write_bytes(synth.make_bfres())
        reopened.scan()
        self.assertEqual(Path(reopened.export_model(self.rel, textures=False)["absolute_folder"]),
                         first_folder)

    def test_unscanned_homonym_cannot_take_an_indexed_model_name(self) -> None:
        # Il peut être adressé directement par l'API avant un scan, par exemple
        # quand le plafond d'inventaire a omis ce fichier.
        other_rel = "Elsewhere/Heros.bfres"
        other = self.project.root / other_rel
        other.parent.mkdir()
        other.write_bytes(synth.make_bfres())
        omitted = self.project.export_model(other_rel, textures=False)
        indexed = self.project.export_model(self.rel, textures=False)
        self.assertEqual(Path(indexed["absolute_folder"]).name, "Heros")
        self.assertNotEqual(indexed["absolute_folder"].casefold(), omitted["absolute_folder"].casefold())
        truncated = Project(self.project.root, self.project.work, scan_limit=1)
        self.assertTrue(truncated.scan()["truncated"])
        for rel, result in ((self.rel, indexed), (other_rel, omitted)):
            reopened = truncated.export_model(rel, textures=False)
            self.assertEqual(reopened["absolute_folder"], result["absolute_folder"])

    def test_changed_owner_before_publication_refuses_to_write(self) -> None:
        first = self.project.export_model(self.rel, textures=False)
        folder = Path(first["absolute_folder"])
        original = folder / "Heros.gltf"
        original.write_bytes(b"ne pas remplacer")
        self.project.clear_cache()
        export_bundle = blender.export_bundle
        def change_owner(*args, **kwargs):
            result = export_bundle(*args, **kwargs)
            (folder / export_folders.OWNER_FILE).write_text("{}", encoding="utf-8")
            return result
        with patch.object(blender, "export_bundle", side_effect=change_owner):
            with self.assertRaisesRegex(ProjectError, "dossier d'export a changé"):
                self.project.export_model(self.rel, textures=False)
        self.assertEqual(original.read_bytes(), b"ne pas remplacer")
        # Un marqueur absent ou invalide ne confère pas un droit d'écrasement.
        new = self.project.export_model(self.rel, textures=False)
        self.assertNotEqual(new["absolute_folder"], str(folder))
        self.assertEqual(original.read_bytes(), b"ne pas remplacer")

    def test_selection_names_are_portable_and_collision_safe(self) -> None:
        second_rel = "B/X.bntx"
        suffix = cache_mod.digest(second_rel, 0)[:8]
        rels = [f"Spoof/X--{suffix}.bntx", "A/X.bntx", second_rel,
                "C/CON.bntx", "D/bad?name.bntx"]
        with patch.object(self.project, "texture_png", side_effect=lambda rel, _index: rel.encode()):
            payload = self.project.export_selection([{"kind": "texture", "rel": rel} for rel in rels])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), len({name.casefold() for name in names}))
            self.assertEqual(archive.read(f"textures/X--{suffix}.png"), rels[0].encode())
            self.assertEqual(archive.read(f"textures/X--{suffix}-2.png"), second_rel.encode())
            self.assertIn("textures/_CON.png", names)
            self.assertIn("textures/bad_name.png", names)

    def test_selection_model_uses_sanitized_name_for_directory_and_references(self) -> None:
        meshes, meta, stamp = self.project._model_mesh_entry(self.rel)
        fake_rel = "Model/CON.bfres"
        maps = {"material_maps": {}, "warnings": [], "textures": 0, "files": []}
        with patch.object(self.project, "_model_mesh_entry", return_value=(meshes, meta, stamp)), \
                patch.object(self.project, "export_maps_for", return_value=maps):
            payload = self.project.export_selection([{"kind": "model", "rel": fake_rel}])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            doc = json.loads(archive.read("modeles/_CON/_CON.gltf"))
            self.assertEqual(doc["buffers"][0]["uri"], "_CON.bin")
            self.assertIn("modeles/_CON/_CON.bin", archive.namelist())
            self.assertIn("modeles/_CON/_CON.obj", archive.namelist())

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


class TextureExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "source"
        (root / "Tex").mkdir(parents=True)
        (root / "Other").mkdir()
        # Deux images internes avec des pixels différents, pas seulement deux noms.
        data = bytearray(_multi_texture_bntx(["Premiere", "Deuxieme"]))
        data.extend(bytes(max(0, 0xA60 - len(data))))
        struct.pack_into("<Q", data, 0x900 + 0x6C, 0xA00)
        struct.pack_into("<Q", data, 0xA00, 0xA20)
        data[0xA20:0xA60] = bytes((15, 200, 30, 255)) * 16
        struct.pack_into("<I", data, 0x1C, len(data))
        self.rel, self.other = "Tex/Multi.bntx", "Other/Multi.bntx"
        (root / self.rel).write_bytes(data)
        (root / self.other).write_bytes(data)
        self.original = bytes(data)
        self.project = Project(root, Path(self.tmp.name) / "work")
        self.project.scan()

    def test_indices_homonyms_and_reopen_keep_distinct_pngs(self) -> None:
        legacy = self.project.work / "export_textures" / "Multi"
        legacy.mkdir(parents=True)
        (legacy / "personnel.png").write_bytes(b"a conserver")
        zero = self.project.export_texture(self.rel, 0)
        one = self.project.export_texture(self.rel, 1)
        other = self.project.export_texture(self.other, 1)
        zero_file, one_file, other_file = [Path(item["absolute_file"]) for item in (zero, one, other)]
        self.assertEqual(zero_file.parent, one_file.parent)
        self.assertNotEqual(zero_file, one_file)
        self.assertNotEqual(one_file.parent.name.casefold(), other_file.parent.name.casefold())
        self.assertNotEqual(zero_file.parent, legacy)
        self.assertEqual((legacy / "personnel.png").read_bytes(), b"a conserver")
        self.assertNotEqual(util.decode_png(zero_file.read_bytes())[2][:4],
                            util.decode_png(one_file.read_bytes())[2][:4])
        self.assertEqual(util.decode_png(one_file.read_bytes())[2][:4], bytes((15, 200, 30, 255)))
        self.project.scan()
        reopened = Project(self.project.root, self.project.work, scan_limit=1)
        reopened.scan()
        for rel, index, previous in ((self.rel, 0, zero_file), (self.rel, 1, one_file),
                                     (self.other, 1, other_file)):
            self.assertEqual(Path(reopened.export_texture(rel, index)["absolute_file"]), previous)
        self.assertEqual((self.project.root / self.rel).read_bytes(), self.original)
        self.assertEqual((self.project.root / self.other).read_bytes(), self.original)
        with self.assertRaises(ProjectError):
            reopened.export_texture(self.rel, 2)

    def test_http_export_uses_the_requested_image_index(self) -> None:
        server = HookyCreatorServer(self.project.root, self.project.work, host="127.0.0.1", port=0).start()
        self.addCleanup(server.stop)
        outputs = []
        for index in (0, 1):
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/texture/export",
                data=json.dumps({"path": self.rel, "index": index}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            self.assertEqual(result["index"], index)
            outputs.append(Path(result["absolute_file"]))
        self.assertNotEqual(outputs[0], outputs[1])
        self.assertEqual(util.decode_png(outputs[1].read_bytes())[2][:4], bytes((15, 200, 30, 255)))
        self.assertEqual((self.project.root / self.rel).read_bytes(), self.original)

    def test_png_export_replaces_a_link_not_its_target(self) -> None:
        result = self.project.export_texture(self.rel, 0)
        png = Path(result["absolute_file"])
        victim = Path(self.tmp.name) / "important.txt"
        victim.write_bytes(b"ne pas modifier")
        png.unlink()
        try:
            png.symlink_to(victim)
        except (OSError, NotImplementedError):
            self.skipTest("liens symboliques indisponibles")
        self.project.export_texture(self.rel, 0)
        self.assertEqual(victim.read_bytes(), b"ne pas modifier")
        self.assertFalse(png.is_symlink())
        self.assertTrue(png.read_bytes().startswith(b"\x89PNG"))


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
