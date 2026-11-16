"""Reprise d'interface : bornes, isolation, concurrence et API native."""
from __future__ import annotations

import concurrent.futures
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
        "fields": {"search": "Héros", "mod-name": "Un brouillon", "model-shots": True},
        "choices": {"mob-model": "Model/Héros.bfres", "mob-textures": ["Model/Héros_Alb.bntx"]},
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
        self.assertEqual(restored["fields"]["mod-name"], "Un brouillon")
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
            {"version": 1, "choices": {"mob-textures": [""]}},
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
        with patch("hookycreator.session.write_atomic", side_effect=PermissionError("read only")):
            with self.assertRaisesRegex(session.SessionError, "Impossible"):
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
                self.assertEqual(json.loads(response)["state"]["fields"]["mod-name"], "Un brouillon")
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
