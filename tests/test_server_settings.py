"""API des réglages et des aperçus, testée sans ouvrir de port."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from hookycreator.project import ProjectError
from hookycreator.server import HookyCreatorServer
from hookymod import native, restbl, sarc, zs

from tests import synth


def make_server(tmp: Path) -> HookyCreatorServer:
    root = tmp / "dump"
    (root / "Model").mkdir(parents=True)
    (root / "Model" / "Petit.bfres").write_bytes(synth.make_bfres())
    return HookyCreatorServer(root, tmp / "work", host="127.0.0.1", port=0)


class SettingsApiTests(unittest.TestCase):
    def test_default_budget_is_6gb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            body, kind = server.api("GET", "/api/settings", {}, {})
            self.assertEqual(kind, "application/json")
            settings = json.loads(body)["settings"]
            self.assertEqual(settings["vram_gb"], 6.0)
            self.assertEqual(settings["vram_max_gb"], 25.0)
            self.assertEqual(settings["budget_bytes"], int(6 * 1024 ** 3))

    def test_settings_response_pairs_settings_with_the_same_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            project = server.project
            entered, release = threading.Event(), threading.Event()
            original = project.update_settings
            def delayed(payload):
                result = original(payload)
                if payload.get("cache_mb") == 32:
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("réponse non libérée")
                return result
            try:
                with mock.patch.object(project, "update_settings", side_effect=delayed):
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        old = pool.submit(server.api, "POST", "/api/settings", {}, {"cache_mb": 32})
                        self.assertTrue(entered.wait(5))
                        original({"cache_mb": 64})
                        revision = project.image_revision()
                        release.set()
                        payload = json.loads(old.result(timeout=5)[0])
                self.assertEqual(payload["settings"]["cache_mb"], 64)
                self.assertEqual(payload["image_revision"], revision)
            finally:
                release.set()

    def test_post_settings_persists_and_clamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            old_revision = server.project.image_revision()
            body, _ = server.api("POST", "/api/settings", {}, {"vram_gb": 18.5})
            payload = json.loads(body)
            self.assertEqual(payload["settings"]["vram_gb"], 18.5)
            self.assertEqual(payload["image_revision"], server.project.image_revision())
            self.assertNotEqual(payload["image_revision"], old_revision)
            stored = json.loads((server.project.work / "settings.json").read_text())
            self.assertEqual(stored["vram_gb"], 18.5)
            # au-delà de 25 Go, on retombe sur le maximum au lieu d'accepter
            body, _ = server.api("POST", "/api/settings", {}, {"vram_gb": 120})
            self.assertEqual(json.loads(body)["settings"]["vram_gb"], 25.0)

    def test_legacy_theme_is_migrated_and_persisted_by_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            path = work / "settings.json"
            path.write_text(json.dumps({"theme": "newgenoldtendo", "vram_gb": 13,
                                        "accent": "menthe"}))
            server = make_server(Path(tmp))
            body, _ = server.api("GET", "/api/settings", {}, {})
            settings = json.loads(body)["settings"]
            self.assertEqual((settings["theme"], settings["vram_gb"], settings["accent"]),
                             ("aurora", 13, "menthe"))
            server.api("POST", "/api/settings", {}, {"language": "en"})
            stored = json.loads(path.read_text())
            self.assertEqual((stored["theme"], stored["vram_gb"], stored["accent"]),
                             ("aurora", 13, "menthe"))

    def test_post_bad_setting_is_a_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            with self.assertRaises(ProjectError):
                server.api("POST", "/api/settings", {}, {"vram_gb": "beaucoup"})

    def test_state_carries_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            body, _ = server.api("GET", "/api/state", {}, {})
            project = json.loads(body)["project"]
            self.assertEqual(project["settings"]["vram_gb"], 6.0)

    def test_system_reports_the_active_native_and_texture_backends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            body, _ = server.api("GET", "/api/system", {}, {})
            payload = json.loads(body)
            self.assertIn("cache", payload)
            self.assertIn("settings", payload)
            self.assertEqual(payload["engine"]["rust"]["available"], native.available())
            self.assertIn(payload["engine"]["texture"]["name"], ("c", "rust", "python"))
            self.assertEqual(payload["engine"]["texture"]["deswizzle"],
                             "Rust" if native.available() else "Python")
            self.assertEqual(payload["engine"]["zstd"], zs.decoder_backend())
            self.assertIn(payload["engine"]["zstd"]["name"], ("rust", "zstandard", "missing"))
            self.assertEqual(payload["engine"]["sarc"], sarc.backend())
            self.assertEqual(payload["engine"]["sarc"]["name"],
                             "rust" if native.available() else "python")
            self.assertEqual(payload["engine"]["restbl"], restbl.backend())
            self.assertEqual(payload["engine"]["restbl"]["name"],
                             "rust" if native.available() else "python")


class StaticPathTests(unittest.TestCase):
    def test_directory_with_same_prefix_is_not_public(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "web"
            sibling = Path(tmp) / "web-secret"
            base.mkdir()
            sibling.mkdir()
            (base / "index.html").write_text("public", encoding="utf-8")
            (sibling / "secret.txt").write_text("privé", encoding="utf-8")
            server = make_server(Path(tmp))
            with mock.patch("hookycreator.server.WEB_DIR", base):
                self.assertIsNotNone(server.static("/"))
                self.assertIsNone(server.static("/static/../web-secret/secret.txt"))


class PreviewApiTests(unittest.TestCase):
    def test_plan_reports_the_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            body, _ = server.api("GET", "/api/model/plan",
                                 {"path": ["Model/Petit.bfres"], "w": ["320"],
                                  "h": ["240"]}, {})
            plan = json.loads(body)["plan"]
            self.assertTrue(plan["fits"])
            self.assertEqual(plan["budget_bytes"], int(6 * 1024 ** 3))
            self.assertGreater(plan["frame_bytes"], 0)

    def test_preview_is_a_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            body, kind = server.api("GET", "/api/model/preview",
                                    {"path": ["Model/Petit.bfres"], "w": ["160"],
                                     "h": ["120"]}, {})
            self.assertEqual(kind, "image/png")
            self.assertTrue(body.startswith(b"\x89PNG"))

    def test_preview_accepts_a_face_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            body, kind = server.api("GET", "/api/model/preview",
                                    {"path": ["Model/Petit.bfres"], "w": ["160"],
                                     "h": ["120"], "faces": ["600"]}, {})
            self.assertEqual(kind, "image/png")
            self.assertTrue(body.startswith(b"\x89PNG"))

    def test_turntable_preview_reuses_render_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            body, kind = server.api("GET", "/api/model/preview",
                                    {"path": ["Model/Petit.bfres"], "w": ["64"],
                                     "h": ["64"], "views": ["3"]}, {})
            self.assertEqual(kind, "image/png")
            from hookymod.util import decode_png
            width, height, _ = decode_png(body)
            self.assertEqual((width, height), (192, 64))

    def test_preview_sizes_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            body, _ = server.api("GET", "/api/model/preview",
                                 {"path": ["Model/Petit.bfres"], "w": ["999999"],
                                  "h": ["1"]}, {})
            # borné à 1600 × 64 : l'image reste petite et le test rapide
            self.assertTrue(body.startswith(b"\x89PNG"))

    def test_preview_refuses_an_unknown_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            with self.assertRaises(ProjectError):
                server.api("GET", "/api/model/preview",
                           {"path": ["Model/Absent.bfres"]}, {})

    def test_settings_route_unknown_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            with self.assertRaises(ProjectError):
                server.api("GET", "/api/nimporte-quoi", {}, {})


if __name__ == "__main__":               # pragma: no cover
    unittest.main()
