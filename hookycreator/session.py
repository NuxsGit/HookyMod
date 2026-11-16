"""État de navigation, propre à un projet et indépendant du port de la webview.

Ce fichier ne contient ni jeton, ni contenu d'asset, ni action à exécuter. Les
réglages et les modifications ont leurs propres fichiers ; cette sauvegarde ne
les remplace pas. Les chemins et onglets restent des indications d'interface :
le client doit vérifier qu'ils existent encore avant de les rouvrir.
"""
from __future__ import annotations

import json
import os
import re
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
VIEWS = frozenset({"home", "edit", "create", "studio", "models", "ensembles", "textures",
                   "sounds", "animations", "videos", "mobs", "import", "settings"})
TEXT_FIELDS = frozenset({"search", "search-tex", "search-ens", "search-sound", "search-animation", "search-video", "tex-role",
                         "mod-name", "mob-name", "mob-parent", "mob-resource-search",
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
            tabs.append({"type": kind, "slug": _text(tab.get("slug"), allow_empty=False)})
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
                     for key in ("mob-model", "model-target") if key in choices}
    if "mob-textures" in choices:
        clean_choices["mob-textures"] = [
            _text(item, allow_empty=False) for item in _list(choices["mob-textures"], MAX_SELECTION)]
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
    return {"version": VERSION, "view": view, "tabs": tabs,
            "active": active,
            "selection": selection, "fields": clean_fields, "choices": clean_choices,
            "scroll": scroll, "shown": clean_shown,
            "inspector": _flag(value.get("inspector", False)),
            "console": _flag(value.get("console", False)),
            "reduced": _flag(value.get("reduced", False)),
            "thumbnails_paused": _flag(value.get("thumbnails_paused", False))}


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

    def _path(self) -> Path:
        base = (self.work / "sessions").resolve()
        if base == self.work or not base.is_relative_to(self.work):
            raise SessionError("Dossier des sessions hors du dossier de travail.")
        path = base / self.name
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            raise SessionError("Fichier de session hors du dossier de travail.")
        return path

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
                write_atomic(self._path(), data)
            except OSError as exc:
                raise SessionError(f"Impossible d'enregistrer la session : {exc}") from exc
            self._sequences[client] = sequence
            self._sequences.move_to_end(client)
            while len(self._sequences) > 128:
                self._sequences.popitem(last=False)
            return True
