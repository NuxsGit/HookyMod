"""Sécurité du serveur navigateur : boucle locale et écritures explicites."""
from __future__ import annotations

import http.client
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hookycreator import __version__
from hookycreator.server import HookyCreatorServer, READ_ROUTES, WRITE_ROUTES


class BrowserServerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "assets"
        root.mkdir()
        self.server = HookyCreatorServer(root, Path(self.temp.name) / "work", port=0).start()
        self.addCleanup(self.server.stop)
        self.origin = f"http://127.0.0.1:{self.server.port}"

    def request(self, method, route, *, headers=None, body=None, server=None):
        server = server or self.server
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            conn.request(method, route, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_default_is_loopback_and_identifies_the_current_version(self):
        self.assertEqual(self.server.host, "127.0.0.1")
        self.assertEqual(self.server.url, self.origin + "/")
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(f"HookyCreator/{__version__}", headers["Server"])

    def test_get_cannot_start_an_operation_that_writes_files(self):
        with mock.patch.object(self.server.project, "scan", return_value={"count": 0}) as build:
            status, _, _ = self.request("GET", "/api/scan")
            self.assertEqual(status, 405)
            build.assert_not_called()

    def test_cross_origin_and_non_json_posts_cannot_start_a_scan(self):
        with mock.patch.object(self.server.project, "scan", return_value={"count": 0}) as build:
            for headers, expected in [
                ({"Origin": "https://attacker.invalid", "Content-Type": "application/json"}, 403),
                ({"Origin": "null", "Content-Type": "application/json"}, 403),
                ({"Origin": self.origin, "Content-Type": "text/plain"}, 415),
                ({"Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"}, 403),
            ]:
                with self.subTest(headers=headers):
                    status, _, _ = self.request("POST", "/api/scan", headers=headers, body=b"{}")
                    self.assertEqual(status, expected)
            build.assert_not_called()
            status, _, _ = self.request("POST", "/api/scan", headers={
                "Origin": self.origin, "Content-Type": "application/json; charset=utf-8"}, body=b"{}")
            self.assertEqual(status, 200)
            build.assert_called_once()

    def test_loopback_host_blocks_dns_rebinding_but_accepts_localhost(self):
        status, _, _ = self.request("GET", "/", headers={"Host": f"attacker.invalid:{self.server.port}"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/", headers={"Host": f"localhost:{self.server.port}"})
        self.assertEqual(status, 200)
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                        {"Origin": "https://attacker.invalid"}, {"Origin": self.origin + "0"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("GET", "/api/state", headers=headers)[0], 403)

    def test_every_write_only_api_route_rejects_get(self):
        for route in sorted(WRITE_ROUTES - READ_ROUTES):
            with self.subTest(route=route):
                self.assertEqual(self.request("GET", route)[0], 405)
        self.assertEqual(self.request("POST", "/api/state", headers={
            "Content-Type": "application/json"}, body=b"{}")[0], 405)

    def test_json_cli_clients_do_not_need_to_forge_an_origin(self):
        with mock.patch.object(self.server.project, "scan", return_value={"count": 0}) as build:
            self.assertEqual(self.request("POST", "/api/scan", headers={
                "Content-Type": "application/json"}, body=b"{}")[0], 200)
            self.assertEqual(self.request("POST", "/api/scan", headers={
                "Content-Type": "application/json"}, body=b"[]")[0], 400)
            build.assert_called_once()

    def test_ambiguous_headers_are_refused_before_any_write(self):
        with mock.patch.object(self.server.project, "scan", return_value={"count": 0}) as build:
            for extra in (("Host", self.origin.removeprefix("http://")),
                          ("Origin", self.origin), ("Sec-Fetch-Site", "same-origin"),
                          ("Content-Type", "application/json"), ("Content-Length", "2"),
                          ("Transfer-Encoding", "chunked")):
                with self.subTest(extra=extra):
                    connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
                    try:
                        connection.putrequest("POST", "/api/scan")
                        connection.putheader("Origin", self.origin)
                        connection.putheader("Sec-Fetch-Site", "same-origin")
                        connection.putheader("Content-Type", "application/json")
                        connection.putheader("Content-Length", "2")
                        connection.putheader(*extra)
                        connection.endheaders(b"{}")
                        self.assertIn(connection.getresponse().status, (400, 403, 415))
                    finally:
                        connection.close()
            build.assert_not_called()

    def test_explicit_wildcard_binding_accepts_a_same_origin_preview_proxy(self):
        root = Path(self.temp.name) / "assets"
        server = HookyCreatorServer(root, Path(self.temp.name) / "other-work", host="0.0.0.0", port=0).start()
        self.addCleanup(server.stop)
        self.assertEqual(server.url, f"http://127.0.0.1:{server.port}/")
        proxy_host = {"Host": "8767-example.e2b.app"}
        # A cross-site iframe navigation is how the preview embeds the app;
        # subsequent same-origin fetches still receive the normal API checks.
        frame = {**proxy_host, "Sec-Fetch-Site": "cross-site",
                 "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"}
        self.assertEqual(self.request("GET", "/", server=server, headers=frame)[0], 200)
        self.assertEqual(self.request("GET", "/api/state", server=server, headers=frame)[0], 403)
        local_frame = {**frame, "Host": f"127.0.0.1:{self.server.port}"}
        self.assertEqual(self.request("GET", "/", headers=local_frame)[0], 403)  # default loopback
        with mock.patch.object(server.project, "scan", return_value={"count": 0}) as build:
            status, _, _ = self.request("POST", "/api/scan", server=server, headers={
                **proxy_host, "Origin": "https://8767-example.e2b.app", "Content-Type": "application/json"}, body=b"{}")
            self.assertEqual(status, 200)
            build.assert_called_once()


if __name__ == "__main__":
    unittest.main()
