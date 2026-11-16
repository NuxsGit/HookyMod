"""Experimental, bounded skin transport for the full WebGL model preview.

The existing H3D1 positions are already in the static model pose for rigid
shapes and in model space for smooth shapes.  Skin matrices therefore use
animated_world @ inverse_rest_world for *both*. All eight influences of the
observed BFRES 10.4 layouts are retained; no triangle or influence is removed.
Only validated unit-scale Euler rest skeletons are accepted. No file is changed.
"""
from __future__ import annotations

import json
import math
import struct
from array import array

from hookymod import bfres_v10_skeleton as skel_mod
from hookymod import bfres_v10_struct as struct_mod
from hookymod.util import HookyError

MAX_INPUT = 128 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
MAX_VERTICES = 2_000_000
MAGIC = b"H3S1"


class SkinError(HookyError):
    """Unsupported or invalid skinned model."""


def _inverse_unit(matrix):
    # The validated rest skeleton contains rigid, unit-scale matrices only.
    r = (matrix[0], matrix[4], matrix[8],
         matrix[1], matrix[5], matrix[9],
         matrix[2], matrix[6], matrix[10])
    t = (matrix[3], matrix[7], matrix[11])
    return [r[0], r[1], r[2], -sum(r[i] * t[i] for i in range(3)),
            r[3], r[4], r[5], -sum(r[3+i] * t[i] for i in range(3)),
            r[6], r[7], r[8], -sum(r[6+i] * t[i] for i in range(3))]


def _attr(attrs, key, kind, stride):
    attr = attrs.get(key)
    if attr is None:
        return None
    fmt, offset = attr
    count = {0x02: 1, 0x09: 2, 0x0b: 4}.get(fmt >> 8)
    if fmt & 0xff != kind or count is None or offset + count > stride:
        raise SkinError(f"Format de {key} non pris en charge.")
    return offset, count


def encode(data: bytes, meshes, *, structured: bool) -> bytes:
    """Pack a complete skin corresponding exactly to the already decoded meshes."""
    try:
        return _encode(data, meshes, structured=structured)
    except (struct.error, IndexError, OverflowError, struct_mod.InvalidStructure,
            skel_mod.SkeletonError) as exc:
        raise SkinError(f"Squelette ou attributs de peau invalides : {exc}") from exc


def _encode(data: bytes, meshes, *, structured: bool) -> bytes:
    if (not structured or len(data) > MAX_INPUT or len(data) < 0xc0
            or data[:4] != b"FRES" or data[0x0a] != 10 or data[0x0c:0x0e] != b"\xff\xfe"):
        raise SkinError("Le skin exige un modèle BFRES 10.x structuré et borné.")
    u64 = struct_mod._u64
    region = struct_mod._region
    model, info = u64(data, 0x28), u64(data, 0xb0)
    region(data, model, 0x78)
    region(data, info, 0x10)
    base = u64(data, info + 8)
    if data[model:model + 4] != b"FMDL":
        raise SkinError("Modèle FMDL absent.")
    skeleton_ptr = u64(data, model + 0x18)
    skeleton = skel_mod.read(data, skeleton_ptr)
    if struct_mod._u32(data, skeleton_ptr + 4) & 0x7000 != 0x1000:
        raise SkinError("Seul le squelette en rotations Euler est pris en charge.")
    bone_ptr = u64(data, skeleton_ptr + 0x10)
    bones = []
    names = set()
    for index, matrix in enumerate(skeleton.matrices):
        p = bone_ptr + index * 0x58
        name = struct_mod._name(data, u64(data, p))
        if name in names:
            raise SkinError("Noms d'os dupliqués.")
        names.add(name)
        parent, = struct.unpack_from("<h", data, p + 0x22)
        flags, = struct.unpack_from("<I", data, p + 0x2c)
        scale = struct.unpack_from("<3f", data, p + 0x30)
        rotation = struct.unpack_from("<4f", data, p + 0x3c)
        translation = struct.unpack_from("<3f", data, p + 0x4c)
        bones.append({"name": name, "parent": parent, "scale": scale,
                      "rotation": rotation, "translation": translation,
                      "ssc": bool(flags & 0x00800000), "inverse": _inverse_unit(matrix)})
    shapes = u64(data, model + 0x28)
    count, = struct.unpack_from("<H", data, model + 0x6a)
    if not count or count != len(meshes):
        raise SkinError("Les formes ne correspondent pas à la géométrie affichée.")
    region(data, shapes, count * 0x60)
    vertex_count = sum(len(m.positions) for m in meshes)
    if vertex_count > MAX_VERTICES or vertex_count * 24 > MAX_OUTPUT:
        raise SkinError("Skin trop volumineux ; aucune face n'a été supprimée.")
    blocks = []
    entries = []
    for index, mesh in enumerate(meshes):
        shape = shapes + index * 0x60
        if data[shape:shape + 4] != b"FSHP" or struct_mod._name(data, u64(data, shape + 8)) != mesh.name:
            raise SkinError("Ordre ou nom des formes incohérent.")
        vertex = u64(data, shape + 0x10)
        region(data, vertex, 0x58)
        if data[vertex:vertex + 4] != b"FVTX":
            raise SkinError("Tampon de sommets absent.")
        stride_ptr, size_ptr = u64(data, vertex + 0x38), u64(data, vertex + 0x30)
        region(data, stride_ptr, 4); region(data, size_ptr, 4)
        stride, capacity = struct_mod._u32(data, stride_ptr), struct_mod._u32(data, size_ptr)
        start = base + struct_mod._u32(data, vertex + 0x48)
        count_vertices = len(mesh.positions)
        if not 4 <= stride <= 1024 or count_vertices * stride > capacity:
            raise SkinError("Tampon de skin invalide.")
        region(data, start, capacity)
        attrs = struct_mod._attributes(data, vertex, stride)
        skin_count = data[shape + 0x5a]
        if data[vertex + 0x54] != skin_count or skin_count > 8:
            raise SkinError("Nombre d'influences non pris en charge.")
        bone_index = struct_mod._u16(data, shape + 0x54)
        i0 = _attr(attrs, "_i0", 3, stride)
        i1 = _attr(attrs, "_i1", 3, stride)
        w0 = _attr(attrs, "_w0", 1, stride)
        w1 = _attr(attrs, "_w1", 1, stride)
        if skin_count == 0:
            if bone_index >= len(bones) or i0 or w0:
                raise SkinError("Liaison rigide invalide.")
        elif skin_count == 1:
            if not i0 or w0:
                raise SkinError("Liaison rigide à un indice invalide.")
        elif (not i0 or not w0 or (i0[1] + (i1[1] if i1 else 0)) < skin_count
              or (w0[1] + (w1[1] if w1 else 0)) < skin_count):
            raise SkinError("Indices ou poids de skin incomplets.")
        position = attrs.get("_p0")
        if not position or position[0] not in (0x1805, 0x1505):
            raise SkinError("Positions de skin non prises en charge.")
        pos_fmt = "<3f" if position[0] == 0x1805 else "<3e"
        if position[1] + struct.calcsize(pos_fmt) > stride:
            raise SkinError("Position hors du tampon.")
        indices = array("H")
        weights = bytearray()
        for j, existing in enumerate(mesh.positions):
            p = start + j * stride
            original = struct.unpack_from(pos_fmt, data, p + position[1])
            if not all(math.isfinite(x) for x in original):
                raise SkinError("Position de skin non finie.")
            if skin_count == 0:
                joints, weight = [bone_index], [255]
            else:
                indices_raw = list(data[p+i0[0]:p+i0[0]+i0[1]])
                if i1:
                    indices_raw.extend(data[p+i1[0]:p+i1[0]+i1[1]])
                if skin_count == 1:
                    idx = indices_raw[0]
                    if not skeleton.smooth_count <= idx < len(skeleton.matrix_to_bone):
                        raise SkinError("Indice de matrice rigide invalide.")
                    joints, weight = [skeleton.matrix_to_bone[idx]], [255]
                else:
                    weight = list(data[p+w0[0]:p+w0[0]+w0[1]])
                    if w1:
                        weight.extend(data[p+w1[0]:p+w1[0]+w1[1]])
                    indices_raw, weight = indices_raw[:skin_count], weight[:skin_count]
                    if not sum(weight):
                        raise SkinError("Sommet sans poids de skin.")
                    joints = []
                    for raw, value in zip(indices_raw, weight):
                        if value and raw >= skeleton.smooth_count:
                            raise SkinError("Indice de matrice souple hors limites.")
                        joints.append(skeleton.matrix_to_bone[raw] if value else 0)
            # H3D1 rigid positions were already transformed to the rest pose.
            expected = (skel_mod.transform(skeleton.matrices[joints[0]], original)
                        if skin_count <= 1 else original)
            if any(abs(a - b) > 1e-5 * max(1., abs(a), abs(b)) for a, b in zip(expected, existing)):
                raise SkinError("La pose du skin ne correspond pas à la géométrie chargée.")
            indices.extend(joints + [0] * (8 - len(joints)))
            weights.extend(weight + [0] * (8 - len(weight)))
        import sys
        if sys.byteorder != "little":
            indices.byteswap()
        blocks.extend((indices.tobytes(), bytes(weights)))
        entries.append({"name": mesh.name, "vertices": count_vertices})
    header = json.dumps({"version": 1, "bones": bones, "meshes": entries},
                        ensure_ascii=True, separators=(",", ":")).encode("ascii")
    total = 8 + len(header) + 3 + sum(len(x) for x in blocks)
    if len(header) > 4 * 1024 * 1024 or total > MAX_OUTPUT:
        raise SkinError("Skin trop volumineux.")
    output = bytearray(MAGIC + struct.pack("<I", len(header)) + header)
    output.extend(b"\0" * (-len(output) % 4))
    for block in blocks:
        output.extend(block)
    return bytes(output)
