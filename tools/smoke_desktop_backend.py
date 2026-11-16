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
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from desktop_fixture import make_desktop_fixture  # noqa: E402

# These routes share one server-side import slot. A 429 is returned before the
# body is read or processed, so retrying that response cannot repeat an import.
IMPORT_ROUTES = frozenset({"/api/model/import", "/api/model/inspect",
                           "/api/video/import", "/api/sound/import"})


def post_json(opener, origin: str, route: str, data: dict) -> dict:
    request = urllib.request.Request(
        origin + route, method="POST", data=json.dumps(data).encode(),
        headers={"Origin": origin, "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with opener.open(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if route not in IMPORT_ROUTES or error.code != 429 or attempt == 2:
                raise
            error.close()
            # The server advertises Retry-After: 2 for this busy slot.
            print(f"Import temporairement occupé ; nouvelle tentative : {route}", flush=True)
            time.sleep(2)
    raise AssertionError("Nombre de tentatives inattendu")


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
            child_env = {**os.environ, "PYTHONUTF8": "1", "HOOKY_SMOKE_TRACEBACKS": "1"}
            # A standalone sidecar must not silently load dependencies from the
            # test runner's PYTHONPATH instead of its own bundled modules.
            child_env.pop("PYTHONPATH", None)
            process = subprocess.Popen([str(binary.resolve()), str(root)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=log, env=child_env)
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
                for protected in ("/api/state", "/api/video/info?path=Model%2FSynthetic.webm",
                                  "/api/video/stream?path=Model%2FSynthetic.webm"):
                    try:
                        opener.open(origin + protected, timeout=10)
                    except urllib.error.HTTPError as exc:
                        assert exc.code == 403, (protected, exc.code)
                    else:
                        raise AssertionError(f"Le backend accepte une requête sans session : {protected}")
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
                assert any(item["rel"] == "Model/Synthetic.webm" and item["kind"] == "video"
                           for item in state["project"]["assets"])
                assert any(item["rel"] == "Model/Heros.anim.bfres" and item["kind"] == "animation"
                           for item in state["project"]["assets"])
                with opener.open(origin + "/api/animation/list?path=Model%2FHeros.anim.bfres", timeout=10) as response:
                    assert json.load(response)["clips"][0]["name"] == "Walk"
                with opener.open(origin + "/api/animation/clip?path=Model%2FHeros.anim.bfres&index=0&download=1", timeout=10) as response:
                    assert "attachment" in response.headers["Content-Disposition"]
                    assert json.load(response)["bones"][0]["name"] == "Root"
                video_original = (root / "Model" / "Synthetic.webm").read_bytes()
                with opener.open(origin + "/api/video/info?path=Model%2FSynthetic.webm", timeout=10) as response:
                    video_info = json.load(response)
                assert video_info["format"] == "WebM" and not video_info["edited"], video_info
                request = urllib.request.Request(origin + "/api/video/stream?path=Model%2FSynthetic.webm",
                                                 headers={"Range": "bytes=4-15"})
                with opener.open(request, timeout=10) as response:
                    assert response.status == 206
                    assert response.headers["Content-Range"] == f"bytes 4-15/{len(video_original)}"
                    assert response.read() == video_original[4:16]
                with opener.open(origin + "/api/system", timeout=10) as response:
                    engine = json.load(response)["engine"]
                assert engine["rust"]["available"], engine["rust"]
                assert engine["texture"]["name"] == "c", engine["texture"]
                with opener.open(origin + "/api/asset/check?kind=model&path=Model%2FHeros.bfres", timeout=30) as response:
                    check = json.load(response)
                assert check["valid"] and check["injectable"], check
                with opener.open(origin + "/api/model/geometry?path=Model%2FHeros.bfres", timeout=30) as response:
                    assert response.headers.get_content_type() == "application/octet-stream"
                    assert response.read().startswith(b"H3D1")
                # A sidecar built earlier may still start correctly while serving
                # stale UI assets. Compare the bundled files with this checkout.
                for name in ("index.html", "style.css", "app.js", "i18n.js", "HookyMod.png",
                             "model_viewer.js", "model_animation.js", "shaders/common.glsl",
                             "shaders/easu.frag.glsl", "shaders/rcas.frag.glsl",
                             "shaders/fullscreen.vert.glsl", "shaders/LICENSE-FSR1.txt"):
                    with opener.open(origin + "/static/" + name, timeout=10) as response:
                        content = response.read()
                    assert len(content) > 100 and content == (ROOT / "hookycreator" / "web" / name).read_bytes(), name
                with opener.open(origin + "/api/sound/ogg?path=Model%2FSilence.bwav&download=1", timeout=30) as response:
                    assert response.read().startswith(b"OggS")
                # Exercise libsndfile and bundled codecs, not just the pure-Python
                # Opus remux. A missing numpy/CFFI/libsndfile must fail the build.
                formats = {"wav": b"RIFF", "flac": b"fLaC", "ogg": b"OggS"}
                wav = None
                for fmt, magic in formats.items():
                    print(f"Vérification de la conversion audio : {fmt}", flush=True)
                    with opener.open(origin + f"/api/sound/convert?path=Model%2FSilence.bwav&format={fmt}", timeout=30) as response:
                        converted = response.read()
                        assert converted.startswith(magic), (fmt, converted[:12])
                        if fmt == "wav": wav = converted
                with opener.open(origin + "/api/sound/convert?path=Model%2FSilence.bwav&format=mp3", timeout=30) as response:
                    mp3 = response.read()
                    assert mp3.startswith(b"ID3") or (mp3[0] == 0xff and mp3[1] & 0xe0 == 0xe0), mp3[:8]
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
                    return post_json(opener, origin, route, data)

                # Replacement copies the supplied bytes under the work directory;
                # neither the original nor codecs/game compatibility are rewritten.
                replacement = video_original + b"synthetic trailer"
                imported_video = post("/api/video/import", {"path": "Model/Synthetic.webm",
                                                              "video": base64.b64encode(replacement).decode("ascii")})
                assert imported_video["size"] == len(replacement), imported_video
                assert (root / "Model" / "Synthetic.webm").read_bytes() == video_original
                with opener.open(origin + "/api/video/info?path=Model%2FSynthetic.webm", timeout=10) as response:
                    assert json.load(response)["edited"]
                with opener.open(origin + "/api/video/stream?path=Model%2FSynthetic.webm&download=1", timeout=10) as response:
                    assert response.read() == replacement
                    assert "attachment" in response.headers["Content-Disposition"]

                sound_original = (root / "Model" / "Silence.bwav").read_bytes()
                imported_sound = post("/api/sound/import", {"path": "Model/Silence.bwav",
                                                              "audio": base64.b64encode(wav).decode("ascii")})
                assert imported_sound["format"] == "BWAV PCM16", imported_sound
                assert (root / "Model" / "Silence.bwav").read_bytes() == sound_original
                with opener.open(origin + "/api/sound/info?path=Model%2FSilence.bwav", timeout=30) as response:
                    assert json.load(response)["format"] == "BWAV PCM16"

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
                print("Sidecar autonome : session, Rust, PNG, glTF/OBJ, injection, animation, audio, vidéo et arrêt OK")
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
