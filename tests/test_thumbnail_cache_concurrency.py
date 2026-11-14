"""Cache des aperçus : coût incrémental, atomicité et décodages partagés."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from hookycreator import cache, geo
from hookycreator.project import Asset, Project, ProjectError, _subsample
from hookymod import bfres, render
from hookymod.mesh import MeshData
from tests import synth


class IncrementalCacheTests(unittest.TestCase):
    def test_writes_do_not_rescan_the_directory_for_each_thumbnail(self):
        with tempfile.TemporaryDirectory() as folder:
            images = cache.ImageCache(Path(folder))
            with mock.patch.object(images, "files", wraps=images.files) as scan:
                for n in range(300):
                    images.put(f"{n:024x}", b"data" * 20)
                self.assertLessEqual(scan.call_count, 1)
                self.assertEqual(images.stats()["entries"], 300)
            self.assertEqual(images.stats()["bytes"], 300 * 80)

    def test_lru_hits_and_overwrites_keep_accounting_correct(self):
        with tempfile.TemporaryDirectory() as folder:
            images = cache.ImageCache(Path(folder), limit_mb=1)
            images.put("aa", b"a" * 400_000)
            images.put("bb", b"b" * 400_000)
            self.assertEqual(images.get("aa"), b"a" * 400_000)
            images.put("cc", b"c" * 400_000)
            self.assertIsNone(images.get("bb"))
            self.assertIsNotNone(images.get("aa"))
            images.put("aa", b"A" * 200_000)
            self.assertEqual(images.stats()["bytes"], 600_000)
            self.assertEqual(images.stats()["entries"], 2)
            images.put("dd", b"d" * (2 * 1024 * 1024))
            self.assertEqual(images.stats()["entries"], 2, "une image trop grande n'évince pas le reste")

    def test_disabled_cache_does_not_read_old_images(self):
        with tempfile.TemporaryDirectory() as folder:
            images = cache.ImageCache(Path(folder), limit_mb=1)
            images.put("aa", b"old")
            images.limit_bytes = 0
            self.assertIsNone(images.get("aa"))
            images.put("bb", b"new")
            images.limit_bytes = 1024 * 1024
            self.assertEqual(images.get("aa"), b"old")
            self.assertIsNone(images.get("bb"))

    def test_parallel_writes_and_reads_do_not_expose_partial_data(self):
        with tempfile.TemporaryDirectory() as folder:
            images = cache.ImageCache(Path(folder), limit_mb=16)
            a, b = b"a" * 100_000, b"b" * 130_000
            images.put("aa", a)
            def update(n):
                if n % 2:
                    images.put("aa", b if n % 4 == 1 else a)
                    return None
                return images.get("aa")
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(update, range(100)))
            self.assertTrue(all(item is None or item in (a, b) for item in results))
            final = images.get("aa")
            self.assertEqual(images.stats()["bytes"], len(final))
            self.assertEqual(images.stats()["entries"], 1)

    def test_reconcile_picks_up_external_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            images = cache.ImageCache(Path(folder), limit_mb=1)
            images.put("aa", b"a")
            external = images._path("bb")
            external.parent.mkdir(parents=True)
            external.write_bytes(b"b" * 9)
            images.trim()
            self.assertEqual(images.stats()["bytes"], 10)
            self.assertEqual(images.stats()["entries"], 2)

    def test_nanosecond_stamp_detects_a_same_second_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "asset"
            path.write_bytes(b"1234")
            fixed = (time.time_ns() // 1_000_000_000 - 5) * 1_000_000_000
            os.utime(path, ns=(fixed, fixed))
            first = cache.file_stamp(path)
            os.utime(path, ns=(fixed + 500_000_000, fixed + 500_000_000))
            self.assertNotEqual(first, cache.file_stamp(path))

    def test_unique_atomic_geometry_writes_from_two_threads(self):
        mesh = MeshData(name="m", positions=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                        faces=[(0, 1, 2)])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "one.geo"
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda i: geo.write(path, [mesh], {"index": i}), range(30)))
            self.assertEqual(len(results), 30)
            read, meta = geo.read(path)
            self.assertEqual(read[0].faces, [(0, 1, 2)])
            self.assertIn(meta["index"], range(30))
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["one.geo"])


class ProjectConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "dump"
        (self.root / "Model").mkdir(parents=True)
        (self.root / "Model" / "A.bfres").write_bytes(synth.make_bfres())
        self.project = Project(self.root, Path(self.tmp.name) / "work")
        self.project.scan()

    def tearDown(self):
        self.tmp.cleanup()

    def test_concurrent_info_and_previews_decode_the_model_once(self):
        started = threading.Event()
        release = threading.Event()
        original = bfres.BfresFile.parse
        def slow_parse(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError("décodage non libéré")
            return original(*args, **kwargs)
        with mock.patch.object(bfres.BfresFile, "parse", side_effect=slow_parse) as parse:
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs = [pool.submit(self.project.model_info, "Model/A.bfres") for _ in range(6)]
                self.assertTrue(started.wait(5))
                release.set()
                infos = [job.result(timeout=10) for job in jobs]
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(len({info["faces"] for info in infos}), 1)
        self.assertEqual(self.project.geo_misses, 1)
        self.assertFalse(self.project._mesh_flights._locks)

    def test_concurrent_identical_thumbnails_render_once(self):
        started = threading.Event()
        release = threading.Event()
        original = render.render_png
        def slow_render(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError("rendu non libéré")
            return original(*args, **kwargs)
        with mock.patch.object(render, "render_png", side_effect=slow_render) as draw:
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs = [pool.submit(self.project.model_preview, "Model/A.bfres", 120, 90)
                        for _ in range(6)]
                self.assertTrue(started.wait(5))
                release.set()
                images = [job.result(timeout=10) for job in jobs]
        self.assertEqual(draw.call_count, 1)
        self.assertTrue(all(image == images[0] for image in images))
        self.assertFalse(self.project._image_flights._locks)

    def test_parallel_json_views_share_one_expensive_build_per_generation(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        def build():
            calls.append(1)
            started.set()
            if not release.wait(5):
                raise AssertionError("sérialisation non libérée")
            return {"rows": ["A", "B"]}
        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs = [pool.submit(self.project.cached_json, "ensembles", build) for _ in range(6)]
                self.assertTrue(started.wait(5))
                time.sleep(0.08)  # laisser les autres requêtes arriver pendant la construction
                release.set()
                bodies = [job.result(timeout=5) for job in jobs]
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(body == bodies[0] for body in bodies))
            self.project.bump()
            self.project.cached_json("ensembles", build)
            self.assertEqual(len(calls), 2, "une nouvelle version doit reconstruire la liste")
        finally:
            release.set()

    def test_scan_during_ensemble_build_does_not_cache_the_old_list(self):
        import json

        started = threading.Event()
        release = threading.Event()
        original = self.project.texture_assets_for
        def delayed(rel):
            started.set()
            if not release.wait(5):
                raise AssertionError("construction non libérée")
            return original(rel)
        builder = lambda: {"ensembles": self.project.ensembles()}
        try:
            with mock.patch.object(self.project, "texture_assets_for", side_effect=delayed):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    old = pool.submit(self.project.cached_json, "ensembles", builder)
                    self.assertTrue(started.wait(5))
                    (self.root / "Model" / "B.bfres").write_bytes(synth.make_bfres())
                    self.project.scan()
                    release.set()
                    old.result(timeout=5)
            current = json.loads(self.project.cached_json("ensembles", builder))
            self.assertEqual([row["rel"] for row in current["ensembles"]],
                             ["Model/A.bfres", "Model/B.bfres"])
        finally:
            release.set()

    def test_state_uses_one_scan_snapshot_while_serialising_assets(self):
        self.project.scan_limit = 1
        old_revision = self.project.image_revision()
        started, release = threading.Event(), threading.Event()
        original = Asset.as_json
        def delayed(asset):
            started.set()
            if not release.wait(5):
                raise AssertionError("sérialisation non libérée")
            return original(asset)
        try:
            with mock.patch.object(Asset, "as_json", autospec=True, side_effect=delayed):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    old = pool.submit(self.project.state)
                    self.assertTrue(started.wait(5))
                    (self.root / "Model" / "B.bfres").write_bytes(synth.make_bfres())
                    self.assertTrue(self.project.scan()["truncated"])
                    release.set()
                    snapshot = old.result(timeout=5)
            self.assertEqual(snapshot["image_revision"], old_revision)
            self.assertFalse(snapshot["scan_truncated"], "ne pas mélanger deux scans")
            self.assertEqual([a["rel"] for a in snapshot["assets"]], ["Model/A.bfres"])
            self.assertTrue(self.project.state()["scan_truncated"])
        finally:
            release.set()

    def test_obsolete_state_payload_is_not_reinstalled_after_scan(self):
        import json

        started, release = threading.Event(), threading.Event()
        original = self.project.state
        def delayed():
            result = original()
            started.set()
            if not release.wait(5):
                raise AssertionError("état non libéré")
            return result
        try:
            with mock.patch.object(self.project, "state", side_effect=delayed):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    old = pool.submit(self.project.state_payload)
                    self.assertTrue(started.wait(5))
                    (self.root / "Model" / "B.bfres").write_bytes(synth.make_bfres())
                    self.project.scan()
                    release.set()
                    self.assertEqual(len(json.loads(old.result(timeout=5))["project"]["assets"]), 1)
            self.assertIsNone(self.project._state_json, "ne pas remettre l'ancien état en cache")
            current = json.loads(self.project.state_payload())["project"]
            self.assertEqual(len(current["assets"]), 2)
            self.assertEqual(current["image_revision"], self.project.image_revision())
        finally:
            release.set()

    def test_waiting_state_request_rechecks_generation_after_rescan(self):
        import json

        started, waiting, release = threading.Event(), threading.Event(), threading.Event()
        original = self.project.state
        class AnnouncedLock:
            def __init__(self):
                self.lock = threading.Lock()
            def __enter__(self):
                if self.lock.locked():
                    waiting.set()
                self.lock.acquire()
            def __exit__(self, *_args):
                self.lock.release()
        self.project._state_lock = AnnouncedLock()
        def delayed():
            result = original()
            if not started.is_set():
                started.set()
                if not release.wait(5):
                    raise AssertionError("état non libéré")
            return result
        try:
            with mock.patch.object(self.project, "state", side_effect=delayed):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    old = pool.submit(self.project.state_payload)
                    self.assertTrue(started.wait(5))
                    new = pool.submit(self.project.state_payload)
                    self.assertTrue(waiting.wait(5))
                    (self.root / "Model" / "B.bfres").write_bytes(synth.make_bfres())
                    self.project.scan()
                    release.set()
                    self.assertEqual(len(json.loads(old.result(timeout=5))["project"]["assets"]), 1)
                    current = json.loads(new.result(timeout=5))["project"]
                    self.assertEqual(len(current["assets"]), 2)
                    self.assertEqual(current["image_revision"], self.project.image_revision())
        finally:
            release.set()

    def test_old_edited_file_enumeration_does_not_restore_stale_cache(self):
        folder = self.project.work / "assets"
        folder.mkdir()
        started, release = threading.Event(), threading.Event()
        original = Path.rglob
        def delayed(path, pattern):
            paths = list(original(path, pattern))
            started.set()
            if not release.wait(5):
                raise AssertionError("recherche non libérée")
            return iter(paths)
        try:
            with mock.patch.object(Path, "rglob", autospec=True, side_effect=delayed):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    old = pool.submit(self.project.edited_files)
                    self.assertTrue(started.wait(5))
                    (folder / "edited.bfres").write_bytes(b"edit")
                    self.project.bump()
                    release.set()
                    self.assertEqual(old.result(timeout=5), [])
            self.assertEqual(self.project.edited_files(), ["edited.bfres"])
        finally:
            release.set()

    def test_settings_and_image_revision_are_published_together(self):
        started, release = threading.Event(), threading.Event()
        original = self.project.thumbs.set_limit_mb
        old_revision = self.project.image_revision()
        def delayed(limit):
            started.set()
            if not release.wait(5):
                raise AssertionError("réglage non libéré")
            return original(limit)
        try:
            with mock.patch.object(self.project.thumbs, "set_limit_mb", side_effect=delayed):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    update = pool.submit(self.project.update_settings, {"cache_mb": 32})
                    self.assertTrue(started.wait(5))
                    snapshot = self.project.state()
                    if snapshot["settings"]["cache_mb"] == 32:
                        self.assertNotEqual(snapshot["image_revision"], old_revision)
                    else:
                        self.assertEqual(snapshot["image_revision"], old_revision)
                    release.set()
                    update.result(timeout=5)
        finally:
            release.set()

    def test_scan_publishes_assets_indexes_and_revision_together(self):
        started = threading.Event()
        release = threading.Event()
        original = self.project._build_indexes
        def delayed(*args):
            result = original(*args)
            started.set()
            if not release.wait(5):
                raise AssertionError("scan non libéré")
            return result
        revision = self.project.image_revision()
        (self.root / "Model" / "B.bfres").write_bytes(synth.make_bfres())
        try:
            with mock.patch.object(self.project, "_build_indexes", side_effect=delayed):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    scan = pool.submit(self.project.scan)
                    self.assertTrue(started.wait(5))
                    # Les vues gardent l'ancien instantané tant que le nouvel
                    # index n'est pas prêt ; elles ne bloquent pas sur le scan.
                    self.assertEqual(self.project.image_revision(), revision)
                    self.assertEqual([asset.rel for asset in self.project.assets], ["Model/A.bfres"])
                    self.assertEqual([asset.rel for asset in self.project.by_kind("model")],
                                     ["Model/A.bfres"])
                    release.set()
                    scan.result(timeout=5)
            self.assertNotEqual(self.project.image_revision(), revision)
            self.assertEqual([asset.rel for asset in self.project.assets],
                             ["Model/A.bfres", "Model/B.bfres"])
            self.assertEqual([asset.rel for asset in self.project.by_kind("model")],
                             ["Model/A.bfres", "Model/B.bfres"])
        finally:
            release.set()

    def test_scan_during_name_lookup_does_not_restore_obsolete_associations(self):
        for method, added in (("texture_assets_for", "Tex/Mt_A_Alb.bntx"),
                              ("texture_maps_for", "Tex/Mt_A_Alb.bntx"),
                              ("animations_for", "Model/A_Animation.anim.bfres")):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as folder:
                root = Path(folder) / "dump"
                (root / "Model").mkdir(parents=True)
                (root / "Model" / "A.bfres").write_bytes(synth.make_bfres())
                project = Project(root, Path(folder) / "work")
                project.scan()
                started, release = threading.Event(), threading.Event()
                original = project._ranked_names
                def delayed(index, rel):
                    started.set()
                    if not release.wait(5):
                        raise AssertionError("recherche non libérée")
                    yield from original(index, rel)
                lookup = getattr(project, method)
                try:
                    with mock.patch.object(project, "_ranked_names", side_effect=delayed):
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            old = pool.submit(lookup, "Model/A.bfres")
                            self.assertTrue(started.wait(5))
                            new = root / added
                            new.parent.mkdir(parents=True, exist_ok=True)
                            new.write_bytes(b"asset")
                            project.scan()
                            release.set()
                            self.assertFalse(old.result(timeout=5))
                    self.assertTrue(lookup("Model/A.bfres"), "le nouvel asset doit être trouvé")
                finally:
                    release.set()

    def test_settings_keep_decoded_geometry_and_do_not_rescan_unchanged_image_cache(self):
        rel = "Model/A.bfres"
        self.project.model_preview(rel, 120, 90)
        with mock.patch.object(self.project.thumbs, "files", wraps=self.project.thumbs.files) as files, \
             mock.patch.object(bfres.BfresFile, "parse", wraps=bfres.BfresFile.parse) as parse:
            self.project.update_settings({"accent": "azur"})
            self.project.update_settings({"supersample": 1})
            self.project.model_info(rel)
            self.assertEqual(files.call_count, 0)
            self.assertEqual(parse.call_count, 0)
        # Le changement du plafond, lui, réconcilie bien le cache sur disque.
        with mock.patch.object(self.project.thumbs, "files", wraps=self.project.thumbs.files) as files:
            self.project.update_settings({"cache_mb": 32})
            self.assertEqual(files.call_count, 1)

    def test_concurrent_settings_merge_and_persist_without_lost_updates(self):
        from hookycreator.settings import Settings

        entered = threading.Event()
        release = threading.Event()
        original = Settings.save
        def slow_save(settings, path):
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise AssertionError("sauvegarde non libérée")
            return original(settings, path)
        try:
            with mock.patch.object(Settings, "save", autospec=True, side_effect=slow_save):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    one = pool.submit(self.project.update_settings, {"vram_gb": 3})
                    self.assertTrue(entered.wait(5))
                    two = pool.submit(self.project.update_settings, {"supersample": 1})
                    release.set()
                    one.result(timeout=5)
                    two.result(timeout=5)
            stored = Settings.load(self.project.settings_path)
            self.assertEqual((stored.vram_gb, stored.supersample), (3, 1))
            self.assertEqual((self.project.settings.vram_gb, self.project.settings.supersample), (3, 1))
        finally:
            release.set()

    def test_sibling_directory_with_same_prefix_is_not_in_project(self):
        sibling = self.root.with_name(self.root.name + "-autre")
        sibling.mkdir()
        (sibling / "secret.bfres").write_bytes(b"secret")
        with self.assertRaises(ProjectError):
            self.project.asset("../dump-autre/secret.bfres")


class OddSubsampleTests(unittest.TestCase):
    def test_odd_and_thin_dimensions_produce_exact_sized_rows(self):
        for width, height, limit in ((17, 11, 16), (1, 33, 16), (33, 1, 16),
                                     (61, 39, 16), (64, 65, 16)):
            source = bytes((i % 251 for i in range(width * height * 4)))
            w, h, out = _subsample(width, height, source, limit)
            step = -(-max(width, height) // limit)
            reference = b"".join(source[(y * width + x) * 4:(y * width + x + 1) * 4]
                                 for y in range(0, height, step)
                                 for x in range(0, width, step))
            with self.subTest(width=width, height=height):
                self.assertEqual(out, reference)
                self.assertEqual(len(out), w * h * 4)
                self.assertLessEqual(max(w, h), limit)


if __name__ == "__main__":
    unittest.main()
