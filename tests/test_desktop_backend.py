"""Protection et durée de vie du backend local de la fenêtre Tauri."""
from __future__ import annotations

import base64
import json
import secrets
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_ensembles_blend import _make_project  # noqa: E402
from synth_video import make_webm  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402


class DesktopBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = _make_project(Path(self.tmp.name))
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "hookycreator.desktop", str(self.project.root),
             "--work", str(self.project.work)],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(self.stop_process)
        self.assertIsNotNone(self.process.stdout)
        ready = json.loads(self.process.stdout.readline().decode("utf-8"))
        self.assertNotIn("error", ready)
        self.token = ready["token"]
        self.origin = f"http://127.0.0.1:{ready['port']}"
        self.cookie_name = f"hooky_session_{ready['port']}"

    def stop_process(self) -> None:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()

    def request(self, route: str, *, method: str = "GET", headers: dict | None = None,
                body: bytes | None = None):
        request = urllib.request.Request(self.origin + route, method=method,
                                         headers=headers or {}, data=body)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, dict(response.headers), response.read()

    def login(self, token: str | None = None) -> tuple[str, dict]:
        status, headers, body = self.request(
            "/api/desktop/session", method="POST",
            headers={"Origin": self.origin, "Content-Type": "application/json"},
            body=json.dumps({"token": token or self.token}).encode(),
        )
        self.assertEqual(status, 200, body)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertTrue(cookie.startswith(f"{self.cookie_name}="))
        return cookie.split(";", 1)[0], headers

    def test_only_authenticated_same_origin_may_access_or_change_data(self) -> None:
        status, headers, _ = self.request("/api/state")
        self.assertEqual(status, 403)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        status, _, _ = self.request("/api/desktop/session", method="POST",
            headers={"Origin": self.origin, "Content-Type": "application/json"},
            body=b'{"token":"incorrect"}')
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/desktop/session", method="POST",
            headers={"Origin": self.origin, "Content-Type": "application/json"},
            body=json.dumps({"token": "é" * 43}).encode())
        self.assertEqual(status, 403)
        cookie, _ = self.login()
        auth = {"Cookie": cookie}
        status, _, data = self.request("/api/state", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(Path(json.loads(data)["project"]["root"]), self.project.root)

        for headers in ({**auth, "Origin": "https://evil.example"},
                        {**auth, "Origin": self.origin.replace(":", ":", 1) + "0"},
                        {**auth, "Sec-Fetch-Site": "cross-site"},
                        {**auth, "Sec-Fetch-Site": "same-site"},
                        {**auth, "Host": "evil.example"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/state", headers=headers)[0], 403)
        for route in ("/api/shutdown", "/api/scan", "/api/texture/export?path=Model/Heros_Alb.bntx"):
            with self.subTest(route=route):
                self.assertEqual(self.request(route, headers=auth)[0], 405)
        post = {**auth, "Content-Type": "application/json"}
        self.assertEqual(self.request("/api/scan", method="POST", headers=post, body=b"{}")[0], 403)
        self.assertEqual(self.request("/api/scan", method="POST",
                          headers={**post, "Origin": "http://127.0.0.1:1"}, body=b"{}")[0], 403)
        self.assertEqual(self.request("/api/scan", method="POST",
                          headers={**post, "Origin": self.origin}, body=b"{}")[0], 200)
        self.assertEqual(self.request("/api/unknown", method="POST",
                          headers={**post, "Origin": self.origin}, body=b"{}")[0], 405)

    def test_interactive_geometry_and_fsr_assets_require_the_desktop_session(self) -> None:
        route = "/api/model/geometry?path=Model%2FHeros.bfres"
        self.assertEqual(self.request(route)[0], 403)
        cookie, _ = self.login()
        status, headers, body = self.request(route, headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertTrue(body.startswith(b"H3D1"))
        self.assertEqual(self.request(route, headers={"Cookie": cookie, "Sec-Fetch-Site": "cross-site"})[0], 403)
        for name in ("model_viewer.js", "shaders/common.glsl", "shaders/easu.frag.glsl",
                     "shaders/rcas.frag.glsl", "shaders/fullscreen.vert.glsl"):
            status, _, content = self.request("/static/" + name, headers={"Cookie": cookie})
            self.assertEqual(status, 200, name)
            self.assertGreater(len(content), 100)

    def test_video_stream_and_import_require_the_desktop_session(self) -> None:
        original = make_webm()
        source = self.project.root / "intro.webm"
        source.write_bytes(original)
        stream = "/api/video/stream?path=intro.webm"
        info = "/api/video/info?path=intro.webm"
        self.assertEqual(self.request(stream)[0], 403)
        self.assertEqual(self.request(info)[0], 403)
        self.assertEqual(self.request("/api/video/import", method="POST", body=b"{}")[0], 403)
        cookie, _ = self.login()
        auth = {"Cookie": cookie}
        status, headers, data = self.request(stream, headers={**auth, "Range": "bytes=2-10"})
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], f"bytes 2-10/{len(original)}")
        self.assertEqual(data, original[2:11])
        self.assertEqual(self.request(stream, headers={**auth, "Sec-Fetch-Site": "cross-site"})[0], 403)
        replacement = original + b"test trailer"
        body = json.dumps({"path": "intro.webm", "video": base64.b64encode(replacement).decode()}).encode()
        post = {**auth, "Content-Type": "application/json"}
        self.assertEqual(self.request("/api/video/import", method="POST", headers=post, body=body)[0], 403)
        status, _, data = self.request("/api/video/import", method="POST",
                                       headers={**post, "Origin": self.origin}, body=body)
        self.assertEqual(status, 200, data)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual((self.project.work / "assets" / "intro.webm").read_bytes(), replacement)
        self.assertTrue(json.loads(self.request(info, headers=auth)[2])["edited"])
        self.assertEqual(self.request(stream, headers=auth)[2], replacement)

    def test_login_page_is_static_and_export_post_still_works(self) -> None:
        status, headers, html = self.request("/desktop-login")
        self.assertEqual(status, 200)
        self.assertIn(b"desktop-login.js", html)
        self.assertNotIn(self.token.encode(), html)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        cookie, _ = self.login()
        status, _, body = self.request(
            "/api/texture/export", method="POST",
            headers={"Cookie": cookie, "Origin": self.origin, "Content-Type": "application/json"},
            body=json.dumps({"path": "Model/Heros_Alb.bntx", "index": 0}).encode(),
        )
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        output = Path(result["absolute_file"])
        self.assertTrue(output.is_relative_to(self.project.work))
        self.assertTrue(output.is_file())
        self.assertEqual(result["index"], 0)

    def test_selection_returns_a_retryable_authenticated_http_archive(self) -> None:
        cookie, _ = self.login()
        auth = {"Cookie": cookie, "Origin": self.origin, "Content-Type": "application/json"}
        status, _, data = self.request(
            "/api/export/selection", method="POST", headers=auth,
            body=json.dumps({"items": [{"kind": "model", "rel": "Model/Heros.bfres"}],
                             "download_link": True}).encode(),
        )
        self.assertEqual(status, 200, data)
        result = json.loads(data)
        self.assertTrue(result["temporary_archive"])
        self.assertTrue(result["download_url"].endswith("&kind=selection"))
        self.assertEqual(self.request(result["download_url"])[0], 403)
        for _ in range(2):
            status, headers, archive = self.request(result["download_url"], headers={"Cookie": cookie})
            self.assertEqual(status, 200)
            self.assertIn("hookycreator-selection.zip", headers["Content-Disposition"])
            with zipfile.ZipFile(BytesIO(archive)) as files:
                self.assertIn("modeles/Heros/Heros.gltf", files.namelist())

    def test_parent_eof_stops_backend_and_closes_port(self) -> None:
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=10), 0)
        with self.assertRaises(urllib.error.URLError):
            self.request("/")

    def test_parent_eof_during_initial_scan_does_not_leave_an_orphan(self) -> None:
        script = (
            "import time; import hookycreator.desktop as desktop; "
            "desktop.HookyCreatorServer=lambda *a, **k: time.sleep(30); "
            "desktop.main(['.'])"
        )
        process = subprocess.Popen([sys.executable, "-u", "-c", script], cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            process.stdin.close()
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()

    def test_desktop_configuration_cannot_bind_to_the_lan_or_a_fixed_port(self) -> None:
        token = secrets.token_urlsafe(32)
        with self.assertRaises(ValueError):
            HookyCreatorServer(self.project.root, host="0.0.0.0", port=0, desktop_token=token)
        with self.assertRaises(ValueError):
            HookyCreatorServer(self.project.root, host="127.0.0.1", port=8765, desktop_token=token)
        with self.assertRaises(ValueError):
            HookyCreatorServer(self.project.root, host="127.0.0.1", port=0, desktop_token="weak")


if __name__ == "__main__":
    unittest.main()
