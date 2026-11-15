"""Vérifie le sidecar PyInstaller réellement construit, sans ouvrir de fenêtre.

Usage : python tools/smoke_desktop_backend.py chemin/vers/hookycreator-backend
Les données sont synthétiques ; le processus et son port doivent s'arrêter à EOF.
"""
from __future__ import annotations

import base64
import http.cookiejar
import io
import json
import os
import queue
import struct
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
        # Fixture minimale du layout historique 9.x que l'injection accepte ;
        # cela ne constitue pas une validation d'un BFRES réel ni en jeu.
        model = root / "Model" / "Heros.bfres"
        original = bytearray(model.read_bytes())
        struct.pack_into("<I", original, 8, 0x00090002)
        original = bytes(original)
        model.write_bytes(original)
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
                    check = json.load(response)
                assert check["valid"] and check["injectable"], check
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
                    assert "Heros/RAPPORT_MODELE.txt" in archive.namelist()
                    assert len([name for name in archive.namelist() if name.endswith(".png")]) == 2
                    assert not any(name.endswith(".hookycreator-source.json") for name in archive.namelist())
                    gltf, geometry = archive.read("Heros/Heros.gltf"), archive.read("Heros/Heros.bin")

                def post(route: str, data: dict) -> dict:
                    request = urllib.request.Request(
                        origin + route, method="POST", data=json.dumps(data).encode(),
                        headers={"Origin": origin, "Content-Type": "application/json"})
                    with opener.open(request, timeout=60) as response:
                        return json.load(response)

                png = post("/api/texture/export", {"path": "Model/Heros_Alb.bntx", "index": 0})
                assert png["index"] == 0 and Path(png["absolute_file"]).read_bytes().startswith(b"\x89PNG")
                def as_file(name: str, data: bytes) -> dict:
                    return {"name": name, "data": base64.b64encode(data).decode("ascii")}

                inspected = post("/api/model/inspect", {"files": [as_file("Heros.gltf", gltf),
                                                                      as_file("Heros.bin", geometry)]})
                assert inspected["faces"] == 1 and inspected["can_import"], inspected
                obj = b"v 0 0 0\nv 2 0 0\nv 0 3 0\nf 1 2 3\n"
                result = post("/api/model/import", {"files": [as_file("triangle.obj", obj)],
                                                     "target": "Model/Heros.bfres", "fit": False})
                assert result["faces"] == 1 and result["vertices"] == 3, result
                copy = root / "hookycreator_work" / "assets" / "Model" / "Heros.bfres"
                assert model.read_bytes() == original and copy.is_file()
                assert struct.unpack_from("<9f", copy.read_bytes(), 0x808) == (0., 0., 0., 2., 0., 0., 0., 3., 0.)
                process.stdin.close()
                assert process.wait(timeout=20) == 0
                print("Sidecar autonome : session, Rust, PNG, glTF/OBJ, injection, son et arrêt OK")
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
