"""Lecture bornée des tables FMDL/FSHP/FVTX et FMAT 10.x observées.

Contrairement au balayage de secours, cette voie suit les pointeurs déclarés :
une forme correspond à son propre tampon d'indices LOD 0 et à son matériau.
Une table présente mais invalide est refusée intégralement, jamais devinée.
Les formes rigides reconnues peuvent être placées en pose de référence ; les
animations, poids et shaders ne sont pas reconstruits.
"""
from __future__ import annotations

import math
import struct
import sys
from array import array

from .mesh import MeshData

MAX_SHAPES = 1024
MAX_MATERIALS = 1024
MAX_VERTICES = 2_000_000
MAX_INDICES = 12_000_000


class InvalidStructure(ValueError):
    pass


def _region(data: bytes, offset: int, size: int) -> None:
    if offset <= 0 or size < 0 or offset + size > len(data):
        raise InvalidStructure("pointeur ou taille hors limites")


def _u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data, offset):
    return struct.unpack_from("<Q", data, offset)[0]


def _name(data: bytes, offset: int) -> str:
    _region(data, offset, 2)
    length = _u16(data, offset)
    if not 0 < length <= 1024:
        raise InvalidStructure("nom absent ou trop long")
    _region(data, offset + 2, length)
    try:
        name = data[offset + 2:offset + 2 + length].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidStructure("nom invalide") from exc
    if not name.isprintable() or "\x00" in name:
        raise InvalidStructure("nom invalide")
    return name


def _materials(data: bytes, model: int, count: int) -> tuple[list[str], dict[str, list[str]]]:
    if not count:
        return [], {}
    start = _u64(data, model + 0x38)
    _region(data, start, count * 0xB0)
    names = []
    references = {}
    for index in range(count):
        offset = start + index * 0xB0
        if data[offset:offset + 4] != b"FMAT":
            raise InvalidStructure("table des matériaux invalide")
        name = _name(data, _u64(data, offset + 8))
        if name in references:
            raise InvalidStructure("noms de matériaux non uniques")
        texture_count = data[offset + 0xA3]
        table = _u64(data, offset + 0x20)
        textures = []
        if texture_count:
            _region(data, table, texture_count * 8)
            textures = [_name(data, _u64(data, table + i * 8)) for i in range(texture_count)]
        names.append(name)
        references[name] = textures
    return names, references


def _attributes(data: bytes, vertex: int, stride: int):
    count = data[vertex + 0x4C]
    if not 1 <= count <= 32 or data[vertex + 0x4D] != 1:
        raise InvalidStructure("tampons de sommets multiples ou attributs invalides")
    start = _u64(data, vertex + 8)
    _region(data, start, count * 16)
    attributes = {}
    for index in range(count):
        offset = start + index * 16
        name = _name(data, _u64(data, offset))
        fmt, slot = _u32(data, offset + 8), _u32(data, offset + 12)
        if name in attributes or slot >= stride:
            raise InvalidStructure("attribut de sommet invalide")
        attributes[name] = (fmt, slot)
    return attributes


def _decode(data, start, stride, count, offset, fmt, width):
    unpack = struct.Struct(fmt)
    if offset + unpack.size > stride:
        raise InvalidStructure("attribut débordant du sommet")
    values = [unpack.unpack_from(data, start + offset + i * stride)[:width] for i in range(count)]
    if not all(math.isfinite(v) for point in values for v in point):
        raise InvalidStructure("attribut non fini")
    return values


def _normals(data, start, stride, count, fmt, offset):
    if fmt != 0x0E02 or offset + 4 > stride:
        return None
    output = []
    for index in range(count):
        word = _u32(data, start + index * stride + offset)
        components = []
        for shift in (0, 10, 20):
            value = (word >> shift) & 1023
            if value >= 512:
                value -= 1024
            components.append(max(-1.0, value / 511.0))
        length = math.sqrt(sum(value * value for value in components))
        if length < 1e-8:
            return None
        output.append(tuple(value / length for value in components))
    return output


def _uv(data, start, stride, count, fmt, offset):
    if offset + 4 > stride:
        raise InvalidStructure("UV débordant du sommet")
    if fmt == 0x1201:  # 2 × unorm16
        return [tuple(v / 65535.0 for v in point)
                for point in _decode(data, start, stride, count, offset, "<2H", 2)]
    if fmt == 0x1202:  # 2 × snorm16, les UV peuvent sortir de [0,1]
        return [tuple(max(-1.0, v / 32767.0) for v in point)
                for point in _decode(data, start, stride, count, offset, "<2h", 2)]
    if fmt == 0x1205:
        return _decode(data, start, stride, count, offset, "<2e", 2)
    return None


def _place_rigid(data, start, stride, positions, normals, attributes, skeleton, skin_count, bone_index):
    """Replace toute une forme, ou échoue sans modifier partiellement ses données."""
    from .bfres_v10_skeleton import SkeletonError, transform

    if skin_count == 0:
        if "_i0" in attributes or "_w0" in attributes:
            raise SkeletonError("attribut de skin inattendu pour une forme sans influence")
        matrices = [skeleton.bone_matrix(bone_index)] * len(positions)
    elif skin_count == 1:
        if "_w0" in attributes:
            raise SkeletonError("poids explicites pour une influence non pris en charge")
        attribute = attributes.get("_i0")
        # Formats observés : 1, 2 ou 4 indices uint8. Une seule influence est
        # déclarée ; les autres composantes ne participent pas à la pose.
        sizes = {0x0203: 1, 0x0903: 2, 0x0B03: 4}
        if not attribute or attribute[0] not in sizes or attribute[1] + sizes[attribute[0]] > stride:
            raise SkeletonError("indices rigides de format inconnu")
        offset = attribute[1]
        matrices = [skeleton.rigid_matrix(data[start + i * stride + offset])
                    for i in range(len(positions))]
    else:
        raise SkeletonError("forme non rigide")
    placed = [transform(matrix, position) for matrix, position in zip(matrices, positions)]
    rotated = ([transform(matrix, normal, normal=True) for matrix, normal in zip(matrices, normals)]
               if normals is not None else None)
    return placed, rotated


def read(data: bytes, model: int, base: int, report):
    """Retourne les maillages et remplit le rapport, ou lève InvalidStructure."""
    _region(data, model, 0x78)
    vertex_count = _u16(data, model + 0x68)
    shape_count = _u16(data, model + 0x6A)
    material_count = _u16(data, model + 0x6C)
    if not (1 <= vertex_count <= MAX_SHAPES and 1 <= shape_count <= MAX_SHAPES
            and material_count <= MAX_MATERIALS):
        raise InvalidStructure("comptes de modèle invalides")
    vertex_table = _u64(data, model + 0x20)
    shape_table = _u64(data, model + 0x28)
    _region(data, vertex_table, vertex_count * 0x58)
    _region(data, shape_table, shape_count * 0x60)
    names, references = _materials(data, model, material_count)
    meshes = []
    total_vertices = total_indices = total_submeshes = 0
    missing_normals = missing_uv = missing_submeshes = morphing = False
    skeleton_offset = _u64(data, model + 0x18)
    skinning = bool(skeleton_offset)
    skeleton = None
    skeleton_error = ""
    rigid_placed = 0
    rigid_unplaced: list[str] = []
    if skeleton_offset:
        from . import bfres_v10_skeleton
        try:
            skeleton = bfres_v10_skeleton.read(data, skeleton_offset)
        except (bfres_v10_skeleton.SkeletonError, struct.error, ValueError, OverflowError) as exc:
            skeleton_error = str(exc)
    for index in range(shape_count):
        shape = shape_table + index * 0x60
        if data[shape:shape + 4] != b"FSHP":
            raise InvalidStructure("table des formes invalide")
        name = _name(data, _u64(data, shape + 8))
        vertex = _u64(data, shape + 0x10)
        if (vertex < vertex_table or (vertex - vertex_table) % 0x58
                or (vertex - vertex_table) // 0x58 >= vertex_count
                or data[vertex:vertex + 4] != b"FVTX"):
            raise InvalidStructure("forme sans tampon de sommets référencé")
        material_index = _u16(data, shape + 0x52)
        if (names and material_index >= len(names)) or (not names and material_index not in (0, 0xFFFF)):
            raise InvalidStructure("matériau hors limites")
        material = names[material_index] if names else ""
        if not data[shape + 0x5B]:
            raise InvalidStructure("forme sans niveau de détail")
        skinning |= bool(data[shape + 0x5A] or _u16(data, shape + 0x58) or _u16(data, shape + 0x54))
        morphing |= bool(data[shape + 0x5C] or data[shape + 0x5D])
        mesh = _u64(data, shape + 0x18)
        _region(data, mesh, 0x38)
        buffer_size = _u64(data, mesh + 0x18)
        _region(data, buffer_size, 4)
        capacity = _u32(data, buffer_size)
        relative, primitive, fmt, count, first = struct.unpack_from("<5I", data, mesh + 0x20)
        if primitive != 3 or fmt not in (0, 1, 2) or count < 3 or count % 3:
            raise InvalidStructure("primitive LOD 0 non prise en charge")
        if count > MAX_INDICES:
            raise InvalidStructure("trop d'indices")
        typecode, size = {0: ("B", 1), 1: ("H", 2), 2: ("I", 4)}[fmt]
        if count * size > capacity:
            raise InvalidStructure("indices plus grands que leur tampon")
        index_start = base + relative
        _region(data, index_start, count * size)
        # Un sous-maillage décrit une plage d'indices, avec un offset en octets.
        # Les espaces ou remplissages ne doivent pas devenir des triangles.
        submesh_table, submesh_count = _u64(data, mesh), _u16(data, mesh + 0x34)
        if submesh_count > 4096:
            raise InvalidStructure("trop de sous-maillages")
        ranges = []
        if submesh_count:
            _region(data, submesh_table, submesh_count * 8)
            for part in range(submesh_count):
                byte_offset, part_count = struct.unpack_from("<II", data, submesh_table + part * 8)
                if byte_offset % size or part_count % 3 or byte_offset + part_count * size > count * size:
                    raise InvalidStructure("plage de sous-maillage invalide")
                if part_count:
                    ranges.append((byte_offset // size, byte_offset // size + part_count))
            ordered = sorted(ranges)
            if not ranges or any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
                raise InvalidStructure("plages de sous-maillages vides ou chevauchantes")
            total_submeshes += len(ranges)
        else:
            ranges = [(0, count)]
            total_submeshes += 1
            missing_submeshes = True
        used = sum(end - start for start, end in ranges)
        total_indices += used
        if total_indices > MAX_INDICES:
            raise InvalidStructure("trop d'indices")
        values = array(typecode)
        values.frombytes(data[index_start:index_start + count * size])
        if sys.byteorder != "little" and size > 1:
            values.byteswap()
        highest = max(max(values[start:end]) for start, end in ranges) + first
        declared_vertices = _u32(data, vertex + 0x50)
        if highest >= declared_vertices:
            raise InvalidStructure("indice hors du tampon de sommets")
        needed = highest + 1
        total_vertices += needed
        if total_vertices > MAX_VERTICES:
            raise InvalidStructure("trop de sommets")
        sizes, strides = _u64(data, vertex + 0x30), _u64(data, vertex + 0x38)
        _region(data, sizes, 4)
        _region(data, strides, 4)
        stride, capacity = _u32(data, strides), _u32(data, sizes)
        start = base + _u32(data, vertex + 0x48)
        if not 4 <= stride <= 1024 or declared_vertices * stride > capacity:
            raise InvalidStructure("taille ou pas des sommets invalide")
        _region(data, start, capacity)
        attributes = _attributes(data, vertex, stride)
        position = attributes.get("_p0")
        if not position or position[0] not in (0x1805, 0x1505):
            raise InvalidStructure("format des positions inconnu")
        positions = _decode(data, start, stride, needed, position[1],
                            "<3f" if position[0] == 0x1805 else "<3e", 3)
        normals = _normals(data, start, stride, needed, *attributes["_n0"]) if "_n0" in attributes else None
        uvs = _uv(data, start, stride, needed, *attributes["_u0"]) if "_u0" in attributes else None
        missing_normals |= normals is None
        missing_uv |= uvs is None
        skin_count = data[shape + 0x5A]
        bone_index = _u16(data, shape + 0x54)
        if skin_count <= 1 and (skeleton_offset or skin_count or bone_index):
            from .bfres_v10_skeleton import SkeletonError
            try:
                if skeleton is None:
                    raise SkeletonError(skeleton_error or "squelette absent")
                if data[vertex + 0x54] != skin_count:
                    raise SkeletonError("nombre d'influences incohérent")
                positions, normals = _place_rigid(data, start, stride, positions, normals,
                                                   attributes, skeleton, skin_count, bone_index)
                rigid_placed += 1
            except (SkeletonError, IndexError, struct.error, ValueError, OverflowError) as exc:
                rigid_unplaced.append(f"{name} ({exc})")
        faces = [(values[i] + first, values[i + 1] + first, values[i + 2] + first)
                 for start_index, end_index in ranges for i in range(start_index, end_index, 3)]
        meshes.append(MeshData(name=name, positions=positions, faces=faces,
                               normals=normals, uvs=uvs, material=material))
        report.shapes.append(f"{name} : {needed} sommets, {len(faces)} triangles (LOD 0)")
    report.material_names = names
    report.material_textures = references
    report.structured = True
    report.buffer_base = base
    report.submeshes = total_submeshes
    report.vertex_count = total_vertices
    report.index_count = total_indices
    report.uv = any(mesh.uvs is not None for mesh in meshes)
    report.warnings.append("Tables de formes et indices LOD 0 lus directement ; autres LOD non exportés.")
    if rigid_placed:
        report.warnings.append(
            f"{rigid_placed} forme(s) rigide(s) replacée(s) en pose de référence FSKL ; "
            "les triangles sont conservés.")
    if rigid_unplaced:
        report.warnings.append(
            f"{len(rigid_unplaced)} forme(s) rigide(s) non replacée(s), géométrie potentiellement "
            f"déplacée : {', '.join(rigid_unplaced[:4])}." +
            (" Autres formes omises du détail." if len(rigid_unplaced) > 4 else ""))
    elif skeleton_error:
        report.warnings.append(f"Squelette FSKL non interprété : {skeleton_error}.")
    if skinning:
        report.warnings.append("Squelette et animation non exportés : géométrie en pose statique non garantie.")
    if morphing:
        report.warnings.append("Formes de déformation non exportées : seule la géométrie de base est lue.")
    if missing_submeshes:
        report.warnings.append("Table de sous-maillages absente : tout le tampon d'indices déclaré est utilisé.")
    if missing_uv:
        report.warnings.append("UV absentes ou de format non reconnu sur certaines formes.")
    if missing_normals:
        report.warnings.append("Normales absentes ou de format non reconnu sur certaines formes.")
    report.warnings.append("Shaders, tangentes et autres attributs propres au format non reconstruits.")
    return meshes, report
