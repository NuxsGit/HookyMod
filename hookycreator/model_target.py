"""Validation et injection prudente de géométrie dans une copie BFRES existante."""
from __future__ import annotations

import base64

from hookymod import bfres as bfres_mod
from hookymod import bfres_safe, render, zs
from hookymod.mesh import MeshData
from hookymod.util import HookyError

from .project import Project, ProjectError

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
        raise ProjectError("Adaptation impossible : géométrie source ou cible absente.")
    lo, hi = _bounds(meshes)
    tlo, thi = _bounds(target)
    ratios = []
    for axis in range(3):
        source = hi[axis] - lo[axis]
        wanted = thi[axis] - tlo[axis]
        if source > 1e-6 and wanted > 1e-6:
            ratios.append(wanted / source)
    if not ratios:
        raise ProjectError("Adaptation impossible : dimensions dégénérées.")
    scale = min(ratios)
    center = [(lo[i] + hi[i]) / 2 for i in range(3)]
    wanted_center = [(tlo[i] + thi[i]) / 2 for i in range(3)]
    for mesh in meshes:
        mesh.positions = [
            tuple((point[i] - center[i]) * scale + wanted_center[i] for i in range(3))
            for point in mesh.positions
        ]
        # Un changement d'échelle uniforme positif et une translation ne
        # changent pas les normales ; conserver celles du fichier importé.
    note = (f"adapté au modèle d'origine : ×{scale:.4f} puis recentré "
            f"({(thi[0] - tlo[0]):.3f} × {(thi[1] - tlo[1]):.3f} × {(thi[2] - tlo[2]):.3f})")
    return scale, [note]


def inject_model(project: Project, result, target_rel: str, source_name: str, *, fit: bool = False) -> dict:
    """Injection OBJ/glTF stricte : une seule forme dont les attributs sont réécrivables."""
    with project._edit_flights.hold(project.asset(target_rel)):  # noqa: SLF001
        original = project.read_asset(target_rel)
        return _inject_result(project, result, source_name, target_rel, fit=fit, original=original)


def why_not_model_target(data: bytes) -> str:
    """Compatibilité de la cible seule ; la source peut encore dépasser sa capacité."""
    try:
        bfres_safe.check_target(data, None)
    except HookyError as exc:
        return str(exc)
    return ""




def _inject_result(project: Project, result, ref: str, target_rel: str, *,
                   fit: bool, original: bytes) -> dict:
    meshes = [mesh for mesh in result.meshes if mesh.faces]
    if not meshes:
        raise ProjectError("Le fichier ne contient aucun triangle à injecter.")
    if result.material_slots > 1:
        raise HookyError("Plusieurs matériaux source ne peuvent pas être conservés dans une seule forme cible.")
    if result.unsupported_attributes:
        raise HookyError("Attributs source non conservables : injection annulée. "
                         "Supprime les tangentes, couleurs ou UV supplémentaires avant l'import.")

    if original[:4] != b"FRES":
        raise ProjectError(f"{target_rel} n'est pas un BFRES : choisis un modèle du jeu.")
    notes: list[str] = list(result.warnings)
    if fit:
        try:
            bfres_safe.check_target(original, None)
            _scale, fitted = fit_to_target(meshes, project.model_meshes(target_rel))
            notes.extend(fitted)
        except HookyError as exc:
            raise ProjectError(f"Adaptation impossible : {exc}. Injection annulée.") from exc
    try:
        updated, report = bfres_safe.rewrite(original, meshes)
        verified = bfres_mod.BfresFile.parse(updated)
    except (HookyError, ValueError, OverflowError) as exc:
        raise ProjectError(f"Injection impossible : {exc}") from exc

    payload = updated
    if target_rel.lower().endswith(".zs"):
        original_id, _unused = zs.frame_info(project.asset(target_rel).read_bytes())
        dicts = project._load_dicts()          # noqa: SLF001 (même paquet)
        try:
            payload = dicts.compress(updated, dict_id=original_id)
        except zs.DictMissing as exc:
            raise ProjectError(str(exc)) from exc
        if len(payload) != len(project._current_asset(target_rel)[0].read_bytes()):  # noqa: SLF001
            notes.append("La taille compressée a changé : la table de tailles (RESTBL) "
                         "peut nécessiter une mise à jour ; teste le mod.")

    from hookymod.util import write_atomic

    written = project.edited_asset_path(target_rel)
    write_atomic(written, payload)
    project.bump()

    preview = b""
    try:
        preview = render.render_png(bfres_mod.meshes_from_file(verified), 480, 360,
                                    cull=project.settings.cull,
                                    supersample=project.settings.supersample,
                                    frustum=project.settings.frustum)
    except Exception:                          # aperçu facultatif, jamais bloquant
        preview = b""

    return {
        "target": target_rel,
        "source": ref,
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
