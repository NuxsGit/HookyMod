"""Les grands projets ne doivent pas perdre silencieusement des fichiers."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hookycreator import __main__ as creator_main, bulk
from hookycreator.project import MAX_SCAN_FILES, Project, ProjectError
from hookycreator.server import HookyCreatorServer
from tests import synth


class ScanLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "dump"
        (self.root / "Model").mkdir(parents=True)
        for name in ("A", "B", "C"):
            (self.root / "Model" / f"{name}.bfres").write_bytes(synth.make_bfres())

    def tearDown(self):
        self.temp.cleanup()

    def project(self, limit):
        return Project(self.root, Path(self.temp.name) / f"work-{limit}", scan_limit=limit)

    def test_default_allows_larger_projects_than_the_old_40000_limit(self):
        self.assertGreaterEqual(MAX_SCAN_FILES, 200_000)

    def test_truncation_is_exact_exposed_and_can_be_disabled(self):
        project = self.project(2)
        result = project.scan()
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["scan_limit"], 2)
        self.assertTrue(project.state()["scan_truncated"])
        self.assertEqual(project.state()["scan_limit"], 2)
        exact = self.project(3)
        self.assertFalse(exact.scan()["truncated"], "atteindre la limite sans omission n'est pas une troncature")
        unlimited = self.project(0)
        self.assertEqual(unlimited.scan()["count"], 3)
        self.assertFalse(unlimited.state()["scan_truncated"])
        with self.assertRaises(ProjectError):
            self.project(-1)

    def test_http_state_and_rescan_expose_incomplete_scan(self):
        server = HookyCreatorServer(self.root, Path(self.temp.name) / "server-work", scan_limit=2)
        state = json.loads(server.api("GET", "/api/state", {}, {})[0])["project"]
        self.assertTrue(state["scan_truncated"])
        self.assertEqual(state["scan_limit"], 2)
        payload = json.loads(server.api("POST", "/api/scan", {}, {})[0])
        self.assertTrue(payload["scan"]["truncated"])
        self.assertTrue(payload["project"]["scan_truncated"])

    def test_unreadable_directory_is_reported_as_an_incomplete_scan(self):
        import os

        denied = self.root / "Interdit"
        denied.mkdir()
        (denied / "Secret.bfres").write_bytes(synth.make_bfres())
        original = os.scandir
        def scandir(path):
            if Path(path) == denied:
                raise PermissionError("accès refusé")
            return original(path)
        project = self.project(0)
        with mock.patch("hookycreator.project.os.scandir", side_effect=scandir):
            result = project.scan()
        self.assertFalse(result["truncated"])
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["scan_errors"], 1)
        self.assertIn("Interdit", result["scan_error_examples"][0])
        state = project.state()
        self.assertTrue(state["scan_incomplete"])
        self.assertEqual(state["scan_errors"], 1)
        report = bulk.export_all(project, Path(self.temp.name) / "partial", kinds=("nonexistent",))
        self.assertTrue(report["scan_incomplete"])
        self.assertIn("illisible", bulk.summary(report).lower())

    def test_bulk_report_and_cli_do_not_claim_a_complete_scan(self):
        project = self.project(2)
        project.scan()
        out = Path(self.temp.name) / "out"
        report = bulk.export_all(project, out, kinds=("nonexistent",))
        self.assertTrue(report["scan_truncated"])
        self.assertTrue(json.loads((out / "rapport.json").read_text(encoding="utf-8"))["scan_truncated"])
        self.assertIn("scan incomplet", bulk.summary(report).lower())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = creator_main.main(["export-all", str(self.root), "-o", str(out),
                                      "--scan-limit", "2", "--kinds", "nonexistent"])
        self.assertNotEqual(code, 0)
        self.assertIn("--scan-limit 0", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            code = creator_main.main(["export-all", str(self.root), "-o", str(out),
                                      "--scan-limit", "0", "--kinds", "nonexistent"])
        self.assertEqual(code, 0)

    def test_bulk_keeps_support_for_project_like_objects(self):
        project = SimpleNamespace(assets=[])
        report = bulk.export_all(project, Path(self.temp.name) / "empty")
        self.assertEqual(report["started"], 0)
        self.assertFalse(report["scan_incomplete"])

    def test_bulk_inventory_and_scan_warning_use_the_same_snapshot(self):
        (self.root / "Model" / "C.bfres").unlink()
        project = self.project(2)
        self.assertFalse(project.scan()["truncated"])
        started, release = threading.Event(), threading.Event()
        original = bulk.discover
        def delayed(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError("découverte non libérée")
            return original(*args, **kwargs)
        try:
            with mock.patch.object(bulk, "discover", side_effect=delayed):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    job = pool.submit(bulk.export_all, project, Path(self.temp.name) / "snapshot")
                    self.assertTrue(started.wait(5))
                    (self.root / "Model" / "B.bfres").unlink()
                    (self.root / "Model" / "C.bfres").write_bytes(synth.make_bfres())
                    (self.root / "Model" / "D.bfres").write_bytes(synth.make_bfres())
                    self.assertTrue(project.scan()["truncated"])
                    release.set()
                    report = job.result(timeout=10)
            self.assertFalse(report["scan_incomplete"])
            self.assertEqual({item["label"] for item in report["items"]},
                             {"Model/A.bfres", "Model/B.bfres"})
            # Si une source disparaît après l'instantané, son échec reste explicite.
            self.assertTrue(any(item["status"] == "error" for item in report["items"]))
        finally:
            release.set()

    def test_gui_cli_passes_the_explicit_limit(self):
        fake = mock.MagicMock()
        fake.scan_summary = {"count": 0, "textures": 0, "models": 0, "bytecode": 0,
                             "root": str(self.root), "truncated": False}
        fake.stopped = False
        fake.port = 8765
        with mock.patch.object(creator_main, "HookyCreatorServer", return_value=fake) as factory, \
             mock.patch("time.sleep", side_effect=KeyboardInterrupt), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(creator_main.main([str(self.root), "--scan-limit", "0"]), 0)
        self.assertEqual(factory.call_args.kwargs["scan_limit"], 0)


if __name__ == "__main__":
    unittest.main()
