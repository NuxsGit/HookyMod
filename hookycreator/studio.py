"""HookyMod's: bounded design workspaces and their .hooky archive variant.

An org.hookymod.workspace v1 archive is a *design project*, not a playable
mod. It contains a manifest and validated workspace JSON, including any small
user-owned assets explicitly attached to elements. Other .hooky variants can
be defined separately. The optional block simulator is pure and isolated: it
does not run scripts or modify files. Generating game formats needs a separate,
verified target adapter.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import json
import re
import stat
import struct
import threading
import uuid
import zipfile
import zlib
from pathlib import Path

from hookymod.util import write_atomic

SCHEMA = 1
FORMAT = "org.hookymod.workspace"
MAX_WORKSPACES = 100
MAX_ELEMENTS = 100
MAX_BLOCKS = 100
MAX_ASSET_BYTES = 2 * 1024 * 1024
MAX_TOTAL_ASSET_BYTES = 6 * 1024 * 1024
MAX_WORKSPACE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 12 * 1024 * 1024
MAX_REQUEST_BYTES = ((MAX_ARCHIVE_BYTES + 2) // 3) * 4 + 8192
MAX_SIMULATION_VALUES = 100
ID_RE = re.compile(r"[a-f0-9]{32}\Z")
KINDS = frozenset({"item", "scene", "dungeon", "building"})
BLOCKS = frozenset({
    "when_enter", "when_interact", "when_use", "if_has_item", "if_flag",
    "show_message", "give_item", "open_door", "play_scene", "wait",
})
EVENT_BLOCKS = frozenset({"when_enter", "when_interact", "when_use"})
ASSET_SUFFIXES = frozenset({".png", ".obj", ".glb"})
# Protects stores belonging to multiple server instances in the same process.
# Revision checks also prevent stale clients from silently replacing a newer save.
_LOCK = threading.RLock()


class StudioError(ValueError):
    """A workspace or archive is invalid or cannot be saved safely."""


def _text(value: object, label: str, limit: int, *, required: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > limit or "\x00" in value
            or any((ord(char) < 32 and char not in "\n\r\t")
                   or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise StudioError(f"{label} invalide.")
    if required and not value.strip():
        raise StudioError(f"{label} ne peut pas être vide.")
    return value


def _identity(value: object, label: str = "Identifiant") -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise StudioError(f"{label} invalide.")
    return value


def _keys(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise StudioError(f"{label} invalide ou version non prise en charge.")
    return value


def _decode_base64(value: object, maximum: int, label: str) -> bytes:
    if not isinstance(value, str) or len(value) > ((maximum + 2) // 3) * 4:
        raise StudioError(f"{label} trop volumineux ou invalide.")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise StudioError(f"{label} invalide.") from exc
    if len(data) > maximum:
        raise StudioError(f"{label} trop volumineux.")
    return data


def _asset(value: object) -> tuple[dict | None, int]:
    if value is None:
        return None, 0
    item = _keys(value, {"name", "data"}, "Ressource")
    name = _text(item["name"], "Nom de ressource", 128, required=True)
    if (name in {".", ".."} or "/" in name or "\\" in name or name.startswith(".")
            or any(ord(char) < 32 for char in name)):
        raise StudioError("Nom de ressource invalide.")
    suffix = Path(name).suffix.lower()
    if suffix not in ASSET_SUFFIXES:
        raise StudioError("Seuls les PNG, OBJ et GLB personnels sont acceptés.")
    data = _decode_base64(item["data"], MAX_ASSET_BYTES, "Ressource")
    if not data:
        raise StudioError("Une ressource ne peut pas être vide.")
    # These are inert attachments, not a promise that a model or image is valid
    # for a game. Never render or execute imported scripts or external links.
    if suffix == ".png":
        if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
            raise StudioError("En-tête PNG invalide.")
        width, height = struct.unpack_from(">II", data, 16)
        if not 1 <= width <= 16384 or not 1 <= height <= 16384:
            raise StudioError("Dimensions PNG invalides.")
    elif suffix == ".glb":
        if (len(data) < 12 or data[:4] != b"glTF" or struct.unpack_from("<I", data, 4)[0] != 2
                or struct.unpack_from("<I", data, 8)[0] != len(data)):
            raise StudioError("En-tête GLB 2.0 invalide.")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError as exc:
            raise StudioError("Le fichier OBJ doit être en UTF-8.") from exc
        if "\x00" in text:
            raise StudioError("Fichier OBJ invalide.")
    return {"name": name, "data": item["data"]}, len(data)


def validate_workspace(value: object) -> dict:
    """Validate and copy a bounded, declarative workspace; do not execute it."""
    obj = _keys(value, {"schema", "id", "name", "description", "author", "target", "revision", "elements"},
                "Espace de travail")
    if type(obj["schema"]) is not int or obj["schema"] != SCHEMA:
        raise StudioError("Version de projet inconnue.")
    identity = _identity(obj["id"])
    revision = obj["revision"]
    if type(revision) is not int or not 0 <= revision < 1_000_000_000:
        raise StudioError("Révision invalide.")
    name = _text(obj["name"], "Nom du projet", 96, required=True).strip()
    description = _text(obj["description"], "Description", 1200)
    author = _text(obj["author"], "Auteur", 96)
    target = _text(obj["target"], "Cible", 160)
    elements = obj["elements"]
    if not isinstance(elements, list) or len(elements) > MAX_ELEMENTS:
        raise StudioError("Nombre d'éléments invalide.")
    seen: set[str] = set()
    clean_elements = []
    total_assets = 0
    for element in elements:
        entry = _keys(element, {"id", "kind", "name", "description", "asset", "blocks"}, "Élément")
        entry_id = _identity(entry["id"], "Identifiant d'élément")
        if entry_id in seen:
            raise StudioError("Identifiants d'éléments en double.")
        seen.add(entry_id)
        if not isinstance(entry["kind"], str) or entry["kind"] not in KINDS:
            raise StudioError("Type d'élément inconnu.")
        entry_name = _text(entry["name"], "Nom de l'élément", 96, required=True).strip()
        entry_description = _text(entry["description"], "Description de l'élément", 1200)
        asset, size = _asset(entry["asset"])
        total_assets += size
        if total_assets > MAX_TOTAL_ASSET_BYTES:
            raise StudioError("Les ressources du projet dépassent 6 Mio au total.")
        blocks = entry["blocks"]
        if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
            raise StudioError("Nombre de blocs invalide.")
        clean_blocks = []
        for block in blocks:
            row = _keys(block, {"type", "value"}, "Bloc")
            if not isinstance(row["type"], str) or row["type"] not in BLOCKS:
                raise StudioError("Type de bloc inconnu.")
            clean_blocks.append({"type": row["type"], "value": _text(row["value"], "Valeur du bloc", 256)})
        clean_elements.append({"id": entry_id, "kind": entry["kind"], "name": entry_name,
                               "description": entry_description, "asset": asset, "blocks": clean_blocks})
    return {"schema": SCHEMA, "id": identity, "name": name, "description": description,
            "author": author, "target": target, "revision": revision, "elements": clean_elements}


def _encode_workspace(workspace: dict) -> bytes:
    encoded = json.dumps(workspace, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_WORKSPACE_BYTES:
        raise StudioError("Projet trop volumineux.")
    return encoded


def check_workspace(workspace: dict) -> dict:
    """Design diagnostics. A successful check is never an in-game build."""
    workspace = validate_workspace(workspace)
    issues = []
    if not workspace["elements"]:
        issues.append("Ajoute au moins un élément à ton projet.")
    for element in workspace["elements"]:
        blocks = element["blocks"]
        if blocks and blocks[0]["type"] not in EVENT_BLOCKS:
            issues.append(f"{element['name']} : la procédure ne commence pas par un événement.")
        if any(not block["value"].strip() for block in blocks if block["type"] not in EVENT_BLOCKS):
            issues.append(f"{element['name']} : au moins un bloc n'a pas de valeur.")
    return {
        "design_valid": not issues,
        "playable": False,
        "adapter": "unavailable",
        "issues": issues,
        "message": "Projet de conception uniquement : aucun générateur ou lanceur en jeu n'est encore validé.",
    }


def _simulation_values(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_SIMULATION_VALUES:
        raise StudioError(f"{label} : 100 valeurs maximum.")
    unique: dict[str, None] = {}
    for entry in value:
        text = _text(entry, label, 256, required=True).strip()
        if any(char in text for char in "\n\r\t"):
            raise StudioError(f"{label} : une valeur par ligne.")
        unique[text] = None
    return list(unique)


def simulate_element(value: object, event: object, items: object, flags: object) -> dict:
    """Interpret one ordered block sketch in memory, with no external effects.

    Each event starts a new group; only groups matching ``event`` are active.
    Conditions form an AND over the remainder of their group. Missing values
    are reported, and a missing condition disables the rest of its group.
    Waits are recorded but never sleep. Asset data and event details are not
    used. This deliberately models no game engine or launcher.
    """
    if not isinstance(event, str) or event not in EVENT_BLOCKS:
        raise StudioError("Événement de simulation inconnu.")
    inventory = _simulation_values(items, "Objets initiaux")
    active_flags = _simulation_values(flags, "États initiaux")
    # Reuse the strict schema without requiring unrelated workspace metadata.
    # The caller should omit the attachment; it is never interpreted here.
    candidate = {"schema": SCHEMA, "id": "0" * 32, "name": "Simulation",
                 "description": "", "author": "", "target": "", "revision": 0,
                 "elements": [value]}
    element = validate_workspace(candidate)["elements"][0]
    trace, doors, scenes = [], [], []
    active = event_found = False
    for index, block in enumerate(element["blocks"], 1):
        kind, raw = block["type"], block["value"]
        text = raw.strip()
        if kind in EVENT_BLOCKS:
            active = kind == event
            event_found |= active
            result = "event_matched" if active else "event_ignored"
        elif not active:
            result = "skipped"
        elif not text:
            result = "missing"
            if kind in {"if_has_item", "if_flag"}:
                active = False
        elif kind in {"if_has_item", "if_flag"}:
            matched = text in (inventory if kind == "if_has_item" else active_flags)
            active = matched
            result = "condition_true" if matched else "condition_false"
        else:
            result = "simulated"
            if kind == "give_item" and text not in inventory:
                inventory.append(text)
            elif kind == "open_door" and text not in doors:
                doors.append(text)
            elif kind == "play_scene":
                scenes.append(text)
            # show_message and wait are represented by their trace only.
        trace.append({"index": index, "type": kind, "value": raw, "result": result})
    return {"playable": False, "event_found": event_found, "trace": trace,
            "items": inventory, "doors": doors, "scenes": scenes}


def make_archive(workspace: dict) -> bytes:
    """Create a self-contained, inert .hooky design archive."""
    workspace = validate_workspace(workspace)
    payload = _encode_workspace(workspace)
    manifest = {
        "format": FORMAT,
        "version": SCHEMA,
        "kind": "design-workspace",
        "playable": False,
        "workspace": "workspace.json",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in (("manifest.json", manifest_bytes), ("workspace.json", payload)):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, data)
    result = buffer.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise StudioError("Archive trop volumineuse.")
    return result


def _read_json(data: bytes) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise StudioError("Clé JSON en double.")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise StudioError("Valeur JSON invalide.")

    return json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def read_archive(data: bytes) -> dict:
    """Read only two bounded, named files; never extract paths or run content."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ARCHIVE_BYTES:
        raise StudioError("Archive .hooky vide ou trop volumineuse.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) != 2 or {entry.filename for entry in entries} != {"manifest.json", "workspace.json"}:
                raise StudioError("Archive .hooky : fichiers inattendus ou en double.")
            for entry in entries:
                limit = 8192 if entry.filename == "manifest.json" else MAX_WORKSPACE_BYTES
                if (entry.is_dir() or entry.flag_bits & 1 or entry.file_size > limit
                        or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or stat.S_ISLNK(entry.external_attr >> 16)):
                    raise StudioError("Archive .hooky invalide ou trop volumineuse.")
            manifest = _read_json(archive.read("manifest.json"))
            payload = archive.read("workspace.json")
        manifest = _keys(manifest, {"format", "version", "kind", "playable", "workspace", "sha256"}, "Manifeste")
        if (manifest["format"] != FORMAT or type(manifest["version"]) is not int or manifest["version"] != SCHEMA
                or manifest["kind"] != "design-workspace" or manifest["playable"] is not False
                or manifest["workspace"] != "workspace.json"
                or not isinstance(manifest["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"])
                or hashlib.sha256(payload).hexdigest() != manifest["sha256"]):
            raise StudioError("Manifeste .hooky incompatible ou empreinte incorrecte.")
        return validate_workspace(_read_json(payload))
    except StudioError:
        raise
    except (zipfile.BadZipFile, ValueError, UnicodeError, OSError, RuntimeError, KeyError,
            NotImplementedError, RecursionError, EOFError, struct.error, zlib.error) as exc:
        raise StudioError("Impossible de lire l'archive .hooky.") from exc


class WorkspaceStore:
    """Save projects under the current work directory without modifying originals."""

    def __init__(self, work: Path) -> None:
        self.work = Path(work).resolve()

    def _base(self, *, create: bool = False) -> Path:
        base = self.work / "hookymods"
        if base.is_symlink() or (base.exists() and not base.is_dir()):
            raise StudioError("Dossier HookyMod's invalide.")
        if create:
            base.mkdir(parents=True, exist_ok=True)
        if base.resolve().parent != self.work:
            raise StudioError("Dossier HookyMod's hors du dossier de travail.")
        return base

    def _path(self, identity: object, *, create: bool = False) -> Path:
        base = self._base(create=create)
        path = base / f"{_identity(identity)}.json"
        if path.is_symlink() or path.resolve().parent != base.resolve():
            raise StudioError("Chemin de projet invalide.")
        return path

    def _load(self, identity: object) -> dict:
        path = self._path(identity)
        try:
            with path.open("rb") as file:
                raw = file.read(MAX_WORKSPACE_BYTES + 1)
            if len(raw) > MAX_WORKSPACE_BYTES:
                raise StudioError("Projet trop volumineux.")
            workspace = validate_workspace(_read_json(raw))
            if workspace["id"] != identity:
                raise StudioError("Identifiant du projet incohérent.")
            return workspace
        except StudioError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise StudioError("Projet introuvable ou illisible.") from exc

    def list(self) -> dict:
        with _LOCK:
            base = self._base()
            if not base.exists():
                return {"workspaces": [], "unreadable": 0}
            items, unreadable = [], 0
            paths = sorted(base.glob("*.json"))
            if len(paths) > MAX_WORKSPACES:
                raise StudioError("Trop de projets dans le dossier HookyMod's.")
            for path in paths:
                try:
                    workspace = self._load(path.stem)
                    items.append({key: workspace[key] for key in ("id", "name", "description", "author", "target", "revision")}
                                 | {"count": len(workspace["elements"])})
                except StudioError:
                    unreadable += 1
            items.sort(key=lambda item: (item["name"].casefold(), item["id"]))
            return {"workspaces": items, "unreadable": unreadable}

    def load(self, identity: object) -> dict:
        with _LOCK:
            return self._load(identity)

    def _save_new(self, workspace: dict) -> dict:
        listing = self.list()
        if len(listing["workspaces"]) + listing["unreadable"] >= MAX_WORKSPACES:
            raise StudioError("Nombre maximal de projets atteint.")
        workspace = copy.deepcopy(workspace)
        # Import always gets a fresh identity; it cannot replace an existing project.
        workspace["id"] = uuid.uuid4().hex
        workspace["revision"] = 0
        workspace = validate_workspace(workspace)
        path = self._path(workspace["id"], create=True)
        if path.exists():
            raise StudioError("Identifiant de projet déjà utilisé ; réessaie.")
        try:
            write_atomic(path, _encode_workspace(workspace))
        except OSError as exc:
            raise StudioError("Impossible d'enregistrer le projet dans le dossier de travail.") from exc
        return workspace

    def create(self, name: object, description: object = "", author: object = "", target: object = "") -> dict:
        candidate = {"schema": SCHEMA, "id": uuid.uuid4().hex, "name": name, "description": description,
                     "author": author, "target": target, "revision": 0, "elements": []}
        with _LOCK:
            return self._save_new(candidate)

    def save(self, candidate: object) -> dict:
        with _LOCK:
            workspace = validate_workspace(candidate)
            current = self._load(workspace["id"])
            if workspace["revision"] != current["revision"]:
                raise StudioError("Conflit de sauvegarde : ce projet a changé dans une autre fenêtre. Recharge-le avant de réessayer.")
            if workspace["revision"] >= 999_999_999:
                raise StudioError("Limite de révisions atteinte : exporte puis réimporte le projet.")
            workspace["revision"] += 1
            try:
                write_atomic(self._path(workspace["id"]), _encode_workspace(workspace))
            except OSError as exc:
                raise StudioError("Impossible d'enregistrer le projet dans le dossier de travail.") from exc
            return workspace

    def delete(self, identity: object, revision: object) -> None:
        with _LOCK:
            current = self._load(identity)
            if type(revision) is not int or revision != current["revision"]:
                raise StudioError("Conflit de suppression : ce projet a changé dans une autre fenêtre.")
            try:
                self._path(identity).unlink()
            except OSError as exc:
                raise StudioError("Impossible de supprimer le projet.") from exc

    def copy(self, candidate: object) -> dict:
        """Preserve a draft under a new identity, including after a conflict."""
        workspace = validate_workspace(candidate)
        with _LOCK:
            return self._save_new(workspace)

    def export(self, identity: object) -> bytes:
        with _LOCK:
            return make_archive(self._load(identity))

    def import_archive(self, encoded: object) -> dict:
        data = _decode_base64(encoded, MAX_ARCHIVE_BYTES, "Archive .hooky")
        workspace = read_archive(data)
        with _LOCK:
            return self._save_new(workspace)
