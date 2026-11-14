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
import gzip
import hashlib
import json
import mimetypes
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from hookymod import bntx, native, restbl, sarc, zs

from . import blend as blend_mod
from .project import MAX_SCAN_FILES, Project, ProjectError

WEB_DIR = Path(__file__).parent / "web"

#: routes qui coûtent un rendu CPU (limitées en parallèle)
IMAGE_ROUTES = ("/api/model/preview", "/api/texture/png", "/api/blend/preview")


class ThumbnailActivity:
    """Le préchauffage explicite cède aux vignettes demandées par les vues."""

    def __init__(self, quiet_seconds: float = 1.5) -> None:
        self.quiet_seconds = quiet_seconds
        self._last = float("-inf")
        self._lock = threading.Lock()

    def note(self) -> None:
        with self._lock:
            self._last = time.monotonic()

    def _remaining(self) -> float:
        with self._lock:
            return self.quiet_seconds - (time.monotonic() - self._last)

    def idle(self) -> bool:
        return self._remaining() <= 0

    def wait_idle(self, cancelled: threading.Event) -> bool:
        while not cancelled.is_set():
            remaining = self._remaining()
            if remaining <= 0:
                return True
            cancelled.wait(min(0.1, remaining))
        return False


class HookyCreatorServer:
    """Serveur HTTP + état du projet (un projet à la fois, comme un éditeur)."""

    def __init__(self, root: str | Path, work: str | Path | None = None,
                 host: str = "0.0.0.0", port: int = 8765, *,
                 scan_limit: int = MAX_SCAN_FILES) -> None:
        self.project = Project(Path(root), Path(work) if work else None, scan_limit=scan_limit)
        self.scan_summary = self.project.scan()
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.stopped = False
        # Au plus un rendu de vignette et deux rendus au total : une action
        # interactive garde toujours une place, même avec plusieurs onglets.
        self.render_slots = threading.BoundedSemaphore(2)
        self.thumbnail_slot = threading.BoundedSemaphore(1)
        self.thumbnail_activity = ThumbnailActivity()
        self._job_lock = threading.Lock()
        self.job: dict = {"running": False, "done": 0, "total": 0, "label": "",
                          "report": None, "error": None}
        # fichiers statiques lus une fois : un rechargement ne relit pas le disque
        self._static_cache: dict[str, tuple] = {}

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
        self.project.stop_warming()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.stopped = True

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    # ------------------------------------------------------------------ routes
    def api(self, method: str, route: str, query: dict, body: dict) -> tuple[bytes, str]:
        """Dispatch de l'API : ``(contenu, type)``."""
        project = self.project

        if route == "/api/state":
            return project.state_payload(), "application/json"

        if route == "/api/settings":
            if method == "POST":
                project.update_settings(body or {})
            # Une autre requête peut avoir mis les réglages à jour entre-temps :
            # renvoyer la version courante et sa révision du même instantané.
            return _json(project.settings_snapshot())

        if route == "/api/model/plan":
            width = _size(query, "w", 640)
            height = _size(query, "h", 480)
            faces = query.get("faces", [None])[0]
            plan = project.preview_plan(query["path"][0], width, height,
                                        int(faces) if faces else None)
            return _json({"plan": plan, "settings": project.settings.as_json()})

        if route == "/api/textures":
            return project.cached_json("textures", lambda: {
                "textures": [a.as_json() for a in project.by_kind("texture")]}), \
                "application/json"

        if route == "/api/models":
            return project.cached_json("models", lambda: {
                "models": [a.as_json() for a in project.by_kind("model")]}), \
                "application/json"

        if route == "/api/texture/info":
            return _json(project.texture_info(query["path"][0]))

        if route == "/api/texture/png":
            index = int(query.get("index", ["0"])[0])
            mip = int(query.get("mip", ["0"])[0])
            max_size = query.get("max", [None])[0]
            return project.texture_png(query["path"][0], index, mip,
                                       max_size=int(max_size) if max_size else None), \
                "image/png"

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

        if route == "/api/ensembles":
            return project.cached_json("ensembles", lambda: {
                "ensembles": project.ensembles(),
                "blender": _blender_state(project)}), "application/json"

        if route == "/api/blender":
            return _json(_blender_state(project))

        if route == "/api/model/info":
            return _json(project.model_info(query["path"][0]))

        if route == "/api/model/preview":
            width = _size(query, "w", 640)
            height = _size(query, "h", 480)
            yaw = float(query.get("yaw", ["0.7"])[0])
            pitch = float(query.get("pitch", ["-0.35"])[0])
            views = max(1, min(8, int(query.get("views", ["1"])[0])))
            faces = query.get("faces", [None])[0]
            textured = query.get("tex", ["1" if project.settings.textured_previews
                                         else "0"])[0] not in ("0", "false", "")
            return project.model_preview(query["path"][0], width, height, yaw, pitch,
                                         views,
                                         int(faces) if faces else None,
                                         textured=textured), "image/png"

        if route == "/api/model/uv":
            if "path" not in query:
                raise ProjectError("Paramètre « path » manquant.")
            size = _size(query, "size", 640)
            flip = query.get("flip", ["0"])[0] not in ("0", "false", "")
            return project.uv_overlay(query["path"][0], size=size, flip=flip), \
                "image/png"

        if route == "/api/model/uv/info":
            if "path" not in query:
                raise ProjectError("Paramètre « path » manquant.")
            return _json(project.uv_info(query["path"][0]))

        if route == "/api/model/blend":
            return _json(project.export_blend(body["path"],
                                              textures=bool(body.get("textures", True))))

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
            with self._job_lock:
                return _json(self.job)

        # -- traduction .blend → modèle du jeu
        if route == "/api/blend/list":
            return _json({"blends": blend_mod.list_blends(project)})

        if route == "/api/blend/inspect":
            ref = body.get("ref") if method == "POST" else query.get("ref", [""])[0]
            return _json(blend_mod.inspect(
                project, ref,
                axis=str((body or {}).get("axis", "y_up")),
                scale=_scale(body or {}),
                only=(body or {}).get("only") or None))

        if route == "/api/blend/preview":
            return blend_mod.preview_png(
                project, query.get("ref", [""])[0], _size(query, "w", 640),
                _size(query, "h", 480),
                axis=str(query.get("axis", ["y_up"])[0]),
                scale=_scale(query),
                only=query.get("only", [None])[0] or None,
                views=max(1, min(8, int(query.get("views", ["1"])[0])))), "image/png"

        if route == "/api/blend/translate":
            return _json(blend_mod.translate(
                project, body.get("ref", ""), body.get("target", ""),
                model=int(body.get("model", 0) or 0), shape=int(body.get("shape", 0) or 0),
                axis=str(body.get("axis", "y_up")), scale=_scale(body),
                only=body.get("only") or None, fit=bool(body.get("fit", True))))

        if route == "/api/mod/build":
            return _json(project.build_mod(body.get("name", "HookyMod")))

        # -- cache, mémoire, maintenance
        if route == "/api/system":
            return _json({"cache": project.cache_stats(),
                          "settings": project.settings.as_json(),
                          "engine": {"rust": native.status(),
                                     "texture": bntx.decoder_backend(),
                                     "zstd": zs.decoder_backend(),
                                     "sarc": sarc.backend(),
                                     "restbl": restbl.backend()}})

        if route == "/api/thumbs/warm":
            return _json({"warm": project.warm_thumbnails(
                background_slot=self.thumbnail_slot, render_slots=self.render_slots,
                visible_activity=self.thumbnail_activity)})

        if route == "/api/thumbs/stop":
            return _json({"warm": project.stop_warming()})

        if route == "/api/thumbs/status":
            return _json({"warm": project.warm_status()})

        if route == "/api/cache/clear":
            removed = project.clear_cache()
            return _json({"cleared": removed, "cache": project.cache_stats()})

        if route == "/api/scan":
            summary = project.scan()
            self.scan_summary = summary
            return _json({"scan": summary, "project": project.state()})

        if route == "/api/export/selection":
            if body.get("blend"):
                return project.export_selection_blend(body.get("items") or []), \
                    "application/zip"
            return project.export_selection(body.get("items") or []), "application/zip"

        if route == "/api/shutdown":
            threading.Thread(target=self._stop_soon, daemon=True).start()
            return _json({"stopping": True})

        raise ProjectError(f"Route inconnue : {route}")

    def _stop_soon(self) -> None:
        time.sleep(0.4)                 # laisser la réponse HTTP partir
        self.stop()

    # ------------------------------------------------------------------ export global
    def start_bulk(self, kinds, limit=None) -> dict:
        """Lance l'export de tout le projet en tâche de fond (non bloquant)."""
        from .bulk import export_all

        def progress(done: int, total: int, label: str) -> None:
            with self._job_lock:
                self.job.update(done=done, total=total, label=label)

        def run() -> None:
            report = None
            error = None
            try:
                out = self.project.work / "export_all"
                report = export_all(self.project, out,
                                    kinds=tuple(kinds) or ("texture", "model"),
                                    limit=limit, progress=progress)
            except Exception as exc:          # l'UI doit voir la raison
                error = str(exc)
            finally:
                with self._job_lock:
                    self.job.update(running=False, label="", report=report, error=error)

        # Réserver le travail avant de lancer le fil pour que deux requêtes ne
        # démarrent pas simultanément et n'écrivent pas dans le même dossier.
        with self._job_lock:
            if self.job["running"]:
                raise ProjectError("Un export est déjà en cours.")
            self.job.update(running=True, done=0, total=0, label="analyse…",
                            report=None, error=None)
            try:
                threading.Thread(target=run, daemon=True).start()
            except Exception:
                self.job.update(running=False, label="")
                raise
        return {"started": True}

    # ------------------------------------------------------------------ statique
    def static(self, path: str) -> tuple[bytes, str, str] | None:
        """Fichier statique + son ETag (calculé une fois par version du fichier)."""
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        rel = rel.removeprefix("static/")
        base = WEB_DIR.resolve()
        candidate = (base / rel).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        kind = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        try:
            stamp = (candidate, candidate.stat().st_mtime_ns, candidate.stat().st_size)
        except OSError:
            return None
        cached = self._static_cache.get(str(candidate))
        if cached and cached[0] == stamp:
            return cached[1], kind, cached[2]
        body = candidate.read_bytes()
        etag = '"%s"' % hashlib.sha1(body).hexdigest()[:20]
        self._static_cache[str(candidate)] = (stamp, body, etag)
        return body, kind, etag


def _blender_state(project) -> dict:
    """Où est Blender, et que faire s'il n'est pas là (affiché dans l'atelier)."""
    from hookymod import blendfile

    executable = blendfile.find_blender(project.settings.blender_path)
    return {
        "found": bool(executable),
        "path": str(executable) if executable else "",
        "configured": project.settings.blender_path,
        "note": blendfile.platform_note(),
    }


def _size(query: dict, name: str, default: int) -> int:
    """Taille d'aperçu bornée : un PNG de 20 000 px de large n'aide personne."""
    try:
        value = int(query.get(name, [str(default)])[0])
    except (TypeError, ValueError):
        value = default
    return max(64, min(1600, value))


def _scale(payload: dict) -> float:
    """Facteur d'échelle borné : 0,001 à 1000 (au-delà, c'est une faute de frappe)."""
    raw = payload.get("scale", 1.0)
    if isinstance(raw, list):
        raw = raw[0] if raw else 1.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 1.0
    if not 0.001 <= value <= 1000.0:
        value = 1.0
    return value


def _json(payload: dict) -> tuple[bytes, str]:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"


def _make_handler(server: HookyCreatorServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HookyCreator/1.0"

        def log_message(self, fmt, *args):        # journal discret
            if "--verbose" in __import__("sys").argv:
                super().log_message(fmt, *args)

        def handle(self) -> None:
            try:
                super().handle()
            except ConnectionError:
                # Le navigateur peut annuler une vignette en changeant de vue.
                self.close_connection = True

        # -- utilitaires
        def _send(self, body: bytes, kind: str, status: int = 200,
                  headers: dict | None = None) -> None:
            headers = dict(headers or {})
            # Un état de projet fait 0,6 Mo en JSON et 7 Mo sur un dump complet :
            # compressé, c'est 10 fois moins à traverser. Les PNG, déjà
            # compressés, ne sont jamais recompressés (ce serait du temps perdu).
            compressible = kind.startswith(("application/json", "text/",
                                            "application/xml", "image/svg"))
            if (compressible and len(body) >= 1024
                    and "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
                    and "Content-Encoding" not in headers):
                body = gzip.compress(body, 6)
                headers["Content-Encoding"] = "gzip"
                headers["Vary"] = "Accept-Encoding"
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in headers:
                self.send_header("Cache-Control", "no-store")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _not_modified(self, etag: str) -> None:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=0, must-revalidate")
            self.end_headers()

        def _error(self, message: str, status: int = 400) -> None:
            self._send(*_json({"error": message}), status=status)

        def do_GET(self) -> None:                # noqa: N802 (API HTTP)
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                query = parse_qs(parsed.query)
                rendered = parsed.path in IMAGE_ROUTES
                background = rendered and query.get("thumb", [""])[0] == "1"
                if background:
                    server.thumbnail_activity.note()

                def busy_thumbnail():
                    # Pas de file de threads serveur pour des images devenues
                    # invisibles : le navigateur réessaie seulement si nécessaire.
                    return self._send(*_json({"error": "Vignette en attente."}),
                                      status=429, headers={"Retry-After": "1"})

                try:
                    if background:
                        if not server.thumbnail_slot.acquire(blocking=False):
                            return busy_thumbnail()
                        try:
                            if not server.render_slots.acquire(blocking=False):
                                return busy_thumbnail()
                            try:
                                body, kind = server.api("GET", parsed.path, query, {})
                            finally:
                                server.render_slots.release()
                        finally:
                            # Laisser le navigateur lancer la vignette suivante
                            # avant qu'un préchauffage facultatif ne reprenne.
                            server.thumbnail_activity.note()
                            server.thumbnail_slot.release()
                    elif rendered:
                        with server.render_slots:
                            body, kind = server.api("GET", parsed.path, query, {})
                    else:
                        body, kind = server.api("GET", parsed.path, query, {})
                except ProjectError as exc:
                    return self._error(str(exc))
                except Exception as exc:         # jamais de trace dans l'UI
                    return self._error(f"Erreur interne : {exc}", 500)
                if rendered and body:
                    # Revalider : un réglage ou une texture peut changer sans
                    # changer l'URL de l'aperçu ; l'ETag évite le retransfert.
                    etag = '"%s"' % hashlib.sha1(body).hexdigest()[:20]
                    if self.headers.get("If-None-Match") == etag:
                        return self._not_modified(etag)
                    return self._send(body, kind, headers={
                        "ETag": etag,
                        "Cache-Control": "public, max-age=0, must-revalidate"})
                return self._send(body, kind)
            found = server.static(parsed.path)
            if not found:
                return self._error("Page inconnue", 404)
            body, kind, etag = found
            # le CSS et le JS changent à chaque mise à jour : on revalide (coût
            # d'un 304, quelques octets) au lieu de tout retélécharger
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self._send(body, kind, headers={"ETag": etag, "Cache-Control": "no-cache"})

        def do_POST(self) -> None:               # noqa: N802
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if parsed.path == "/api/blend/upload":
                # envoi binaire brut : le nom arrive dans l'en-tête, pas dans du JSON
                data = self.rfile.read(length) if length else b""
                try:
                    name = self.headers.get("X-Hooky-Filename") or "modele.blend"
                    return self._send(*_json(blend_mod.save_upload(
                        server.project, name, data)))
                except ProjectError as exc:
                    return self._error(str(exc))
                except Exception as exc:         # jamais de trace dans l'UI
                    return self._error(f"Erreur interne : {exc}", 500)
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
            if kind == "application/zip":
                return self._send(content, kind, headers={
                    "Content-Disposition": 'attachment; filename="hookycreator-selection.zip"'})
            self._send(content, kind)

    return Handler


def serve(root, work=None, host: str = "0.0.0.0", port: int = 8765,
          open_browser: bool = False, *, scan_limit: int = MAX_SCAN_FILES) -> HookyCreatorServer:
    return HookyCreatorServer(root, work, host, port, scan_limit=scan_limit).start(open_browser)
