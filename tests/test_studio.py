"""Synthetic tests for HookyMod's design projects and the inert .hooky format."""
from __future__ import annotations

import base64
import concurrent.futures
import copy
import hashlib
import io
import json
import os
import secrets
import stat
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from hookycreator.server import HookyCreatorServer
from modmaker import studio

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)
OBJ = b"# A synthetic triangle\no Example\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"


def element(kind="item", name="Fichier", asset=None, blocks=None, identity="a" * 32):
    return {"id": identity, "kind": kind, "name": name, "description": "Idée personnelle",
            "asset": asset, "blocks": blocks or []}


def asset(name="mesh.obj", data=OBJ):
    return {"name": name, "data": base64.b64encode(data).decode("ascii")}


def archive_with(entries):
    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
    return out.getvalue()


def archive_from_json(workspace_bytes):
    manifest = {"format": studio.FORMAT, "version": 1, "kind": "design-workspace",
                "playable": False, "workspace": "workspace.json",
                "sha256": hashlib.sha256(workspace_bytes).hexdigest()}
    return archive_with([("manifest.json", json.dumps(manifest).encode()),
                         ("workspace.json", workspace_bytes)])


def corrupt_deflate(data):
    damaged = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entry = archive.getinfo("manifest.json")
        name_size, extra_size = struct.unpack_from("<HH", damaged, entry.header_offset + 26)
        start = entry.header_offset + 30 + name_size + extra_size
        # The first three bits specify the reserved DEFLATE block type 3.
        damaged[start] = 0x06
    return bytes(damaged)


class StudioStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = studio.WorkspaceStore(self.base / "work")

    def test_projects_save_copy_export_and_import_without_touching_other_files(self):
        original = self.base / "original.dat"
        original.write_bytes(b"do not change me")
        first = self.store.create(" Fichier ", "Une idée", "Alice", "Cible non vérifiée")
        self.assertEqual(first["name"], "Fichier")
        first["elements"] = [
            element(asset=asset(), blocks=[{"type": "when_interact", "value": ""},
                                           {"type": "show_message", "value": "Fichier"}]),
            element("scene", "A <story>", asset("image.png", PNG), identity="b" * 32),
            element("building", "Home", asset("model.glb", b"glTF" + struct.pack("<II", 2, 12)), identity="c" * 32),
        ]
        saved = self.store.save(first)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(self.store.load(saved["id"]), saved)
        self.assertTrue(studio.check_workspace(saved)["design_valid"])
        self.assertFalse(studio.check_workspace(saved)["playable"])
        self.assertEqual(original.read_bytes(), b"do not change me")
        self.assertEqual(self.store.list()["workspaces"][0]["count"], 3)

        # Export is deterministic for the same saved revision, with no local
        # paths, game files, or content beyond the two format entries.
        data = self.store.export(saved["id"])
        self.assertEqual(data, self.store.export(saved["id"]))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {"manifest.json", "workspace.json"})
            manifest = json.loads(archive.read("manifest.json"))
            payload = archive.read("workspace.json")
            self.assertEqual(manifest["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertFalse(manifest["playable"])
            self.assertNotIn(str(self.base).encode(), payload)
            self.assertEqual(json.loads(payload), saved)
        self.assertEqual(studio.read_archive(data), saved)

        imported = self.store.import_archive(base64.b64encode(data).decode())
        self.assertNotEqual(imported["id"], saved["id"])
        self.assertEqual(imported["revision"], 0)
        self.assertEqual(imported["elements"], saved["elements"])
        self.assertEqual(self.store.load(saved["id"]), saved)
        draft = copy.deepcopy(saved)
        draft["description"] = "A draft not yet saved"
        duplicate = self.store.copy(draft)
        self.assertNotEqual(duplicate["id"], saved["id"])
        self.assertEqual(duplicate["description"], draft["description"])
        self.assertEqual(self.store.load(saved["id"])["description"], "Une idée")
        self.assertEqual(len(self.store.list()["workspaces"]), 3)
        self.store.delete(imported["id"], imported["revision"])
        self.assertEqual(len(self.store.list()["workspaces"]), 2)
        self.assertEqual(original.read_bytes(), b"do not change me")

    def test_design_checks_are_not_execution_or_playability_checks(self):
        workspace = self.store.create("Design")
        empty = studio.check_workspace(workspace)
        self.assertFalse(empty["design_valid"])
        self.assertFalse(empty["playable"])
        self.assertEqual(empty["adapter"], "unavailable")
        workspace["elements"] = [element(blocks=[{"type": "show_message", "value": ""}])]
        result = studio.check_workspace(workspace)
        self.assertEqual(len(result["issues"]), 2)
        workspace["elements"][0]["blocks"] = [{"type": "when_enter", "value": ""},
                                              {"type": "show_message", "value": "Bonjour"}]
        checked = studio.check_workspace(workspace)
        self.assertTrue(checked["design_valid"])
        self.assertFalse(checked["playable"])

    def test_local_simulation_respects_events_and_conditions_without_side_effects(self):
        sketch = element(blocks=[
            {"type": "show_message", "value": "Before an event"},
            {"type": "when_interact", "value": "Optional detail only"},
            {"type": "if_has_item", "value": " key "},
            {"type": "give_item", "value": " Lantern "},
            {"type": "if_has_item", "value": "Lantern"},
            {"type": "if_flag", "value": "night"},
            {"type": "open_door", "value": "Bridge"},
            {"type": "play_scene", "value": "First light"},
            {"type": "wait", "value": "9000000"},
            {"type": "show_message", "value": "<script>not executable</script>"},
            {"type": "when_enter", "value": ""},
            {"type": "give_item", "value": "Wrong event"},
            {"type": "when_interact", "value": ""},
            {"type": "if_flag", "value": "NIGHT"},
            {"type": "give_item", "value": "Wrong case"},
            {"type": "when_use", "value": ""},
        ])
        before = copy.deepcopy(sketch)
        with patch("modmaker.studio.write_atomic", side_effect=AssertionError("simulation wrote a file")):
            result = studio.simulate_element(sketch, "when_interact", [" key ", "key"], ["night"])
        self.assertEqual(sketch, before)
        self.assertFalse(result["playable"])
        self.assertTrue(result["event_found"])
        self.assertEqual(result["items"], ["key", "Lantern"])
        self.assertEqual(result["doors"], ["Bridge"])
        self.assertEqual(result["scenes"], ["First light"])
        self.assertEqual([row["result"] for row in result["trace"]], [
            "skipped", "event_matched", "condition_true", "simulated", "condition_true",
            "condition_true", "simulated", "simulated", "simulated", "simulated",
            "event_ignored", "skipped", "event_matched", "condition_false", "skipped", "event_ignored",
        ])
        self.assertEqual(result["trace"][9]["value"], "<script>not executable</script>")
        self.assertEqual(list(self.base.iterdir()), [])

    def test_simulation_missing_values_group_reset_and_invalid_inputs(self):
        sketch = element(blocks=[{"type": "when_use", "value": ""},
                                 {"type": "if_flag", "value": ""},
                                 {"type": "give_item", "value": "Skipped"},
                                 {"type": "when_use", "value": ""},
                                 {"type": "give_item", "value": ""},
                                 {"type": "show_message", "value": "Still here"}])
        result = studio.simulate_element(sketch, "when_use", [], [])
        self.assertEqual([row["result"] for row in result["trace"]],
                         ["event_matched", "missing", "skipped", "event_matched", "missing", "simulated"])
        self.assertEqual(result["items"], [])
        result = studio.simulate_element(sketch, "when_enter", [], [])
        self.assertFalse(result["event_found"])
        self.assertTrue(all(row["result"] in {"event_ignored", "skipped"} for row in result["trace"]))
        for event, items, flags in [([], [], []), ("run", [], []), ("when_use", None, []),
                                     ("when_use", ["\n"], []), ("when_use", ["a\nb"], []),
                                     ("when_use", ["x" * 257], []),
                                     ("when_use", [], ["x"] * (studio.MAX_SIMULATION_VALUES + 1))]:
            with self.subTest(event=event, items=str(items)[:40], flags=len(flags)), self.assertRaises(studio.StudioError):
                studio.simulate_element(sketch, event, items, flags)
        malicious = copy.deepcopy(sketch)
        malicious["blocks"][1] = {"type": "run_script", "value": "evil"}
        with self.assertRaises(studio.StudioError):
            studio.simulate_element(malicious, "when_use", [], [])

    def test_schema_validation_rejects_bad_data_without_writing(self):
        workspace = self.store.create("Valid")
        good = element()
        cases = []
        for key, value in [("schema", True), ("schema", 2), ("id", "../escape"),
                           ("revision", True), ("revision", -1), ("revision", 10**9),
                           ("name", "   "), ("name", "x" * 97), ("name", "\ud800"),
                           ("description", "bad\x00value"), ("elements", {}),
                           ("elements", [good] * (studio.MAX_ELEMENTS + 1))]:
            candidate = copy.deepcopy(workspace); candidate[key] = value; cases.append(candidate)
        for key, value in [("kind", []), ("kind", "executable"), ("id", ".."),
                           ("name", ""), ("blocks", [{}]),
                           ("blocks", [{"type": [], "value": ""}]),
                           ("blocks", [{"type": "run_script", "value": "x"}]),
                           ("blocks", [{"type": "wait", "value": "x" * 257}]),
                           ("blocks", [{"type": "wait", "value": "1"}] * (studio.MAX_BLOCKS + 1)),
                           ("asset", asset("../evil.obj")), ("asset", asset("evil.exe")),
                           ("asset", {"name": "model.obj", "data": "!!!"}),
                           ("asset", asset("image.png", b"not a png")),
                           ("asset", asset("image.png", b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 20000, 1))),
                           ("asset", asset("model.glb", b"glTF" + struct.pack("<II", 1, 12))),
                           ("asset", asset("model.obj", b"\xff")),
                           ("asset", asset("model.obj", b"\x00")),
                           ("asset", asset("big.obj", b"a" * (studio.MAX_ASSET_BYTES + 1)))]:
            candidate = copy.deepcopy(workspace); candidate["elements"] = [copy.deepcopy(good)]
            candidate["elements"][0][key] = value; cases.append(candidate)
        candidate = copy.deepcopy(workspace); candidate["elements"] = [good, good]; cases.append(candidate)
        candidate = copy.deepcopy(workspace); candidate["unknown"] = "future"; cases.append(candidate)
        before = self.store.load(workspace["id"])
        for candidate in cases:
            with self.subTest(candidate=str(candidate)[:150]), self.assertRaises(studio.StudioError):
                self.store.save(candidate)
            self.assertEqual(self.store.load(workspace["id"]), before)
        # The aggregate asset limit is independent of the per-attachment limit.
        candidate = copy.deepcopy(workspace)
        candidate["elements"] = [element(asset=asset(data=b"a" * studio.MAX_ASSET_BYTES), identity=f"{i:032x}")
                                 for i in range(3)]
        candidate["elements"].append(element(asset=asset(data=b"a"), identity="f" * 32))
        with self.assertRaises(studio.StudioError):
            self.store.save(candidate)

    def test_stale_revision_concurrent_writes_and_failed_atomic_write(self):
        first = self.store.create("Concurrency")
        barrier = threading.Barrier(8)
        def save(number):
            candidate = copy.deepcopy(first)
            candidate["description"] = str(number)
            barrier.wait(timeout=5)
            try:
                return self.store.save(candidate)
            except studio.StudioError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(save, range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)
        current = self.store.load(first["id"])
        self.assertEqual(current["revision"], 1)
        with self.assertRaisesRegex(studio.StudioError, "Conflit"):
            self.store.save(first)
        with self.assertRaisesRegex(studio.StudioError, "Conflit"):
            self.store.delete(first["id"], first["revision"])
        old = self.store._path(first["id"]).read_bytes()
        with patch("modmaker.studio.write_atomic", side_effect=PermissionError("read only")):
            with self.assertRaisesRegex(studio.StudioError, "Impossible"):
                self.store.save(current)
        self.assertEqual(self.store._path(first["id"]).read_bytes(), old)
        self.assertEqual(self.store.save(current)["revision"], 2)

    def test_unreadable_projects_are_reported_not_replaced(self):
        workspace = self.store.create("Good")
        corrupt = self.store._base() / ("c" * 32 + ".json")
        corrupt.write_bytes(b"{")
        listing = self.store.list()
        self.assertEqual([item["id"] for item in listing["workspaces"]], [workspace["id"]])
        self.assertEqual(listing["unreadable"], 1)
        with self.assertRaises(studio.StudioError):
            self.store.load("c" * 32)
        self.assertEqual(corrupt.read_bytes(), b"{")

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links unavailable")
    def test_symlinks_cannot_redirect_project_writes_or_reads(self):
        outside = self.base / "outside"
        outside.mkdir()
        work = self.base / "work"
        work.mkdir()
        try:
            (work / "hookymods").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symbolic link creation not allowed: {exc}")
        with self.assertRaises(studio.StudioError):
            self.store.create("Unsafe")
        self.assertEqual(list(outside.iterdir()), [])
        (work / "hookymods").unlink()
        workspace = self.store.create("Safe")
        path = self.store._path(workspace["id"])
        path.unlink()
        external = outside / "other.json"
        external.write_text("protected", encoding="utf-8")
        path.symlink_to(external)
        with self.assertRaises(studio.StudioError):
            self.store.load(workspace["id"])
        with self.assertRaises(studio.StudioError):
            self.store.save(workspace)
        self.assertEqual(external.read_text(), "protected")

    def test_import_refuses_untrusted_or_ambiguous_archives_without_extracting(self):
        workspace = self.store.create("Keep")
        payload = json.dumps(workspace).encode()
        good = self.store.export(workspace["id"])
        with zipfile.ZipFile(io.BytesIO(good)) as archive:
            manifest = archive.read("manifest.json")
        symlink = zipfile.ZipInfo("workspace.json")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        bad_archives = [b"", b"not zip", good[:-10], corrupt_deflate(good),
                        archive_with([("manifest.json", manifest), ("../workspace.json", payload)]),
                        archive_with([("manifest.json", manifest), ("workspace.json", payload), ("evil", b"x")]),
                        archive_with([("manifest.json", manifest), ("workspace.json", payload), ("workspace.json", payload)]),
                        archive_with([("manifest.json", manifest), (symlink, payload)]),
                        archive_with([("manifest.json", manifest), ("workspace.json", b"tampered")]),
                        archive_with([("manifest.json", b'{"format":"x","format":"y"}'), ("workspace.json", payload)]),
                        archive_from_json(b'{"schema":1,"schema":1}'),
                        archive_from_json(b"[" * 2000 + b"]" * 2000),
                        archive_from_json(b" " * (studio.MAX_WORKSPACE_BYTES + 1))]
        for data in bad_archives:
            with self.subTest(prefix=data[:12], size=len(data)), self.assertRaises(studio.StudioError):
                self.store.import_archive(base64.b64encode(data).decode())
            self.assertEqual(len(self.store.list()["workspaces"]), 1)
        self.assertEqual(self.store.load(workspace["id"]), workspace)
        self.assertFalse((self.base / "workspace.json").exists())


class RetiredModMakerHttpTests(unittest.TestCase):
    def test_old_routes_are_not_exposed_or_destructive(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, work = base / "root", base / "work"
            root.mkdir()
            # Existing designs, mob sheets and assembled mods must survive
            # starting, rescanning, and requests against the retired endpoints.
            files = {
                work / "hookymods" / "existing.json": b"design data",
                work / "mobs" / "mobs.json": b"mob data",
                root / "mods" / "Existing" / "romfs" / "keep.txt": b"mod data",
            }
            for path, contents in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            token = secrets.token_urlsafe(32)
            server = HookyCreatorServer(root, work, host="127.0.0.1", port=0, desktop_token=token).start()
            origin = f"http://127.0.0.1:{server.port}"

            def request(path, method="GET", body=None, headers=None):
                data = None if body is None else json.dumps(body).encode()
                req = urllib.request.Request(origin + path, method=method, data=data, headers=headers or {})
                try:
                    response = urllib.request.urlopen(req, timeout=5)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    return response.status, dict(response.headers), response.read()

            try:
                status, headers, _ = request("/api/desktop/session", "POST", {"token": token},
                    {"Origin": origin, "Content-Type": "application/json"})
                self.assertEqual(status, 200)
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                auth = {"Cookie": cookie, "Origin": origin, "Content-Type": "application/json"}
                for route in ("/api/studio/workspaces", "/api/studio/workspace", "/api/studio/export",
                              "/api/studio/import", "/api/studio/check", "/api/studio/simulate",
                              "/api/studio/delete", "/api/studio/copy", "/api/mod/build", "/api/mobs"):
                    with self.subTest(route=route):
                        self.assertEqual(request(route, headers=auth)[0], 405)
                        self.assertEqual(request(route, "POST", {"name": "Existing"}, auth)[0], 405)
                self.assertEqual(request("/static/studio.js", headers=auth)[0], 404)
                self.assertEqual(request("/api/scan", "POST", {}, auth)[0], 200)
                self.assertEqual(request("/api/state", headers=auth)[0], 200)
                self.assertFalse(hasattr(server.project, "build_mod"))
                self.assertFalse(hasattr(server.project, "create_mob"))
                self.assertFalse(hasattr(server, "studio"))
                for path, contents in files.items():
                    self.assertEqual(path.read_bytes(), contents)
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
