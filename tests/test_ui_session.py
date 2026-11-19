"""Reprise d'interface : bornes, isolation, concurrence et API native."""
from __future__ import annotations

import concurrent.futures
import errno
import hashlib
import http.client
import json
import os
import secrets
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from hookycreator import session
from hookycreator.server import HookyCreatorServer


def snapshot(view="models"):
    return {
        "version": 1,
        "view": view,
        "tabs": [{"type": "view", "view": "home"}, {"type": "view", "view": view},
                 {"type": "model", "rel": "Model/Héros.bfres"}],
        "active": "model:Model/Héros.bfres",
        "selection": ["model:Model/Héros.bfres", "texture:Model/Héros_Alb.bntx"],
        "fields": {"search": "Héros", "model-target-search": "Héros", "model-shots": True},
        "choices": {"model-target": "Model/Héros.bfres"},
        "shown": {"models": 96},
        "scroll": 123,
        "inspector": True,
        "console": False,
        "reduced": True,
        "thumbnails_paused": False,
    }


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.root = self.directory / "jeu"
        self.root.mkdir()
        self.work = self.directory / "work"
        self.store = session.SessionStore(self.work, self.root)

    def test_round_trip_is_independent_of_port_and_project(self):
        state = snapshot()
        state["fields"]["future"] = "ignored"
        self.assertTrue(self.store.save("client_123", 1, state))
        restored = session.SessionStore(self.work, self.root).load()
        self.assertEqual(restored["choices"]["model-target"], "Model/Héros.bfres")
        self.assertNotIn("future", restored["fields"])
        self.assertEqual(restored["tabs"], state["tabs"])
        self.assertEqual(restored["selection"], state["selection"])
        self.assertEqual(restored["scroll"], 123)
        self.assertEqual(restored["shown"]["models"], 96)
        self.assertEqual(restored["shown"]["textures"], 48)
        other_root = self.directory / "autre"
        other_root.mkdir()
        other = session.SessionStore(self.work, other_root)
        self.assertIsNone(other.load())
        self.assertTrue(other.save("client_456", 1, snapshot("sounds")))
        self.assertEqual(self.store.load()["view"], "models")
        self.assertEqual(other.load()["view"], "sounds")
        self.assertNotEqual(self.store.name, other.name)
        self.assertNotIn(str(self.root), self.store.name)

    def test_retired_views_and_mob_tabs_migrate_without_losing_other_state(self):
        for view in ("studio", "create", "mobs"):
            for active in (f"view:{view}", "mob:MyMob"):
                with self.subTest(view=view, active=active):
                    old = snapshot(view)
                    old["active"] = active
                    old["tabs"] += [{"type": "mob", "slug": "MyMob"},
                                    {"type": "view", "view": "import"}]
                    old["fields"]["mod-name"] = "Un brouillon historique"
                    old["choices"].update({"mob-model": "Model/Héros.bfres",
                                           "mob-textures": ["Model/Héros_Alb.bntx"]})
                    raw = json.dumps({"root": self.store.root, "state": old}, ensure_ascii=False).encode()
                    path = self.store._path()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                    migrated = self.store.load()
                    self.assertEqual(path.read_bytes(), raw)  # loading alone never rewrites files
                    self.assertEqual(migrated["view"], "home")
                    self.assertEqual(migrated["active"], "view:home")
                    self.assertEqual(migrated["selection"], old["selection"])
                    self.assertEqual(migrated["choices"]["model-target"], "Model/Héros.bfres")
                    self.assertEqual(migrated["fields"]["search"], "Héros")
                    self.assertIn({"type": "view", "view": "import"}, migrated["tabs"])
                    self.assertIn({"type": "model", "rel": "Model/Héros.bfres"}, migrated["tabs"])
                    self.assertFalse(any(t.get("type") == "mob" or t.get("view") == view
                                         for t in migrated["tabs"]))

    def test_retired_drafts_are_backed_up_before_the_first_new_checkpoint(self):
        old = snapshot("models")
        old["fields"]["mod-name"] = "Brouillon à ne pas perdre"
        old["choices"]["mob-textures"] = ["Model/Héros_Alb.bntx"]
        raw = json.dumps({"root": self.store.root, "state": old}, ensure_ascii=False).encode()
        path = self.store._path()
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        migrated = self.store.load()
        self.assertTrue(self.store.save("client_123", 1, migrated))
        backups = list(path.parent.glob("*.pre-modmaker-*.json.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), raw)
        self.assertNotIn("mod-name", self.store.load()["fields"])
        self.assertTrue(self.store.save("client_123", 2, snapshot("sounds")))
        self.assertEqual(backups[0].read_bytes(), raw)
        self.assertEqual(len(list(path.parent.glob("*.pre-modmaker-*.json.bak"))), 1)

    def test_failed_retired_backup_keeps_original_session_and_retries(self):
        old = snapshot("create")
        raw = json.dumps({"root": self.store.root, "state": old}).encode()
        path = self.store._path()
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        migrated = self.store.load()
        with patch("hookycreator.session.os.link", side_effect=PermissionError(errno.EACCES, "read only")), \
                self.assertRaisesRegex(session.SessionError, "Impossible"):
            self.store.save("client_123", 1, migrated)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(list(path.parent.glob(".hookymod-session-*")), [])
        self.assertTrue(self.store.save("client_123", 1, migrated))
        self.assertEqual(next(path.parent.glob("*.pre-modmaker-*.json.bak")).read_bytes(), raw)

    def test_retired_backup_works_without_hard_link_support(self):
        old = snapshot("mobs")
        raw = json.dumps({"root": self.store.root, "state": old}).encode()
        path = self.store._path()
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        with patch("hookycreator.session.os.link", side_effect=OSError(errno.EINVAL, "unsupported")):
            self.assertTrue(self.store.save("client_123", 1, self.store.load()))
        self.assertEqual(next(path.parent.glob("*.pre-modmaker-*.json.bak")).read_bytes(), raw)
        self.assertEqual(list(path.parent.glob(".hookymod-session-*")), [])

    def test_failed_exclusive_backup_does_not_replace_original_session(self):
        old = snapshot("mobs")
        raw = json.dumps({"root": self.store.root, "state": old}).encode()
        path = self.store._path()
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        backup = path.with_name(f"{path.stem}.pre-modmaker-{hashlib.sha256(raw).hexdigest()}.json.bak")
        real_open = os.open

        def fail_after_partial(name, flags, *args, **kwargs):
            fd = real_open(name, flags, *args, **kwargs)
            if Path(name) == backup:
                os.write(fd, b"incomplete")
                os.close(fd)
                raise OSError(errno.ENOSPC, "disk full")
            return fd

        with patch("hookycreator.session.os.link", side_effect=OSError(errno.EINVAL, "unsupported")), \
                patch("hookycreator.session.os.open", side_effect=fail_after_partial), \
                self.assertRaisesRegex(session.SessionError, "Impossible"):
            self.store.save("client_123", 1, self.store.load())
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(backup.read_bytes(), b"incomplete")
        # An incomplete file must not be silently treated as a valid backup.
        with self.assertRaisesRegex(session.SessionError, "différente"):
            self.store.save("client_123", 1, self.store.load())
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(backup.read_bytes(), b"incomplete")

    def test_concurrent_backup_collision_never_replaces_existing_file(self):
        old = snapshot("studio")
        raw = json.dumps({"root": self.store.root, "state": old}).encode()
        path = self.store._path()
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        backup = path.with_name(f"{path.stem}.pre-modmaker-{hashlib.sha256(raw).hexdigest()}.json.bak")
        real_link = os.link

        def collision(source, destination):
            # Another instance creates a different file after our temporary
            # copy is complete but before it is published.
            backup.write_bytes(b"different, do not overwrite")
            return real_link(source, destination)

        with patch("hookycreator.session.os.link", side_effect=collision), \
                self.assertRaisesRegex(session.SessionError, "différente"):
            self.store.save("client_123", 1, self.store.load())
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(backup.read_bytes(), b"different, do not overwrite")
        self.assertEqual(list(path.parent.glob(".hookymod-session-*")), [])

    def test_checkpoint_failure_reuses_the_intact_backup(self):
        old = snapshot("create")
        raw = json.dumps({"root": self.store.root, "state": old}).encode()
        path = self.store._path()
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        migrated = self.store.load()
        with patch("hookycreator.session.write_atomic", side_effect=PermissionError("read only")), \
                self.assertRaisesRegex(session.SessionError, "Impossible"):
            self.store.save("client_123", 1, migrated)
        backup = next(path.parent.glob("*.pre-modmaker-*.json.bak"))
        timestamp = backup.stat().st_mtime_ns
        self.assertEqual(path.read_bytes(), raw)
        self.assertTrue(self.store.save("client_123", 1, migrated))
        self.assertEqual(backup.read_bytes(), raw)
        self.assertEqual(backup.stat().st_mtime_ns, timestamp)

    @unittest.skipUnless(hasattr(os, "symlink"), "liens symboliques indisponibles")
    def test_retired_backup_cannot_follow_a_symlink(self):
        old = snapshot("studio")
        raw = json.dumps({"root": self.store.root, "state": old}).encode()
        path = self.store._path()
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        outside = self.directory / "outside"
        outside.write_bytes(b"safe")
        backup = path.with_name(f"{path.stem}.pre-modmaker-{hashlib.sha256(raw).hexdigest()}.json.bak")
        try:
            backup.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"création des liens interdite : {error}")
        with self.assertRaises(session.SessionError):
            self.store.save("client_123", 1, self.store.load())
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(outside.read_bytes(), b"safe")

    def test_player_view_is_restored_as_a_normal_view_tab(self):
        state = snapshot("player")
        state["active"] = "view:player"
        self.assertTrue(self.store.save("client_123", 1, state))
        restored = self.store.load()
        self.assertEqual(restored["view"], "player")
        self.assertEqual(restored["active"], "view:player")
        self.assertIn({"type": "view", "view": "player"}, restored["tabs"])

    def test_corrupt_truncated_oversized_or_wrong_root_falls_back(self):
        self.store.save("client_123", 1, snapshot())
        path = self.store._path()
        for raw in (b"{", b"\xff", b"x" * (session.MAX_BYTES + 1),
                    json.dumps({"root": "somewhere else", "state": snapshot()}).encode(),
                    json.dumps({"root": self.store.root, "state": {"version": 2}}).encode()):
            with self.subTest(size=len(raw), prefix=raw[:20]):
                path.write_bytes(raw)
                self.assertIsNone(self.store.load())

    def test_invalid_requests_never_overwrite_the_previous_state(self):
        self.store.save("client_123", 1, snapshot())
        bad_states = [
            None, {"version": True}, {"version": 2}, {"version": 1, "view": "external"},
            {"version": 1, "tabs": [{"type": "exec", "rel": "../danger"}]},
            {"version": 1, "tabs": [{"type": "view", "view": "bad"}]},
            {"version": 1, "tabs": [{"type": "model", "rel": "\x00"}]},
            {"version": 1, "tabs": [{}] * (session.MAX_TABS + 1)},
            {"version": 1, "selection": ["x"] * (session.MAX_SELECTION + 1)},
            {"version": 1, "selection": ["a" * session.MAX_TEXT] * 2100},
            {"version": 1, "fields": {"model-shots": 1}},
            {"version": 1, "fields": {"search": "x" * (session.MAX_TEXT + 1)}},
            {"version": 1, "choices": {"model-target": []}},
            {"version": 1, "scroll": -1}, {"version": 1, "scroll": True},
            {"version": 1, "shown": {"models": 10_001}},
            {"version": 1, "inspector": "yes"},
        ]
        for state in bad_states:
            with self.subTest(state=str(state)[:120]), self.assertRaises(session.SessionError):
                self.store.save("client_123", 2, state)
        for client, sequence in (("x", 2), ("bad client", 2), ("client_123", -1),
                                 ("client_123", True), ("client_123", 2**53)):
            with self.subTest(client=client, sequence=sequence), self.assertRaises(session.SessionError):
                self.store.save(client, sequence, snapshot())
        self.assertEqual(self.store.load()["view"], "models")
        self.assertTrue(self.store.save("client_123", 2, snapshot("sounds")))

    def test_same_client_reordered_writes_cannot_replace_the_latest(self):
        barrier = threading.Barrier(16)
        def save(number):
            barrier.wait(timeout=5)
            return self.store.save("client_123", number,
                                   {**snapshot(), "fields": {"search": str(number)}})
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(save, range(1, 17)))
        self.assertTrue(any(results))
        self.assertEqual(self.store.load()["fields"]["search"], "16")
        self.assertFalse(self.store.save("client_123", 15, snapshot("sounds")))
        # Un autre client a son propre compteur ; l'écriture reçue en dernier gagne.
        self.assertTrue(self.store.save("client_456", 1, snapshot("sounds")))
        self.assertEqual(self.store.load()["view"], "sounds")

    def test_failed_atomic_write_does_not_consume_the_sequence(self):
        self.store.save("client_123", 1, snapshot())
        old = self.store._path().read_bytes()
        with patch("hookycreator.session.write_atomic", side_effect=PermissionError("read only")), \
                self.assertRaisesRegex(session.SessionError, "Impossible"):
            self.store.save("client_123", 2, snapshot("sounds"))
        self.assertEqual(self.store._path().read_bytes(), old)
        self.assertTrue(self.store.save("client_123", 2, snapshot("sounds")))
        self.assertEqual(self.store.load()["view"], "sounds")

    @unittest.skipUnless(hasattr(os, "symlink"), "liens symboliques indisponibles")
    def test_symlink_outside_work_is_not_written_or_read(self):
        outside = self.directory / "outside"
        outside.mkdir()
        self.work.mkdir()
        try:
            (self.work / "sessions").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"création des liens interdite : {error}")
        self.assertIsNone(self.store.load())
        with self.assertRaises(session.SessionError):
            self.store.save("client_123", 1, snapshot())
        self.assertEqual(list(outside.iterdir()), [])


class SessionHttpTests(unittest.TestCase):
    def test_restart_limits_and_desktop_origin_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, work = Path(tmp) / "root", Path(tmp) / "work"
            root.mkdir()
            token = secrets.token_urlsafe(32)
            server = HookyCreatorServer(root, work, host="127.0.0.1", port=0,
                                        desktop_token=token).start()
            def request(path, *, method="GET", data=None, headers=None):
                url = f"http://127.0.0.1:{server.port}{path}"
                req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
                try:
                    response = urllib.request.urlopen(req, timeout=5)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    return response.status, dict(response.headers), response.read()
            try:
                origin = f"http://127.0.0.1:{server.port}"
                body = json.dumps({"client": "client_123", "sequence": 1,
                                   "state": snapshot()}, ensure_ascii=False).encode()
                self.assertEqual(request("/api/ui-state")[0], 403)
                self.assertEqual(request("/api/ui-state", method="POST", data=body,
                                         headers={"Origin": origin, "Content-Type": "application/json"})[0], 403)
                status, headers, _ = request("/api/desktop/session", method="POST",
                    data=json.dumps({"token": token}).encode(),
                    headers={"Origin": origin, "Content-Type": "application/json"})
                self.assertEqual(status, 200)
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                auth = {"Cookie": cookie, "Origin": origin, "Content-Type": "application/json"}
                self.assertEqual(request("/api/ui-state", method="POST", data=body,
                                         headers={**auth, "Origin": "https://evil.example"})[0], 403)
                status, _, response = request("/api/ui-state", method="POST", data=body, headers=auth)
                self.assertEqual(status, 200, response)
                self.assertTrue(json.loads(response)["saved"])
                self.assertEqual(json.loads(request("/api/ui-state", headers=auth)[2])["state"]["view"], "models")
                self.assertEqual(request("/api/ui-state", method="POST", data=b"[]", headers=auth)[0], 400)
                connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
                connection.putrequest("POST", "/api/ui-state")
                for name, value in auth.items():
                    connection.putheader(name, value)
                connection.putheader("Content-Length", str(session.MAX_BYTES + 1))
                connection.endheaders()
                self.assertEqual(connection.getresponse().status, 413)
                connection.close()
            finally:
                server.stop()
            server = HookyCreatorServer(root, work, host="127.0.0.1", port=0).start()
            try:
                status, _, response = request("/api/ui-state")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(response)["state"]["choices"]["model-target"], "Model/Héros.bfres")
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
