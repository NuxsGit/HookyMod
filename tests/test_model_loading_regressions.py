"""Régressions du chargement BFRES à travers le projet et l'API HTTP.

Les fixtures sont synthétiques : aucun fichier du jeu n'est nécessaire.
"""
from __future__ import annotations

import json
import struct
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from hookymod import bfres_safe
from hookycreator.project import ProjectError
from hookycreator.server import HookyCreatorServer
from tests import synth, test_bfres_v10 as v10_fixtures


class ModelLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        models = self.root / "Model"
        models.mkdir()
        data = bytearray(v10_fixtures.make_v10(*v10_fixtures.DataDrivenTests()._grid()))
        struct.pack_into("<Q", data, 0x30, 0)  # dictionnaire absent, FMDL et FVTX valides
        (models / "Valide.bfres").write_bytes(data)
        compressed = synth.compress(bytes(data))
        (models / "Compresse.bfres.zs").write_bytes(compressed)
        (models / "Tronque.bfres.zs").write_bytes(compressed[:-4])
        dictionary = synth.train_dict([b"asset %d" % i * 40 for i in range(64)], 1024)
        (models / "Dictionnaire_Manquant.bfres.zs").write_bytes(synth.compress(bytes(data), dictionary))
        animation = bytearray(0x200)
        animation[:4] = b"FRES"
        animation[8:12] = struct.pack("<I", 0x000A0400)
        animation[0x0C:0x0E] = b"\xff\xfe"
        animation[0x100:0x104] = b"FSKA"       # animation, pas de FMDL
        (models / "SansMaillage.bfres").write_bytes(animation)
        (models / "Invalide.bfres").write_bytes(b"pas un BFRES")
        self.server = HookyCreatorServer(self.root, host="127.0.0.1", port=0).start()
        self.base = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()
        self.temp.cleanup()

    def get(self, route: str, rel: str):
        query = urllib.parse.urlencode({"path": rel, "w": 96, "h": 96, "tex": 0})
        with urllib.request.urlopen(f"{self.base}{route}?{query}") as response:
            return response.status, response.headers.get_content_type(), response.read()

    def post(self, route: str, rel: str):
        request = urllib.request.Request(
            self.base + route, data=json.dumps({"path": rel, "textures": False}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request) as response:
            return response.status, response.headers.get_content_type(), response.read()

    def test_missing_dictionary_model_can_be_inspected_previewed_and_exported(self):
        for name in ("Valide.bfres", "Compresse.bfres.zs"):
            with self.subTest(name=name):
                rel = f"Model/{name}"
                status, _, body = self.get("/api/model/info", rel)
                info = json.loads(body)
                self.assertEqual(status, 200)
                self.assertEqual(info["faces"], len(v10_fixtures.DataDrivenTests()._grid()[2]))
                self.assertTrue(any("dictionnaire de modèles" in w.lower()
                                    for w in info["warnings"]))
                status, kind, body = self.get("/api/model/preview", rel)
                self.assertEqual((status, kind), (200, "image/png"))
                self.assertTrue(body.startswith(b"\x89PNG"))
                status, _, body = self.post("/api/model/export", rel)
                result = json.loads(body)
                self.assertEqual(status, 200)
                self.assertTrue(any(name.endswith(".obj") for name in result["files"]))
                self.assertTrue(any(name.endswith(".gltf") for name in result["files"]))

    def test_unreadable_zstd_has_a_contextual_error_instead_of_a_server_error(self):
        for name in ("Tronque.bfres.zs", "Dictionnaire_Manquant.bfres.zs"):
            for route in ("/api/model/info", "/api/model/preview", "/api/model/export"):
                with self.subTest(name=name, route=route):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        if route.endswith("export"):
                            self.post(route, f"Model/{name}")
                        else:
                            self.get(route, f"Model/{name}")
                    with caught.exception as response:
                        self.assertEqual(response.code, 400)
                        error = json.loads(response.read())["error"]
                    self.assertIn(name, error)
                    self.assertIn("zstd", error.lower())
                    self.assertNotIn("Erreur interne", error)

    def test_truncated_frame_with_a_loaded_dictionary_reports_zstd(self):
        dictionary = synth.train_dict([b"synthetic dictionary %d" % i * 40
                                       for i in range(64)], 1024)
        pack = self.root / "Pack"
        pack.mkdir()
        (pack / "ZsDic.pack.zs").write_bytes(
            synth.compress(synth.make_sarc([("zs.zsdic", dictionary)])))
        data = (self.root / "Model" / "Valide.bfres").read_bytes()
        name = "Tronque_avec_dictionnaire.bfres.zs"
        (self.root / "Model" / name).write_bytes(synth.compress(data, dictionary)[:-4])
        for route in ("/api/model/info", "/api/model/preview", "/api/model/export"):
            with self.subTest(route=route):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    if route.endswith("export"):
                        self.post(route, f"Model/{name}")
                    else:
                        self.get(route, f"Model/{name}")
                with caught.exception as response:
                    self.assertEqual(response.code, 400)
                    error = json.loads(response.read())["error"]
                self.assertIn(name, error)
                self.assertIn("zstd", error.lower())
                self.assertNotIn("Erreur interne", error)

    def test_recovered_v10_is_still_not_claimed_as_reinjectable(self):
        data = (self.root / "Model" / "Valide.bfres").read_bytes()
        reason = bfres_safe.why_not_injectable(data)
        self.assertIn("10.x", reason)
        self.assertIn("réécriture", reason)
        self.assertIn("sous-maillages", reason)

    def test_no_mesh_and_invalid_bfres_are_actionable_client_errors(self):
        for name in ("SansMaillage.bfres", "Invalide.bfres"):
            rel = f"Model/{name}"
            with self.subTest(name=name):
                with self.assertRaises(ProjectError):
                    self.server.project.model_info(rel)
                for route in ("/api/model/info", "/api/model/preview"):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        self.get(route, rel)
                    with caught.exception as response:
                        self.assertEqual(response.code, 400)
                        error = json.loads(response.read())["error"]
                    self.assertIn(name, error)
                    self.assertNotIn("Erreur interne", error)
                    self.assertIn("animation", error.lower())
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.post("/api/model/export", rel)
                with caught.exception as response:
                    self.assertEqual(response.code, 400)
                    self.assertNotIn("Erreur interne", json.loads(response.read())["error"])


if __name__ == "__main__":
    unittest.main()
