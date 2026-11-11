"""Import de géométrie dans un BFRES existant (Blender / OBJ → BFRES).

Un BFRES contient bien plus qu'un maillage : matériaux, os, animations, tables
de tailles, offsets absolus partout. Réécrire un BFRES complet depuis zéro
demanderait de reconstruire tout l'arbre (c'est le travail de BfresLibrary /
Switch-Toolbox). La méthode robuste pour un mod — celle qu'utilisent les
moddeurs — est donc :

1. exporter le modèle du jeu (OBJ), l'éditer dans Blender ;
2. réimporter **la géométrie** dans le BFRES d'origine, en gardant sa
   disposition de sommets (attributs, formats, strides) et ses tampons.

C'est ce que fait :func:`import_obj`, avec la contrainte assumée : la nouvelle
géométrie doit tenir dans les tampons existants (moins de sommets et moins
d'indices que l'original). Les sommets en trop sont comblés en dupliquant le
dernier, les indices en trop sont laissés à zéro.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import bfres as bfres_mod
from .mesh import MeshData
from .util import FormatError, HookyError

# attributs que l'on sait réécrire
SEMANTICS = {
    "position": "positions",
    "normal": "normals",
    "texcoord0": "uvs",
    "texcoord01": "uvs",
}

SKIPPED_HINTS = ("tangent", "binormal", "color", "skin", "blend", "fog", "user")


@dataclass
class ImportReport:
    shape: str = ""
    vertices: int = 0
    capacity_vertices: int = 0
    indices: int = 0
    capacity_indices: int = 0
    attributes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"Shape « {self.shape} » : {self.vertices}/{self.capacity_vertices} "
               f"sommets, {self.indices}/{self.capacity_indices} indices"]
        if self.attributes:
            out.append("Attributs réécrits : " + ", ".join(self.attributes))
        if self.skipped:
            out.append("Attributs laissés intacts : " + ", ".join(self.skipped))
        out += [f"⚠ {w}" for w in self.warnings]
        return out


# --------------------------------------------------------------------------- encodage
def _encode_value(value: float, ncomp: int, bits: int, kind: str) -> bytes | None:
    """Encode une composante scalaire selon le format d'attribut BFRES."""
    if bits == "4_4" or bits == "10_10_10_2" or bits == "10_11_11" or kind in (
            "uint2f", "sint2f"):
        return None
    width = bits // 8
    if kind == "half":
        if width != 2:
            return None
        return struct.pack("<e", float(value))
    if kind == "float":
        if width != 4:
            return None
        return struct.pack("<f", float(value))
    if kind in ("unorm", "snorm"):
        limit = (1 << bits) - 1
        top = (1 << (bits - 1)) - 1
        if kind == "unorm":
            scaled = int(round(max(0.0, min(1.0, float(value))) * limit))
        else:
            scaled = int(round(max(-1.0, min(1.0, float(value))) * top))
            if scaled < 0:
                scaled += 1 << bits
        return int(scaled).to_bytes(width, "little", signed=False)
    if kind in ("uint", "sint"):
        return int(round(float(value))).to_bytes(width, "little", signed=(kind == "sint"))
    return None


def encode_attribute(values: list[tuple[float, ...]], ncomp: int, bits: int,
                     kind: str) -> bytes | None:
    """Encode une liste de valeurs pour un format d'attribut donné (ou ``None``)."""
    out = bytearray()
    for value in values:
        components = list(value) + [0.0] * (ncomp - len(value))
        for index in range(ncomp):
            encoded = _encode_value(components[index], ncomp, bits, kind)
            if encoded is None:
                return None
            out += encoded
    return bytes(out)


# --------------------------------------------------------------------------- import
def _flatten(meshes: list[MeshData]) -> tuple[list, list, list]:
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    for mesh in meshes:
        if not mesh.faces:
            continue
        mesh.ensure_normals()
        base = len(positions)
        positions.extend(mesh.positions)
        normals.extend(mesh.normals or [(0.0, 0.0, 1.0)] * len(mesh.positions))
        if mesh.uvs:
            uvs.extend(mesh.uvs)
        else:
            uvs.extend([(0.0, 0.0)] * len(mesh.positions))
        # les indices des faces sont locaux au maillage
        for face in mesh.faces:
            if len(face) != 3:
                raise HookyError("Seuls les triangles sont acceptés "
                                 f"(face à {len(face)} sommets).")
        mesh.name = mesh.name or "mesh"
        del base
    return positions, normals, uvs


def _triangles(meshes: list[MeshData]) -> list[tuple[int, int, int]]:
    faces: list[tuple[int, int, int]] = []
    offset = 0
    for mesh in meshes:
        for face in mesh.faces:
            if len(face) != 3:
                raise HookyError("Seuls les triangles sont acceptés "
                                 f"(face à {len(face)} sommets).")
            faces.append((face[0] + offset, face[1] + offset, face[2] + offset))
        offset += len(mesh.positions)
    return faces


def import_obj(data: bytes, meshes: list[MeshData], model: int = 0, shape: int = 0,
               mesh_index: int = 0, lod: int = 0) -> tuple[bytes, ImportReport]:
    """Réécrit la géométrie d'un BFRES à partir de maillages (OBJ, Blender)."""
    if not meshes or not any(mesh.faces for mesh in meshes):
        raise HookyError("Aucun triangle à importer.")
    file = bfres_mod.BfresFile.parse(data)
    if not file.models:
        raise FormatError("BFRES sans modèle.")
    if model >= len(file.models):
        raise HookyError(f"Modèle #{model} absent ({len(file.models)} modèle(s)).")
    chosen = file.models[model]
    if not chosen.shapes:
        raise FormatError("Modèle sans shape.")
    if shape >= len(chosen.shapes):
        raise HookyError(f"Shape #{shape} absente ({len(chosen.shapes)} shape(s)).")
    target = chosen.shapes[shape]
    if target.vtx_buffer is None:
        raise FormatError(f"Shape « {target.name} » sans tampon de sommets.")
    mesh = target.meshes[lod] if lod < len(target.meshes) else (
        target.meshes[0] if target.meshes else None)
    if mesh is None:
        raise FormatError(f"Shape « {target.name} » sans maillage.")
    if mesh.primitive not in (0x03,):
        raise HookyError(
            f"Primitive {mesh.primitive} (strip/fan) non gérée : réexporte le "
            "modèle en triangles (Blender : triangulate) puis réimporte.")
    if not mesh.index_data_offset:
        raise FormatError("Tampon d'index introuvable dans le BFRES.")

    positions, normals, uvs = _flatten(meshes)
    faces = _triangles(meshes)
    vtx = target.vtx_buffer

    capacity_vertices = vtx.vertex_count or (len(vtx.buffers[0]) // max(1, vtx.strides[0])
                                             if vtx.buffers and vtx.strides else 0)
    if capacity_vertices and len(positions) > capacity_vertices:
        raise HookyError(
            f"Le modèle importé a {len(positions)} sommets, le BFRES d'origine en "
            f"réserve {capacity_vertices} : impossible d'agrandir les tampons sans "
            "reconstruire le fichier entier.\n"
            "Réduis le maillage (décimation), ou réimporte dans un BFRES dont le "
            "maillage d'origine est au moins aussi dense.")
    index_capacity = len(mesh.indices_raw) // {0: 1, 1: 2, 2: 4}.get(mesh.index_format, 2)
    needed = len(faces) * 3
    if needed > index_capacity:
        raise HookyError(
            f"{needed} indices nécessaires, {index_capacity} disponibles dans le "
            "BFRES d'origine : réduis le nombre de triangles.")

    report = ImportReport(shape=target.name, vertices=len(positions),
                          capacity_vertices=capacity_vertices, indices=needed,
                          capacity_indices=index_capacity)

    out = bytearray(data)
    sources = {"positions": positions, "normals": normals, "uvs": uvs}

    # --- tampons de sommets
    for buffer_index, offset in enumerate(vtx.buffer_offsets):
        if not offset or buffer_index >= len(vtx.buffers):
            continue
        stride = vtx.strides[buffer_index] if buffer_index < len(vtx.strides) else 0
        if stride <= 0:
            continue
        data_buffer = bytearray(vtx.buffers[buffer_index])
        filled = False
        for attribute in vtx.attributes:
            if attribute.buffer_index != buffer_index:
                continue
            key = SEMANTICS.get(attribute.name.lower())
            spec = bfres_mod.ATTRIB_FORMATS.get(attribute.format_code)
            if key is None or spec is None:
                continue
            ncomp, bits, kind = spec
            values = sources.get(key)
            if not values:
                continue
            encoded = encode_attribute(values, ncomp, bits, kind)
            if encoded is None:
                report.skipped.append(f"{attribute.name} (format 0x{attribute.format_code:04X})")
                continue
            size = bfres_mod.attrib_size(attribute.format_code)
            last = encoded
            for vertex in range(len(data_buffer) // stride):
                at = vertex * stride + attribute.offset
                if at + size > len(data_buffer):
                    break
                chunk = (encoded[vertex * size:(vertex + 1) * size]
                         if vertex < len(values) else
                         (last[-size:] if size <= len(last) else last))
                data_buffer[at:at + size] = chunk
            report.attributes.append(attribute.name)
            filled = True
        if filled:
            out[offset:offset + len(data_buffer)] = data_buffer

    # --- indices
    width = {0: ("B", 1), 1: ("H", 2), 2: ("I", 4)}[mesh.index_format]
    fmt, size = width
    flat = [index for face in faces for index in face]
    if fmt == "H" and max(flat) > 0xFFFF:
        raise HookyError("Trop de sommets pour des indices 16 bits.")
    packed = struct.pack(f"<{len(flat)}{fmt}", *flat)
    leftover = len(mesh.indices_raw) - len(packed)
    if leftover > 0:
        packed += bytes(leftover)
    out[mesh.index_data_offset:mesh.index_data_offset + len(packed)] = packed

    if mesh.index_count_offset:
        struct.pack_into("<I", out, mesh.index_count_offset, len(flat))
    if mesh.first_vertex_offset:
        struct.pack_into("<I", out, mesh.first_vertex_offset, 0)
    if mesh.submeshes and mesh.submesh_array_offset:
        first = mesh.submesh_array_offset
        # un seul sous-maillage pour toute la surface ; les autres sont vides
        struct.pack_into("<II", out, first, 0, len(flat))
        for index in range(1, len(mesh.submeshes)):
            struct.pack_into("<II", out, first + 8 * index, len(flat), 0)
        if len(mesh.submeshes) > 1:
            report.warnings.append(
                f"{len(mesh.submeshes)} sous-maillages ramenés à un seul "
                "(les autres sont vides) : les matériaux secondaires ne dessineront "
                "plus rien.")
    if len(positions) < capacity_vertices:
        report.warnings.append(
            f"{capacity_vertices - len(positions)} sommet(s) inutilisé(s) comblé(s) "
            "par duplication du dernier.")
    report.warnings.append(
        "Les boîtes englobantes du BFRES ne sont pas recalculées : si le modèle "
        "change de volume, le culling du jeu peut le faire disparaître par moments.")
    if len(file.warnings):
        report.warnings.extend(f"lecture : {w}" for w in file.warnings[:4])
    return bytes(out), report


def import_obj_bytes(data: bytes, obj_bytes: bytes, **kwargs) -> tuple[bytes, ImportReport]:
    from .mesh import load_obj
    return import_obj(data, [load_obj(obj_bytes)], **kwargs)


def describe_capacity(data: bytes, model: int = 0, shape: int = 0) -> list[str]:
    """Décrit ce que le BFRES peut accueillir (sommets, indices, attributs)."""
    file = bfres_mod.BfresFile.parse(data)
    if not file.models or model >= len(file.models):
        raise HookyError("Modèle introuvable.")
    shapes = file.models[model].shapes
    if not shapes:
        raise HookyError("Aucune shape.")
    lines: list[str] = []
    for index, item in enumerate(shapes if shape is None else [shapes[shape]]):
        vtx = item.vtx_buffer
        if vtx is None:
            continue
        indices = sum(len(mesh.indices_raw) // {0: 1, 1: 2, 2: 4}.get(
            mesh.index_format, 2) for mesh in item.meshes)
        lines.append(f"Shape « {item.name} » : {vtx.vertex_count} sommet(s), "
                     f"{indices} indice(s) de capacité")
        for attribute in vtx.attributes:
            spec = bfres_mod.ATTRIB_FORMATS.get(attribute.format_code)
            name = bfres_mod.attrib_name(attribute.format_code) if hasattr(
                bfres_mod, "attrib_name") else f"0x{attribute.format_code:04X}"
            writable = "réécrit" if attribute.name.lower() in SEMANTICS else "conservé"
            lines.append(f"    {attribute.name:<14} {name:<22} "
                         f"tampon {attribute.buffer_index} @ {attribute.offset:<3} "
                         f"— {writable}")
        del spec
    return lines
