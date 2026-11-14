"""Cache des vignettes, mémoire, sélection multiple et arrêt de l'atelier."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookycreator import cache as cache_mod  # noqa: E402
from hookycreator.project import Project, ProjectError  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402


class ImageCacheTests(unittest.TestCase):
    def test_put_then_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = cache_mod.ImageCache(Path(tmp), limit_mb=8)
            self.assertIsNone(store.get("abc"))
            store.put("abc", b"\x89PNG-faux")
            self.assertEqual(store.get("abc"), b"\x89PNG-faux")
            stats = store.stats()
            self.assertEqual(stats["entries"], 1)
            self.assertEqual(stats["hits"], 1)

    def test_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = cache_mod.ImageCache(Path(tmp), limit_mb=0)
            store.put("gros", b"x" * 4096)
            self.assertEqual(store.stats()["entries"], 0, "plafond 0 = pas de cache")

    def test_trim_keeps_the_newest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = cache_mod.ImageCache(Path(tmp), limit_mb=1)
            for index in range(40):
                store.put(f"k{index:02d}", b"x" * 60000)
            self.assertLessEqual(store.stats()["bytes"], 1024 * 1024 + 60000)
            self.assertIsNotNone(store.get("k39"))

    def test_clear_empties_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = cache_mod.ImageCache(Path(tmp), limit_mb=8)
            store.put("a", b"x" * 100)
            removed = store.clear()
            self.assertEqual(removed["removed_entries"], 1)
            self.assertEqual(store.stats()["entries"], 0)

    def test_digest_changes_with_any_part(self) -> None:
        self.assertNotEqual(cache_mod.digest(("a", 1)), cache_mod.digest(("a", 2)))
        self.assertEqual(cache_mod.digest(("a", 1)), cache_mod.digest(("a", 1)))

    def test_rss_is_reported_when_possible(self) -> None:
        value = cache_mod.rss_bytes()
        self.assertTrue(value is None or value > 0)


class ProjectCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "dump"
        (self.root / "Model").mkdir(parents=True)
        self.bfres = self.root / "Model" / "Petit.bfres"
        self.bfres.write_bytes(synth.make_bfres())
        self.project = Project(self.root, Path(self.tmp.name) / "work")
        self.project.scan()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_second_preview_comes_from_the_cache(self) -> None:
        with mock.patch("hookycreator.project.render.render_png",
                        wraps=__import__("hookymod.render", fromlist=["x"]).render_png) as spy:
            first = self.project.model_preview("Model/Petit.bfres", 240, 180)
            second = self.project.model_preview("Model/Petit.bfres", 240, 180)
        self.assertEqual(first, second)
        self.assertEqual(spy.call_count, 1, "la seconde vignette doit venir du disque")
        self.assertGreaterEqual(self.project.thumbs.hits, 1)

    def test_changing_quality_invalidates_the_cache(self) -> None:
        calls = []
        real = __import__("hookymod.render", fromlist=["x"]).render_png

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        with mock.patch("hookycreator.project.render.render_png", side_effect=counting):
            self.project.model_preview("Model/Petit.bfres", 200, 150)
            self.project.model_preview("Model/Petit.bfres", 200, 150)
            self.project.update_settings({"supersample": 1})
            self.project.model_preview("Model/Petit.bfres", 200, 150)
        self.assertEqual(len(calls), 2)

    def test_a_modified_model_gets_a_new_thumbnail(self) -> None:
        with mock.patch("hookycreator.project.render.render_png") as spy:
            spy.return_value = b"png-1"
            self.project.model_preview("Model/Petit.bfres", 180, 140)
            self.project.scan()
            self.bfres.write_bytes(synth.make_bfres() + b"\0")
            self.project.scan()
            self.project.model_preview("Model/Petit.bfres", 180, 140)
        self.assertEqual(spy.call_count, 2)

    def test_state_reports_cache_and_scan_time(self) -> None:
        payload = self.project.state()
        self.assertIn("cache", payload)
        self.assertIn("thumbs", payload["cache"])
        self.assertIn("scan_seconds", payload)
        self.assertGreaterEqual(payload["scan_seconds"], 0)

    def test_clear_cache_resets_counters(self) -> None:
        self.project.model_preview("Model/Petit.bfres", 180, 140)
        self.assertGreater(self.project.thumbs.stats()["entries"], 0)
        self.project.clear_cache()
        self.assertEqual(self.project.thumbs.stats()["entries"], 0)

    def test_export_selection_builds_a_zip(self) -> None:
        data = self.project.export_selection([
            {"kind": "model", "rel": "Model/Petit.bfres"},
        ])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
        self.assertIn("LISEZMOI.txt", names)
        self.assertTrue(any(name.startswith("modeles/") for name in names))

    def test_export_selection_refuses_an_empty_list(self) -> None:
        with self.assertRaises(ProjectError):
            self.project.export_selection([])

    def test_export_selection_includes_textures(self) -> None:
        (self.root / "Tex").mkdir()
        (self.root / "Tex" / "Couleur.bntx").write_bytes(
            synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8)))
        self.project.scan()
        data = self.project.export_selection([
            {"kind": "texture", "rel": "Tex/Couleur.bntx"},
        ])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
        self.assertTrue(any(name.startswith("textures/") for name in names))


class ConcurrencyTests(unittest.TestCase):
    """Deux vignettes qui se rendent ensemble ne doivent pas se voler le dictionnaire."""

    def test_parallel_previews_of_compressed_models(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dump"
            (root / "Pack").mkdir(parents=True)
            (root / "Model").mkdir()
            dictionary = synth.train_dict([b"asset %d" % i * 40 for i in range(64)], 1024)
            pack = synth.make_sarc([("0.zsdic", dictionary)])
            (root / "Pack" / "ZsDic.pack.zs").write_bytes(synth.compress(pack))
            for index in range(6):
                raw = synth.make_bfres()
                (root / "Model" / f"M{index}.bfres.zs").write_bytes(
                    synth.compress(raw, dictionary=dictionary))
            project = Project(root, Path(tmp) / "work")
            project.scan()
            project.update_settings({"cache_mb": 64})
            models = [asset.rel for asset in project.by_kind("model")]

            def one(rel: str):
                try:
                    return len(project.model_preview(rel, 160, 120))
                except Exception as exc:              # l'erreur doit remonter au test
                    return exc

            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(one, models * 2))
            errors = [item for item in results if isinstance(item, Exception)]
            self.assertEqual(errors, [], f"échecs en concurrence : {errors}")
            self.assertTrue(all(isinstance(item, int) and item > 0
                                for item in results if not isinstance(item, Exception)))


class HttpCacheTests(unittest.TestCase):
    """ETag, mémoire, scan, sélection et arrêt, par-dessus HTTP."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "dump"
        (root / "Model").mkdir(parents=True)
        (root / "Model" / "Petit.bfres").write_bytes(synth.make_bfres())
        self.server = HookyCreatorServer(root, Path(self.tmp.name) / "work",
                                         host="127.0.0.1", port=0).start()
        self.base = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.tmp.cleanup()

    def get(self, route: str, headers: dict | None = None):
        request = urllib.request.Request(self.base + route, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_thumbnail_carries_an_etag_and_304_afterwards(self) -> None:
        route = "/api/model/preview?path=Model/Petit.bfres&w=200&h=150"
        status, headers, body = self.get(route)
        self.assertEqual(status, 200)
        etag = headers.get("ETag")
        self.assertTrue(etag)
        self.assertIn("max-age", headers.get("Cache-Control", ""))
        again = self.get(route, {"If-None-Match": etag})
        self.assertEqual(again[0], 304)
        self.assertEqual(again[2], b"")

    def test_system_route_reports_memory(self) -> None:
        status, _headers, body = self.get("/api/system")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("thumbs", payload["cache"])
        self.assertIn("cache_mb", payload["cache"])

    def post(self, route: str, payload: dict):
        request = urllib.request.Request(
            self.base + route, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(response.headers), response.read()

    def test_cache_can_be_cleared(self) -> None:
        self.get("/api/model/preview?path=Model/Petit.bfres&w=180&h=140")
        status, _headers, body = self.post("/api/cache/clear", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["cache"]["thumbs"]["entries"], 0)

    def test_scan_route_returns_a_timing(self) -> None:
        status, _headers, body = self.post("/api/scan", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertGreaterEqual(payload["scan"]["scan_seconds"], 0)
        self.assertEqual(payload["scan"]["count"], 1)

    def test_selection_export_returns_a_zip_download(self) -> None:
        status, headers, body = self.post("/api/export/selection", {
            "items": [{"kind": "model", "rel": "Model/Petit.bfres"}],
        })
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/zip")
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertTrue(archive.namelist())

    def test_shutdown_stops_the_server(self) -> None:
        self.assertFalse(self.server.stopped)
        status, _headers, _body = self.post("/api/shutdown", {})
        self.assertEqual(status, 200)
        for _ in range(60):
            if self.server.stopped:
                break
            __import__("time").sleep(0.05)
        self.assertTrue(self.server.stopped)


if __name__ == "__main__":               # pragma: no cover
    unittest.main()
