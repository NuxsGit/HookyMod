"""Vitesse : ce qui a rendu l'atelier lent, et ce qui l'empêche de le redevenir.

Trois causes réelles, mesurées sur un dump de 3 600 fichiers :

1. la vue Ensembles et la fiche d'un modèle reparcouraient **tous** les
   fichiers pour chaque modèle (600 modèles × 3 600 textures → 4 s) ;
2. l'état du projet était re-sérialisé (0,6 Mo de JSON) à chaque demande, et
   relisait le dossier de géométrie pour ses statistiques ;
3. les vignettes de textures servaient l'image en taille pleine (1024²) dans
   des tuiles de 160 px.

Les bornes de temps sont volontairement larges (facteur ~10 sur la mesure) :
un test de performance doit signaler un retour au comportement quadratique,
pas échouer sur une machine chargée.
"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookycreator.project import Project  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402


def _build(root: Path, models: int = 400) -> None:
    """``models`` modèles, chacun avec quatre cartes et une animation."""
    (root / "Model").mkdir(parents=True, exist_ok=True)
    bfres = synth.make_bfres()
    bntx = synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8))
    for index in range(models):
        stem = f"Model/Armor_Perf_{index:04d}"
        (root / f"{stem}.bfres").write_bytes(bfres)
        (root / f"{stem}.anim.bfres").write_bytes(bfres)
        for role in ("Alb", "Nrm", "Rgh", "Mtl"):
            (root / f"Model/Mt_Armor_Perf_{index:04d}_{role}.bntx").write_bytes(bntx)


class IndexScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        _build(cls.tmp / "dump")
        cls.project = Project(cls.tmp / "dump", cls.tmp / "work")
        cls.project.scan()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_listing_ensembles_stays_fast(self) -> None:
        start = time.perf_counter()
        rows = self.project.ensembles()
        elapsed = time.perf_counter() - start
        self.assertEqual(len(rows), 400)
        self.assertEqual(rows[0]["textures"], 1)          # l'albédo seul colore
        self.assertLess(elapsed, 1.5, f"ensembles trop lents : {elapsed:.2f} s")

    def test_ensembles_are_memoised(self) -> None:
        self.project.ensembles()
        start = time.perf_counter()
        self.project.ensembles()
        self.assertLess(time.perf_counter() - start, 0.05)

    def test_looking_up_the_maps_of_every_model_is_cheap(self) -> None:
        models = [asset.rel for asset in self.project.by_kind("model")]
        start = time.perf_counter()
        for rel in models:
            self.project.texture_maps_for(rel)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.0, f"recherche des cartes trop lente : {elapsed:.2f} s")

    def test_every_model_finds_its_four_maps(self) -> None:
        rel = self.project.by_kind("model")[0].rel
        buckets = self.project.texture_maps_for(rel)
        self.assertEqual(set(buckets), {"albedo", "normal", "roughness", "metallic"})
        for items in buckets.values():
            self.assertEqual(len(items), 1)
            self.assertIn("Armor_Perf_0000", items[0].rel)

    def test_by_kind_does_not_walk_the_asset_list(self) -> None:
        first = self.project.by_kind("texture")
        self.assertIs(first, self.project.by_kind("texture"))
        self.assertEqual(len(first), 1600)

    def test_scan_is_a_single_pass(self) -> None:
        start = time.perf_counter()
        summary = self.project.scan()
        elapsed = time.perf_counter() - start
        self.assertGreaterEqual(summary["count"], 2000)
        self.assertLess(elapsed, 2.0, f"scan trop lent : {elapsed:.2f} s")

    def test_scanning_again_rebuilds_the_indexes(self) -> None:
        self.project.scan()
        self.assertEqual(len(self.project.by_kind("model")), 400)
        self.assertEqual(len(self.project.ensembles()), 400)


class StatePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        _build(self.tmp / "dump", models=60)
        self.project = Project(self.tmp / "dump", self.tmp / "work")
        self.project.scan()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_payload_is_serialised_once(self) -> None:
        start = time.perf_counter()
        first = self.project.state_payload()
        cold = time.perf_counter() - start
        start = time.perf_counter()
        second = self.project.state_payload()
        warm = time.perf_counter() - start
        self.assertEqual(first, second)
        self.assertLess(warm, cold)
        self.assertLess(warm, 0.05, f"état resérialisé à chaque appel ({warm:.3f} s)")

    def test_payload_survives_a_rescan(self) -> None:
        self.project.state_payload()
        self.project.scan()
        payload = json.loads(self.project.state_payload())
        self.assertEqual(len(payload["project"]["assets"]), 60 * 6)

    def test_payload_compresses_well(self) -> None:
        body = self.project.state_payload()
        packed = gzip.compress(body, 6)
        self.assertLess(len(packed), len(body) / 4,
                        "l'état ne se compresse pas : vérifier qu'il n'est pas déjà binaire")

    def test_edited_list_is_memoised(self) -> None:
        first = self.project.edited_files()
        second = self.project.edited_files()
        self.assertIs(first, second)
        self.project.bump()
        self.assertIsNot(self.project.edited_files(), first)


class HttpSpeedTests(unittest.TestCase):
    """Ce que le navigateur reçoit vraiment : compression, ETag, vignettes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        root = cls.tmp / "dump"
        _build(root, models=8)
        cls.server = HookyCreatorServer(root, cls.tmp / "work",
                                        host="127.0.0.1", port=0).start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()
        cls._tmp.cleanup()

    def get(self, route: str, headers: dict | None = None):
        request = urllib.request.Request(self.base + route, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_state_is_sent_compressed(self) -> None:
        status, headers, body = self.get("/api/state", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        payload = json.loads(gzip.decompress(body))
        self.assertEqual(len(payload["project"]["assets"]), 48)
        plain = self.get("/api/state")[2]
        self.assertLess(len(body), len(plain) / 2, "état non compressé à la volée")

    def test_static_files_use_an_etag(self) -> None:
        status, headers, body = self.get("/static/app.js")
        self.assertEqual(status, 200)
        etag = headers.get("ETag")
        self.assertTrue(etag)
        self.assertIn("no-cache", headers.get("Cache-Control", ""))
        again = self.get("/static/app.js", {"If-None-Match": etag})
        self.assertEqual(again[0], 304)
        self.assertEqual(again[2], b"")

    def test_texture_thumbnail_can_be_small(self) -> None:
        rel = "Model/Mt_Armor_Perf_0000_Alb.bntx"
        # une vraie texture fait 512 ou 1024 px : on en fabrique une de 96 px
        (self.tmp / "dump" / rel).write_bytes(
            synth.make_bntx(96, 96, synth.rgba8_pixels(96, 96)))
        self.server.project.scan()
        full = self.get(f"/api/texture/png?path={rel}&index=0")[2]
        small = self.get(f"/api/texture/png?path={rel}&index=0&max=16")[2]
        self.assertTrue(full.startswith(b"\x89PNG"))
        self.assertTrue(small.startswith(b"\x89PNG"))
        self.assertLess(len(small), len(full))
        from hookycreator.pngread import decode_image

        width, height, _pixels = decode_image(small)
        self.assertEqual((width, height), (16, 16))

    def test_repeated_big_requests_do_not_recompute(self) -> None:
        self.get("/api/ensembles")
        start = time.perf_counter()
        self.get("/api/ensembles")
        self.assertLess(time.perf_counter() - start, 0.5)


class SubsampleTests(unittest.TestCase):
    def test_subsample_keeps_the_aspect_and_the_limit(self) -> None:
        from hookycreator.project import _subsample

        width = height = 64
        pixels = bytes(bytearray(index % 256 for index in range(width * height * 4)))
        new_w, new_h, rgba = _subsample(width, height, pixels, 16)
        self.assertEqual((new_w, new_h), (16, 16))
        self.assertEqual(len(rgba), 16 * 16 * 4)
        # le premier pixel est conservé tel quel (pas d'interpolation)
        self.assertEqual(rgba[:4], pixels[:4])

    def test_subsample_leaves_a_small_image_alone(self) -> None:
        from hookycreator.project import _subsample

        pixels = b"\x01\x02\x03\x04" * 4
        self.assertEqual(_subsample(2, 2, pixels, 64), (2, 2, pixels))


if __name__ == "__main__":
    unittest.main()
