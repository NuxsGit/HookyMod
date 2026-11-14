"""Lecture d'un fichier ``.blend`` **sans Blender**, pour en sortir la géométrie.

Un ``.blend`` n'est pas un format opaque : c'est une suite de blocs, chacun
décrit par l'ADN que le fichier embarque lui-même (la « SDNA » : noms de types,
tailles, membres de structures). On ne code donc aucune disposition en dur : on
lit la SDNA du fichier, on s'en sert pour interpréter les blocs, et on récupère
les objets, leur matrice et le maillage qu'ils pointent.

Trois dispositions de maillage sont reconnues, parce que Blender a changé trois
fois de représentation :

* ``mvert`` / ``mpoly`` / ``mloop`` — Blender 2.5 → 3.6 ;
* attributs ``CustomData`` (``vert_positions``, ``corner_vert``) — Blender 3.5+ ;
* ``face_offset_indices`` — Blender 4.1+.

Si le fichier ne tombe dans aucun de ces cas, on **refuse** en expliquant
pourquoi (version, structure manquante) plutôt que de produire une géométrie
fausse. Les faces sont triangulées par éventail, les coordonnées ramenées de
l'axe Z (Blender) à l'axe Y (Zelda), et chaque objet garde son nom.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterable

from .mesh import MeshData
from .util import HookyError

BLENDER_MAGIC = b"BLENDER"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
SUPPORTED_VERSIONS = "2.5x à 4.x"

# types scalaires de la SDNA -> format struct
_SCALARS = {
    "char": "b", "uchar": "B", "int8_t": "b", "uint8_t": "B",
    "short": "h", "ushort": "H", "int16_t": "h", "uint16_t": "H",
    "int": "i", "uint": "I", "int32_t": "i", "uint32_t": "I",
    "int64_t": "q", "uint64_t": "Q",
    "float": "f", "double": "d",
}

# champs CustomData candidats (le nom exact a bougé selon les versions)
_POSITION_LAYERS = ("position", "vert_position", ".vert_position")
_CORNER_LAYERS = ("corner_vert", ".corner_vert", "corner_verts")
_UV_LAYERS = ("uvmap", "uv", "uvmap_0")


class BlendError(HookyError):
    """Fichier illisible : le message dit toujours ce qui bloque."""


# --------------------------------------------------------------------------- ADN
@dataclass(frozen=True)
class _Field:
    name: str          # nom sans les crochets ("co", "object_to_world")
    raw_name: str      # nom tel qu'écrit dans l'ADN ("co[3]")
    type: str
    offset: int
    size: int
    elem: int
    dims: tuple[int, ...]
    is_ptr: bool


@dataclass
class _Dna:
    names: list[str]
    types: list[str]
    lens: list[int]
    entries: list[tuple[int, list[tuple[int, int]]]]
    _cache: dict[int, list[_Field]] = field(default_factory=dict)

    # -- lecture du bloc SDNA
    @classmethod
    def parse(cls, payload: bytes, endian: str) -> "_Dna":
        pos = 0

        def expect(token: bytes) -> None:
            nonlocal pos
            if payload[pos:pos + 4] != token:
                raise BlendError(
                    f"ADN du fichier illisible : « {token.decode(errors='replace')} » "
                    f"attendu à l'offset {pos}.")
            pos += 4

        def align() -> None:
            nonlocal pos
            pos = (pos + 3) & ~3

        def strings(count: int) -> list[str]:
            nonlocal pos
            out = []
            for _ in range(count):
                end = payload.index(b"\x00", pos)
                out.append(payload[pos:end].decode("latin-1"))
                pos = end + 1
            align()
            return out

        expect(b"SDNA")
        expect(b"NAME")
        (name_count,) = _unpack(endian + "i", payload, pos)
        pos += 4
        names = strings(name_count)

        expect(b"TYPE")
        (type_count,) = _unpack(endian + "i", payload, pos)
        pos += 4
        types = strings(type_count)

        expect(b"TLEN")
        lens = [_unpack(endian + "h", payload, pos + 2 * i)[0] for i in range(type_count)]
        pos += 2 * type_count
        align()

        expect(b"STRC")
        (struct_count,) = _unpack(endian + "i", payload, pos)
        pos += 4
        entries: list[tuple[int, list[tuple[int, int]]]] = []
        for _ in range(struct_count):
            stype, nfields = _unpack(endian + "hh", payload, pos)
            pos += 4
            pairs = []
            for _ in range(nfields):
                ftype, fname = _unpack(endian + "hh", payload, pos)
                pos += 4
                pairs.append((ftype, fname))
            entries.append((stype, pairs))
        return cls(names, types, lens, entries)

    # -- interrogation
    def index(self, name: str) -> int | None:
        for i, (stype, _) in enumerate(self.entries):
            if self.types[stype] == name:
                return i
        return None

    def fields(self, struct_index: int) -> list[_Field]:
        cached = self._cache.get(struct_index)
        if cached is not None:
            return cached
        stype, pairs = self.entries[struct_index]
        del stype
        out: list[_Field] = []
        offset = 0
        for ftype, fname in pairs:
            raw = self.names[fname]
            type_name = self.types[ftype]
            base, dims = _split_dims(raw)
            is_ptr = type_name.startswith("*")
            elem = 4 if is_ptr else self.lens[ftype]
            count = 1
            for dim in dims:
                count *= dim
            size = elem * count
            out.append(_Field(base, raw, type_name, offset, size, elem, dims, is_ptr))
            offset += size
        self._cache[struct_index] = out
        return out

    def size(self, struct_index: int) -> int:
        fields = self.fields(struct_index)
        return (fields[-1].offset + fields[-1].size) if fields else 0

    def field(self, struct_index: int, name: str) -> _Field | None:
        for item in self.fields(struct_index):
            if item.name == name:
                return item
        return None

    def has(self, struct_index: int, name: str) -> bool:
        return self.field(struct_index, name) is not None


def _split_dims(raw: str) -> tuple[str, tuple[int, ...]]:
    if "[" not in raw:
        return raw, ()
    base = raw[:raw.index("[")]
    dims: list[int] = []
    rest = raw[raw.index("["):]
    while rest.startswith("["):
        end = rest.index("]")
        try:
            dims.append(int(rest[1:end]))
        except ValueError:
            dims.append(1)
        rest = rest[end + 1:]
    return base, tuple(dims)


def _unpack(fmt: str, data: bytes, offset: int) -> tuple:
    if offset + struct.calcsize(fmt) > len(data):
        raise BlendError("ADN tronqué : le fichier semble coupé.")
    return struct.unpack_from(fmt, data, offset)


# --------------------------------------------------------------------------- blocs
@dataclass
class _Block:
    code: bytes
    size: int
    addr: int
    sdna: int
    count: int
    offset: int        # position des données dans le fichier


class _Rec:
    """Vue typée sur une structure à un offset donné."""

    __slots__ = ("blend", "offset", "index")

    def __init__(self, blend: "_Blend", offset: int, index: int) -> None:
        self.blend = blend
        self.offset = offset
        self.index = index

    # -- accès
    def field(self, name: str) -> _Field | None:
        return self.blend.dna.field(self.index, name)

    def value(self, name: str, default=None):
        item = self.field(name)
        if item is None:
            return default
        if item.is_ptr:
            return self.pointer(name)
        fmt = self.blend.elem_format(item)
        count = item.size // item.elem if item.elem else 1
        if count <= 1 and not item.dims:
            return _unpack(self.blend.endian + fmt, self.blend.data,
                           self.offset + item.offset)[0]
        values = tuple(_unpack(self.blend.endian + fmt, self.blend.data,
                               self.offset + item.offset + item.elem * i)[0]
                       for i in range(count))
        return values

    def pointer(self, name: str) -> int:
        item = self.field(name)
        if item is None or not item.is_ptr:
            return 0
        fmt = "Q" if self.blend.ptr == 8 else "I"
        return _unpack(self.blend.endian + fmt, self.blend.data,
                       self.offset + item.offset)[0]

    def ptr_array(self, name: str) -> list[int]:
        item = self.field(name)
        if item is None or not item.is_ptr:
            return []
        fmt = "Q" if self.blend.ptr == 8 else "I"
        count = item.size // item.elem
        return [_unpack(self.blend.endian + fmt, self.blend.data,
                        self.offset + item.offset + item.elem * i)[0]
                for i in range(count)]

    def struct(self, name: str) -> "_Rec | None":
        item = self.field(name)
        if item is None or item.is_ptr:
            return None
        index = self.blend.dna.index(item.type)
        if index is None:
            return None
        return _Rec(self.blend, self.offset + item.offset, index)

    def string(self, name: str, length: int | None = None) -> str:
        item = self.field(name)
        if item is None:
            return ""
        size = length or item.size
        raw = self.blend.data[self.offset + item.offset:self.offset + item.offset + size]
        return raw.split(b"\x00")[0].decode("latin-1").strip()

    def matrix(self, name: str) -> tuple[float, ...] | None:
        item = self.field(name)
        if item is None or item.is_ptr:
            return None
        count = item.size // 4
        if count != 16:
            return None
        values = _unpack(self.blend.endian + "16f", self.blend.data,
                         self.offset + item.offset)
        return tuple(float(v) for v in values)

    def text(self, dotted: str, length: int | None = None) -> str:
        """Chaîne éventuellement nichée (``"id.name"``)."""
        if "." in dotted:
            head, tail = dotted.rsplit(".", 1)
            node = self.path(head)
            return node.text(tail, length) if node is not None else ""
        return self.string(dotted, length)

    def path(self, dotted: str) -> "_Rec | None":
        node = self
        for part in dotted.split("."):
            nxt = node.struct(part)
            if nxt is None:
                return None
            node = nxt
        return node


class _Blend:
    """Fichier .blend décompressé, découpé en blocs, avec son ADN."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        if data[:7] != BLENDER_MAGIC:
            raise BlendError("Ce n'est pas un fichier .blend (signature absente).")
        if len(data) < 12:
            raise BlendError("En-tête .blend tronqué.")
        self.ptr = 8 if data[7:8] == b"-" else 4
        self.endian = ">" if data[8:9] == b"V" else "<"
        header_version = data[9:12].decode("latin-1")
        self.version = self._readable_version(header_version)
        self.blocks: list[_Block] = []
        self.by_addr: dict[int, _Block] = {}
        self._parse_blocks()
        self.dna = self._parse_dna()

    # -- utilitaires
    @staticmethod
    def _readable_version(raw: str) -> str:
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) == 3:
            return f"{digits[0]}.{digits[1:]}{'' }"
        return raw

    def elem_format(self, item: _Field) -> str:
        fmt = _SCALARS.get(item.type)
        if fmt is None:
            raise BlendError(f"Type « {item.type} » inconnu dans l'ADN (champ {item.raw_name}).")
        return fmt

    def _parse_blocks(self) -> None:
        pos = 12
        header_len = 8 + self.ptr + 8
        while pos + header_len <= len(self.data):
            code = self.data[pos:pos + 4]
            if code == b"ENDB":
                break
            size = _unpack(self.endian + "i", self.data, pos + 4)[0]
            addr = _unpack(self.endian + ("Q" if self.ptr == 8 else "I"),
                           self.data, pos + 8)[0]
            sdna = _unpack(self.endian + "i", self.data, pos + 8 + self.ptr)[0]
            count = _unpack(self.endian + "i", self.data, pos + 12 + self.ptr)[0]
            start = pos + header_len
            if size < 0 or start + size > len(self.data):
                raise BlendError(f"Bloc « {code.decode(errors='replace')} » hors fichier "
                                 f"(offset {pos}, taille {size}).")
            block = _Block(code, size, addr, sdna, count, start)
            self.blocks.append(block)
            if addr:
                self.by_addr[addr] = block
            pos = start + size

    def _parse_dna(self) -> _Dna:
        for block in self.blocks:
            if block.code == b"DNA1":
                endian = self.endian
                return _Dna.parse(self.data[block.offset:block.offset + block.size], endian)
        raise BlendError("Ce .blend ne contient pas d'ADN (bloc DNA1 absent) : "
                         "fichier incomplet ou compressé d'une façon non reconnue.")

    # -- blocs
    def block_at(self, addr: int) -> _Block | None:
        return self.by_addr.get(addr)

    def bytes_at(self, addr: int, length: int | None = None) -> bytes:
        block = self.block_at(addr)
        if block is None:
            return b""
        return self.data[block.offset:block.offset + (block.size if length is None else length)]

    def records(self, struct_name: str) -> Iterable[_Rec]:
        index = self.dna.index(struct_name)
        if index is None:
            return []
        size = self.dna.size(index) or 1
        for block in self.blocks:
            if block.sdna != index:
                continue
            count = block.count or max(1, block.size // size)
            for i in range(count):
                yield _Rec(self, block.offset + i * size, index)


# --------------------------------------------------------------------------- résultat
@dataclass
class BlendObject:
    name: str
    mesh: MeshData | None = None
    materials: list[str] = field(default_factory=list)
    reason: str = ""          # pourquoi l'objet n'a pas de géométrie

    def as_json(self) -> dict:
        return {
            "name": self.name,
            "faces": len(self.mesh.faces) if self.mesh else 0,
            "vertices": len(self.mesh.positions) if self.mesh else 0,
            "materials": list(self.materials),
            "reason": self.reason,
        }


@dataclass
class BlendImport:
    version: str
    objects: list[BlendObject]
    warnings: list[str] = field(default_factory=list)

    @property
    def meshes(self) -> list[MeshData]:
        return [obj.mesh for obj in self.objects if obj.mesh and obj.mesh.faces]

    @property
    def faces(self) -> int:
        return sum(len(mesh.faces) for mesh in self.meshes)

    @property
    def vertices(self) -> int:
        return sum(len(mesh.positions) for mesh in self.meshes)

    @property
    def materials(self) -> list[str]:
        seen: list[str] = []
        for obj in self.objects:
            for name in obj.materials:
                if name not in seen:
                    seen.append(name)
        return seen

    def as_json(self) -> dict:
        return {
            "version": self.version,
            "objects": [obj.as_json() for obj in self.objects],
            "meshes": len(self.meshes),
            "faces": self.faces,
            "vertices": self.vertices,
            "materials": self.materials,
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------- lecture
def is_blend(data: bytes) -> bool:
    """Reconnaît un .blend, éventuellement compressé en zstd (Blender 3.0+)."""
    if data[:7] == BLENDER_MAGIC:
        return True
    return data[:4] == ZSTD_MAGIC


def inflate(data: bytes) -> bytes:
    if data[:7] == BLENDER_MAGIC:
        return data
    if data[:4] != ZSTD_MAGIC:
        raise BlendError("Ce n'est pas un fichier .blend (signature absente).")
    try:
        import zstandard
    except ImportError as exc:                     # pragma: no cover - dépend de l'env
        raise BlendError("Ce .blend est compressé en zstd : installe « zstandard » "
                         "(pip install zstandard) pour le lire.") from exc
    try:
        stream = zstandard.ZstdDecompressor().decompressobj()
        out = stream.decompress(data)
    except Exception as exc:                       # zstandard lève plusieurs types
        raise BlendError(f"Décompression zstd impossible : {exc}") from exc
    if out[:7] != BLENDER_MAGIC:
        raise BlendError("Le contenu décompressé n'est pas un .blend.")
    return out


def read_blend(data: bytes, *, axis: str = "y_up", scale: float = 1.0,
               only: str | None = None) -> BlendImport:
    """Extrait les maillages d'un ``.blend`` (aucun Blender requis).

    ``axis`` : ``"y_up"`` convertit de l'axe Z (Blender) vers l'axe Y (Zelda),
    ``"keep"`` garde les coordonnées telles quelles.
    ``only`` : ne garder que l'objet dont le nom contient cette chaîne.
    """
    if axis not in ("y_up", "keep"):
        raise BlendError(f"Axe inconnu : {axis} (attendu « y_up » ou « keep »).")
    blend = _Blend(inflate(data))
    result = BlendImport(blend.version, [])
    mesh_index = blend.dna.index("Mesh")
    object_index = blend.dna.index("Object")
    if object_index is None:
        raise BlendError("Ce .blend ne décrit aucun objet (structure « Object » absente "
                         "de l'ADN) : est-ce bien un fichier de scène Blender ?")

    seen: dict[int, MeshData | None] = {}
    for rec in blend.records("Object"):
        name = rec.text("id.name")
        name = name or "objet"
        obj = BlendObject(name=name)
        if only and only.lower() not in name.lower():
            obj.reason = "filtré"
            result.objects.append(obj)
            continue

        kind = rec.value("type", None)
        if isinstance(kind, int) and kind != 1:
            # OB_MESH vaut 1 ; les autres portent une caméra, une lampe, une courbe…
            obj.reason = f"objet de type {kind} (pas un maillage)"
            result.objects.append(obj)
            continue

        data_ptr = rec.pointer("data")
        obj.materials = _materials_of(blend, rec, data_ptr)
        if not data_ptr:
            obj.reason = "pas de données de maillage (objet vide, caméra, lampe…)"
            result.objects.append(obj)
            continue
        if data_ptr in seen:
            obj.mesh = seen[data_ptr]
            obj.reason = "" if obj.mesh else "maillage partagé sans géométrie"
            result.objects.append(obj)
            continue

        target = blend.block_at(data_ptr)
        if target is None or mesh_index is None:
            obj.reason = "les données pointées ne sont pas un maillage"
            seen[data_ptr] = None
            result.objects.append(obj)
            continue
        mesh_rec = _Rec(blend, target.offset, mesh_index)
        try:
            positions, faces = _geometry(blend, mesh_rec, result.warnings, name)
        except BlendError as exc:
            obj.reason = str(exc)
            seen[data_ptr] = None
            result.objects.append(obj)
            continue

        matrix = _matrix_of(blend, rec)
        points = _transform(positions, matrix)
        points = _orient(points, axis, scale)
        mesh = MeshData(name=name, positions=points, faces=faces)
        seen[data_ptr] = mesh
        obj.mesh = mesh
        result.objects.append(obj)

    if not result.meshes:
        reasons = sorted({obj.reason for obj in result.objects if obj.reason})
        detail = " · ".join(reasons[:4]) or "aucun objet"
        raise BlendError(
            "Aucune géométrie exploitable dans ce .blend "
            f"(version {blend.version}). Raison(s) : {detail}.")
    if blend.version and blend.version.split(".")[0].isdigit():
        major = int(blend.version.split(".")[0])
        if major and major < 2:
            result.warnings.append(
                f"Fichier Blender {blend.version} : très ancien, les maillages ont été "
                "lus au mieux (vérifie l'aperçu avant d'injecter).")
    return result


# --------------------------------------------------------------------------- géométrie
def _geometry(blend: _Blend, mesh: _Rec, warnings: list[str],
              name: str) -> tuple[list[tuple[float, float, float]],
                                   list[tuple[int, ...]]]:
    positions = _positions(blend, mesh, warnings, name)
    faces = _faces(blend, mesh, warnings, name)
    count = len(positions)
    clean: list[tuple[int, ...]] = []
    dropped = 0
    quads = 0
    for face in faces:
        if any(index < 0 or index >= count for index in face):
            dropped += 1
            continue
        if len(set(face)) < 3:
            dropped += 1
            continue
        if len(face) > 3:
            quads += 1
            for i in range(1, len(face) - 1):       # éventail
                triangle = (face[0], face[i], face[i + 1])
                if len(set(triangle)) == 3:
                    clean.append(triangle)
            continue
        clean.append(face)
    if quads:
        warnings.append(f"« {name} » : {quads} face(s) à plus de 3 sommets "
                        "triangulée(s) en éventail.")
    if dropped:
        warnings.append(f"« {name} » : {dropped} face(s) hors bornes ou dégénérée(s) "
                        "écartée(s).")
    if not clean:
        raise BlendError("maillage sans face valide")
    return positions, clean


def _positions(blend: _Blend, mesh: _Rec, warnings: list[str],
               name: str) -> list[tuple[float, float, float]]:
    # 1) Blender 2.5 → 3.6 : tableau de MVert
    if blend.dna.has(mesh.index, "mvert") and blend.dna.has(mesh.index, "totvert"):
        total = int(mesh.value("totvert", 0) or 0)
        ptr = mesh.pointer("mvert")
        if total and ptr:
            block = blend.block_at(ptr)
            mvert = blend.dna.index("MVert")
            if block is not None and mvert is not None:
                stride = blend.dna.size(mvert) or 12
                field = blend.dna.field(mvert, "co")
                if field is None:
                    raise BlendError("structure « MVert » sans champ « co »")
                base = block.offset + field.offset
                out = []
                for i in range(total):
                    out.append(_unpack(blend.endian + "3f", blend.data, base + i * stride))
                return out

    # 2) Blender 3.5+ : attribut « position » dans le CustomData des sommets
    layer = _layer(blend, mesh, "vdata", _POSITION_LAYERS)
    if layer is not None:
        ptr, length = layer
        block = blend.block_at(ptr)
        if block is not None:
            total = min(length or block.size // 12, block.size // 12)
            return [_unpack(blend.endian + "3f", blend.data, block.offset + 12 * i)
                    for i in range(total)]

    # 3) très ancien : MVert avec « co » en 3 floats consécutifs, sans tableau
    raise BlendError("positions introuvables (ni « mvert », ni attribut « position »)")


def _faces(blend: _Blend, mesh: _Rec, warnings: list[str],
           name: str) -> list[tuple[int, ...]]:
    # 1) MPoly + MLoop (2.5 → 4.0)
    if blend.dna.has(mesh.index, "mpoly") and blend.dna.has(mesh.index, "mloop"):
        polys = _polygons(blend, mesh)
        if polys:
            return polys

    # 2) Blender 4.1+ : face_offset_indices + attribut corner_vert
    offsets_ptr = mesh.pointer("face_offset_indices") if blend.dna.has(
        mesh.index, "face_offset_indices") else 0
    corner = _layer(blend, mesh, "ldata", _CORNER_LAYERS)
    if offsets_ptr and corner is not None:
        block = blend.block_at(offsets_ptr)
        if block is not None:
            number = int(mesh.value("faces_num", 0) or mesh.value("totpoly", 0) or 0)
            if not number:
                number = block.size // 4 - 1
            offsets = [_unpack(blend.endian + "i", blend.data, block.offset + 4 * i)[0]
                       for i in range(min(number + 1, block.size // 4))]
            loops = _int_array(blend, corner)
            out = []
            for i in range(len(offsets) - 1):
                start, end = offsets[i], offsets[i + 1]
                if 0 <= start < end <= len(loops):
                    out.append(tuple(loops[start:end]))
            if out:
                warnings.append(f"« {name} » lu via les attributs de Blender 4.1 "
                                "(face_offset_indices).")
                return out

    # 3) attribut corner_vert seul : impossible de deviner les faces
    if corner is not None:
        raise BlendError("faces introuvables : le fichier donne les boucles mais pas "
                         "le découpage des faces (Blender récent) — exporte un OBJ")
    raise BlendError("faces introuvables (ni « mpoly », ni « face_offset_indices »)")


def _polygons(blend: _Blend, mesh: _Rec) -> list[tuple[int, ...]]:
    total = int(mesh.value("totpoly", 0) or 0)
    poly_ptr = mesh.pointer("mpoly")
    loop_ptr = mesh.pointer("mloop")
    if not (total and poly_ptr and loop_ptr):
        return []
    poly_block = blend.block_at(poly_ptr)
    loop_block = blend.block_at(loop_ptr)
    mpoly = blend.dna.index("MPoly")
    mloop = blend.dna.index("MLoop")
    if not (poly_block and loop_block and mpoly is not None and mloop is not None):
        return []
    poly_stride = blend.dna.size(mpoly)
    loop_stride = blend.dna.size(mloop)
    start_field = blend.dna.field(mpoly, "loopstart")
    len_field = blend.dna.field(mpoly, "totloop")
    v_field = blend.dna.field(mloop, "v")
    if not (start_field and len_field and v_field):
        return []
    loops = [_unpack(blend.endian + "i", blend.data,
                     loop_block.offset + i * loop_stride + v_field.offset)[0]
             for i in range(loop_block.size // max(1, loop_stride))]
    out = []
    for i in range(total):
        base = poly_block.offset + i * poly_stride
        start = _unpack(blend.endian + "i", blend.data, base + start_field.offset)[0]
        size = _unpack(blend.endian + "i", blend.data, base + len_field.offset)[0]
        if size < 3 or start < 0 or start + size > len(loops):
            continue
        out.append(tuple(loops[start:start + size]))
    return out


def _layer(blend: _Blend, mesh: _Rec, holder: str,
           names: tuple[str, ...]) -> tuple[int, int] | None:
    """Cherche un attribut CustomData par son nom ; renvoie (pointeur, longueur)."""
    data = mesh.struct(holder)
    if data is None:
        return None
    layer_struct = blend.dna.index("CustomDataLayer")
    if layer_struct is None:
        return None
    total = int(data.value("totlayer", 0) or 0)
    ptr = data.pointer("layers")
    block = blend.block_at(ptr) if ptr else None
    if block is None or not total:
        return None
    stride = blend.dna.size(layer_struct) or 1
    name_field = blend.dna.field(layer_struct, "name")
    data_field = blend.dna.field(layer_struct, "data")
    if name_field is None or data_field is None:
        return None
    for i in range(min(total, block.size // stride)):
        base = block.offset + i * stride
        raw = blend.data[base + name_field.offset:base + name_field.offset + name_field.size]
        label = raw.split(b"\x00")[0].decode("latin-1").strip()
        if label.strip(".").lower() not in {n.strip(".").lower() for n in names}:
            continue
        fmt = "Q" if blend.ptr == 8 else "I"
        address = _unpack(blend.endian + fmt, blend.data, base + data_field.offset)[0]
        target = blend.block_at(address)
        if target is None:
            return None
        return address, target.size
    return None


def _int_array(blend: _Blend, layer: tuple[int, int]) -> list[int]:
    address, length = layer
    block = blend.block_at(address)
    if block is None:
        return []
    count = min(length, block.size) // 4
    return [_unpack(blend.endian + "i", blend.data, block.offset + 4 * i)[0]
            for i in range(count)]


def _materials_of(blend: _Blend, rec: _Rec, data_ptr: int) -> list[str]:
    names: list[str] = []
    total = int(rec.value("totcol", 0) or 0)
    if total:
        addresses = rec.ptr_array("mat")[:total]
    else:
        addresses = []
    for address in addresses:
        block = blend.block_at(address) if address else None
        if block is None:
            continue
        index = blend.dna.index("Material")
        if index is None:
            break
        mat = _Rec(blend, block.offset, index)
        label = mat.text("id.name")
        if label:
            names.append(label)
    return names


def _matrix_of(blend: _Blend, rec: _Rec) -> tuple[float, ...]:
    for field_name in ("object_to_world", "obmat", "matrix_world"):
        matrix = rec.matrix(field_name)
        if matrix:
            return matrix
    # très ancienne disposition : loc / rot (euler XYZ) / size
    loc = rec.value("loc", None)
    rot = rec.value("rot", None)
    size = rec.value("size", None)
    if loc and rot and size:
        return _compose(loc, rot, size)
    return _IDENTITY


_IDENTITY = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)


def _compose(loc, rot, size) -> tuple[float, ...]:
    import math
    cx, cy, cz = (math.cos(rot[0]), math.cos(rot[1]), math.cos(rot[2]))
    sx, sy, sz = (math.sin(rot[0]), math.sin(rot[1]), math.sin(rot[2]))
    # R = Rx * Ry * Rz (convention Euler XYZ de Blender)
    r = [
        [cy * cz, -cy * sz, sy],
        [sx * sy * cz + cx * sz, -sx * sy * sz + cx * cz, -sx * cy],
        [-cx * sy * cz + sx * sz, cx * sy * sz + sx * cz, cx * cy],
    ]
    out = [
        r[0][0] * size[0], r[0][1] * size[1], r[0][2] * size[2], loc[0],
        r[1][0] * size[0], r[1][1] * size[1], r[1][2] * size[2], loc[1],
        r[2][0] * size[0], r[2][1] * size[1], r[2][2] * size[2], loc[2],
        0.0, 0.0, 0.0, 1.0,
    ]
    return tuple(out)


def _transform(points, matrix: tuple[float, ...]):
    if matrix == _IDENTITY:
        return list(points)
    out = []
    for x, y, z in points:
        out.append((
            matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
            matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
            matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
        ))
    return out


def _orient(points, axis: str, scale: float):
    if axis == "keep" and scale == 1.0:
        return list(points)
    out = []
    for x, y, z in points:
        if axis == "y_up":
            value = (x * scale, z * scale, -y * scale)
        else:
            value = (x * scale, y * scale, z * scale)
        out.append(value)
    return out
