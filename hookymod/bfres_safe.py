"""Réécriture BFRES prudente, partagée par l'atelier et la CLI.

Seules les structures explicitement vérifiées sont acceptées. Ces contrôles ne
prouvent pas la compatibilité en jeu ; aucune donnée d'origine n'est modifiée.
"""
from __future__ import annotations

import math
from dataclasses import replace

from . import bfres as bfres_mod, bfres_import
from .mesh import MeshData, triangle_has_area
from .util import HookyError

def why_not_injectable(data: bytes) -> str:
    """Premier diagnostic, avant les contrôles plus stricts de la cible."""
    try:
        file = bfres_mod.BfresFile.parse(data)
    except HookyError as exc:
        return f"BFRES illisible ({exc})."
    if file.version_major >= 10:
        return ("BFRES 10.x : la réécriture des tampons et sous-maillages n'est pas "
                "prise en charge, même si la lecture est possible.")
    if file.version_major != 9:
        return "Écriture limitée à certaines structures BFRES 9.x ; cette version n'est pas prise en charge."
    if any(model.shapes for model in file.models):
        return ""
    return "Aucune forme exploitable dans ce BFRES."


def check_target(data: bytes, meshes: list[MeshData] | None) -> None:
    """Ne pas présenter une réécriture partielle comme le remplacement d'un modèle entier."""
    blocker = why_not_injectable(data)
    if blocker:
        raise HookyError(blocker)
    try:
        file = bfres_mod.BfresFile.parse(data)
        if file.version_major >= 10:
            raise HookyError("BFRES 10.x : écriture non prise en charge, injection annulée.")
        if file.warnings:
            raise HookyError("Lecture incomplète de la cible : injection annulée (" + file.warnings[0] + ").")
        if len(file.models) != 1 or len(file.models[0].shapes) != 1:
            raise HookyError("L'injection exige un modèle avec une seule forme réécrivable.")
        if file.models[0].skeleton_offset:
            raise HookyError("Squelette de la cible non réécrit : injection annulée.")
        shape = file.models[0].shapes[0]
        if shape.bounds_offset or shape.radius_offset:
            raise HookyError("Boîtes ou rayons englobants de la cible non réécrits : injection annulée.")
        if (shape.skin_bone_count or shape.vertex_skin_count or shape.bone_index
                or shape.key_shape_count or shape.target_attribute_count):
            raise HookyError("Squelette ou morph targets de la cible non réécrits : injection annulée.")
        if len(shape.meshes) != 1:
            raise HookyError("L'injection sûre ne remplace pas encore les modèles à plusieurs niveaux de détail.")
        target_mesh = shape.meshes[0]
        if len(target_mesh.submeshes) > 1:
            raise HookyError("Les modèles à plusieurs sous-maillages ne sont pas encore remplaçables intégralement.")
        if (target_mesh.primitive != 3 or target_mesh.index_format not in (0, 1, 2)
                or not target_mesh.index_data_offset or not target_mesh.index_count_offset
                or not target_mesh.first_vertex_offset or not target_mesh.indices_raw
                or (target_mesh.submeshes and not target_mesh.submesh_array_offset)):
            raise HookyError("Tampon d'indices cible non réécrivable en triangles.")
        if shape.vtx_buffer is None:
            raise HookyError("Le tampon de sommets cible est absent.")
        vtx = shape.vtx_buffer
        if vtx.vertex_skin_count or not vtx.vertex_count:
            raise HookyError("Squelette ou capacité des sommets cible non pris en charge.")
        attributes = vtx.attributes
        semantics = {bfres_import.SEMANTICS.get(a.name.lower()): a for a in attributes
                     if bfres_import.SEMANTICS.get(a.name.lower())}
        if "positions" not in semantics:
            raise HookyError("Les positions du modèle cible ne sont pas réécrivables.")
        unknown = [a.name for a in attributes if a.name.lower() not in bfres_import.SEMANTICS]
        if unknown:
            raise HookyError("Attributs cible non réécrits (squelette, tangentes ou autres) : "
                               + ", ".join(unknown[:8]) + ". Injection annulée.")
        if len(semantics) != len(attributes):
            raise HookyError("Sémantiques cible dupliquées : injection annulée.")
        if meshes is not None:
            if any(mesh.colors is not None for mesh in meshes):
                raise HookyError("La cible ne permet pas de conserver les couleurs de sommets de la source.")
            if any(mesh.uvs is not None for mesh in meshes) and any(mesh.uvs is None for mesh in meshes):
                raise HookyError("Certaines parties du fichier importé n'ont pas d'UV : injection annulée.")
            source_uvs = all(mesh.uvs is not None for mesh in meshes)
            if bool(semantics.get("uvs")) != source_uvs:
                raise HookyError("Les UV de la source et de la cible ne correspondent pas : injection annulée.")
            if any(mesh.normals is not None for mesh in meshes) and "normals" not in semantics:
                raise HookyError("La cible ne permet pas de conserver les normales de la source.")
            names = {mesh.material for mesh in meshes if mesh.material}
            if len(names) > 1:
                raise HookyError("Plusieurs matériaux source ne peuvent pas être conservés dans une seule forme cible.")
        if not 0 < file.buffer_offset < len(data):
            raise HookyError("Base des tampons cible invalide : injection annulée.")
        buffer_ranges = [(target_mesh.index_data_offset,
                          target_mesh.index_data_offset + len(target_mesh.indices_raw))]
        buffer_ranges += [(offset, offset + len(vtx.buffers[i]))
                          for i, offset in enumerate(vtx.buffer_offsets)
                          if offset and i < len(vtx.buffers)]
        ordered = sorted(buffer_ranges)
        if (any(start < file.buffer_offset or end > len(data) or end <= start for start, end in ordered)
                or any(left[1] > right[0] for left, right in zip(ordered, ordered[1:]))
                or target_mesh.index_count_offset + 4 > file.buffer_offset
                or target_mesh.first_vertex_offset + 4 > file.buffer_offset
                or (target_mesh.submeshes and target_mesh.submesh_array_offset + 8 > file.buffer_offset)):
            raise HookyError("Tampons ou métadonnées cible chevauchants : injection annulée.")
        attribute_ranges: dict[int, list[tuple[int, int]]] = {}
        for attribute in attributes:
            semantic = bfres_import.SEMANTICS[attribute.name.lower()]
            spec = bfres_mod.ATTRIB_FORMATS.get(attribute.format_code)
            expected_components = 2 if semantic == "uvs" else 3
            if (not spec or spec[0] != expected_components or not isinstance(spec[1], int)
                    or spec[2] in ("uint2f", "sint2f")):
                raise HookyError(f"Format de l'attribut {attribute.name} non réécrivable.")
            buffer_index = attribute.buffer_index
            if (buffer_index >= len(vtx.buffers) or buffer_index >= len(vtx.strides)
                    or buffer_index >= len(vtx.buffer_offsets) or not vtx.buffer_offsets[buffer_index]):
                raise HookyError(f"Tampon de {attribute.name} non réécrivable.")
            stride = vtx.strides[buffer_index]
            size = bfres_mod.attrib_size(attribute.format_code)
            if (stride <= 0 or attribute.offset + size > stride
                    or len(vtx.buffers[buffer_index]) < stride * vtx.vertex_count):
                raise HookyError(f"Disposition de {attribute.name} incohérente : injection annulée.")
            ranges = attribute_ranges.setdefault(buffer_index, [])
            if any(attribute.offset < end and attribute.offset + size > start for start, end in ranges):
                raise HookyError(f"Attribut {attribute.name} chevauchant : injection annulée.")
            ranges.append((attribute.offset, attribute.offset + size))
            kind = spec[2]
            values = [point for mesh in (meshes or []) for point in (getattr(mesh, semantic) or [])]
            if kind in ("unorm", "snorm"):
                low = 0 if kind == "unorm" else -1
                if any(not low <= value <= 1 for point in values for value in point):
                    raise HookyError(f"Valeurs {attribute.name} hors de la plage de la cible : injection annulée.")
            elif kind in ("uint", "sint") and any(
                    not float(value).is_integer() for point in values for value in point):
                raise HookyError(f"{attribute.name} entier ne peut pas conserver des valeurs fractionnaires.")
    except HookyError as exc:
        raise HookyError(f"Modèle cible illisible : {exc}") from exc


def verify_rewrite(data: bytes, meshes: list[MeshData]) -> bfres_mod.BfresFile:
    """Relit indices et attributs avant toute écriture, avec tolérance de quantification."""
    try:
        file = bfres_mod.BfresFile.parse(data)
        if file.warnings or len(file.models) != 1 or len(file.models[0].shapes) != 1:
            raise HookyError("Le BFRES réécrit ne se relit pas intégralement.")
        shape = file.models[0].shapes[0]
        triangles = bfres_import._triangles(meshes)
        if len(shape.meshes) != 1 or shape.meshes[0].triangles() != triangles:
            raise HookyError("Les triangles relus diffèrent de la source.")
        vertex_buffer = shape.vtx_buffer
        if vertex_buffer is None:
            raise HookyError("Tampon de sommets réécrit absent.")
        for attribute in vertex_buffer.attributes:
            semantic = bfres_import.SEMANTICS[attribute.name.lower()]
            expected = [value for mesh in meshes for value in (getattr(mesh, semantic) or [])]
            decoded = vertex_buffer.read(attribute)
            if len(expected) != sum(len(mesh.positions) for mesh in meshes) or not decoded or len(decoded) < len(expected):
                raise HookyError(f"Attribut {attribute.name} réécrit incomplet.")
            # Les cibles UNORM/SNORM 8 bits et float16 sont quantifiées. Au-delà
            # de cette marge, mieux vaut refuser que livrer une géométrie altérée.
            if any(not math.isclose(want, got, rel_tol=0.002, abs_tol=0.0041)
                   for source, target in zip(expected, decoded)
                   for want, got in zip(source, target)):
                raise HookyError(f"Attribut {attribute.name} réécrit trop différent de la source.")
            if semantic == "positions" and any(
                    not triangle_has_area(*(decoded[index] for index in face)) for face in triangles):
                raise HookyError("Triangle réécrit d'aire nulle après quantification.")
        return file
    except (HookyError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise HookyError(f"Vérification du BFRES réécrit impossible : {exc}") from exc


def rewrite(data: bytes, meshes: list[MeshData]) -> tuple[bytes, bfres_import.ImportReport]:
    """Construit et relit un résultat de même taille ; ne touche pas au disque."""
    # L'encodeur peut calculer des normales et compléter le nom : conserver
    # les attributs de l'objet source intacts pour une réutilisation ultérieure.
    meshes = [replace(mesh) for mesh in meshes if mesh.faces]
    if not meshes:
        raise HookyError("Aucun triangle à importer.")
    check_target(data, meshes)
    # Avant le calcul de normales : ni indices répétés ni faces d'aire nulle.
    bfres_import._triangles(meshes)
    try:
        updated, report = bfres_import.import_obj(data, meshes, model=0, shape=0)
    except (ValueError, OverflowError) as exc:
        raise HookyError(f"Valeur impossible à encoder : {exc}") from exc
    if report.skipped:
        raise HookyError("Des attributs n'ont pas pu être réécrits : injection annulée.")
    if len(updated) != len(data):
        raise HookyError("Le BFRES produit n'a pas la même taille que l'original : écriture annulée.")
    verify_rewrite(updated, meshes)
    return updated, report
