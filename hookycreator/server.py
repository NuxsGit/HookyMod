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
import binascii
import gzip
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import re
from http.cookies import CookieError, SimpleCookie
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from hookymod import audio_codec, bntx, native, restbl, sarc, video, zs
from hookymod.util import HookyError

from . import __version__
from . import model_import as model_import_mod
from . import session as session_mod
from . import studio as studio_mod
from .project import MAX_SCAN_FILES, Project, ProjectError

WEB_DIR = Path(__file__).parent / "web"

#: routes qui coûtent un rendu CPU (limitées en parallèle)
IMAGE_ROUTES = ("/api/model/preview", "/api/texture/png")

# Dans les deux modes, une navigation GET ne doit pas lancer d'action d'écriture.
# Les routes sont explicites afin qu'une nouvelle action ne devienne pas
# accidentellement accessible comme image ou lien depuis un autre site.
READ_ROUTES = frozenset({
    "/api/state", "/api/settings", "/api/model/plan", "/api/textures", "/api/models",
    "/api/asset/check", "/api/sound/info", "/api/sound/ogg", "/api/sound/convert", "/api/texture/info",
    "/api/animation/list", "/api/animation/clip", "/api/video/info", "/api/video/stream",
    "/api/texture/png", "/api/ensembles", "/api/model/info",
    "/api/model/preview", "/api/model/geometry", "/api/model/skin", "/api/model/uv", "/api/model/uv/info", "/api/mobs",
    "/api/bulk/status",
    "/api/system", "/api/thumbs/status", "/api/export/download", "/api/ui-state",
    "/api/studio/workspaces", "/api/studio/workspace", "/api/studio/export",
})
WRITE_ROUTES = frozenset({
    "/api/settings", "/api/texture/import", "/api/texture/export", "/api/sound/import", "/api/video/import",
    "/api/model/export", "/api/model/import", "/api/model/inspect", "/api/mobs", "/api/bulk/start",
    "/api/mod/build", "/api/thumbs/warm",
    "/api/thumbs/stop", "/api/cache/clear", "/api/scan", "/api/export/selection",
    "/api/shutdown", "/api/ui-state",
    "/api/studio/workspaces", "/api/studio/workspace", "/api/studio/import",
    "/api/studio/check", "/api/studio/simulate", "/api/studio/delete", "/api/studio/copy",
})
DESKTOP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


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
                 host: str = "127.0.0.1", port: int = 8765, *,
                 scan_limit: int = MAX_SCAN_FILES, desktop_token: str | None = None) -> None:
        if desktop_token is not None:
            if host != "127.0.0.1" or port != 0:
                raise ValueError("Le serveur de l'application native doit écouter sur une boucle locale et un port aléatoire.")
            if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", desktop_token):
                raise ValueError("Jeton de session desktop invalide.")
        self.desktop_token = desktop_token
        self.project = Project(Path(root), Path(work) if work else None, scan_limit=scan_limit)
        self.ui_session = session_mod.SessionStore(self.project.work, self.project.root)
        self.studio = studio_mod.WorkspaceStore(self.project.work)
        self.scan_summary = self.project.scan()
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.stopped = False
        self._stop_lock = threading.Lock()
        # Au plus un rendu de vignette et deux rendus au total : une action
        # interactive garde toujours une place, même avec plusieurs onglets.
        self.render_slots = threading.BoundedSemaphore(2)
        self.thumbnail_slot = threading.BoundedSemaphore(1)
        # L'import peut allouer des tampons et des maillages volumineux : une
        # seule requête à la fois, avant même de lire son corps HTTP.
        self.model_import_slot = threading.BoundedSemaphore(1)
        # Les archives et projets .hooky sont bornés et lus un à la fois.
        self.studio_slot = threading.BoundedSemaphore(1)
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
            webbrowser.open(self.url)
        return self

    def stop(self) -> None:
        # La fenêtre native peut se fermer pendant qu'une requête /shutdown
        # s'achève ; ne pas fermer deux fois le même serveur dans deux fils.
        with self._stop_lock:
            if self.stopped:
                return
            self.project.stop_warming()
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
                self.httpd = None
            self.stopped = True

    @property
    def url(self) -> str:
        # 0.0.0.0 désigne toutes les interfaces pour l'écoute, pas une adresse
        # utilisable par le navigateur. Conserver la boucle locale comme lien.
        host = "127.0.0.1" if self.host == "0.0.0.0" else self.host
        return f"http://{host}:{self.port}/"

    # ------------------------------------------------------------------ routes
    def api(self, method: str, route: str, query: dict, body: dict) -> tuple[bytes, str]:
        """Dispatch de l'API : ``(contenu, type)``."""
        project = self.project

        if route == "/api/state":
            return project.state_payload(quick=query.get("quick") == ["1"]), "application/json"

        if route == "/api/ui-state":
            if method == "GET":
                return _json({"state": self.ui_session.load()})
            if not isinstance(body, dict):
                raise ProjectError("Sauvegarde d'interface invalide.")
            try:
                saved = self.ui_session.save(body.get("client"), body.get("sequence"), body.get("state"))
            except session_mod.SessionError as exc:
                raise ProjectError(str(exc)) from exc
            return _json({"saved": saved})

        # HookyMod's stores only design projects in the work directory; none of
        # these routes build a game mod or use the legacy assembly destination.
        if route == "/api/studio/workspaces":
            if method == "GET":
                return _json(self.studio.list())
            if method == "POST":
                if not isinstance(body, dict):
                    raise studio_mod.StudioError("Demande de création invalide.")
                return _json({"workspace": self.studio.create(
                    body.get("name", ""), body.get("description", ""),
                    body.get("author", ""), body.get("target", ""))})

        if route == "/api/studio/workspace":
            if method == "GET":
                return _json({"workspace": self.studio.load(query.get("id", [""])[0])})
            if method == "POST":
                if not isinstance(body, dict):
                    raise studio_mod.StudioError("Demande de sauvegarde invalide.")
                return _json({"workspace": self.studio.save(body.get("workspace"))})

        if route == "/api/studio/check" and method == "POST":
            if not isinstance(body, dict):
                raise studio_mod.StudioError("Demande de vérification invalide.")
            return _json(studio_mod.check_workspace(body.get("workspace")))

        if route == "/api/studio/simulate" and method == "POST":
            if not isinstance(body, dict):
                raise studio_mod.StudioError("Demande de simulation invalide.")
            return _json(studio_mod.simulate_element(body.get("element"), body.get("event"),
                                                     body.get("items"), body.get("flags")))

        if route == "/api/studio/import" and method == "POST":
            if not isinstance(body, dict):
                raise studio_mod.StudioError("Demande d'import invalide.")
            return _json({"workspace": self.studio.import_archive(body.get("archive"))})

        if route == "/api/studio/copy" and method == "POST":
            if not isinstance(body, dict):
                raise studio_mod.StudioError("Demande de copie invalide.")
            return _json({"workspace": self.studio.copy(body.get("workspace"))})

        if route == "/api/studio/delete" and method == "POST":
            if not isinstance(body, dict):
                raise studio_mod.StudioError("Demande de suppression invalide.")
            self.studio.delete(body.get("id"), body.get("revision"))
            return _json({"deleted": True})

        if route == "/api/studio/export" and method == "GET":
            return self.studio.export(query.get("id", [""])[0]), "application/vnd.hookymod+zip"

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

        if route == "/api/asset/check":
            return _json(project.check_asset(query.get("path", [""])[0],
                                             query.get("kind", [""])[0]))

        if route == "/api/animation/list" and method == "GET":
            return _json(project.animation_list(query.get("path", [""])[0]))

        if route == "/api/animation/clip" and method == "GET":
            try:
                index = int(query.get("index", ["0"])[0])
            except (TypeError, ValueError) as exc:
                raise ProjectError("Index de clip invalide.") from exc
            return _json(project.animation_clip(query.get("path", [""])[0], index))

        if route == "/api/video/info" and method == "GET":
            return _json(project.video_info(query.get("path", [""])[0]))

        if route == "/api/video/import" and method == "POST":
            encoded = body.get("video") if isinstance(body, dict) else None
            if not isinstance(encoded, str) or len(encoded) > ((video.MAX_IMPORT + 2) // 3) * 4:
                raise ProjectError("Fichier vidéo invalide ou trop volumineux.")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ProjectError("Données vidéo invalides.") from exc
            return _json(project.import_video(str(body.get("path", "")), content))

        if route == "/api/sound/info":
            return _json(project.sound_info(query.get("path", [""])[0]))

        if route == "/api/sound/ogg":
            return project.sound_ogg(query.get("path", [""])[0]), "audio/ogg"

        if route == "/api/sound/convert" and method == "GET":
            content, mime, _ = project.sound_convert(query.get("path", [""])[0],
                                                      query.get("format", ["wav"])[0])
            return content, mime

        if route == "/api/sound/import" and method == "POST":
            encoded = body.get("audio") if isinstance(body, dict) else None
            # Limit before decoding, including for callers of api() in-process.
            if not isinstance(encoded, str) or len(encoded) > ((audio_codec.MAX_IMPORT_BYTES + 2) // 3) * 4:
                raise ProjectError("Fichier audio invalide ou trop volumineux.")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ProjectError("Données audio invalides.") from exc
            return _json(project.import_sound(str(body.get("path", "")), data))

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
            rel = str(body.get("path", "")) if method == "POST" else query["path"][0]
            index = int(body.get("index", 0)) if method == "POST" else int(query.get("index", ["0"])[0])
            return _json(project.export_texture(rel, index))

        if route == "/api/ensembles":
            return project.cached_json("ensembles", lambda: {
                "ensembles": project.ensembles()}), "application/json"

        if route == "/api/model/info":
            return _json(project.model_info(query["path"][0]))

        if route == "/api/model/geometry":
            if "path" not in query:
                raise ProjectError("Paramètre « path » manquant.")
            return project.model_geometry(query["path"][0]), "application/octet-stream"

        if route == "/api/model/skin" and method == "GET":
            if "path" not in query:
                raise ProjectError("Paramètre « path » manquant.")
            return project.model_skin(query["path"][0]), "application/octet-stream"

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

        if route == "/api/model/export":
            selected = body.get("animation")
            all_animations = body.get("all_animations", False)
            extra = body.get("animation_file")
            if (type(all_animations) is not bool
                    or (extra is not None and (not isinstance(extra, str) or not extra))
                    or (not all_animations and "animation_file" in body)
                    or (all_animations and selected is not None)):
                raise ProjectError("Mode d'export d'animations invalide.")
            if selected is not None and (not isinstance(selected, dict)
                                         or set(selected) != {"path", "index"}
                                         or not isinstance(selected["path"], str) or not selected["path"]
                                         or type(selected["index"]) is not int or selected["index"] < 0):
                raise ProjectError("Sélection d'animation invalide : indique le fichier et l'index du clip.")
            return _json(project.export_model(body["path"],
                                              textures=bool(body.get("textures", True)),
                                              archive=True, all_animations=all_animations,
                                              animation_rel=extra if all_animations else selected["path"] if selected else None,
                                              animation_index=selected["index"] if selected else None))

        if route == "/api/model/inspect" and method == "POST":
            return _json(model_import_mod.inspect_model(body))

        if route == "/api/model/import" and method == "POST":
            return _json(model_import_mod.import_model(project, body))

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
                raise ProjectError("Cet export a été retiré : utilise le ZIP glTF / OBJ.")
            content = project.export_selection(body.get("items") or [])
            if body.get("download_link") is True:
                return _json(project.store_export_download(content))
            # Compatibilité avec les clients historiques de l'API.
            return content, "application/zip"

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


def _size(query: dict, name: str, default: int) -> int:
    """Taille d'aperçu bornée : un PNG de 20 000 px de large n'aide personne."""
    try:
        value = int(query.get(name, [str(default)])[0])
    except (TypeError, ValueError):
        value = default
    return max(64, min(1600, value))


def _json(payload: dict) -> tuple[bytes, str]:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"


def _loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _make_handler(server: HookyCreatorServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"HookyCreator/{__version__}"

        def log_message(self, fmt, *args):        # journal discret
            if "--verbose" in __import__("sys").argv:
                super().log_message(fmt, *args)

        def handle(self) -> None:
            try:
                super().handle()
            except ConnectionError:
                # Le navigateur peut annuler une vignette en changeant de vue.
                self.close_connection = True

        def end_headers(self) -> None:
            if server.desktop_token is not None:
                self.send_header("Content-Security-Policy", DESKTOP_CSP)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def _desktop_allowed(self, route: str, method: str) -> bool:
            """Mode natif : boucle locale, Host/Origin exacts et session secrète.

            Le cookie est HttpOnly/SameSite et distinct par port. Même un autre
            service sur 127.0.0.1 ne peut pas lancer une écriture par formulaire :
            elle exige un POST avec l'origine exacte, pas seulement le cookie.
            """
            if server.desktop_token is None:
                return True
            expected = f"127.0.0.1:{server.port}"
            hosts = self.headers.get_all("Host", [])
            origins = self.headers.get_all("Origin", [])
            sites = self.headers.get_all("Sec-Fetch-Site", [])
            if (hosts != [expected] or len(origins) > 1 or len(sites) > 1
                    or (origins and origins[0] != f"http://{expected}")
                    or (sites and sites[0] not in ("same-origin", "none"))
                    or (method == "POST" and origins != [f"http://{expected}"])):
                self.close_connection = True
                self._error("Origine locale refusée.", 403)
                return False
            if route == "/api/desktop/session" and method == "POST":
                return True  # le jeton du premier échange est vérifié séparément
            if not route.startswith("/api/"):
                if method == "GET":
                    return True
                self.close_connection = True
                self._error("Méthode ou action non autorisée.", 405)
                return False
            allowed = READ_ROUTES if method == "GET" else WRITE_ROUTES
            if route not in allowed:
                self.close_connection = True
                self._error("Méthode ou action non autorisée.", 405)
                return False
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
                session = cookies.get(f"hooky_session_{server.port}")
                token = session.value if session is not None else ""
            except CookieError:
                token = ""
            if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token) or not hmac.compare_digest(token, server.desktop_token):
                self.close_connection = True
                self._error("Session native absente ou expirée.", 403)
                return False
            return True

        def _browser_allowed(self, route: str, method: str) -> bool:
            """Protéger le serveur navigateur local des requêtes inter-origines.

            Les clients non navigateur peuvent omettre Origin, mais doivent
            envoyer du JSON pour les POST. Les adresses externes ne sont admises
            que si l'utilisateur a explicitement choisi une écoute non locale ;
            dans ce cas il n'y a pas d'authentification réseau.
            """
            if server.desktop_token is not None:
                return True  # le mode natif possède ses propres contrôles et cookie
            hosts = self.headers.get_all("Host", [])
            origins = self.headers.get_all("Origin", [])
            sites = self.headers.get_all("Sec-Fetch-Site", [])
            valid = len(hosts) == 1 and len(origins) <= 1 and len(sites) <= 1
            host = hosts[0].lower() if len(hosts) == 1 else ""
            # Refuser notamment les userinfo, espaces et chemins ambigus dans
            # Host, tout en acceptant les hôtes transmis par un proxy de preview.
            if not re.fullmatch(r"(?:[a-z0-9.-]+|\[[0-9a-f:.]+\])(?::[0-9]{1,5})?", host):
                valid = False
            try:
                authority = urlparse(f"http://{host}")
                hostname = authority.hostname or ""
                port = authority.port
            except ValueError:
                hostname, port, valid = "", None, False
            local = _loopback_host(server.host)
            if local:
                names = {server.host.lower()}
                if server.host.lower() in ("127.0.0.1", "localhost"):
                    names.update(("127.0.0.1", "localhost"))
                if hostname not in names or (port if port is not None else 80) != server.port:
                    valid = False  # protège aussi contre le DNS rebinding
            schemes = ("http",) if local else ("http", "https")
            if origins and origins[0].lower() not in {f"{scheme}://{host}" for scheme in schemes}:
                valid = False
            # Une prévisualisation derrière proxy peut ouvrir la page dans une
            # iframe d'une autre origine. Autoriser uniquement cette navigation
            # documentaire lorsque l'écoute non locale est explicite : les API
            # et toutes les écritures gardent les contrôles inter-origines.
            embedded_document = (not local and method == "GET"
                                 and route in ("/", "/index.html", "/static/index.html")
                                 and self.headers.get_all("Sec-Fetch-Mode", []) == ["navigate"]
                                 and self.headers.get_all("Sec-Fetch-Dest", []) == ["iframe"])
            if sites and sites[0] not in ("same-origin", "none") and not embedded_document:
                valid = False
            if not valid:
                self.close_connection = True
                self._error("Origine ou hôte refusé.", 403)
                return False
            allowed = READ_ROUTES if method == "GET" else WRITE_ROUTES
            if (route.startswith("/api/") and route not in allowed) or (method != "GET" and not route.startswith("/api/")):
                self.close_connection = True
                self._error("Méthode ou action non autorisée.", 405)
                return False
            return True

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
                # Sur la boucle locale, privilégier la latence plutôt que les
                # derniers pourcents de compression d'un grand inventaire.
                body = gzip.compress(body, 1)
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
            if not self._desktop_allowed(parsed.path, "GET") or not self._browser_allowed(parsed.path, "GET"):
                return
            if parsed.path == "/desktop-login" and server.desktop_token is not None:
                parsed = parsed._replace(path="/static/desktop-login.html")
            if parsed.path == "/api/video/stream":
                try:
                    query = parse_qs(parsed.query)
                    path = server.project.video_file(query.get("path", [""])[0])
                    with path.open("rb") as source:
                        import os
                        size = os.fstat(source.fileno()).st_size
                        video.check_header(source.read(video.MAX_HEADER), size)
                        start, end, partial = 0, size - 1, False
                        requested = self.headers.get("Range")
                        if requested:
                            match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", requested.strip())
                            if not match or not (match[1] or match[2]):
                                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}")
                                self.send_header("Content-Length", "0"); self.end_headers(); return
                            if match[1]:
                                start = int(match[1]); end = min(int(match[2]), size - 1) if match[2] else size - 1
                            else:
                                suffix = int(match[2]); start = max(0, size - suffix)
                            if start >= size or start > end:
                                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}")
                                self.send_header("Content-Length", "0"); self.end_headers(); return
                            partial = True
                        source.seek(start)
                        self.send_response(206 if partial else 200)
                        self.send_header("Content-Type", "video/webm")
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Length", str(end - start + 1))
                        self.send_header("Cache-Control", "no-store")
                        if partial:
                            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                        if query.get("download") == ["1"]:
                            self.send_header("Content-Disposition", 'attachment; filename="hookycreator-video.webm"')
                        self.end_headers()
                        remaining = end - start + 1
                        while remaining:
                            chunk = source.read(min(1024 * 1024, remaining))
                            if not chunk:
                                self.close_connection = True; break
                            self.wfile.write(chunk); remaining -= len(chunk)
                except (ProjectError, HookyError) as exc:
                    return self._error(str(exc))
                except OSError:
                    self.close_connection = True
                return
            if parsed.path == "/api/export/download":
                import shutil

                try:
                    parameters = parse_qs(parsed.query, keep_blank_values=True)
                    ids = parameters.get("id", [])
                    kind = parameters.get("kind", [])
                    if len(ids) != 1 or kind not in ([], [""], ["selection"], ["glb"]):
                        raise ProjectError("Téléchargement introuvable.")
                    token = ids[0]
                    is_glb = kind == ["glb"]
                    path = server.project.export_download(token, "glb" if is_glb else "zip")
                    name = ("hookycreator-animations.glb" if is_glb else
                            "hookycreator-selection.zip" if kind == ["selection"] else
                            "hookycreator-modele.zip")
                    with path.open("rb") as source:
                        size = path.stat().st_size
                        self.send_response(200)
                        self.send_header("Content-Type", "model/gltf-binary" if is_glb else "application/zip")
                        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                        self.send_header("Content-Length", str(size))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
                except ProjectError as exc:
                    return self._error(str(exc), 404)
                except OSError:
                    self.close_connection = True
                return
            if parsed.path.startswith("/api/"):
                query = parse_qs(parsed.query)
                rendered = parsed.path in IMAGE_ROUTES
                background = ((rendered or parsed.path == "/api/asset/check")
                              and query.get("thumb", [""])[0] == "1")
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
                    elif rendered or parsed.path in ("/api/model/geometry", "/api/model/skin"):
                        # La sérialisation de gros maillages est elle aussi bornée
                        # en concurrence ; ce n'est pas une vignette à mettre en cache HTTP.
                        with server.render_slots:
                            body, kind = server.api("GET", parsed.path, query, {})
                    else:
                        body, kind = server.api("GET", parsed.path, query, {})
                except (ProjectError, HookyError, studio_mod.StudioError) as exc:
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
                if parsed.path == "/api/studio/export":
                    return self._send(body, kind, headers={"Content-Disposition":
                                      'attachment; filename="hookymods.hooky"'})
                if parsed.path == "/api/animation/clip" and query.get("download") == ["1"]:
                    return self._send(body, kind, headers={"Content-Disposition":
                                      'attachment; filename="hookycreator-animation.json"'})
                if parsed.path in ("/api/sound/ogg", "/api/sound/convert") and query.get("download") == ["1"]:
                    source = Path(query.get("path", ["son"])[0]).name
                    fmt = "opus" if parsed.path.endswith("/ogg") else query.get("format", ["wav"])[0].lower()
                    # The converter validates fmt before this point.
                    name = re.sub(r"\.bwav(?:\.zs)?$", "", source, flags=re.IGNORECASE) + "." + fmt
                    disposition = f'attachment; filename="hookycreator-sound.{fmt}"; filename*=UTF-8\'\'' + quote(name, safe="")
                    return self._send(body, kind, headers={"Content-Disposition": disposition})
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
            if not self._desktop_allowed(parsed.path, "POST") or not self._browser_allowed(parsed.path, "POST"):
                return
            # Aucun formulaire cross-origin ne peut envoyer ce type de contenu
            # sans preflight, que le serveur ne prend pas en charge.
            content_types = self.headers.get_all("Content-Type", [])
            if (parsed.path != "/api/desktop/session"
                    and (len(content_types) != 1 or self.headers.get_content_type() != "application/json")):
                self.close_connection = True
                return self._error("Contenu JSON requis.", 415)
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1 or self.headers.get_all("Transfer-Encoding", []):
                self.close_connection = True
                return self._error("Longueur de requête invalide.")
            try:
                length = int(lengths[0]) if lengths else 0
            except ValueError:
                self.close_connection = True
                return self._error("Longueur de requête invalide.")
            if (length < 0 or length > 128 * 1024 * 1024
                    or (parsed.path == "/api/ui-state" and length > session_mod.MAX_BYTES)
                    or (parsed.path.startswith("/api/studio/") and length > studio_mod.MAX_REQUEST_BYTES)
                    or (parsed.path == "/api/sound/import" and length > ((audio_codec.MAX_IMPORT_BYTES + 2) // 3) * 4 + 4096)
                    or (parsed.path == "/api/video/import" and length > ((video.MAX_IMPORT + 2) // 3) * 4 + 4096)
                    or (parsed.path in ("/api/model/import", "/api/model/inspect") and length > model_import_mod.MAX_REQUEST)):
                self.close_connection = True
                return self._error("Requête trop volumineuse.", 413)
            if parsed.path == "/api/desktop/session" and server.desktop_token is not None:
                if length > 1024 or self.headers.get_content_type() != "application/json":
                    self.close_connection = True
                    return self._error("Session invalide.", 403)
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    token = payload.get("token", "") if isinstance(payload, dict) else ""
                except (ValueError, UnicodeError):
                    token = ""
                if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token)
                        or not hmac.compare_digest(token, server.desktop_token)):
                    return self._error("Session invalide.", 403)
                cookie = f"hooky_session_{server.port}={token}; Path=/; HttpOnly; SameSite=Strict"
                return self._send(*_json({"ok": True}), headers={"Set-Cookie": cookie})
            if parsed.path.startswith("/api/studio/"):
                if not server.studio_slot.acquire(blocking=False):
                    self.close_connection = True
                    return self._send(*_json({"error": "Une opération HookyMod's est déjà en cours."}),
                                      status=429, headers={"Retry-After": "2"})
                try:
                    self.connection.settimeout(30)
                    return self._post_json(parsed, length)
                except TimeoutError:
                    self.close_connection = True
                    return self._error("Lecture du projet interrompue.", 408)
                finally:
                    server.studio_slot.release()
            if parsed.path in ("/api/model/import", "/api/model/inspect", "/api/video/import", "/api/sound/import"):
                if not server.model_import_slot.acquire(blocking=False):
                    self.close_connection = True
                    return self._send(*_json({"error": "Un import est déjà en cours. Réessaie après sa fin."}),
                                      status=429, headers={"Retry-After": "2"})
                try:
                    self.connection.settimeout(30)
                    return self._post_json(parsed, length)
                except TimeoutError:
                    self.close_connection = True
                    return self._error("Lecture de l'import interrompue.", 408)
                finally:
                    server.model_import_slot.release()
            return self._post_json(parsed, length)

        def _post_json(self, parsed, length: int) -> None:
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, RecursionError):
                return self._error("Requête JSON invalide.")
            if not isinstance(body, dict):
                return self._error("Objet JSON attendu.")
            if not parsed.path.startswith("/api/"):
                return self._error("Page inconnue", 404)
            try:
                content, kind = server.api("POST", parsed.path, parse_qs(parsed.query), body)
            except (ProjectError, HookyError, studio_mod.StudioError) as exc:
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


def serve(root, work=None, host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = False, *, scan_limit: int = MAX_SCAN_FILES) -> HookyCreatorServer:
    return HookyCreatorServer(root, work, host, port, scan_limit=scan_limit).start(open_browser)
