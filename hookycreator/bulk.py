"""Exploitation en masse : tout ce que le projet contient, textures et modèles.

Un romfs ne contient pas « des fichiers posés par terre » : les textures et les
modèles sont souvent **dans des packs** (`.pack.zs`, donc des archives SARC
compressées). Ce module descend dans les archives, classe ce qu'il trouve par
son contenu (magic) et pas seulement par son nom, puis exporte :

* textures → PNG (format d'origine décodé : Orin, ASTC, BCn…) ;
* modèles → glTF + OBJ + textures voisines.

Chaque élément reçoit un **statut explicite** : ``ok``, ``skip`` (format pas
encore décodable) ou ``error`` (avec la raison). Le rapport est écrit à côté des
fichiers : on peut donc savoir exactement ce qui a été exploité et ce qui ne
l'a pas été, sans relire des logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from hookymod import bfres as bfres_mod
from hookymod import bntx as bntx_mod
from hookymod import blender, render
from hookymod.container import Level, is_navigable
from hookymod.util import HookyError, safe_filename_stem, write_atomic

from .asset_names import is_animation_bfres, is_auxiliary_bfres
from .export_folders import ExportFolders

#: suffixes qui cachent une archive (contient un SARC ou une frame zstd + SARC)
ARCHIVE_SUFFIXES = (".pack.zs", ".pack", ".sarc", ".szs", ".sarc.zs")
#: profondeur maximale d'ouverture d'archives imbriquées
MAX_DEPTH = 3


@dataclass
class Item:
    """Un asset exploitable, qu'il soit posé dans le dossier ou dans un pack."""

    label: str                  # chemin lisible (peut contenir « !/ »)
    kind: str                   # texture | model | animation | auxiliary | other
    size: int = 0
    archive: str = ""           # pack d'origine (vide si fichier seul)
    editable: bool = False      # on sait réécrire ce type d'asset

    def as_json(self) -> dict:
        return {"label": self.label, "kind": self.kind, "size": self.size,
                "archive": self.archive, "editable": self.editable}


def _classify(data: bytes, name: str) -> str:
    """Classe par contenu quand c'est fiable, par nom quand le contenu se tait.

    Les noms ``*.anim.bfres`` ou ``*.Skeleton.bfres`` signalent généralement
    des ressources auxiliaires plutôt qu'un modèle. Ce tri reste heuristique :
    il ne garantit pas qu'un BFRES classé modèle contienne de la géométrie.
    """
    if data[:4] == b"BNTX":
        return "texture"
    low = name.lower()
    if is_animation_bfres(name):
        return "animation"
    if is_auxiliary_bfres(name):
        return "auxiliary"
    if data[:4] == b"FRES":
        return "model"
    if low.endswith((".bntx", ".bntx.zs")):
        return "texture"
    if low.endswith((".bfres", ".bfres.zs")):
        return "model"
    return "other"


def _walk(level: Level, prefix: str, depth: int, items: list[Item], seen: int = 0) -> int:
    """Parcourt une archive et ajoute ce qui est exploitable (compteur de garde)."""
    if depth > MAX_DEPTH or not level.archive:
        return seen
    for index in range(len(level)):
        if seen > 200_000:                      # garde-fou anti-archive folle
            return seen
        name = level.entry_name(index)
        label = f"{prefix}!/{name}"
        try:
            blob = level.entry_blob(index)
        except HookyError:
            continue
        seen += 1
        kind = _classify(blob, name)
        if kind in ("texture", "model", "animation", "auxiliary") and not is_archive_name(name):
            items.append(Item(label=label, kind=kind, size=len(blob), archive=prefix))
            continue
        if is_archive_name(name) or is_navigable(blob):
            try:
                child = level.open_entry(index)
            except HookyError:
                continue
            if child.archive:
                seen = _walk(child, label, depth + 1, items, seen)
    return seen


def is_archive_name(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def discover(project, include_archives: bool = True, *, assets=None) -> list[Item]:
    """Liste les fichiers seuls **et** le contenu des packs d'un inventaire donné."""
    items: list[Item] = []
    for asset in project.assets if assets is None else assets:
        if is_animation_bfres(asset.rel):
            kind = "animation"
        elif is_auxiliary_bfres(asset.rel):
            kind = "auxiliary"
        else:
            kind = asset.kind
        if kind in ("animation", "auxiliary"):
            items.append(Item(label=asset.rel, kind=kind, size=asset.size))
            continue
        if asset.kind in ("model", "texture"):
            items.append(Item(label=asset.rel, kind=asset.kind, size=asset.size,
                              editable=not asset.compressed or asset.kind == "texture"))
        elif include_archives and (asset.kind == "archive" or is_archive_name(asset.rel)):
            try:
                level = project.open_level(asset.rel)
            except (HookyError, OSError):
                continue
            if level and level.archive:
                _walk(level, asset.rel, 1, items)
    return items


def export_all(project, out_dir, kinds: tuple[str, ...] = ("texture", "model"),
               limit: int | None = None, progress=None) -> dict:
    """Exporte tout ce qui est reconnu et renvoie un rapport détaillé.

    :param progress: callback ``(done, total, label)`` pour l'avancement.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Un scan simultané ne doit pas associer le diagnostic d'un inventaire à
    # la liste d'un autre, ni bloquer pendant l'ouverture de toutes les archives.
    snapshot = getattr(project, "scan_snapshot", None)
    if callable(snapshot):
        assets, scan = snapshot()
    else:
        # Compatibilité avec les projets légers utilisés hors de l'application.
        assets = tuple(project.assets)
        truncated = bool(getattr(project, "scan_truncated", False))
        errors = getattr(project, "scan_errors", 0)
        scan = {"scan_truncated": truncated, "scan_incomplete": truncated or bool(errors),
                "scan_limit": getattr(project, "scan_limit", 0), "scan_errors": errors,
                "scan_error_examples": list(getattr(project, "scan_error_examples", ()))}
    items = [item for item in discover(project, assets=assets) if item.kind in kinds]
    if limit:
        items = items[:limit]
    report = {"out": str(out), "started": len(items), "ok": 0, "skip": 0,
              "error": 0, "items": [], "textures": 0, "models": 0, **scan}
    registries: dict[str, ExportFolders] = {}
    # Pour un objet compatible dépourvu de racine, ne pas prétendre reconnaître
    # ses dossiers lors d'une autre exécution : une identité provisoire évite
    # de les écraser si un autre objet emploie les mêmes libellés.
    source_root = getattr(project, "root", None) or out / f".anonymous-{uuid.uuid4().hex}"

    def output_folder(kind: str, label: str) -> Path:
        parent = out / ("textures" if kind == "texture" else "models")
        base, boundary = parent.resolve(), out.resolve()
        if base == boundary or not base.is_relative_to(boundary):
            raise HookyError("Le dossier d'export sort du dossier de destination.")
        registry = registries.get(kind)
        if registry is None:
            registry = registries[kind] = ExportFolders(parent, source_root, f"bulk-{kind}")
        stem = _slug(label)
        suffix = hashlib.sha256(label.encode("utf-8", "surrogatepass")).hexdigest()[:12]
        folder = registry.claim(label, stem, f"{stem}--{suffix}")
        if not folder.resolve().is_relative_to(base) or not registry.owns(folder, label):
            raise HookyError("Le dossier d'export a changé ou n'appartient plus à cette source.")
        return folder

    def publish(kind: str, label: str, folder: Path, stage: Path) -> None:
        boundary, base = out.resolve(), folder.parent.resolve()
        if (base == boundary or not base.is_relative_to(boundary)
                or not folder.resolve().is_relative_to(base)
                or not registries[kind].owns(folder, label)):
            raise HookyError("Le dossier d'export a changé ou n'appartient plus à cette source.")
        # Préparation isolée, puis remplacement fichier par fichier : un lien
        # symbolique préexistant est remplacé, jamais suivi vers sa cible.
        paths = sorted(stage.iterdir())
        if any(path.is_symlink() or not path.is_file() for path in paths):
            raise HookyError("Fichier inattendu dans l'export préparé.")
        for path in paths:
            os.replace(path, folder / path.name)

    for index, item in enumerate(items, start=1):
        if progress:
            progress(index - 1, len(items), item.label)
        entry = {"label": item.label, "kind": item.kind, "status": "ok"}
        try:
            if item.kind == "texture":
                data = project.read_asset(item.label)
                file = bntx_mod.BntxFile.parse(data, Path(item.label).name)
                if not file.textures:
                    raise HookyError("aucune texture dans ce BNTX")
                folder = output_folder("texture", item.label)
                entry["folder"] = folder.relative_to(out).as_posix()
                written = []
                used_names: set[str] = set()
                with tempfile.TemporaryDirectory(prefix=".hooky-export-", dir=out) as tmp:
                    stage = Path(tmp) / folder.name
                    stage.mkdir()
                    for texture in file.textures:
                        try:
                            stem = _slug(texture.name or str(texture.index))
                            name = f"{stem}.png"
                            counter = 2
                            while name.casefold() in used_names:
                                name = f"{stem[:100]}--{counter}.png"
                                counter += 1
                            used_names.add(name.casefold())
                            target = stage / name
                            texture.save_png(target, 0)
                            written.append(target.name)
                        except HookyError as exc:
                            entry.setdefault("notes", []).append(f"{texture.name} : {exc}")
                    if not written:
                        raise HookyError(entry.get("notes", ["aucun PNG écrit"])[0])
                    publish("texture", item.label, folder, stage)
                entry["files"] = written
                entry["format"] = file.textures[0].format_name
                report["textures"] += len(written)
            elif item.kind in ("animation", "auxiliary"):
                raise HookyError(
                    "BFRES auxiliaire (animation, squelette, caméra ou lumière) : "
                    "la lecture de ces données n'est pas encore prise en charge."
                )
            else:
                data = project.read_asset(item.label)
                file = bfres_mod.BfresFile.parse(data)
                try:
                    meshes = bfres_mod.meshes_from_file(file)
                except HookyError as exc:
                    if b"FSKA" in data or ".anim." in item.label.lower():
                        raise HookyError(
                            "animations BFRES : lecture pas encore implémentée"
                        ) from exc
                    raise
                folder = output_folder("model", item.label)
                entry["folder"] = folder.relative_to(out).as_posix()
                with tempfile.TemporaryDirectory(prefix=".hooky-export-", dir=out) as tmp:
                    stage = Path(tmp) / folder.name
                    stage.mkdir()
                    if item.archive:
                        # Les textures internes aux packs ne sont pas indexées par
                        # matériau : ne jamais deviner leur raccordement.
                        maps = {"material_maps": {}, "files": [], "warnings": [
                            "Modèle dans une archive : textures non associées automatiquement ; "
                            "exporte-les séparément et vérifie les matériaux."]}
                    else:
                        maps = project.export_maps_for(item.label, stage)
                    notes = ["Export en masse statique : aucun clip sélectionné."] + list(file.warnings) + maps["warnings"]
                    if maps["files"] and any(not mesh.uvs for mesh in meshes):
                        notes.append(blender.NO_UV_WARNING)
                    written = blender.export_bundle(meshes, stage, folder.name,
                                                    formats=("gltf", "glb", "obj"),
                                                    material_maps=maps.get("material_maps", {}),
                                                    warnings=notes)
                    produced = {p.name for p in written.values()}
                    produced.update(map_file["file"] for map_file in maps["files"])
                    if (stage / "TEXTURES.md").is_file() and not item.archive:
                        produced.add("TEXTURES.md")
                    if "obj" in written and written["obj"].with_suffix(".mtl").is_file():
                        produced.add(written["obj"].with_suffix(".mtl").name)
                    publish("model", item.label, folder, stage)
                entry["files"] = sorted(produced)
                entry["notes"] = notes[:100] + ([f"{len(notes) - 100} remarque(s) omise(s)."]
                                                if len(notes) > 100 else [])
                entry["faces"] = sum(len(m.faces) for m in meshes)
                entry["version"] = f"{file.version >> 16}.{(file.version >> 8) & 0xFF}"
                report["models"] += 1
        except HookyError as exc:
            message = str(exc).splitlines()[0]
            entry.update(status="skip", reason=message)
            report["skip"] += 1
        except Exception as exc:      # jamais d'arrêt : un asset ne bloque pas tout
            entry.update(status="error", reason=f"{type(exc).__name__}: {exc}")
            report["error"] += 1
        else:
            report["ok"] += 1
        report["items"].append(entry)
    if progress:
        progress(len(items), len(items), "")
    # Remplacer un ancien rapport sans suivre un éventuel lien symbolique.
    write_atomic(out / "rapport.json",
                 json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8", "backslashreplace"))
    return report


def summary(report: dict) -> str:
    lines = [
        f"{report['started']} élément(s) examiné(s) → {report['ok']} ok, "
        f"{report['skip']} ignoré(s), {report['error']} en erreur",
        f"  textures : {report['textures']} PNG   ·   modèles : {report['models']} export(s)",
    ]
    if report.get("scan_truncated"):
        lines.append("  ⚠ Scan incomplet : relance avec --scan-limit 0 pour indexer "
                     "tous les fichiers reconnus.")
    if report.get("scan_errors"):
        lines.append(f"  ⚠ Scan incomplet : {report['scan_errors']} entrée(s) illisible(s).")
        lines.extend(f"    {detail}" for detail in report.get("scan_error_examples", ()))
    for item in report["items"]:
        if item["status"] == "skip":
            lines.append(f"  ⚠ {item['label']} — {item.get('reason', '')}")
        elif item["status"] == "error":
            lines.append(f"  ✗ {item['label']} — {item.get('reason', '')}")
    return "\n".join(lines)


def _slug(label: str) -> str:
    return safe_filename_stem(label)
