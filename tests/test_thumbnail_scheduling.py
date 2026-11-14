"""Les vignettes ne doivent pas monopoliser les requêtes interactives."""
from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from hookycreator import __main__ as creator_main
from hookycreator.project import ProjectError
from hookycreator.server import HookyCreatorServer
from hookymod.util import png_bytes
from tests import synth


class ThumbnailHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "dump"
        (root / "Model").mkdir(parents=True)
        (root / "Tex").mkdir()
        (root / "Tex" / "M0_Alb.bntx").write_bytes(
            synth.make_bntx(32, 32, synth.rgba8_pixels(32, 32)))
        for index in range(3):
            (root / "Model" / f"M{index}.bfres").write_bytes(synth.make_bfres())
        self.server = HookyCreatorServer(root, Path(self.tmp.name) / "work",
                                         host="127.0.0.1", port=0).start()
        self.base = f"http://127.0.0.1:{self.server.port}"
        self.png = png_bytes(2, 2, b"\xff\0\0\xff" * 4)

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def get(self, path, timeout=5, headers=None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def post(self, path, payload=None):
        request = urllib.request.Request(self.base + path,
                                         data=json.dumps(payload or {}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_background_slot_refuses_excess_and_leaves_interactive_slot_free(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def slow(rel, width, *args, **kwargs):
            calls.append((rel, width))
            if width == 320:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("rendu de test non libéré")
            return self.png
        path = "/api/model/preview?path=Model/M0.bfres"
        try:
            with mock.patch.object(self.server.project, "model_preview", side_effect=slow):
                with ThreadPoolExecutor(max_workers=3) as pool:
                    background = pool.submit(self.get, path + "&w=320&thumb=1")
                    self.assertTrue(entered.wait(5))
                    code, headers, _ = self.get(path + "&w=320&thumb=1")
                    self.assertEqual(code, 429)
                    self.assertEqual(headers.get("Retry-After"), "1")
                    # Même pendant ce rendu, une fiche et un grand aperçu passent.
                    info = pool.submit(self.get, "/api/model/info?path=Model/M1.bfres")
                    foreground = pool.submit(self.get, path + "&w=400")
                    self.assertEqual(info.result(timeout=3)[0], 200)
                    self.assertEqual(foreground.result(timeout=3)[0], 200)
                    self.assertEqual(calls.count(("Model/M0.bfres", 320)), 1)
                    release.set()
                    self.assertEqual(background.result(timeout=5)[0], 200)
        finally:
            release.set()

    def test_background_does_not_queue_when_both_foreground_slots_are_busy(self):
        lock = threading.Lock()
        entered = threading.Event()
        release = threading.Event()
        active = []
        def slow(rel, width, *args, **kwargs):
            with lock:
                active.append(width)
                if len(active) == 2:
                    entered.set()
            if not release.wait(5):
                raise RuntimeError("rendu de test non libéré")
            return self.png
        path = "/api/model/preview?path=Model/M0.bfres"
        try:
            with mock.patch.object(self.server.project, "model_preview", side_effect=slow):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(self.get, path + "&w=400")
                    second = pool.submit(self.get, path + "&w=401")
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(self.get(path + "&w=320&thumb=1")[0], 429)
                    self.assertEqual(active, [400, 401] if active[0] == 400 else [401, 400])
                    release.set()
                    self.assertEqual(first.result(timeout=5)[0], 200)
                    self.assertEqual(second.result(timeout=5)[0], 200)
        finally:
            release.set()

    def test_explicit_warming_can_be_cancelled_between_renders(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def slow(rel, *args, **kwargs):
            calls.append(rel)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("rendu de test non libéré")
            return self.png
        try:
            with mock.patch.object(self.server.project, "model_preview", side_effect=slow):
                result = self.post("/api/thumbs/warm")["warm"]
                self.assertTrue(result["running"])
                self.assertTrue(entered.wait(5))
                # Le préchauffage et les vignettes visibles partagent le même quota.
                self.assertEqual(self.get("/api/model/preview?path=Model/M1.bfres&thumb=1")[0], 429)
                self.post("/api/thumbs/stop")
                release.set()
                self.server.project._warm_thread.join(timeout=5)
                status = self.server.project.warm_status()
                self.assertFalse(status["running"])
                self.assertTrue(status["cancelled"])
                self.assertEqual(status["done"], 1)
                self.assertEqual(len(calls), 1)
                self.assertTrue(self.server.project._warm_thread.daemon)
        finally:
            release.set()

    def test_explicit_warming_yields_to_visible_thumbnails(self):
        self.server.thumbnail_activity.quiet_seconds = 0.4
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def slow(rel, *args, **kwargs):
            calls.append(rel)
            if rel == "Model/M0.bfres":
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("rendu de test non libéré")
            return self.png
        try:
            with mock.patch.object(self.server.project, "model_preview", side_effect=slow):
                self.post("/api/thumbs/warm")
                self.assertTrue(entered.wait(5))
                route = "/api/model/preview?path=Model/M2.bfres&thumb=1"
                self.assertEqual(self.get(route)[0], 429)
                release.set()
                deadline = time.monotonic() + 2
                while True:
                    code = self.get(route)[0]
                    if code == 200 or time.monotonic() >= deadline:
                        break
                    time.sleep(0.02)
                self.assertEqual(code, 200)
                time.sleep(0.08)
                self.assertEqual(calls[:2], ["Model/M0.bfres", "Model/M2.bfres"])
                self.assertEqual(calls.count("Model/M1.bfres"), 0)
                self.post("/api/thumbs/stop")
                self.server.project._warm_thread.join(timeout=5)
                self.assertFalse(self.server.project.warm_status()["running"])
        finally:
            release.set()

    def test_bulk_job_is_reserved_before_thread_start_and_recovers_from_start_failure(self):
        with mock.patch("hookycreator.server.threading.Thread.start", side_effect=RuntimeError("refus")):
            with self.assertRaises(RuntimeError):
                self.server.start_bulk(["model"])
        self.assertFalse(self.server.job["running"])
        # Avant même que le fil ne s'exécute, un deuxième export est refusé.
        with mock.patch("hookycreator.server.threading.Thread.start", return_value=None):
            self.server.start_bulk(["model"])
            self.assertTrue(self.server.job["running"])
            with self.assertRaises(ProjectError):
                self.server.start_bulk(["model"])
        self.server.job["running"] = False

    def test_image_etag_is_revalidated_instead_of_cached_for_a_week(self):
        route = "/api/texture/png?path=Tex/M0_Alb.bntx&index=0"
        status, headers, original = self.get(route)
        self.assertEqual(status, 200)
        self.assertIn("max-age=0", headers["Cache-Control"])
        self.assertIn("must-revalidate", headers["Cache-Control"])
        old_tag = headers["ETag"]
        self.assertEqual(self.get(route, headers={"If-None-Match": old_tag})[0], 304)
        red = png_bytes(32, 32, bytes((255, 0, 0, 255)) * 1024)
        self.post("/api/texture/import", {"path": "Tex/M0_Alb.bntx", "index": 0,
                                          "image": base64.b64encode(red).decode("ascii")})
        status, new_headers, changed = self.get(route, headers={"If-None-Match": old_tag})
        self.assertEqual(status, 200)
        self.assertNotEqual(new_headers["ETag"], old_tag)
        self.assertNotEqual(changed, original)
        self.assertEqual(self.get(route, headers={"If-None-Match": new_headers["ETag"]})[0], 304)


class StartupTests(unittest.TestCase):
    def test_command_reuses_initial_scan_instead_of_scanning_twice(self):
        fake = mock.MagicMock()
        fake.scan_summary = {"count": 3, "textures": 1, "models": 1,
                             "bytecode": 1, "root": "test"}
        fake.stopped = False
        fake.port = 8765
        with mock.patch.object(creator_main, "HookyCreatorServer", return_value=fake), \
             mock.patch("time.sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(creator_main.main(["test"]), 0)
        fake.project.scan.assert_not_called()
        fake.start.assert_called_once()
        fake.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
