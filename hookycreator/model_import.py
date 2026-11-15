"""Import et inspection OBJ/glTF bornés, sans accès réseau ni écriture des sources."""
from __future__ import annotations

import base64
import binascii
import math
from pathlib import PurePosixPath

from hookymod import modelio, render
from hookymod.util import HookyError

from . import model_target
from .project import Project, ProjectError

MAX_FILES = 17  # un modèle et jusqu'à 16 tampons glTF
MAX_REQUEST = 96 * 1024 * 1024  # JSON base64, avant lecture par le serveur


def _read_source(payload: dict):
    if not isinstance(payload, dict):
        raise ProjectError("Requête d'import invalide.")
    files = payload.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ProjectError("Sélectionne un modèle et au plus 16 fichiers .bin.")
    decoded: dict[str, bytes] = {}
    folded = set()
    size = 0
    primary = []
    for entry in files:
        if not isinstance(entry, dict):
            raise ProjectError("Fichier envoyé invalide.")
        name, encoded = entry.get("name"), entry.get("data")
        if (not isinstance(name, str) or not name or len(name) > 255 or name in (".", "..")
                or "/" in name or "\\" in name or "\x00" in name):
            raise ProjectError("Nom de fichier importé invalide.")
        if name.casefold() in folded:
            raise ProjectError("Deux fichiers importés portent le même nom.")
        folded.add(name.casefold())
        suffix = PurePosixPath(name.lower()).suffix
        if suffix in (".obj", ".gltf", ".glb"):
            primary.append(name)
        elif suffix != ".bin":
            raise ProjectError("Formats attendus : .obj, .gltf, .glb et leurs fichiers .bin.")
        if not isinstance(encoded, str) or len(encoded) > (modelio.MAX_BYTES * 4 // 3 + 8):
            raise ProjectError("Fichier importé trop volumineux ou invalide.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ProjectError("Données du fichier importé invalides.") from None
        size += len(raw)
        if not raw or size > modelio.MAX_BYTES:
            raise ProjectError("Fichiers vides ou import supérieur à 64 Mio.")
        decoded[name] = raw
    if len(primary) != 1:
        raise ProjectError("Sélectionne exactement un fichier .obj, .gltf ou .glb.")
    filename = primary[0]
    if len(files) > 1 and not filename.lower().endswith(".gltf"):
        raise ProjectError("Les fichiers .bin supplémentaires sont réservés aux .gltf.")
    axis = payload.get("axis", "keep")
    try:
        scale = float(payload.get("scale", 1))
    except (TypeError, ValueError, OverflowError):
        raise ProjectError("Échelle d'import invalide.") from None
    if not math.isfinite(scale) or not 0.001 <= scale <= 1000:
        raise ProjectError("Échelle attendue entre 0,001 et 1 000.")
    result = modelio.read_model(filename, decoded.pop(filename), decoded, axis=axis, scale=scale)
    result.warnings.append("Seule la géométrie est injectée : les matériaux et textures de la cible sont conservés ; "
                           "ceux du fichier importé ne sont pas transférés.")
    if any(mesh.normals is None for mesh in result.meshes):
        result.warnings.append("Normales absentes dans le fichier importé : elles seront recalculées si nécessaires.")
    return filename, result


def _has_polygons(result) -> bool:
    return any(note.startswith("Polygones OBJ") for note in result.warnings)


def inspect_model(payload: dict) -> dict:
    """Analyse sans écriture ; l'aperçu est limité et ne remplace pas l'export."""
    try:
        filename, result = _read_source(payload)
        if _has_polygons(result):
            result.warnings.append("Triangule l'OBJ avant l'injection : les polygones ne sont pas acceptés.")
        if result.material_slots > 1:
            result.warnings.append("Plusieurs matériaux source : une cible à une seule forme ne peut pas les conserver.")
        preview = b""
        try:
            preview = render.render_png(result.meshes, 480, 360, cull=False, frustum=False,
                                        max_faces=20_000, supersample=1)
        except HookyError:
            result.warnings.append("Aperçu indisponible ; l'analyse de géométrie reste valable.")
        return {"file": filename, "meshes": len(result.meshes),
                "vertices": sum(len(mesh.positions) for mesh in result.meshes),
                "faces": sum(len(mesh.faces) for mesh in result.meshes),
                "materials": sorted({mesh.material for mesh in result.meshes if mesh.material})[:128],
                "can_import": (not _has_polygons(result) and result.material_slots <= 1
                               and not result.unsupported_attributes),
                "warnings": result.warnings,
                "preview": ("data:image/png;base64," + base64.b64encode(preview).decode("ascii")) if preview else ""}
    except HookyError as exc:
        raise ProjectError(f"Lecture impossible : {exc}") from exc
    except (IndexError, KeyError, TypeError, RecursionError, ValueError) as exc:
        raise ProjectError("Structure du fichier importé invalide ou non prise en charge.") from exc


def import_model(project: Project, payload: dict) -> dict:
    try:
        filename, result = _read_source(payload)
        if _has_polygons(result):
            raise ProjectError("L'OBJ contient des polygones : triangule-le avant l'injection pour éviter une géométrie incorrecte.")
        target = payload.get("target")
        if not isinstance(target, str) or not target or len(target) > 2048:
            raise ProjectError("Choisis un modèle cible du projet.")
        fit = payload.get("fit", False)
        if not isinstance(fit, bool):
            raise ProjectError("Option d'adaptation invalide.")
        return model_target.inject_model(project, result, target, filename, fit=fit)
    except HookyError as exc:
        raise ProjectError(f"Import impossible : {exc}") from exc
    except (IndexError, KeyError, TypeError, RecursionError, ValueError) as exc:
        # Les fichiers tiers sont non fiables : ne jamais exposer de traceback.
        raise ProjectError("Structure du fichier importé invalide ou non prise en charge.") from exc
