"""HookyCreator — serveur local (bibliothèque standard uniquement).

L'application est une interface web servie depuis le poste : pas d'installation,
pas de Tkinter, et l'éditeur s'ouvre dans le navigateur, là où les outils de
retouche d'image sont déjà.

* API JSON sous ``/api/...`` (tout échec renvoie ``{"error": "..."}`` en 400) ;
* fichiers statiques sous ``/static/...`` ;
* les chemins d'assets sont toujours relatifs au projet et validés côté serveur.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .project import Project, ProjectError

WEB_DIR = Path(__file__).parent / "web"


class HookyCreatorServer:
    """Serveur HTTP + état du projet (un projet à la fois, comme un éditeur)."""

    def __init__(self, root: str | Path, work: str | Path | None = None,
                 host: str = "0.0.0.0", port: int = 8765) -> None:
        self.project = Project(Path(root), Path(work) if work else None)
        self.project.scan()
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.job: dict = {"running": False, "done": 0, "total": 0, "label": "",
                          "report": None, "error": None}

    # ------------------------------------------------------------------ cycle
    def start(self, open_browser: bool = False) -> "HookyCreatorServer":
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{self.port}/")
        return self

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    # ------------------------------------------------------------------ routes
    def api(self, method: str, route: str, query: dict, body: dict) -> tuple[bytes, str]:
        """Dispatch de l'API : ``(contenu, type)``."""
        project = self.project

        if route == "/api/state":
            return _json({"project": project.state()})

        if route == "/api/textures":
            entries = [a.as_json() for a in project.by_kind("texture")]
            return _json({"textures": entries})

        if route == "/api/models":
            entries = [a.as_json() for a in project.by_kind("model")]
            return _json({"models": entries})

        if route == "/api/texture/info":
            return _json(project.texture_info(query["path"][0]))

        if route == "/api/texture/png":
            index = int(query.get("index", ["0"])[0])
            mip = int(query.get("mip", ["0"])[0])
            return project.texture_png(query["path"][0], index, mip), "image/png"

        if route == "/api/texture/import":
            image = base64.b64decode(body["image"])
            return _json(project.import_texture(body["path"], int(body.get("index", 0)), image))

        if route == "/api/texture/export":
            data = project.texture_png(query["path"][0], int(query.get("index", ["0"])[0]))
            directory = project.work / "export"
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{Path(query['path'][0]).name.split('.')[0]}.png"
            target.write_bytes(data)
            return _json({"file": project.shown_path(target)})

        if route == "/api/model/info":
            return _json(project.model_info(query["path"][0]))

        if route == "/api/model/preview":
            width = max(64, min(1600, int(query.get("w", ["640"])[0])))
            height = max(64, min(1600, int(query.get("h", ["480"])[0])))
            yaw = float(query.get("yaw", ["0.7"])[0])
            pitch = float(query.get("pitch", ["-0.35"])[0])
            views = max(1, min(8, int(query.get("views", ["1"])[0])))
            return project.model_preview(query["path"][0], width, height, yaw, pitch,
                                         views), "image/png"

        if route == "/api/model/export":
            return _json(project.export_model(body["path"], textures=bool(body.get("textures", True))))

        if route == "/api/mobs":
            if method == "POST":
                return _json(project.create_mob(body.get("name", ""), body.get("model", ""),
                                                body.get("textures", []),
                                                body.get("parent", "")))
            return _json({"mobs": project.list_mobs()})

        if route == "/api/bulk/start":
            return _json(self.start_bulk(body.get("kinds", ["texture", "model"]),
                                         body.get("limit")))

        if route == "/api/bulk/status":
            return _json(self.job)

        if route == "/api/mod/build":
            return _json(project.build_mod(body.get("name", "HookyMod")))

        raise ProjectError(f"Route inconnue : {route}")

    # ------------------------------------------------------------------ export global
    def start_bulk(self, kinds, limit=None) -> dict:
        """Lance l'export de tout le projet en tâche de fond (non bloquant)."""
        from .bulk import export_all

        if self.job["running"]:
            raise ProjectError("Un export est déjà en cours.")

        def progress(done: int, total: int, label: str) -> None:
            self.job.update(done=done, total=total, label=label)

        def run() -> None:
            self.job.update(running=True, done=0, total=0, label="analyse…",
                            report=None, error=None)
            try:
                out = self.project.work / "export_all"
                report = export_all(self.project, out,
                                    kinds=tuple(kinds) or ("texture", "model"),
                                    limit=limit, progress=progress)
                self.job["report"] = report
            except Exception as exc:          # l'UI doit voir la raison
                self.job["error"] = str(exc)
            finally:
                self.job["running"] = False
                self.job["label"] = ""

        threading.Thread(target=run, daemon=True).start()
        return {"started": True}

    # ------------------------------------------------------------------ statique
    def static(self, path: str) -> tuple[bytes, str] | None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        rel = rel.removeprefix("static/")
        candidate = (WEB_DIR / rel).resolve()
        if not str(candidate).startswith(str(WEB_DIR)) or not candidate.is_file():
            return None
        kind = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        return candidate.read_bytes(), kind


def _json(payload: dict) -> tuple[bytes, str]:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"


def _make_handler(server: HookyCreatorServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HookyCreator/1.0"

        def log_message(self, fmt, *args):        # journal discret
            if "--verbose" in __import__("sys").argv:
                super().log_message(fmt, *args)

        # -- utilitaires
        def _send(self, body: bytes, kind: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, message: str, status: int = 400) -> None:
            self._send(*_json({"error": message}), status=status)

        def do_GET(self) -> None:                # noqa: N802 (API HTTP)
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                try:
                    body, kind = server.api("GET", parsed.path, parse_qs(parsed.query), {})
                except ProjectError as exc:
                    return self._error(str(exc))
                except Exception as exc:         # jamais de trace dans l'UI
                    return self._error(f"Erreur interne : {exc}", 500)
                return self._send(body, kind)
            found = server.static(parsed.path)
            if not found:
                return self._error("Page inconnue", 404)
            self._send(*found)

        def do_POST(self) -> None:               # noqa: N802
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                return self._error("Requête JSON invalide.")
            if not parsed.path.startswith("/api/"):
                return self._error("Page inconnue", 404)
            try:
                content, kind = server.api("POST", parsed.path, parse_qs(parsed.query), body)
            except ProjectError as exc:
                return self._error(str(exc))
            except KeyError as exc:
                return self._error(f"Champ manquant : {exc}")
            except Exception as exc:
                return self._error(f"Erreur interne : {exc}", 500)
            self._send(content, kind)

    return Handler


def serve(root, work=None, host: str = "0.0.0.0", port: int = 8765,
          open_browser: bool = False) -> HookyCreatorServer:
    return HookyCreatorServer(root, work, host, port).start(open_browser)
