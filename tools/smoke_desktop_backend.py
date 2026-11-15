"""Vérifie le sidecar PyInstaller réellement construit, sans ouvrir de fenêtre.

Usage : python tools/smoke_desktop_backend.py chemin/vers/hookycreator-backend
Les données sont synthétiques ; le processus et son port doivent s'arrêter à EOF.
"""
from __future__ import annotations

import http.cookiejar
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from desktop_fixture import make_desktop_fixture  # noqa: E402


def run(binary: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="hooky-sidecar-test-") as tmp:
        root = make_desktop_fixture(Path(tmp) / "dump")
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([str(binary.resolve()), str(root)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=log,
                                       env={**os.environ, "PYTHONUTF8": "1"})
            try:
                lines: queue.Queue[bytes] = queue.Queue()
                threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
                try:
                    line = lines.get(timeout=90)
                except queue.Empty:
                    raise RuntimeError("Le backend ne répond pas après 90 secondes.") from None
                if not line:
                    raise RuntimeError("Le backend s'est arrêté sans transmettre sa session.")
                ready = json.loads(line)
                if "error" in ready:
                    raise RuntimeError(ready["error"])
                origin = f"http://127.0.0.1:{int(ready['port'])}"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                     urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                try:
                    opener.open(origin + "/api/state", timeout=10)
                except urllib.error.HTTPError as exc:
                    assert exc.code == 403
                else:
                    raise AssertionError("Le backend accepte une requête sans session.")
                request = urllib.request.Request(
                    origin + "/api/desktop/session", method="POST",
                    data=json.dumps({"token": ready["token"]}).encode(),
                    headers={"Origin": origin, "Content-Type": "application/json"})
                with opener.open(request, timeout=10) as response:
                    assert response.status == 200
                    assert "HttpOnly" in response.headers["Set-Cookie"]
                with opener.open(origin + "/api/state", timeout=20) as response:
                    state = json.load(response)
                assert any(item["rel"] == "Model/Heros.bfres" for item in state["project"]["assets"])
                with opener.open(origin + "/api/system", timeout=10) as response:
                    engine = json.load(response)["engine"]
                assert engine["rust"]["available"], engine["rust"]
                assert engine["texture"]["name"] == "c", engine["texture"]
                with opener.open(origin + "/api/asset/check?kind=model&path=Model%2FHeros.bfres", timeout=30) as response:
                    assert json.load(response)["valid"]
                with opener.open(origin + "/api/sound/ogg?path=Model%2FSilence.bwav&download=1", timeout=30) as response:
                    assert response.read().startswith(b"OggS")
                request = urllib.request.Request(
                    origin + "/api/model/export", method="POST",
                    data=json.dumps({"path": "Model/Heros.bfres"}).encode(),
                    headers={"Origin": origin, "Content-Type": "application/json"})
                with opener.open(request, timeout=60) as response:
                    exported = json.load(response)
                assert Path(exported["absolute_folder"]).is_absolute()
                with opener.open(origin + exported["download_url"], timeout=60) as response:
                    content = response.read()
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    assert "Heros/Heros.gltf" in archive.namelist()
                    assert "Heros/Heros.bin" in archive.namelist()
                    assert len([name for name in archive.namelist() if name.endswith(".png")]) == 2
                process.stdin.close()
                assert process.wait(timeout=20) == 0
                print("Sidecar autonome : session, Rust, export, son et arrêt OK")
            except Exception:
                log.seek(0)
                print(log.read().decode("utf-8", errors="replace"), file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                if not process.stdin.closed:
                    process.stdin.close()
                process.stdout.close()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    run(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
