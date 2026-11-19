"""État de navigation, propre à un projet et indépendant du port de la webview.

Ce fichier ne contient ni jeton, ni contenu d'asset, ni action à exécuter. Les
réglages et les modifications ont leurs propres fichiers ; cette sauvegarde ne
les remplace pas. Les chemins et onglets restent des indications d'interface :
le client doit vérifier qu'ils existent encore avant de les rouvrir.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from hookymod.util import write_atomic

from . import cache

VERSION = 1
MAX_BYTES = 4 * 1024 * 1024
MAX_TEXT = 2048
MAX_TABS = 64
MAX_SELECTION = 5000
VIEWS = frozenset({"home", "edit", "models", "ensembles", "player", "textures",
                   "sounds", "animations", "videos", "import", "settings"})
# Sessions from versions with the mod maker remain usable; project data is not touched.
RETIRED_VIEWS = frozenset({"create", "studio", "mobs"})
RETIRED_FIELDS = frozenset({"mod-name", "mob-name", "mob-parent", "mob-resource-search"})
RETIRED_CHOICES = frozenset({"mob-model", "mob-textures"})
TEXT_FIELDS = frozenset({"search", "search-tex", "search-ens", "search-sound", "search-animation", "search-video", "tex-role",
                         "model-axis", "model-scale", "model-target-search", "model-quality"})
CHECK_FIELDS = frozenset({"model-shots", "ens-textures", "bulk-anim", "model-fit"})
# Migration des sessions créées avant le remplacement de l'ancien parcours.
LEGACY_FIELDS = {"blend-axis": "model-axis", "blend-scale": "model-scale",
                 "blend-target-search": "model-target-search", "blend-fit": "model-fit"}


class SessionError(ValueError):
    """Sauvegarde invalide ou impossible."""


def _text(value, *, allow_empty: bool = True) -> str:
    if (not isinstance(value, str) or len(value) > MAX_TEXT or "\x00" in value
            or (not allow_empty and not value)):
        raise SessionError("État d'interface invalide : texte incorrect.")
    return value


def _flag(value) -> bool:
    if not isinstance(value, bool):
        raise SessionError("État d'interface invalide : booléen attendu.")
    return value


def _list(value, limit: int) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise SessionError("État d'interface invalide : liste trop grande ou incorrecte.")
    return value


def validate_state(value: object) -> dict:
    """Valide et copie un instantané borné ; ignore les champs futurs inconnus."""
    if (not isinstance(value, dict) or type(value.get("version")) is not int
            or value["version"] != VERSION):
        raise SessionError("Version de sauvegarde d'interface inconnue.")
    view = _text(value.get("view", "home"))
    if view == "blender":
        view = "import"
    elif view in RETIRED_VIEWS:
        view = "home"
    if view not in VIEWS:
        raise SessionError("Vue inconnue dans la sauvegarde.")
    tabs = []
    for tab in _list(value.get("tabs", []), MAX_TABS):
        if not isinstance(tab, dict):
            raise SessionError("Onglet invalide dans la sauvegarde.")
        kind = _text(tab.get("type"), allow_empty=False)
        if kind == "view":
            name = _text(tab.get("view"), allow_empty=False)
            if name == "blender":
                name = "import"
            elif name in RETIRED_VIEWS:
                continue
            if name not in VIEWS:
                raise SessionError("Vue d'onglet inconnue.")
            tabs.append({"type": kind, "view": name})
        elif kind in {"model", "texture", "sound", "animation", "video"}:
            entry = {"type": kind, "rel": _text(tab.get("rel"), allow_empty=False)}
            if kind == "texture" and "index" in tab:
                index = tab["index"]
                if type(index) is not int or not 0 <= index <= 65535:
                    raise SessionError("Index de texture invalide dans la sauvegarde.")
                entry["index"] = index
            tabs.append(entry)
        elif kind == "mob":
            # Ancienne fiche de mod : ne pas rejeter les autres onglets.
            _text(tab.get("slug"), allow_empty=False)
        elif kind == "blend":
            # Les anciens documents ne peuvent plus être rouverts. Conserver
            # le reste de la session plutôt que de rejeter toute la sauvegarde.
            _text(tab.get("ref"), allow_empty=False)
        else:
            raise SessionError("Type d'onglet inconnu.")
    selection = [_text(item, allow_empty=False)
                 for item in _list(value.get("selection", []), MAX_SELECTION)]
    fields = value.get("fields", {})
    if not isinstance(fields, dict):
        raise SessionError("Champs d'interface invalides.")
    fields = dict(fields)
    for old, new in LEGACY_FIELDS.items():
        if old in fields and new not in fields:
            fields[new] = fields[old]
    # L'ancien sélecteur d'axes utilisait « y_up » pour une autre convention.
    # Ne pas l'interpréter silencieusement comme un réglage du nouveau lecteur.
    if fields.get("model-axis") == "y_up":
        fields["model-axis"] = "z_up"
    clean_fields = {key: _text(fields[key]) for key in TEXT_FIELDS if key in fields}
    clean_fields.update({key: _flag(fields[key]) for key in CHECK_FIELDS if key in fields})
    choices = value.get("choices", {})
    if not isinstance(choices, dict):
        raise SessionError("Choix d'interface invalides.")
    choices = dict(choices)
    if "blend-target" in choices and "model-target" not in choices:
        choices["model-target"] = choices["blend-target"]
    clean_choices = {key: _text(choices[key])
                     for key in ("model-target",) if key in choices}
    scroll = value.get("scroll", 0)
    if type(scroll) is not int or not 0 <= scroll <= 100_000_000:
        raise SessionError("Position de défilement invalide.")
    shown = value.get("shown", {})
    if not isinstance(shown, dict):
        raise SessionError("Nombre d'éléments affichés invalide.")
    clean_shown = {}
    for key in ("models", "textures", "ensembles", "sounds", "animations", "videos", "chooser"):
        count = shown.get(key, 48)
        if type(count) is not int or not 48 <= count <= 10_000:
            raise SessionError("Nombre d'éléments affichés invalide.")
        clean_shown[key] = count
    active = _text(value.get("active", "view:home"))
    if active == "view:blender":
        active = "view:import"
    elif active in {f"view:{name}" for name in RETIRED_VIEWS} or active.startswith("mob:"):
        active = f"view:{view}"
    return {"version": VERSION, "view": view, "tabs": tabs,
            "active": active,
            "selection": selection, "fields": clean_fields, "choices": clean_choices,
            "scroll": scroll, "shown": clean_shown,
            "inspector": _flag(value.get("inspector", False)),
            "console": _flag(value.get("console", False)),
            "reduced": _flag(value.get("reduced", False)),
            "thumbnails_paused": _flag(value.get("thumbnails_paused", False))}


def _has_retired_maker_state(value: object) -> bool:
    """Identify historical drafts even if another view was active on exit."""
    if not isinstance(value, dict):
        return False
    view, active = value.get("view"), value.get("active")
    if isinstance(view, str) and view in RETIRED_VIEWS:
        return True
    if isinstance(active, str) and (active.startswith("mob:")
            or active in {f"view:{name}" for name in RETIRED_VIEWS}):
        return True
    fields, choices = value.get("fields"), value.get("choices")
    if isinstance(fields, dict) and RETIRED_FIELDS.intersection(fields):
        return True
    if isinstance(choices, dict) and RETIRED_CHOICES.intersection(choices):
        return True
    tabs = value.get("tabs")
    return isinstance(tabs, list) and any(
        isinstance(tab, dict) and (tab.get("type") == "mob" or (
            tab.get("type") == "view" and isinstance(tab.get("view"), str)
            and tab["view"] in RETIRED_VIEWS)) for tab in tabs)


class SessionStore:
    """Instantané atomique, avec protection contre les requêtes réordonnées.

    Deux fenêtres peuvent écrire : la dernière écriture reçue gagne. Pour une
    même fenêtre, un compteur empêche une ancienne requête retardée d'écraser
    une sauvegarde plus récente (notamment le beacon envoyé à la fermeture).
    """

    def __init__(self, work: Path, root: Path) -> None:
        self.work = Path(work).resolve()
        self.root = os.path.normcase(str(Path(root).resolve()))
        self.name = f"{cache.digest(self.root)}.json"
        self._lock = threading.Lock()
        self._sequences: OrderedDict[str, int] = OrderedDict()
        self._legacy_checked = False

    def _path(self) -> Path:
        base = (self.work / "sessions").resolve()
        if base == self.work or not base.is_relative_to(self.work):
            raise SessionError("Dossier des sessions hors du dossier de travail.")
        path = base / self.name
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            raise SessionError("Fichier de session hors du dossier de travail.")
        return path

    def _preserve_retired_session(self, path: Path) -> None:
        """Back up the exact old session before the first migrated write.

        Removed form drafts have no controls in the new UI. Keeping the raw
        file prevents a normal checkpoint from silently discarding them. An
        existing backup is never replaced, and a failed backup fails the save.
        """
        if self._legacy_checked:
            return
        try:
            with path.open("rb") as file:
                raw = file.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return
        if len(raw) > MAX_BYTES:
            return  # not a valid bounded session
        try:
            saved = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            return
        if (not isinstance(saved, dict) or saved.get("root") != self.root
                or not _has_retired_maker_state(saved.get("state"))):
            return
        digest = hashlib.sha256(raw).hexdigest()
        backup = path.with_name(f"{path.stem}.pre-modmaker-{digest}.json.bak")
        def verify_existing() -> None:
            if backup.is_symlink() or not backup.resolve().is_relative_to(path.parent):
                raise SessionError("Sauvegarde de l'ancienne session hors du dossier de travail.")
            with backup.open("rb") as file:
                existing = file.read(MAX_BYTES + 1)
            if existing != raw:
                raise SessionError("Une sauvegarde différente de l'ancienne session existe déjà.")

        # Publish a completed temporary file without replacing an existing name,
        # even when another application instance creates a backup concurrently.
        # Hard links are unavailable on some filesystems (e.g. FAT), so fall
        # back to exclusive creation there. A failed fallback may leave a
        # partial backup, but never replaces the original session.
        fd, temporary = tempfile.mkstemp(prefix=".hookymod-session-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(raw)
            try:
                os.link(temporary, backup)
            except FileExistsError:
                verify_existing()
            except OSError as exc:
                unsupported = {value for name in ("ENOTSUP", "EOPNOTSUPP", "ENOSYS", "EINVAL", "EPERM")
                               if (value := getattr(errno, name, None)) is not None}
                if exc.errno not in unsupported:
                    raise
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                try:
                    backup_fd = os.open(backup, flags, 0o600)
                except FileExistsError:
                    verify_existing()
                else:
                    with os.fdopen(backup_fd, "wb") as file:
                        file.write(raw)
        finally:
            os.unlink(temporary)

    def load(self) -> dict | None:
        with self._lock:
            try:
                path = self._path()
                with path.open("rb") as file:
                    raw = file.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    return None
                saved = json.loads(raw.decode("utf-8"))
                if not isinstance(saved, dict) or saved.get("root") != self.root:
                    return None
                return validate_state(saved.get("state"))
            except (OSError, ValueError, UnicodeError, SessionError, RecursionError):
                # Une ancienne version, un fichier tronqué ou un projet déplacé
                # ne doivent jamais empêcher l'ouverture de l'application.
                return None

    def save(self, client: object, sequence: object, value: object) -> bool:
        if not isinstance(client, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", client):
            raise SessionError("Identifiant de sauvegarde invalide.")
        if type(sequence) is not int or not 0 <= sequence <= 2**53 - 1:
            raise SessionError("Numéro de sauvegarde invalide.")
        state = validate_state(value)
        data = json.dumps({"root": self.root, "state": state}, ensure_ascii=True,
                          separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_BYTES:
            raise SessionError("Sauvegarde d'interface trop volumineuse.")
        with self._lock:
            if sequence <= self._sequences.get(client, -1):
                return False
            try:
                path = self._path()
                self._preserve_retired_session(path)
                write_atomic(path, data)
                self._legacy_checked = True
            except OSError as exc:
                raise SessionError(f"Impossible d'enregistrer la session : {exc}") from exc
            self._sequences[client] = sequence
            self._sequences.move_to_end(client)
            while len(self._sequences) > 128:
                self._sequences.popitem(last=False)
            return True
