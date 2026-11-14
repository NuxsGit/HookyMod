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

import json
from dataclasses import dataclass, field
from pathlib import Path

from hookymod import bfres as bfres_mod
from hookymod import bntx as bntx_mod
from hookymod import blender, render
from hookymod.container import Level, is_navigable
from hookymod.util import HookyError

#: suffixes qui cachent une archive (contient un SARC ou une frame zstd + SARC)
ARCHIVE_SUFFIXES = (".pack.zs", ".pack", ".sarc", ".szs", ".sarc.zs")
#: profondeur maximale d'ouverture d'archives imbriquées
MAX_DEPTH = 3


@dataclass
class Item:
    """Un asset exploitable, qu'il soit posé dans le dossier ou dans un pack."""

    label: str                  # chemin lisible (peut contenir « !/ »)
    kind: str                   # texture | model | other
    size: int = 0
    archive: str = ""           # pack d'origine (vide si fichier seul)
    editable: bool = False      # on sait réécrire ce type d'asset

    def as_json(self) -> dict:
        return {"label": self.label, "kind": self.kind, "size": self.size,
                "archive": self.archive, "editable": self.editable}


#: mots qui trahissent un BFRES sans maillage (animations, squelettes, coquilles)
ANIM_NAMES = (".anim.", "animation", "skeleton", ".camera.", ".light.")


def _classify(data: bytes, name: str) -> str:
    """Classe par contenu quand c'est fiable, par nom quand le contenu se tait.

    Les BFRES de ce jeu ne portent pas les magies ``FMDL``/``FSHP`` en clair :
    les sections sont atteintes par des offsets relatifs. On ne peut donc pas
    décider « modèle » sur la présence d'une magie ; en revanche un fichier
    ``*.anim.bfres`` ou ``*.Skeleton.bfres`` ne contiendra jamais de faces.
    """
    if data[:4] == b"BNTX":
        return "texture"
    low = name.lower()
    if any(word in low for word in ANIM_NAMES):
        return "animation"
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
        if kind in ("texture", "model") and not is_archive_name(name):
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
        if ".anim." in asset.rel.lower():
            items.append(Item(label=asset.rel, kind="animation", size=asset.size))
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
                folder = out / "textures" / _slug(item.label)
                folder.mkdir(parents=True, exist_ok=True)
                written = []
                for texture in file.textures:
                    try:
                        target = folder / f"{texture.name or texture.index}.png"
                        texture.save_png(target, 0)
                        written.append(target.name)
                    except HookyError as exc:
                        entry.setdefault("notes", []).append(f"{texture.name} : {exc}")
                if not written:
                    raise HookyError(entry.get("notes", ["aucun PNG écrit"])[0])
                entry["files"] = written
                entry["format"] = file.textures[0].format_name
                report["textures"] += len(written)
            elif item.kind == "animation":
                raise HookyError(
                    "BFRES d'animations : les pistes ne sont pas encore lues "
                    "(seule la géométrie l'est)."
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
                folder = out / "models" / _slug(item.label)
                folder.mkdir(parents=True, exist_ok=True)
                written = blender.export_bundle(meshes, folder, folder.name,
                                                formats=("gltf", "obj"))
                entry["files"] = sorted(p.name for p in written.values())
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
    (out / "rapport.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
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
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in label)
    return cleaned.strip("_")[:120] or "asset"
