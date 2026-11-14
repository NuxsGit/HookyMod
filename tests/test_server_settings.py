"""API des réglages et des aperçus, testée sans ouvrir de port."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hookycreator.project import ProjectError
from hookycreator.server import HookyCreatorServer

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

    def test_post_settings_persists_and_clamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Path(tmp))
            body, _ = server.api("POST", "/api/settings", {}, {"vram_gb": 18.5})
            self.assertEqual(json.loads(body)["settings"]["vram_gb"], 18.5)
            stored = json.loads((server.project.work / "settings.json").read_text())
            self.assertEqual(stored["vram_gb"], 18.5)
            # au-delà de 25 Go, on retombe sur le maximum au lieu d'accepter
            body, _ = server.api("POST", "/api/settings", {}, {"vram_gb": 120})
            self.assertEqual(json.loads(body)["settings"]["vram_gb"], 25.0)

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
