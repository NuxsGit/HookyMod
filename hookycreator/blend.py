"""Traduction ``.blend`` → modèle du jeu, côté HookyCreator.

Le trajet complet, sans Blender installé :

1. :mod:`hookymod.blendread` ouvre le ``.blend`` et en sort les maillages ;
2. on peut les mettre à l'échelle du modèle d'origine (option « adapter ») ;
3. :mod:`hookymod.bfres_import` réécrit la géométrie **dans** le BFRES du jeu,
   en gardant sa disposition de sommets, ses matériaux et ses animations ;
4. le résultat part dans ``work/assets/`` comme n'importe quelle modification,
   donc il est repris par l'assemblage du mod.

Rien n'est écrit dans le dump : l'original reste intact.
"""

from __future__ import annotations

import base64
from pathlib import Path

from hookymod import bfres as bfres_mod
from hookymod import bfres_import, blendread, render, zs
from hookymod.mesh import MeshData, load_obj  # noqa: F401  (réexport pratique)
from hookymod.util import HookyError

from .project import Project, ProjectError

MAX_UPLOAD = 96 * 1024 * 1024          # un .blend de mod tient largement dedans
MAX_BLENDS = 300


def blend_dir(project: Project) -> Path:
    folder = project.work / "blends"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _clean_name(name: str) -> str:
    stem = Path(str(name)).name.strip().replace("\\", "_")
    keep = "".join(ch for ch in stem if ch.isalnum() or ch in " ._-()")
    keep = keep.strip(" .") or "modele"
    if not keep.lower().endswith(".blend"):
        keep += ".blend"
    return keep


def save_upload(project: Project, filename: str, raw: bytes) -> dict:
    """Range un .blend envoyé par le navigateur dans ``work/blends``."""
    if not raw:
        raise ProjectError("Fichier vide.")
    if len(raw) > MAX_UPLOAD:
        raise ProjectError(f"Fichier trop gros ({len(raw) / 1024 ** 2:.0f} Mo, "
                           f"maximum {MAX_UPLOAD // 1024 ** 2} Mo).")
    if not blendread.is_blend(raw):
        raise ProjectError("Ce fichier n'est pas un .blend (ni compressé en zstd).")
    target = blend_dir(project) / _clean_name(filename)
    target.write_bytes(raw)
    return {"ref": f"work:{target.name}", "name": target.name, "size": len(raw),
            "path": project.shown_path(target)}


def resolve(project: Project, ref: str) -> Path:
    """Retrouve un .blend : ``work:nom.blend`` ou chemin relatif au projet."""
    ref = str(ref or "").strip()
    if not ref:
        raise ProjectError("Aucun fichier .blend indiqué.")
    if ref.startswith("work:"):
        candidate = (blend_dir(project) / _clean_name(ref[5:])).resolve()
        if not candidate.is_file():
            raise ProjectError(f".blend introuvable : {ref[5:]}")
        return candidate
    path = project.asset(ref)                 # refuse « .. » et les absents
    if not blendread.is_blend(path.read_bytes()[:16]):
        raise ProjectError(f"{ref} n'est pas un fichier .blend.")
    return path


def list_blends(project: Project) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for path in sorted(blend_dir(project).glob("*.blend")):
        found.append({"ref": f"work:{path.name}", "name": path.name,
                      "size": path.stat().st_size, "origin": "envoi"})
        seen.add(path.name.lower())
    for path in sorted(project.root.rglob("*.blend")):
        if len(found) >= MAX_BLENDS:
            break
        if path.name.lower() in seen or "hookycreator_work" in path.parts:
            continue
        rel = path.relative_to(project.root).as_posix()
        found.append({"ref": rel, "name": path.name, "size": path.stat().st_size,
                      "origin": "projet"})
    return found


def inspect(project: Project, ref: str, *, axis: str = "y_up", scale: float = 1.0,
            only: str | None = None) -> dict:
    path = resolve(project, ref)
    try:
        result = blendread.read_blend(path.read_bytes(), axis=axis, scale=scale, only=only)
    except HookyError as exc:
        raise ProjectError(str(exc)) from exc
    payload = result.as_json()
    payload.update({"ref": ref, "file": path.name, "size": path.stat().st_size})
    if path.suffix.lower() != ".blend":
        payload.setdefault("warnings", []).append("extension inattendue")
    return payload


def why_not_injectable(data: bytes) -> str:
    """Pourquoi la géométrie ne peut pas être réécrite dans ce BFRES (vide = ok).

    HookyMod réécrit la géométrie **dans** un BFRES en gardant sa disposition :
    cela demande de connaître ses shapes, leurs tampons et la table de
    sous-maillages. Un BFRES 10.x se lit très bien, mais sa table de
    sous-maillages n'est pas décodée : sans elle, écrire un autre nombre de
    triangles promettrait une géométrie que le jeu n'afficherait pas. On refuse
    donc, en le disant, plutôt que de produire un fichier faux.
    """
    try:
        file = bfres_mod.BfresFile.parse(data)
    except HookyError as exc:
        return f"BFRES illisible ({exc})."
    if any(model.shapes for model in file.models):
        return ""
    from hookymod import bfres_v10

    if bfres_v10.parse(data) is not None:
        return ("BFRES 10.x : lecture OK, mais la réécriture des sous-maillages 10.x "
                "n'est pas encore faite (table des sous-maillages non décodée). "
                "Passe par « Exporter pour Blender », ou choisis un modèle BFRES 7/8.")
    return "aucune shape exploitable dans ce BFRES."


def _load(project: Project, ref: str, *, axis: str, scale: float,
          only: str | None) -> blendread.BlendImport:
    path = resolve(project, ref)
    try:
        return blendread.read_blend(path.read_bytes(), axis=axis, scale=scale, only=only)
    except HookyError as exc:
        raise ProjectError(str(exc)) from exc


def preview_png(project: Project, ref: str, width: int = 640, height: int = 480,
                *, axis: str = "y_up", scale: float = 1.0, only: str | None = None,
                views: int = 1) -> bytes:
    result = _load(project, ref, axis=axis, scale=scale, only=only)
    meshes = result.meshes
    if views > 1:
        try:
            return render.turntable_png(meshes, views=views, width=width, height=height,
                                        cull=project.settings.cull,
                                        frustum=project.settings.frustum)
        except HookyError:
            return render.turntable_png(meshes, views=views, width=width, height=height,
                                        cull=False, frustum=False)
    # un maillage plat ou retourné reste visible : on retente sans culling plutôt
    # que d'afficher « aucune face visible » sur un fichier valide.
    attempts = [(0.7, -0.35, project.settings.cull, project.settings.frustum),
                (0.7, -0.35, False, project.settings.frustum),
                (0.0, 0.0, False, False)]
    last: HookyError | None = None
    for yaw, pitch, cull, frustum in attempts:
        try:
            return render.render_png(meshes, width, height, yaw, pitch, cull=cull,
                                     max_faces=project.settings.max_faces,
                                     supersample=project.settings.supersample,
                                     frustum=frustum)
        except HookyError as exc:
            last = exc
    raise ProjectError(str(last) if last else "Aperçu impossible.")


def _bounds(meshes: list[MeshData]) -> tuple[list[float], list[float]]:
    lows = [float("inf")] * 3
    highs = [float("-inf")] * 3
    for mesh in meshes:
        lo, hi = mesh.bounds()
        for axis in range(3):
            lows[axis] = min(lows[axis], lo[axis])
            highs[axis] = max(highs[axis], hi[axis])
    return lows, highs


def fit_to_target(meshes: list[MeshData], target: list[MeshData]) -> tuple[float, list[str]]:
    """Met les maillages à l'échelle du modèle d'origine (et les recentre)."""
    if not meshes or not target:
        return 1.0, []
    lo, hi = _bounds(meshes)
    tlo, thi = _bounds(target)
    ratios = []
    for axis in range(3):
        source = hi[axis] - lo[axis]
        wanted = thi[axis] - tlo[axis]
        if source > 1e-6 and wanted > 1e-6:
            ratios.append(wanted / source)
    if not ratios:
        return 1.0, ["dimensions dégénérées : aucune mise à l'échelle appliquée"]
    scale = min(ratios)
    center = [(lo[i] + hi[i]) / 2 for i in range(3)]
    wanted_center = [(tlo[i] + thi[i]) / 2 for i in range(3)]
    for mesh in meshes:
        mesh.positions = [
            tuple((point[i] - center[i]) * scale + wanted_center[i] for i in range(3))
            for point in mesh.positions
        ]
        mesh.normals = None
    note = (f"adapté au modèle d'origine : ×{scale:.4f} puis recentré "
            f"({(thi[0] - tlo[0]):.3f} × {(thi[1] - tlo[1]):.3f} × {(thi[2] - tlo[2]):.3f})")
    return scale, [note]


def translate(project: Project, ref: str, target_rel: str, *, model: int = 0,
              shape: int = 0, axis: str = "y_up", scale: float = 1.0,
              only: str | None = None, fit: bool = True) -> dict:
    """Écrit la géométrie du ``.blend`` dans le BFRES cible et rend compte."""
    result = _load(project, ref, axis=axis, scale=scale, only=only)
    meshes = [mesh for mesh in result.meshes if mesh.faces]
    if not meshes:
        raise ProjectError("Le .blend ne contient aucun triangle à injecter.")

    original = project.read_asset(target_rel)
    if original[:4] != b"FRES":
        raise ProjectError(f"{target_rel} n'est pas un BFRES : choisis un modèle du jeu.")
    blocker = why_not_injectable(original)
    if blocker:
        raise ProjectError(f"{target_rel} : {blocker}")
    notes: list[str] = list(result.warnings)
    if fit:
        try:
            _scale, fitted = fit_to_target(meshes, project.model_meshes(target_rel))
            notes.extend(fitted)
        except HookyError as exc:
            notes.append(f"adaptation impossible : {exc}")

    try:
        updated, report = bfres_import.import_obj(original, meshes, model=model, shape=shape)
    except HookyError as exc:
        raise ProjectError(f"Injection impossible : {exc}") from exc

    if len(updated) != len(original):
        raise ProjectError("Le BFRES produit n'a pas la même taille que l'original : "
                           "écriture annulée pour ne pas corrompre le fichier.")

    payload = updated
    if target_rel.lower().endswith(".zs"):
        original_id, _unused = zs.frame_info(project.asset(target_rel).read_bytes())
        dicts = project._load_dicts()          # noqa: SLF001 (même paquet)
        try:
            payload = dicts.compress(updated, dict_id=original_id)
        except zs.DictMissing as exc:
            raise ProjectError(str(exc)) from exc
        if len(payload) != len(project.asset(target_rel).read_bytes()):
            notes.append("la taille compressée change d'un octet ou deux : si le jeu "
                         "refuse le fichier, mets à jour la table de tailles (restbl).")

    written = project.work / "assets" / target_rel
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_bytes(payload)

    preview = b""
    try:
        file = bfres_mod.BfresFile.parse(updated)
        preview = render.render_png(bfres_mod.meshes_from_file(file), 480, 360,
                                    cull=project.settings.cull,
                                    supersample=project.settings.supersample,
                                    frustum=project.settings.frustum)
    except Exception:                          # aperçu facultatif, jamais bloquant
        preview = b""

    return {
        "target": target_rel,
        "blend": ref,
        "wrote": project.shown_path(written),
        "faces": report.indices // 3,
        "vertices": report.vertices,
        "source_faces": sum(len(mesh.faces) for mesh in meshes),
        "source_vertices": sum(len(mesh.positions) for mesh in meshes),
        "capacity_vertices": report.capacity_vertices,
        "capacity_indices": report.capacity_indices,
        "notes": notes,
        "report": report.lines(),
        "preview": ("data:image/png;base64," + base64.b64encode(preview).decode("ascii"))
                   if preview else "",
    }
