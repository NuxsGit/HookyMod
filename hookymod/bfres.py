"""BFRES (modèles) : lecture des maillages, export OBJ.

Le parseur est volontairement tolérant : un BFRES contient beaucoup de données
optionnelles, et un fichier légèrement exotique ne doit pas empêcher d'afficher
le modèle.  Les erreurs sont donc collectées dans ``warnings`` et chaque
maillage est décodé indépendamment.

Références de format : documentation publique BFRES / BfresLibrary (Syroot,
KillzXGaming).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .mesh import MeshData, export_obj
from .util import FormatError, HookyError, cstring

MAGIC = b"FRES"

# --------------------------------------------------------------------------- attributs
# code -> (nombre de composantes, taille d'une composante, type)
# Les tailles spéciales "10_10_10_2" / "10_11_11" / "4_4" sont gérées à part.
ATTRIB_FORMATS: dict[int, tuple[int, int | str, str]] = {
    0x0102: (1, 8, "unorm"),
    0x0302: (1, 8, "uint"),
    0x0202: (1, 8, "snorm"),
    0x0402: (1, 8, "sint"),
    0x0802: (1, 8, "uint2f"),
    0x0A02: (1, 8, "sint2f"),
    0x0001: (2, "4_4", "unorm"),
    0x010A: (1, 16, "unorm"),
    0x020A: (1, 16, "uint"),
    0x030A: (1, 16, "snorm"),
    0x040A: (1, 16, "sint"),
    0x050A: (1, 16, "half"),
    0x0803: (1, 16, "uint2f"),
    0x0A03: (1, 16, "sint2f"),
    0x0109: (2, 8, "unorm"),
    0x0309: (2, 8, "uint"),
    0x0209: (2, 8, "snorm"),
    0x0409: (2, 8, "sint"),
    0x0804: (2, 8, "uint2f"),
    0x0A04: (2, 8, "sint2f"),
    0x0112: (2, 16, "unorm"),
    0x0212: (2, 16, "snorm"),
    0x0312: (2, 16, "uint"),
    0x0412: (2, 16, "sint"),
    0x0512: (2, 16, "half"),
    0x0807: (2, 16, "uint2f"),
    0x0A07: (2, 16, "sint2f"),
    0x0809: (3, "10_11_11", "float"),
    0x010B: (4, 8, "unorm"),
    0x020B: (4, 8, "snorm"),
    0x030B: (4, 8, "uint"),
    0x040B: (4, 8, "sint"),
    0x080B: (4, 8, "uint2f"),
    0x0A0B: (4, 8, "sint2f"),
    0x000B: (4, "10_10_10_2", "unorm"),
    0x090B: (4, "10_10_10_2", "uint"),
    0x020E: (4, "10_10_10_2", "snorm"),
    0x099B: (4, "10_10_10_2", "sint"),
    0x0115: (4, 16, "unorm"),
    0x0215: (4, 16, "snorm"),
    0x0315: (4, 16, "uint"),
    0x0415: (4, 16, "sint"),
    0x0515: (4, 16, "half"),
    0x080E: (4, 16, "uint2f"),
    0x0A0E: (4, 16, "sint2f"),
    0x0314: (1, 32, "uint"),
    0x0416: (1, 32, "sint"),
    0x0516: (1, 32, "float"),
    0x0317: (2, 32, "uint"),
    0x0417: (2, 32, "sint"),
    0x0517: (2, 32, "float"),
    0x0318: (3, 32, "uint"),
    0x0418: (3, 32, "sint"),
    0x0518: (3, 32, "float"),
    0x0319: (4, 32, "uint"),
    0x0419: (4, 32, "sint"),
    0x0519: (4, 32, "float"),
}


def attrib_size(code: int) -> int:
    spec = ATTRIB_FORMATS.get(code)
    if not spec:
        return 0
    ncomp, bits, _kind = spec
    if bits == "4_4":
        return 1
    if bits in ("10_10_10_2", "10_11_11"):
        return 4
    return ncomp * bits // 8


def _snorm(value: int, bits: int) -> float:
    if value >= (1 << (bits - 1)):
        value -= 1 << bits
    return max(-1.0, value / float((1 << (bits - 1)) - 1))


def _unorm(value: int, bits: int) -> float:
    return value / float((1 << bits) - 1)


def _u16_codes(data: bytes, fmt: str, count: int):
    return struct.unpack_from("<%d%s" % (count, fmt), data, 0)


def decode_attribute(data: bytes, count: int, stride: int, offset: int,
                     code: int) -> list[tuple[float, ...]] | None:
    """Décode ``count`` valeurs d'attribut depuis un tampon de sommets."""
    spec = ATTRIB_FORMATS.get(code)
    if spec is None:
        return None
    ncomp, bits, kind = spec
    size = attrib_size(code)
    if stride <= 0 or offset + size > stride:
        # Certains fichiers ont un offset égal à la taille du sommet (dernier
        # attribut) : on tolère largement.
        if offset + size > stride and len(data) < offset + size:
            return None
    values: list[tuple[float, ...]] = []
    half = struct.Struct("<e")
    for i in range(count):
        pos = i * stride + offset
        if pos + size > len(data):
            break
        chunk = data[pos:pos + size]
        if kind in ("uint", "sint", "uint2f", "sint2f") and bits in (8, 16, 32):
            signed = kind.startswith("sint")
            if bits == 8:
                comps = [struct.unpack_from("<b" if signed else "<B", chunk, c)[0]
                         for c in range(ncomp)]
            elif bits == 16:
                comps = list(_u16_codes(chunk, "h" if signed else "H", ncomp))
            else:
                comps = list(_u16_codes(chunk, "i" if signed else "I", ncomp))
            if kind.endswith("2f"):
                values.append(tuple(float(c) for c in comps))
            else:
                values.append(tuple(float(c) for c in comps))
            continue
        if kind == "half":
            values.append(tuple(half.unpack_from(chunk, 2 * c)[0] for c in range(ncomp)))
            continue
        if kind == "float" and bits == 32:
            values.append(tuple(struct.unpack_from("<f", chunk, 4 * c)[0] for c in range(ncomp)))
            continue
        if bits == "10_10_10_2":
            raw = struct.unpack_from("<I", chunk, 0)[0]
            parts = [raw & 0x3FF, (raw >> 10) & 0x3FF, (raw >> 20) & 0x3FF, (raw >> 30) & 0x3]
            widths = [10, 10, 10, 2]
        elif bits == "10_11_11":
            raw = struct.unpack_from("<I", chunk, 0)[0]
            parts = [raw & 0x7FF, (raw >> 11) & 0x7FF, (raw >> 22) & 0x3FF]
            widths = [11, 11, 10]
        elif bits == "4_4":
            raw = chunk[0]
            parts = [raw & 0xF, raw >> 4]
            widths = [4, 4]
        elif bits == 8:
            parts = list(chunk[:ncomp])
            widths = [8] * ncomp
        elif bits == 16:
            parts = list(_u16_codes(chunk, "H", ncomp))
            widths = [16] * ncomp
        elif bits == 32:
            parts = list(_u16_codes(chunk, "I", ncomp))
            widths = [32] * ncomp
        else:
            return None
        if kind == "snorm":
            values.append(tuple(_snorm(p, w) for p, w in zip(parts, widths)))
        elif kind == "unorm":
            values.append(tuple(_unorm(p, w) for p, w in zip(parts, widths)))
        else:
            values.append(tuple(float(p) for p in parts))
    return values


# --------------------------------------------------------------------------- structures
@dataclass
class VertexAttrib:
    name: str = ""
    buffer_index: int = 0
    offset: int = 0
    format_code: int = 0

    @property
    def size(self) -> int:
        return attrib_size(self.format_code)


@dataclass
class VertexBuffer:
    attributes: list[VertexAttrib] = field(default_factory=list)
    buffers: list[bytes] = field(default_factory=list)
    strides: list[int] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    vertex_count: int = 0
    vertex_skin_count: int = 0
    gpu_alignment: int = 8
    buffer_offsets: list[int] = field(default_factory=list)   # absolus, dans le fichier
    header_offset: int = 0

    def attrib(self, *names: str) -> VertexAttrib | None:
        for attribute in self.attributes:
            if attribute.name.lower() in [n.lower() for n in names]:
                return attribute
        return None

    def read(self, attribute: VertexAttrib, count: int | None = None):
        if attribute.buffer_index >= len(self.buffers):
            return None
        buffer = self.buffers[attribute.buffer_index]
        stride = self.strides[attribute.buffer_index] if attribute.buffer_index < len(self.strides) else 0
        if stride <= 0:
            stride = sum(a.size for a in self.attributes) or 1
        return decode_attribute(buffer, count or self.vertex_count, stride,
                                attribute.offset, attribute.format_code)


@dataclass
class SubMesh:
    offset: int = 0
    count: int = 0


@dataclass
class Mesh:
    primitive: int = 3
    index_format: int = 1
    index_count: int = 0
    first_vertex: int = 0
    submeshes: list[SubMesh] = field(default_factory=list)
    indices_raw: bytes = b""
    struct_offset: int = 0        # position de la structure Mesh
    index_data_offset: int = 0    # position absolue des indices
    submesh_array_offset: int = 0
    index_count_offset: int = 0   # emplacements des champs à réécrire
    first_vertex_offset: int = 0
    face_buffer_offset: int = 0

    def indices(self) -> list[int]:
        code = {0: ("B", 1), 1: ("H", 2), 2: ("I", 4)}.get(self.index_format, ("H", 2))
        fmt, size = code
        count = len(self.indices_raw) // size
        if self.index_count:
            count = min(count, self.index_count)
        if count <= 0:
            return []
        values = struct.unpack_from("<%d%s" % (count, fmt), self.indices_raw, 0)
        if self.first_vertex:
            values = tuple(v + self.first_vertex for v in values)
        return list(values)

    def face_count(self) -> int:
        """Nombre de triangles, sans décoder ni convertir les indices (rapide)."""
        count = self.index_count or (len(self.indices_raw) //
                                     {0: 1, 1: 2, 2: 4}.get(self.index_format, 2))
        if self.primitive == 0x03:
            return max(0, count // 3)
        if self.primitive == 0x04:
            return max(0, count - 2)
        if self.primitive == 0x07:
            return max(0, count // 6)
        return 0

    def triangles(self) -> list[tuple[int, int, int]]:
        indices = self.indices()
        faces: list[tuple[int, int, int]] = []
        if self.primitive == 0x03:  # TRIANGLES
            for i in range(0, len(indices) - 2, 3):
                faces.append((indices[i], indices[i + 1], indices[i + 2]))
        elif self.primitive == 0x04:  # TRIANGLE_STRIP
            for i in range(len(indices) - 2):
                a, b, c = indices[i], indices[i + 1], indices[i + 2]
                faces.append((a, b, c) if i % 2 == 0 else (b, a, c))
        elif self.primitive == 0x07:  # TRIANGLES_ADJACENCY
            for i in range(0, len(indices) - 5, 6):
                faces.append((indices[i], indices[i + 2], indices[i + 4]))
        return faces


@dataclass
class Shape:
    name: str = ""
    index: int = 0
    material_index: int = 0
    bone_index: int = 0
    vtx_index: int = 0
    vtx_buffer: VertexBuffer | None = None
    meshes: list[Mesh] = field(default_factory=list)
    flags: int = 0
    lod_count: int = 1
    skin_bone_count: int = 0
    vertex_skin_count: int = 0
    key_shape_count: int = 0
    target_attribute_count: int = 0
    bounds_offset: int = 0
    radius_offset: int = 0

    def _used_indices(self, lod: int) -> list[tuple[int, int, int]]:
        """Triangles du LOD demandé.

        Les sous-maillages d'un FSHP découpent la surface en morceaux tous
        visibles : on garde donc l'intégralité du tampon d'index.
        """
        if not self.meshes:
            return []
        return self.meshes[min(lod, len(self.meshes) - 1)].triangles()

    def face_count(self, lod: int = 0) -> int:
        """Nombre de triangles d'un LOD, sans construire le maillage."""
        if not self.meshes:
            return 0
        return self.meshes[min(lod, len(self.meshes) - 1)].face_count()

    def build_mesh(self, lod: int = 0, warnings: list[str] | None = None) -> MeshData | None:
        warnings = warnings if warnings is not None else []
        if self.vtx_buffer is None:
            warnings.append(f"{self.name} : pas de tampon de sommets.")
            return None
        vertex_buffer = self.vtx_buffer
        if not vertex_buffer.buffers or vertex_buffer.vertex_count <= 0:
            warnings.append(f"{self.name} : données de sommets vides.")
            return None
        faces = self._used_indices(lod)
        if not faces:
            warnings.append(f"{self.name} : aucun triangle (primitive non gérée ?).")
            return None

        pos_attr = vertex_buffer.attrib("Position", "Position0", "_Position")
        if pos_attr is None:
            warnings.append(f"{self.name} : attribut « Position » absent.")
            return None
        positions_raw = vertex_buffer.read(pos_attr)
        if not positions_raw:
            warnings.append(f"{self.name} : positions illisibles.")
            return None

        normal_attr = vertex_buffer.attrib("Normal", "Normal0", "_Normal")
        uv_attr = vertex_buffer.attrib("TexCoord0", "UV0", "_TexCoord0")
        color_attr = vertex_buffer.attrib("Color0", "Color", "_Color0")
        normals_raw = vertex_buffer.read(normal_attr) if normal_attr else None
        uvs_raw = vertex_buffer.read(uv_attr) if uv_attr else None
        colors_raw = vertex_buffer.read(color_attr) if color_attr else None

        vertex_count = len(positions_raw)
        used: set[int] = set()
        for face in faces:
            for idx in face:
                if 0 <= idx < vertex_count:
                    used.add(idx)
        if not used:
            warnings.append(f"{self.name} : indices hors limites.")
            return None
        mapping = {old: new for new, old in enumerate(sorted(used))}
        positions = [tuple(positions_raw[i][:3]) for i in sorted(used)]
        remapped: list[tuple[int, int, int]] = []
        for face in faces:
            if all(0 <= i < vertex_count for i in face):
                remapped.append(tuple(mapping[i] for i in face))
        if not remapped:
            return None

        mesh = MeshData(name=self.name or f"shape_{self.index}",
                        positions=positions, faces=remapped)
        if normals_raw and len(normals_raw) >= vertex_count:
            mesh.normals = [tuple(normals_raw[i][:3]) for i in sorted(used)]
        if uvs_raw and len(uvs_raw) >= vertex_count:
            mesh.uvs = [tuple(uvs_raw[i][:2]) for i in sorted(used)]
        if colors_raw and len(colors_raw) >= vertex_count:
            mesh.colors = [tuple(colors_raw[i][:3]) for i in sorted(used)]
        if not mesh.normals:
            # Ne pas présenter des normales synthétiques comme des attributs
            # d'origine : une cible sans canal normal ne pourrait pas les
            # conserver lors de l'aller-retour.
            message = f"{self.name} : normales absentes ; elles ne sont pas exportées."
            if message not in warnings:
                warnings.append(message)
        return mesh


@dataclass
class Material:
    name: str = ""
    index: int = 0


@dataclass
class Model:
    name: str = ""
    index: int = 0
    shapes: list[Shape] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    vertex_buffers: list[VertexBuffer] = field(default_factory=list)
    skeleton_name: str = ""
    skeleton_offset: int = 0
    #: maillages déjà construits (BFRES 10.x : voir :mod:`bfres_v10`)
    prebuilt: list[MeshData] = field(default_factory=list)

    def build_meshes(self, lod: int = 0, warnings: list[str] | None = None) -> list[MeshData]:
        warnings = warnings if warnings is not None else []
        if self.prebuilt:
            if lod != 0:
                raise HookyError("BFRES : seul le LOD 0 est accessible avec ce lecteur.")
            return list(self.prebuilt)
        meshes: list[MeshData] = []
        for shape in self.shapes:
            try:
                mesh = shape.build_mesh(lod, warnings)
            except (struct.error, IndexError, ValueError, ZeroDivisionError) as exc:
                warnings.append(f"{shape.name} : {exc}")
                continue
            if mesh is None:
                continue
            if 0 <= shape.material_index < len(self.materials):
                mesh.material = self.materials[shape.material_index].name
            meshes.append(mesh)
        return meshes

    def mesh_info(self) -> str:
        lines = [f"Modèle : {self.name or f'#{self.index}'}"]
        shapes = self.shapes
        total_v = sum(s.vtx_buffer.vertex_count for s in shapes if s.vtx_buffer)
        total_f = sum(s.face_count(0) for s in shapes)
        lines.append(f"Formes  : {len(shapes)}   ·   Matériaux : {len(self.materials)}")
        lines.append(f"Géométrie (LOD0) : {total_v:,} sommets · {total_f:,} triangles")
        if self.materials:
            lines.append("Matériaux : " + ", ".join(m.name or "?" for m in self.materials[:12]))
        for shape in shapes[:40]:
            vtx = shape.vtx_buffer
            count = vtx.vertex_count if vtx else 0
            lods = len(shape.meshes)
            lines.append(f"  • {shape.name} — {count:,} sommets · {lods} LOD"
                         + (f" — matériau #{shape.material_index}" if self.materials else ""))
        if len(self.shapes) > 40:
            lines.append(f"  … {len(self.shapes) - 40} autres formes")
        return "\n".join(lines)


class Cursor:
    """Petit lecteur binaire avec cache de chaînes (offsets 64 bits BFRES)."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.endian = "<"
        self.strings: dict[int, str] = {}
        self.warnings: list[str] = []

    # -- primitives
    def seek(self, pos: int) -> None:
        self.pos = max(0, min(pos, len(self.data)))

    def skip(self, count: int) -> None:
        self.pos = min(len(self.data), self.pos + count)

    def align(self, alignment: int) -> None:
        self.pos = (self.pos + alignment - 1) // alignment * alignment

    def _unpack(self, fmt: str):
        try:
            values = struct.unpack_from(self.endian + fmt, self.data, self.pos)
        except struct.error as exc:
            raise FormatError("Fichier BFRES tronqué.") from exc
        self.pos += struct.calcsize(self.endian + fmt)
        return values

    def u8(self) -> int:
        return self._unpack("B")[0]

    def u16(self) -> int:
        return self._unpack("H")[0]

    def u32(self) -> int:
        return self._unpack("I")[0]

    def i32(self) -> int:
        return self._unpack("i")[0]

    def u64(self) -> int:
        return self._unpack("Q")[0]

    def i64(self) -> int:
        return self._unpack("q")[0]

    def f32(self) -> float:
        return self._unpack("f")[0]

    def offset(self) -> int:
        return self.u64()

    def raw(self, count: int) -> bytes:
        if self.pos + count > len(self.data):
            raise FormatError("Fichier BFRES tronqué.")
        out = self.data[self.pos:self.pos + count]
        self.pos += count
        return out

    # -- chaînes (BFRES : uint16 longueur + chaîne zéro-terminée)
    def string_at(self, offset: int) -> str:
        if not offset or offset + 2 > len(self.data):
            return ""
        cached = self.strings.get(offset)
        if cached is not None:
            return cached
        size = self.data[offset] | (self.data[offset + 1] << 8)
        text = cstring(self.data, offset + 2, limit=size + 1 if size else 4096)
        self.strings[offset] = text
        return text

    def string(self) -> str:
        return self.string_at(self.offset())

    # -- dictionnaires
    def dict_entries(self, dict_offset: int, values_offset: int) -> list[tuple[str, int]]:
        if not dict_offset or dict_offset + 8 > len(self.data):
            return []
        saved = self.pos
        try:
            self.seek(dict_offset)
            self.u32()  # champ réservé ou taille selon la variante
            count = self.u32()
            if count > 100000:
                self.warnings.append(f"dictionnaire invraisemblable ({count} entrées)")
                return []
            entries: list[tuple[str, int]] = []
            keys: list[str] = []
            for i in range(count + 1):
                self.u32()  # reference
                self.u16()  # left
                self.u16()  # right
                key_offset = self.u64()
                keys.append(self.string_at(key_offset) if i else "")
            if values_offset:
                self.seek(values_offset)
                for i in range(1, count + 1):
                    value_offset = self.u64()
                    entries.append((keys[i] if i < len(keys) else "", value_offset))
            return entries
        finally:
            self.pos = saved


def meshes_from_file(file: "BfresFile", lod: int = 0,
                     budget: int | None = None) -> list:
    """Maillages d'un BFRES, LOD donné, sous un budget de faces optionnel.

    Utilisé par l'aperçu hors interface (``cli view``) : même conversion que le
    viewer, sans Tkinter ni cache d'application.
    """
    from .mesh import MeshData  # import local : mesh ne dépend pas de bfres

    meshes: list[MeshData] = []
    total = 0
    # (le bloc ci-dessous traite les modèles « classiques », v7 à v9)
    for model in file.models:
        if model.prebuilt:
            for mesh in model.build_meshes(lod, file.warnings):
                if budget is not None and total + len(mesh.faces) > budget:
                    break
                meshes.append(mesh)
                total += len(mesh.faces)
            continue
        for shape in model.shapes:
            if budget is not None and total >= budget:
                break
            try:
                mesh = shape.build_mesh(lod, file.warnings)
            except HookyError as exc:
                message = f"{shape.name or 'forme'} : non exportée ({exc})."
                if message not in file.warnings:
                    file.warnings.append(message)
                continue
            if mesh is None or not mesh.faces:
                message = f"{shape.name or 'forme'} : aucune géométrie exportable."
                if message not in file.warnings:
                    file.warnings.append(message)
                continue
            if 0 <= shape.material_index < len(model.materials):
                mesh.material = model.materials[shape.material_index].name
            meshes.append(mesh)
            total += len(mesh.faces)
        if budget is not None and total >= budget:
            break
    if not meshes:
        raise HookyError("Aucun maillage exploitable dans ce BFRES.\n\n"
                         + "\n".join(file.warnings[:8]))
    return meshes


class BfresFile:
    """Fichier BFRES little-endian."""

    def __init__(self) -> None:
        self.version = 0
        self.version_major = 0
        self.alignment = 0
        self.name = ""
        self.models: list[Model] = []
        self.buffer_offset = 0
        self.warnings: list[str] = []
        self.has_external_strings = False
        self.data = b""
        self.v10_report = None

    # ------------------------------------------------------------------ lecture
    @classmethod
    def parse(cls, data: bytes) -> "BfresFile":
        if data[:4] != MAGIC:
            raise FormatError("Ce n'est pas un fichier BFRES (magic « FRES » absent).")
        if len(data) < 0x40:
            raise FormatError("Fichier BFRES tronqué.")
        self = cls()
        self.data = data
        cur = Cursor(data)
        if data[0x0C:0x0E] != b"\xff\xfe":
            raise FormatError(
                "BFRES big-endian non géré : seule la variante little-endian est prise en charge."
            )
        cur.seek(0x08)
        self.version = cur.u32()
        self.version_major = (self.version >> 16) & 0xFFFF
        cur.seek(0x0E)
        self.alignment = cur.u8()
        cur.seek(0x1C)
        file_size = cur.u32()

        # --- en-tête BFRES (suit toujours le même ordre, cf. BfresLibrary)
        cur.seek(0x20)
        self.name = cur.string()
        model_offset = cur.offset()
        model_dict_offset = cur.offset()
        if self.version_major >= 9:
            cur.skip(32)
        cur.skip(16 * 5)          # 5 dictionnaires d'animations
        cur.skip(8)               # MemoryPool
        buffer_info_offset = cur.offset()

        if self.version_major >= 10 and len(data) > 0xEF:
            flags = data[0xEE]
            if flags & 0x04:
                self.has_external_strings = True
                self.warnings.append("Chaînes externes : certains noms seront vides.")

        self.buffer_offset = 0
        if buffer_info_offset and buffer_info_offset + 16 <= len(data):
            try:
                cur.seek(buffer_info_offset)
                cur.u32()  # inconnu
                cur.u32()  # taille
                self.buffer_offset = cur.u64()
            except FormatError:
                self.buffer_offset = 0
        if self.version_major >= 10 and len(data) > 0xEF and data[0xEE] & 0x08:
            # Données GPU stockées à la fin du fichier : base décalée.
            if file_size + 288 < len(data):
                self.buffer_offset = file_size + 288

        entries = []
        if not model_dict_offset or model_dict_offset + 8 > len(data):
            self.warnings.append("Dictionnaire de modèles absent ou hors limites.")
        else:
            try:
                entries = cur.dict_entries(model_dict_offset, model_offset)
            except FormatError as exc:
                self.warnings.append(f"Dictionnaire de modèles illisible : {exc}")
            else:
                if not entries:
                    self.warnings.append("Dictionnaire de modèles vide ou sans valeurs lisibles.")
        # Dans le layout 10.x observé, l'en-tête pointe directement sur un
        # tableau FMDL, et non sur le tableau de pointeurs du lecteur historique.
        # Ne jamais laisser le lecteur historique contourner une table 10.x
        # déclarée mais corrompue.
        if (self.version_major == 10 and model_offset
                and data[model_offset:model_offset + 4] == b"FMDL"):
            self._parse_v10(data, require_model_shape=True)
            return self
        for index, (name, offset) in enumerate(entries):
            if not offset or offset >= len(data):
                continue
            try:
                model = self._read_model(cur, name, index, offset)
            except (FormatError, struct.error, IndexError, ValueError) as exc:
                self.warnings.append(f"modèle « {name or index} » illisible : {exc}")
                continue
            if model is not None:
                self.models.append(model)
        if not self.models and self.version_major >= 10:
            # En 10.x le pointeur FMDL de l'en-tête permet aussi de lire la
            # géométrie. Ne pas retourner avant ce repli si le dictionnaire est
            # absent ou illisible. Sans entrées, exiger en plus que le FMDL
            # référence un FVTX validé, pour ne pas associer une table orpheline.
            self._parse_v10(data, require_model_shape=not entries)
        return self

    def _parse_v10(self, data: bytes, *, require_model_shape: bool = False) -> None:
        """BFRES 10.x : géométrie décodée par :mod:`bfres_v10` (validée).

        La lecture v10 n'est pas documentée : si une seule vérification échoue,
        on n'ajoute aucun modèle et on explique pourquoi dans ``warnings``.
        """
        try:
            from .bfres_v10 import parse as parse_v10
        except ImportError:      # pragma: no cover - dépendance interne
            return
        try:
            result = parse_v10(data, require_model_shape=require_model_shape)
        except (struct.error, IndexError, ValueError, ZeroDivisionError) as exc:
            self.warnings.append(f"Lecture v10 impossible : {exc}")
            return
        if result is None:
            self.warnings.append(
                "BFRES 10.x : structure non reconnue, géométrie non extraite "
                "(rien n'est inventé)."
            )
            return
        meshes, report = result
        if not meshes:
            self.warnings.append("BFRES 10.x : aucun maillage validé.")
            return
        model = Model(name=report.model_name or self.name or "modele", index=0,
                      prebuilt=list(meshes))
        for index, material in enumerate(report.material_names):
            if material:
                model.materials.append(Material(name=material, index=index))
        model.skeleton_name = getattr(report, "skeleton_name", "") or model.skeleton_name
        self.models.append(model)
        self.v10_report = report
        if require_model_shape:
            method = "tables de formes" if report.structured else "extraction heuristique"
            self.warnings.append(f"BFRES 10.x : FMDL et FVTX référencés ; {method}.")
        self.warnings.extend(report.warnings)

    # -- objets
    def _read_model(self, cur: Cursor, name: str, index: int, offset: int) -> Model:
        saved = cur.pos
        cur.seek(offset)
        model = Model(name=name, index=index)
        if self.version_major >= 9:
            cur.u32()  # flags
        else:
            cur.skip(12)  # header block
        model.name = cur.string() or name
        cur.string()  # chemin interne
        skeleton_offset = cur.offset()
        model.skeleton_offset = skeleton_offset
        vertex_array_offset = cur.offset()
        vertex_buffer = cur
        shapes_values = cur.offset()
        shapes_dict = cur.offset()
        if self.version_major == 9:
            material_values = cur.offset()
            extra = cur.offset()
            material_dict = cur.offset()
            if not material_dict:
                material_dict = extra
        else:
            material_values = cur.offset()
            material_dict = cur.offset()
            if self.version_major >= 10:
                cur.offset()  # shader assign
        user_data_values = cur.offset()
        user_data_dict = cur.offset()
        cur.u64()  # user pointer
        num_vertex_buffers = cur.u16()
        num_shapes = cur.u16()
        cur.u16()  # num materials
        cur.skip(10 if self.version_major >= 9 else 10)

        if skeleton_offset:
            try:
                cur.seek(skeleton_offset)
                if self.version_major >= 9:
                    cur.u32()
                else:
                    cur.skip(12)
                model.skeleton_name = cur.string()
            except FormatError:
                pass

        if vertex_array_offset:
            cur.seek(vertex_array_offset)
            for i in range(min(num_vertex_buffers, 256)):
                offset_i = cur.offset()
                if not offset_i:
                    continue
                try:
                    model.vertex_buffers.append(self._read_vertex_buffer(cur, offset_i))
                except (FormatError, struct.error, IndexError, ValueError) as exc:
                    self.warnings.append(f"{model.name} : tampon de sommets {i} illisible ({exc}).")

        if shapes_dict:
            shape_index = 0
            for shape_name, shape_offset in cur.dict_entries(shapes_dict, shapes_values):
                if not shape_offset:
                    continue
                try:
                    shape = self._read_shape(cur, shape_name, shape_index, shape_offset)
                except (FormatError, struct.error, IndexError, ValueError) as exc:
                    self.warnings.append(f"{model.name}/{shape_name} : forme illisible ({exc}).")
                    shape_index += 1
                    continue
                if shape is not None:
                    if shape.vtx_buffer is None and model.vertex_buffers:
                        idx = min(shape.vtx_index, len(model.vertex_buffers) - 1)
                        shape.vtx_buffer = model.vertex_buffers[idx]
                    model.shapes.append(shape)
                shape_index += 1

        if material_dict:
            for mat_name, mat_offset in cur.dict_entries(material_dict, material_values):
                if not mat_offset:
                    continue
                material = Material(name=mat_name, index=len(model.materials))
                try:
                    cur.seek(mat_offset)
                    if self.version_major >= 9:
                        cur.u32()
                    else:
                        cur.skip(12)
                    real_name = cur.string()
                    if real_name:
                        material.name = real_name
                except FormatError:
                    pass
                model.materials.append(material)

        cur.pos = saved
        return model

    def _read_shape(self, cur: Cursor, name: str, index: int, offset: int) -> Shape:
        saved = cur.pos
        cur.seek(offset)
        shape = Shape(name=name, index=index)
        if self.version_major >= 9:
            shape.flags = cur.u32()
        else:
            cur.skip(12)
        shape.name = cur.string() or name
        vertex_buffer_offset = cur.offset()
        mesh_array_offset = cur.offset()
        cur.offset()  # liste d'index d'os (skinning)
        key_shapes_values = cur.offset()
        key_shapes_dict = cur.offset()
        shape.bounds_offset = cur.offset()
        if self.version_major > 3:
            shape.radius_offset = cur.offset()
            cur.i64()     # user pointer
        else:
            cur.i64()
            cur.f32()
        if self.version_major < 9:
            cur.u32()  # flags
        cur.u16()  # idx
        shape.material_index = cur.u16()
        shape.bone_index = cur.u16()
        shape.vtx_index = cur.u16()
        shape.skin_bone_count = cur.u16()
        shape.vertex_skin_count = cur.u8()
        num_mesh = cur.u8()
        shape.key_shape_count = cur.u8()
        shape.target_attribute_count = cur.u8()
        cur.skip(2 if (self.version_major <= 3 or self.version_major >= 9) else 6)

        if vertex_buffer_offset:
            try:
                shape.vtx_buffer = self._read_vertex_buffer(cur, vertex_buffer_offset)
            except (FormatError, struct.error, IndexError, ValueError) as exc:
                self.warnings.append(f"{shape.name} : FVTX illisible ({exc}).")

        if mesh_array_offset and num_mesh:
            cur.seek(mesh_array_offset)
            for i in range(min(num_mesh, 64)):
                mesh_offset = cur.offset()
                if not mesh_offset:
                    continue
                try:
                    shape.meshes.append(self._read_mesh(cur, mesh_offset))
                except (FormatError, struct.error, IndexError, ValueError) as exc:
                    self.warnings.append(f"{shape.name} : mesh {i} illisible ({exc}).")
        shape.lod_count = max(1, len(shape.meshes))
        cur.pos = saved
        return shape

    def _read_vertex_buffer(self, cur: Cursor, offset: int) -> VertexBuffer:
        saved = cur.pos
        cur.seek(offset)
        vtx = VertexBuffer()
        vtx.header_offset = offset
        if self.version_major >= 9:
            cur.u32()
        else:
            cur.skip(12)
        attributes_values = cur.offset()
        attributes_dict = cur.offset()
        cur.offset()        # memory pool
        cur.offset()        # inconnu
        if self.version_major > 2:
            cur.offset()    # inconnu 2
        sizes_offset = cur.offset()
        strides_offset = cur.offset()
        cur.u64()           # padding
        buffer_offset = cur.i32()
        cur.u8()            # nombre d'attributs
        num_buffers = cur.u8()
        cur.u16()           # idx
        vtx.vertex_count = cur.u32()
        vtx.vertex_skin_count = cur.u16()
        vtx.gpu_alignment = cur.u16() or 8

        if attributes_dict:
            for attr_name, attr_offset in cur.dict_entries(attributes_dict, attributes_values):
                if not attr_offset:
                    continue
                try:
                    vtx.attributes.append(self._read_attribute(cur, attr_name, attr_offset))
                except (FormatError, struct.error) as exc:
                    self.warnings.append(f"attribut {attr_name} illisible ({exc}).")

        num_buffers = min(num_buffers, 16)
        if strides_offset and num_buffers:
            cur.seek(strides_offset)
            for _ in range(num_buffers):
                vtx.strides.append(cur.u32())
                cur.skip(12)
        if sizes_offset and num_buffers:
            cur.seek(sizes_offset)
            for _ in range(num_buffers):
                vtx.sizes.append(cur.u32())
                cur.skip(12)

        base = self.buffer_offset + buffer_offset
        if num_buffers and base > 0:
            cur.seek(base)
            for i in range(num_buffers):
                cur.align(max(1, vtx.gpu_alignment))
                size = vtx.sizes[i] if i < len(vtx.sizes) else 0
                if size <= 0 or cur.pos + size > len(self.data):
                    vtx.buffers.append(b"")
                    vtx.buffer_offsets.append(0)
                    continue
                vtx.buffer_offsets.append(cur.pos)
                vtx.buffers.append(cur.raw(size))
        cur.pos = saved
        return vtx

    def _read_attribute(self, cur: Cursor, name: str, offset: int) -> VertexAttrib:
        saved = cur.pos
        cur.seek(offset)
        attribute = VertexAttrib(name=name)
        attribute.name = cur.string() or name
        cur.endian = ">"
        try:
            attribute.format_code = cur.u16()
        finally:
            cur.endian = "<"
        cur.skip(2)
        attribute.offset = cur.u16()
        attribute.buffer_index = cur.u16()
        cur.pos = saved
        return attribute

    def _read_mesh(self, cur: Cursor, offset: int) -> Mesh:
        saved = cur.pos
        cur.seek(offset)
        mesh = Mesh()
        mesh.struct_offset = offset
        submesh_array = cur.offset()
        cur.offset()  # memory pool
        cur.offset()  # tampon d'index (non utilisé directement)
        size = cur.u32()
        cur.u32()     # drapeaux
        cur.skip(40)  # BufferSize
        mesh.face_buffer_offset = cur.u32()
        face_buffer_offset = mesh.face_buffer_offset
        mesh.primitive = cur.u32()
        mesh.index_format = cur.u32()
        mesh.index_count_offset = cur.pos
        mesh.index_count = cur.u32()
        mesh.first_vertex_offset = cur.pos
        mesh.first_vertex = cur.u32()
        num_submesh = cur.u16()
        cur.u16()

        if submesh_array and num_submesh:
            cur.seek(submesh_array)
            for _ in range(min(num_submesh, 4096)):
                submesh = SubMesh(offset=cur.u32(), count=cur.u32())
                mesh.submeshes.append(submesh)

        mesh.submesh_array_offset = submesh_array
        data_offset = self.buffer_offset + face_buffer_offset
        if size and data_offset and data_offset + size <= len(self.data):
            mesh.index_data_offset = data_offset
            mesh.indices_raw = self.data[data_offset:data_offset + size]
        cur.pos = saved
        return mesh

    # ------------------------------------------------------------------ helpers
    def build_meshes(self, model_index: int = 0, lod: int = 0) -> list[MeshData]:
        if not self.models:
            raise HookyError("Aucun modèle lisible dans ce BFRES.")
        model = self.models[max(0, min(model_index, len(self.models) - 1))]
        return model.build_meshes(lod, self.warnings)

    def export_obj(self, path, model_index: int = 0, lod: int = 0,
                   scale: float = 1.0) -> Path:
        meshes = self.build_meshes(model_index, lod)
        if not meshes:
            raise HookyError("Aucun maillage exploitable (voir les avertissements).")
        return export_obj(meshes, path, scale=scale)

    def info(self) -> str:
        lines = [f"Fichier BFRES : {self.name or '(sans nom)'}",
                 f"Version : {self.version >> 16}.{(self.version >> 8) & 0xFF}.{self.version & 0xFF}"
                 f"   ·   alignement : {1 << self.alignment if self.alignment < 32 else self.alignment}",
                 f"Modèles : {len(self.models)}"]
        if self.warnings:
            lines += [""] + [f"⚠ {w}" for w in self.warnings[:10]]
        for model in self.models:
            lines += ["", model.mesh_info()]
        return "\n".join(lines)


def is_bfres(data: bytes) -> bool:
    return data[:4] == MAGIC


def parse_bfres(data: bytes) -> BfresFile:
    return BfresFile.parse(data)
