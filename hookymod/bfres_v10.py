"""BFRES version 10 : lecture de la géométrie, pilotée par les données.

Il n'existe **aucune documentation publique** du BFRES 10.x.  Ce module ne suit
donc pas un cahier des charges : il lit les tables que le fichier contient
lui-même (table d'attributs de sommets), puis **vérifie** chaque étape avant de
livrer une géométrie.  Rien n'est codé en dur pour un modèle particulier.

Structure mesurée sur les fichiers du jeu (papillon + 4 ennemis)
---------------------------------------------------------------
* en-tête : ``FRES``, version ``u32`` à ``0x08`` (``0x000A0400`` = 10.4),
  BOM ``u16`` à ``0x0C`` (``0xFFFE`` = petit-boutiste), ``FMDL`` en ``u64`` à
  ``0x28``, base des tampons GPU en ``u64`` à ``0x170`` (0x4000, 0xE000,
  0x15000… selon le modèle) ;
* ``FMDL`` : nom ``+0x08``, ``FVTX`` ``+0x20``, matériaux ``+0x38`` ;
* ``FVTX`` (un par forme) : table d'attributs ``u64 +0x08``, table des LOD
  ``u64 +0x10``, fin des données ``u64 +0x18``, **tampon de sommets** ``u32
  +0x48`` (relatif à la base), et ``u32 +0x4C`` dont l'octet bas donne le
  **nombre d'attributs** ;
* attribut (16 octets) : référence de chaîne ``+0`` (les chaînes sont
  préfixées par leur longueur ``u16``), format ``u32 +8``, offset ``u32 +12``
  dans l'enregistrement ;
* les tampons de sommets sont rangés **bout à bout** : le nombre de sommets
  d'une forme est l'écart avec le tampon suivant divisé par le pas, ce qui se
  vérifie au lieu de se deviner ;
* formats rencontrés : ``0x1805`` = position ``3 x f32`` (12 o), ``0x1505`` =
  position ``3 x f16`` (6 o), ``0x0E02`` = normale/tangente sur 4 o,
  ``0x1201``/``0x1202`` = UV ``2 x u16``, ``0x0B0x``/``0x090x``/``0x02xx`` =
  peaux (indices/weights).

Tout ce qui n'est pas vérifiable est refusé : une géométrie douteuse ne doit
jamais arriver jusqu'à Blender ou au viewer.
"""

from __future__ import annotations

import math
import struct
import sys
from array import array
from dataclasses import dataclass, field

from . import native
from .mesh import MeshData

#: valeurs historiques (papillon) conservées comme repli
STRIDE = 24
POSITION_OFFSET = 0
UV_OFFSET = 16
#: découpage des sous-maillages dans le tampon d'indices
SUBMESH_ALIGN = 0x1000

#: en-tête
HEADER_VERSION = 0x08
HEADER_MODEL = 0x28
HEADER_BUFFER_BASE = 0x170
#: FMDL
FMDL_NAME = 0x08
FMDL_VERTEX_INFO = 0x20
FMDL_MATERIALS = 0x38
#: FVTX
FVTX_ATTRIBUTES = 0x08
FVTX_LODS = 0x10
FVTX_DATA_END = 0x18
FVTX_BUFFER = 0x48
FVTX_FLAGS = 0x4C
#: table d'attributs
ATTRIBUTE_SIZE = 16

#: tailles connues des formats rencontrés (octets par sommet)
FORMAT_SIZES: dict[int, int] = {
    0x1805: 12,   # position 3 x f32
    0x1505: 6,    # position 3 x f16 (rangée sur 8 dans l'enregistrement)
    0x1802: 12,   # position 2 x f32 + 1 x f32
    0x1502: 6,
    0x0E02: 4,    # normale / tangente compactée
    0x1201: 4,    # UV 2 x u16
    0x1202: 4,
    0x0B03: 4,    # indices de peaux
    0x0903: 4,
    0x0203: 4,
    0x0B01: 2,    # poids de peaux
    0x0901: 2,
}

#: préfixes de noms d'attributs -> rôle
_KIND_PREFIXES: tuple[tuple[str, str], ...] = (
    ("_p", "position"),
    ("_n", "normal"),
    ("_t", "tangent"),
    ("_u", "uv"),
    ("_c", "color"),
    ("_i", "index"),
    ("_w", "weight"),
)
# Le lecteur natif reproduit ces règles au moment de la compilation. Si un
# outil externe les modifie à l'exécution, conserver le comportement Python.
_NATIVE_SHAPE_FORMATS = FORMAT_SIZES.copy()
_NATIVE_SHAPE_PREFIXES = _KIND_PREFIXES


@dataclass
class VertexAttribute:
    """Un attribut de sommet tel que déclaré par le fichier."""

    name: str
    format: int
    offset: int
    size: int
    kind: str = "unknown"

    def describe(self) -> str:
        return f"{self.name}@0x{self.offset:X} (f{self.format:X}, {self.size} o)"


@dataclass
class VertexShape:
    """Une forme (un ``FVTX``) : ses attributs et son tampon de sommets."""

    offset: int = 0
    attributes: list[VertexAttribute] = field(default_factory=list)
    stride: int = STRIDE
    buffer_offset: int = 0        # relatif à la base des tampons
    vertex_offset: int = 0        # absolu dans le fichier
    vertex_count: int = 0
    data_end: int = 0
    flags: int = 0

    def attribute(self, kind: str) -> VertexAttribute | None:
        for attribute in self.attributes:
            if attribute.kind == kind:
                return attribute
        return None

    def summary(self) -> str:
        return (f"forme @0x{self.offset:X} : {self.vertex_count} sommets x "
                f"{self.stride} o @0x{self.vertex_offset:X} "
                f"({', '.join(a.name for a in self.attributes)})")


@dataclass
class V10Report:
    """Ce que la lecture a réellement trouvé (affiché, jamais deviné)."""

    version: int = 0            # u32 complet (0x000A0400)
    major: int = 0
    minor: int = 0
    model_name: str = ""
    material_names: list[str] = field(default_factory=list)
    material_textures: dict[str, list[str]] = field(default_factory=dict)
    structured: bool = False
    buffer_base: int = 0
    vertex_offset: int = 0
    stride: int = STRIDE
    vertex_count: int = 0
    index_offset: int = 0
    index_count: int = 0
    submeshes: int = 0
    dropped: int = 0            # sous-maillages refusés faute de validation
    uv: bool = False
    attributes: list[str] = field(default_factory=list)
    shapes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = [
            f"BFRES {self.major}.{self.minor} « {self.model_name or '(sans nom)'} »",
            f"  base des tampons : 0x{self.buffer_base:X}",
            f"  sommets          : {self.vertex_count} × {self.stride} o à 0x{self.vertex_offset:X}",
            f"  indices          : {self.index_count} u16 à 0x{self.index_offset:X}",
            f"  sous-maillages   : {self.submeshes}",
            f"  attributs        : {', '.join(self.attributes) or '(aucun)'}",
            f"  formes           : {len(self.shapes)}",
            f"  UV               : {'oui' if self.uv else 'non (non décodées)'}",
        ]
        lines += [f"  ⚠ {w}" for w in self.warnings]
        return "\n".join(lines)


# --------------------------------------------------------------------------- outils
def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def _valid_pointer(value: int, data: bytes, align: int = 1) -> bool:
    return bool(value) and value % align == 0 and 0 < value < len(data)


def _string_at(data: bytes, offset: int) -> str:
    """Chaîne préfixée par sa longueur (u16), comme dans ``_STR``."""
    if not (0 < offset < len(data) - 2):
        return ""
    length = _u16(data, offset)
    if not (0 < length < 64) or offset + 2 + length > len(data):
        return ""
    raw = data[offset + 2:offset + 2 + length]
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return ""
    return text if text.isprintable() else ""


def _kind_of(name: str, fmt: int) -> str:
    for prefix, kind in _KIND_PREFIXES:
        if name.startswith(prefix):
            return kind
    if fmt in (0x1805, 0x1505, 0x1802, 0x1502):
        return "position"
    return "unknown"


def _round_up(value: int, multiple: int = 4) -> int:
    return (value + multiple - 1) // multiple * multiple


# --------------------------------------------------------------------------- attributs
def read_attributes(data: bytes, array_offset: int, count: int = 0) -> list[VertexAttribute]:
    """Lit la table d'attributs d'un ``FVTX``.

    La taille d'un attribut se déduit de l'offset du suivant (donc du fichier
    lui-même, pas d'une table externe) ; pour le dernier on retombe sur la
    taille connue de son format, arrondie à 4 octets.
    """
    if not (0 < array_offset < len(data) - ATTRIBUTE_SIZE):
        return []
    raw: list[VertexAttribute] = []
    limit = count if count else 32
    for index in range(min(limit, 32)):
        entry = array_offset + ATTRIBUTE_SIZE * index
        if entry + ATTRIBUTE_SIZE > len(data):
            break
        reference = _u32(data, entry)
        name = _string_at(data, reference)
        if not name:
            break
        fmt = _u32(data, entry + 8)
        offset = _u32(data, entry + 12)
        raw.append(VertexAttribute(name=name, format=fmt, offset=offset,
                                   size=0, kind=_kind_of(name, fmt)))
    if not raw:
        return []
    for index, attribute in enumerate(raw[:-1]):
        following = raw[index + 1].offset
        size = following - attribute.offset
        if size <= 0:
            return []
        attribute.size = size
    last = raw[-1]
    known = FORMAT_SIZES.get(last.format)
    last.size = known if known else 4
    return raw


def _stride_of(attributes: list[VertexAttribute]) -> int:
    """Pas d'enregistrement : fin du dernier attribut, arrondie à 4 octets."""
    if not attributes:
        return 0
    end = max(a.offset + (a.size or 4) for a in attributes)
    return _round_up(end, 4)


# --------------------------------------------------------------------------- formes
def _looks_like_shape(data: bytes, offset: int, base: int) -> VertexShape | None:
    """Valide un candidat ``FVTX`` : attributs lisibles + tampon dans le fichier."""
    if offset + FVTX_FLAGS + 4 > len(data):
        return None
    array_offset = _u64(data, offset + FVTX_ATTRIBUTES)
    if not (0 < array_offset < len(data) - ATTRIBUTE_SIZE):
        return None
    flags = _u32(data, offset + FVTX_FLAGS)
    declared = flags & 0xFF
    buffer_relative = _u32(data, offset + FVTX_BUFFER)
    if not buffer_relative or base + buffer_relative >= len(data):
        return None
    attributes = read_attributes(data, array_offset, declared)
    if len(attributes) < 2:
        return None
    if not any(a.kind == "position" for a in attributes):
        return None
    if declared and len(attributes) != declared:
        return None
    shape = VertexShape(
        offset=offset,
        attributes=attributes,
        stride=_stride_of(attributes),
        buffer_offset=buffer_relative,
        vertex_offset=base + buffer_relative,
        data_end=_u64(data, offset + FVTX_DATA_END),
        flags=flags,
    )
    return shape if shape.stride >= 4 else None


def find_shapes(data: bytes, base: int) -> list[VertexShape]:
    """Toutes les formes du fichier, dans l'ordre des tampons.

    On balaye les signatures ``FVTX`` puis on ne garde que celles qui passent
    la validation ci-dessus. Cela écarte beaucoup de fausses signatures, sans
    garantir qu'une table apparemment cohérente décrive la géométrie réelle.
    """
    if (type(data) is bytes and FORMAT_SIZES == _NATIVE_SHAPE_FORMATS
            and _KIND_PREFIXES == _NATIVE_SHAPE_PREFIXES and native.available()):
        parsed = native.bfres_find_shapes(data, base)
        if parsed is not None:
            return [VertexShape(
                offset=offset, stride=stride, buffer_offset=relative,
                vertex_offset=vertex, vertex_count=count, data_end=data_end, flags=flags,
                attributes=[VertexAttribute(name, fmt, attr_offset, size, _kind_of(name, fmt))
                            for name, fmt, attr_offset, size in attributes])
                    for offset, stride, relative, vertex, count, data_end, flags, attributes in parsed]
    shapes: dict[int, VertexShape] = {}
    start = 0
    while True:
        found = data.find(b"FVTX", start)
        if found < 0:
            break
        start = found + 4
        if found % 4:
            continue
        shape = _looks_like_shape(data, found, base)
        if shape and shape.vertex_offset not in shapes:
            shapes[shape.vertex_offset] = shape
    ordered = sorted(shapes.values(), key=lambda s: s.vertex_offset)
    # le nombre de sommets se déduit de l'écart entre tampons voisins
    for index, shape in enumerate(ordered):
        following = ordered[index + 1].vertex_offset if index + 1 < len(ordered) else len(data)
        available = following - shape.vertex_offset
        if shape.vertex_count == 0:
            shape.vertex_count = max(0, available // max(1, shape.stride))
    return ordered


# --------------------------------------------------------------------------- tampons
def _dist2(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """Distance au carré, sans appel de fonction externe.

    ``math.dist`` coûtait 0,65 s à lui seul sur un modèle de 80 000 faces
    (4,7 millions d'appels pour 25 modèles) : ici on compare des carrés.
    """
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return dx * dx + dy * dy + dz * dz


def _coherence(positions: list[tuple[float, float, float]],
               triangles: list[tuple[int, int, int]]) -> float:
    """Rapport « longueur moyenne des arêtes / distance moyenne au hasard ».

    Proche de 0 pour une vraie surface (les sommets d'un triangle sont voisins),
    proche de 1 pour du bruit. C'est la vérification centrale : elle attrape un
    mauvais décalage ou un mauvais pas d'enregistrement.
    """
    if not positions or not triangles:
        return 1.0
    count = len(positions)
    step = max(1, len(triangles) // 400)
    local = total = 0.0
    pairs = 0
    for index in range(0, len(triangles), step):
        a, b, c = triangles[index]
        try:
            pa, pb, pc = positions[a], positions[b], positions[c]
        except IndexError:
            continue
        local += math.sqrt(_dist2(pa, pb)) + math.sqrt(_dist2(pb, pc))
        pairs += 2
    if not pairs:
        return 1.0
    local /= pairs
    far = total = 0.0
    stride = max(1, count // 200)
    for i in range(0, count, stride):
        j = (i * 7 + 13) % count
        far += math.sqrt(_dist2(positions[i], positions[j]))
        total += 1
    far /= max(1, total)
    return local / far if far > 0 else 1.0


#: cohérence maximale d'un bloc d'indices pour être retenu (0 = parfait)
COHERENCE_MAX = 0.33
#: rapport arête médiane / diagonale au-delà duquel un bloc est refusé
EDGE_RATIO_MAX = 0.15
#: rapport arête / diagonale au-delà duquel un triangle est écarté
OVERSIZED_RATIO = 0.15

# L'ABI doit copier les positions et les indices. Pour une petite suite ou un
# énorme nuage associé à peu de triangles, cette copie coûte davantage que le
# filtre Python ; la décision ne change jamais les critères géométriques.
_NATIVE_FILTER_MIN = 512
_NATIVE_POSITION_RATIO = 4


def _use_native_filter(positions, triangles) -> bool:
    return (native.available() and len(triangles) >= _NATIVE_FILTER_MIN
            and len(positions) <= _NATIVE_POSITION_RATIO * len(triangles))


def _diagonal(positions, step_max: int = 4000, cut: float = 0.02) -> float:
    """Diagonale **robuste** du nuage de points (2 % – 98 % par axe).

    Le minimum/maximum absolu suffisait à valider un tampon de sommets, mais pas
    à écarter un triangle aberrant : quelques sommets faux gonflaient la
    diagonale, donc le seuil de 15 % devenait énorme et **rien** n'était
    rejeté — d'où les longues traînées horizontales à l'écran. Les centiles
    ignorent ces points isolés.
    """
    if not positions:
        return 0.0
    step = max(1, len(positions) // step_max)
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for index in range(0, len(positions), step):
        x, y, z = positions[index]
        xs.append(x)
        ys.append(y)
        zs.append(z)

    def span(values: list[float]) -> float:
        values.sort()
        last = len(values) - 1
        low = values[min(last, int(last * cut))]
        high = values[max(0, min(last, int(last * (1.0 - cut))))]
        return max(0.0, high - low)

    dx, dy, dz = span(xs), span(ys), span(zs)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _edge_ratio(positions, triangles, diagonal: float) -> float:
    """Longueur médiane des arêtes rapportée à la diagonale du modèle.

    Un triangle juste est petit devant le modèle ; des arêtes qui traversent
    tout le volume trahissent des indices appartenant à une autre forme ou à
    un autre niveau de détail (rendu en « spaghettis »).
    """
    if not positions or not triangles or diagonal <= 0:
        return 1.0
    step = max(1, len(triangles) // 200)
    lengths = []
    for index in range(0, len(triangles), step):
        a, b, c = triangles[index]
        try:
            pa, pb, pc = positions[a], positions[b], positions[c]
        except IndexError:
            return 1.0
        lengths.append(math.sqrt(_dist2(pa, pb)))
        lengths.append(math.sqrt(_dist2(pb, pc)))
    if not lengths:
        return 1.0
    lengths.sort()
    return lengths[len(lengths) // 2] / diagonal


def _drop_oversized(positions, triangles, diagonal: float,
                    limit: float = OVERSIZED_RATIO) -> list[tuple[int, int, int]]:
    """Retire les triangles dont une arête traverse tout le modèle.

    Ces triangles viennent d'indices appartenant à une autre forme (ou à un
    autre tampon) : ils dessinent de longues pointes au lieu d'une surface.
    On garde les triangles dont la plus grande arête reste sous 15 % de la
    diagonale du modèle.
    """
    if not positions or diagonal <= 0:
        return triangles
    threshold = diagonal * limit
    if _use_native_filter(positions, triangles):
        filtered = native.filter_triangles(positions, triangles, threshold * threshold)
        if filtered is not None:
            return filtered
    kept: list[tuple[int, int, int]] = []
    for a, b, c in triangles:
        try:
            pa, pb, pc = positions[a], positions[b], positions[c]
        except IndexError:
            continue
        limit = threshold * threshold
        if (_dist2(pa, pb) > limit or _dist2(pb, pc) > limit
                or _dist2(pa, pc) > limit):
            continue
        kept.append((a, b, c))
    return kept


def _drop_outliers(positions, triangles, factor: float = 8.0,
                   sample: int = 4000) -> list[tuple[int, int, int]]:
    """Retire les triangles dont une arête dépasse ``factor`` × l'arête médiane.

    Un seuil exprimé en pourcentage de la diagonale du modèle dépend de la
    taille du modèle : sur une armure, 15 % laisse passer des triangles qui
    relient deux points distants et dessinent de longues traînées à l'écran.
    L'arête **médiane** est une échelle locale : un triangle normal en est
    proche, un triangle parasite en est dix fois plus loin.
    """
    if not triangles or not positions:
        return triangles
    step = max(1, len(triangles) // sample)
    lengths: list[float] = []
    for index in range(0, len(triangles), step):
        a, b, c = triangles[index]
        try:
            pa, pb, pc = positions[a], positions[b], positions[c]
        except IndexError:
            continue
        lengths.append(math.sqrt(_dist2(pa, pb)))
        lengths.append(math.sqrt(_dist2(pb, pc)))
        lengths.append(math.sqrt(_dist2(pa, pc)))
    if not lengths:
        return triangles
    lengths.sort()
    median = lengths[len(lengths) // 2]
    if median <= 0.0:
        return triangles
    limit = median * factor
    limit2 = limit * limit
    if _use_native_filter(positions, triangles):
        filtered = native.filter_triangles(positions, triangles, limit2)
        if filtered is not None:
            return filtered
    kept: list[tuple[int, int, int]] = []
    for a, b, c in triangles:
        try:
            pa, pb, pc = positions[a], positions[b], positions[c]
        except IndexError:
            continue
        if (_dist2(pa, pb) > limit2 or _dist2(pb, pc) > limit2
                or _dist2(pa, pc) > limit2):
            continue
        kept.append((a, b, c))
    return kept


def _decode_positions_f32(data: bytes, start: int, count: int, stride: int,
                          offset: int) -> list[tuple[float, float, float]] | None:
    """Positions ``3 x f32`` lues via :mod:`array` (4 à 5 fois plus vite).

    Mêmes garde-fous que la version générique : les 128 premiers sommets fixent
    l'échelle de référence et le premier enregistrement aberrant arrête la
    lecture (un tampon qui déborde ne doit pas inventer des millions de sommets).
    """
    step = stride // 4
    first = offset // 4
    if step < 3 or count < 1 or start < 0 or start + stride > len(data):
        return None
    available = (len(data) - start) // 4
    limit = min(count, available // step)
    if limit < 1:
        return None
    words = array("f")
    words.frombytes(data[start:start + limit * step * 4])
    if sys.byteorder != "little":
        words.byteswap()
    total = len(words)
    positions: list[tuple[float, float, float]] = []
    reference = 0.0
    isfinite = math.isfinite
    for index in range(limit):
        base = index * step + first
        if base + 2 >= total:
            break
        x = words[base]
        y = words[base + 1]
        z = words[base + 2]
        if not (isfinite(x) and isfinite(y) and isfinite(z)):
            break
        scale = max(abs(x), abs(y), abs(z))
        if index < 128:
            if scale > reference:
                reference = scale
        elif scale > max(reference * 50.0, 1e-4):
            break
        positions.append((x, y, z))
    return positions


def _decode_positions(data: bytes, start: int, count: int, stride: int,
                      offset: int = 0, fmt: int = 0x1505):
    """Positions d'un tampon, selon le format déclaré par la table d'attributs.

    Le compte demandé vient de l'écart entre tampons : quand celui-ci est
    anormal (plusieurs mégaoctets), le tampon déborde sur autre chose. On
    s'arrête donc au premier enregistrement incohérent, l'échelle de référence
    étant prise sur le début du tampon (les 128 premiers sommets).
    """
    if type(count) is int and count >= 128 and native.available():
        decoded = native.decode_positions(data, start, count, stride, offset, fmt)
        if decoded is not None:
            return decoded
    if fmt == 0x1805 and stride % 4 == 0 and offset % 4 == 0:
        fast = _decode_positions_f32(data, start, count, stride, offset)
        if fast is not None:
            return fast
    unpack = "<3f" if fmt == 0x1805 else "<3e"
    positions: list[tuple[float, float, float]] = []
    reference = 0.0
    for index in range(count):
        try:
            x, y, z = struct.unpack_from(unpack, data, start + index * stride + offset)
        except struct.error:
            break
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            break
        scale = max(abs(x), abs(y), abs(z))
        if index < 128:
            reference = max(reference, scale)
        elif scale > max(reference * 50.0, 1e-4):
            break
        positions.append((x, y, z))
    return positions


def _decode_positions_of(data: bytes, shape: VertexShape) -> list[tuple[float, float, float]]:
    attribute = shape.attribute("position")
    if attribute is None:
        return []
    return _decode_positions(data, shape.vertex_offset, shape.vertex_count,
                             shape.stride, attribute.offset, attribute.format)


def _snorm(bits: int, width: int) -> float:
    half = 1 << (width - 1)
    value = bits & ((1 << width) - 1)
    if value >= half:
        value -= 1 << width
    return value / (half - 1)


def _decode_normals(data: bytes, shape: VertexShape, count: int) -> list[tuple[float, float, float]]:
    """Normales, quel que soit le compactage (10_10_10_2, 4 x s8, f16)."""
    attribute = shape.attribute("normal")
    if attribute is None:
        return []
    start = shape.vertex_offset + attribute.offset
    stride = shape.stride
    candidates: list[tuple[str, list[tuple[float, float, float]]]] = []

    if attribute.size == 4:
        packed: list[tuple[float, float, float]] = []
        for index in range(count):
            try:
                bits = _u32(data, start + index * stride)
            except struct.error:
                packed = []
                break
            packed.append((_snorm(bits, 10), _snorm(bits >> 10, 10), _snorm(bits >> 20, 10)))
        candidates.append(("10_10_10_2", packed))
        octets: list[tuple[float, float, float]] = []
        for index in range(count):
            try:
                x, y, z = struct.unpack_from("<3b", data, start + index * stride)
            except struct.error:
                octets = []
                break
            octets.append((x / 127.0, y / 127.0, z / 127.0))
        candidates.append(("4x s8", octets))
    elif attribute.size in (6, 8):
        halves: list[tuple[float, float, float]] = []
        for index in range(count):
            try:
                halves.append(struct.unpack_from("<3e", data, start + index * stride))
            except struct.error:
                halves = []
                break
        candidates.append(("3x f16", halves))

    best: list[tuple[float, float, float]] = []
    best_score = 0.0
    for _, values in candidates:
        if not values:
            continue
        sample = values[: min(400, len(values))]
        good = 0
        for x, y, z in sample:
            length = math.sqrt(x * x + y * y + z * z)
            if 0.8 <= length <= 1.25:
                good += 1
        score = good / len(sample)
        if score > best_score:
            best, best_score = values, score
    return best if best_score >= 0.9 else []


def _uv_plausible(positions, uvs, triangles) -> bool:
    """Une UV correcte garde une densité de texels à peu près constante.

    On corrèle l'aire 3D de chaque triangle avec son aire UV : une vraie
    projection donne une corrélation franche (> 0,3), des octets quelconques
    donnent 0.
    """
    if not uvs or not triangles:
        return False
    samples = triangles[:: max(1, len(triangles) // 300)]
    xs, ys = [], []
    for a, b, c in samples:
        try:
            pa, pb, pc = positions[a], positions[b], positions[c]
            (ax, ay), (bx, by), (cx2, cy2) = uvs[a], uvs[b], uvs[c]
        except IndexError:
            continue
        u = [pb[i] - pa[i] for i in range(3)]
        v = [pc[i] - pa[i] for i in range(3)]
        cx, cy, cz = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                      u[0] * v[1] - u[1] * v[0])
        area3 = 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
        area2 = 0.5 * abs((bx - ax) * (cy2 - ay) - (cx2 - ax) * (by - ay))
        if area3 > 0 and area2 > 0:
            xs.append(area3)
            ys.append(area2)
    if len(xs) < 20:
        return False
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return den > 0 and num / den > 0.3


def _decode_uvs(data: bytes, shape: VertexShape, count: int, positions,
                triangles) -> tuple[list[tuple[float, float]], str]:
    """UV : ``2 x u16`` ou ``2 x f16``, choisi par validation (jamais au hasard)."""
    attribute = shape.attribute("uv")
    if attribute is None:
        return [], ""
    start = shape.vertex_offset + attribute.offset
    stride = shape.stride
    candidates: list[tuple[str, list[tuple[float, float]]]] = []

    halves: list[tuple[float, float]] = []
    for index in range(count):
        try:
            halves.append(struct.unpack_from("<2e", data, start + index * stride))
        except struct.error:
            halves = []
            break
    candidates.append(("2 x f16", halves))

    shorts: list[tuple[float, float]] = []
    for index in range(count):
        try:
            u, v = struct.unpack_from("<2H", data, start + index * stride)
        except struct.error:
            shorts = []
            break
        shorts.append((u / 65535.0, v / 65535.0))
    candidates.append(("2 x u16", shorts))

    for label, values in candidates:
        if not values:
            continue
        sample = values[: min(600, len(values))]
        inside = sum(1 for u, v in sample if -0.05 <= u <= 1.05 and -0.05 <= v <= 1.05)
        if inside / len(sample) < 0.9:
            continue
        if _uv_plausible(positions, values, triangles):
            return values, label
    return [], ""


def _detect_stride(data: bytes, start: int, end: int, fallback: int = STRIDE) -> int:
    """Pas d'enregistrement des sommets, par autocorrélation des octets.

    Conservé comme **repli** : quand la table d'attributs est illisible, ce
    détecteur donne une indication, et l'appelant décide de faire confiance ou
    non (le rapport le signale).
    """
    window = data[start:min(end, start + 0x4000)]
    if len(window) < 128:
        return fallback
    scores: dict[int, float] = {}
    for lag in range(8, 65):
        same = sum(1 for k in range(0, len(window) - lag, 7) if window[k] == window[k + lag])
        scores[lag] = same / max(1, len(range(0, len(window) - lag, 7)))
    best_lag = max(scores, key=lambda lag: scores[lag])
    best = scores[best_lag]
    if best <= 0.15:
        return fallback
    for lag in range(8, best_lag):
        if best_lag % lag == 0 and scores[lag] >= best - 0.02:
            return lag
    return best_lag


# --------------------------------------------------------------------------- indices
def _block_triangles(data: bytes, start: int, limit: int, vertex_count: int):
    """Triangles d'un bloc d'indices, jusqu'au remplissage ou à l'incohérence."""
    triangles: list[tuple[int, int, int]] = []
    offset = start
    while offset + 6 <= limit:
        a, b, c = struct.unpack_from("<3H", data, offset)
        offset += 6
        if max(a, b, c) >= vertex_count:
            break
        if a == b or b == c or a == c:      # remplissage / fin de sous-maillage
            break
        triangles.append((a, b, c))
    return triangles


def _index_triangles(data: bytes, start: int, end: int, vertex_count: int):
    """Triangles du tampon d'indices, découpés en blocs alignés sur 0x1000."""
    groups: list[list[tuple[int, int, int]]] = []
    for block in range(start, end, SUBMESH_ALIGN):
        limit = min(block + SUBMESH_ALIGN, end)
        triangles = _block_triangles(data, block, limit, vertex_count)
        if triangles:
            groups.append(triangles)
    return groups


def _scan_block(data: bytes, start: int, stop: int, vertex_count: int,
                maximum: int = 2048) -> list[tuple[int, int, int]]:
    """Triangles d'un bloc aligné, en sautant le remplissage de tête.

    Un sous-maillage commence à une frontière de 0x1000 octets : les premiers
    octets du bloc peuvent être du remplissage, d'où le balayage par pas de 2
    octets avant le premier triangle exploitable. On s'arrête au premier
    triangle dégénéré (fin du sous-maillage) ou hors bornes.
    """
    offset = start
    triangles: list[tuple[int, int, int]] = []
    while offset + 6 <= stop:
        a, b, c = struct.unpack_from("<3H", data, offset)
        if max(a, b, c) >= vertex_count or a == b or b == c or a == c:
            if triangles:
                break
            offset += 2
            continue
        triangles.append((a, b, c))
        offset += 6
        if len(triangles) >= maximum:
            break
    return triangles


def _index_words(data: bytes, base: int, stop: int) -> "array":
    """Tampon d'indices comme mots ``u16``, une seule fois (au lieu de 6 lectures)."""
    raw = data[base:stop]
    words = array("H")
    words.frombytes(raw[:len(raw) - (len(raw) % 2)])
    if sys.byteorder != "little":
        words.byteswap()
    return words


def _segment_indices(words, bounds: list[int],
                     minimum: int = 4) -> list[tuple[int, int, list]]:
    """Découpe le tampon d'indices en sous-maillages **sans supposer un alignement**.

    L'ancienne version ne regardait que des blocs de 0x1000 octets et
    s'arrêtait au premier triangle douteux : un sous-maillage qui commençait au
    milieu d'un bloc (ou juste après du remplissage) était perdu — d'où une épée
    exportée en morceaux. Ici on avance mot par mot : dès qu'un triangle est
    valide pour une forme, on prolonge la suite tant qu'elle reste valide, ce qui
    retrouve un sous-maillage où qu'il commence.

    Les formes sont essayées de la plus contrainte (moins de sommets) à la plus
    large : celle qui accepte le moins d'indices est la plus précise.

    :return: liste de ``(indice de forme, position en mots, triangles)``.
    """
    if len(words) >= 128 and native.available():
        runs = native.segment_indices(words, bounds, minimum)
        if runs is not None:
            return runs
    ordered = sorted(range(len(bounds)), key=lambda index: bounds[index])
    total = len(words)
    runs: list[tuple[int, int, list]] = []
    cursor = 0
    while cursor + 2 < total:
        accepted = None
        for index in ordered:
            bound = bounds[index]
            if bound < 3:
                continue
            a = words[cursor]
            if a >= bound:
                continue
            b = words[cursor + 1]
            c = words[cursor + 2]
            if b >= bound or c >= bound or a == b or b == c or a == c:
                continue
            triangles = [(a, b, c)]
            step = cursor + 3
            while step + 2 < total:
                x = words[step]
                y = words[step + 1]
                z = words[step + 2]
                if (x >= bound or y >= bound or z >= bound
                        or x == y or y == z or x == z):
                    break
                triangles.append((x, y, z))
                step += 3
            if len(triangles) >= minimum:
                accepted = (index, cursor, triangles, step)
                break
        if accepted is None:
            cursor += 1
            continue
        index, start, triangles, end = accepted
        runs.append((index, start, triangles))
        cursor = end
    return runs


def _merge_runs(runs: list[tuple[int, int, list]],
                gap: int = 12) -> list[tuple[int, int, list]]:
    """Recolle les morceaux voisins d'une même forme (même objet dans Blender)."""
    merged: list[tuple[int, int, list]] = []
    for index, start, triangles in runs:
        if merged:
            previous_index, previous_start, previous_tris = merged[-1]
            previous_end = previous_start + 3 * len(previous_tris)
            if previous_index == index and start - previous_end <= gap:
                merged[-1] = (index, previous_start,
                              previous_tris + triangles)
                continue
        merged.append((index, start, list(triangles)))
    return merged


# --------------------------------------------------------------------------- lecture
def parse(data: bytes, *, require_model_shape: bool = False) -> tuple[list[MeshData], V10Report] | None:
    """Maillages d'un BFRES 10.x, ou ``None`` si la structure n'est pas reconnue.

    Le retour est volontairement « tout ou rien » : soit les tables du fichier
    vérifient leurs hypothèses, soit on ne renvoie rien et l'appelant dit
    pourquoi. Sans dictionnaire de modèles exploitable, l'appelant peut exiger
    que le FMDL référence lui-même un FVTX validé : le balayage seul pourrait
    autrement ramasser une table de sommets orpheline.
    """
    if len(data) < 0x200 or data[:4] != b"FRES":
        return None
    report = V10Report(version=_u32(data, HEADER_VERSION))
    report.major = (report.version >> 16) & 0xFFFF
    report.minor = (report.version >> 8) & 0xFF
    if report.major != 10:
        return None
    # Ce lecteur ne traverse pour l'instant qu'un FMDL. Ne jamais livrer un
    # fichier multi-modèle comme s'il était complet.
    if len(data) > 0xDD and _u16(data, 0xDC) > 1:
        return None

    model_offset = _u64(data, HEADER_MODEL)
    if not _valid_pointer(model_offset, data) or data[model_offset:model_offset + 4] != b"FMDL":
        report.warnings.append("Pointeur FMDL invalide : structure 10.x non reconnue.")
        return None
    base = _u64(data, HEADER_BUFFER_BASE)
    if not _valid_pointer(base, data, 8):
        report.warnings.append("Base des tampons GPU (0x170) invalide.")
        return None
    report.buffer_base = base
    report.model_name = _string_at(data, _u64(data, model_offset + FMDL_NAME))

    # Si une table FSHP est déclarée, la suivre intégralement plutôt que
    # segmenter des indices heuristiquement. Une table invalide ne doit pas
    # déclencher un faux succès par balayage de secours.
    if model_offset + 0x78 <= len(data) and (
            _u64(data, model_offset + 0x28) or _u16(data, model_offset + 0x6A)):
        from . import bfres_v10_struct
        try:
            return bfres_v10_struct.read(data, model_offset, base, report)
        except (bfres_v10_struct.InvalidStructure, struct.error, IndexError, ValueError, OverflowError):
            return None

    materials_offset = _u64(data, model_offset + FMDL_MATERIALS)
    if _valid_pointer(materials_offset, data):
        report.material_names.append(_string_at(data, _u64(data, materials_offset + 0x08)))

    shapes = find_shapes(data, base)
    if not shapes:
        report.warnings.append(
            "Aucune forme lisible : table d'attributs (FVTX) absente ou incohérente.")
        return None
    if require_model_shape:
        field = model_offset + FMDL_VERTEX_INFO
        if field + 8 > len(data):
            return None
        model_fvtx = _u64(data, field)
        if not any(shape.offset == model_fvtx for shape in shapes):
            # Le balayage global a trouvé un FVTX, mais rien ne démontre qu'il
            # appartient au FMDL référencé par l'en-tête.
            return None
    report.shapes = [shape.summary() for shape in shapes]
    first = shapes[0]
    report.attributes = [a.describe() for a in first.attributes]
    report.stride = first.stride
    report.vertex_offset = first.vertex_offset
    report.vertex_count = first.vertex_count

    # tampon d'indices : de la base jusqu'au premier tampon de sommets
    first_vertex = min(shape.vertex_offset for shape in shapes)
    report.index_offset = base
    report.index_count = max(0, (first_vertex - base) // 2)

    # --- segmentation du tampon d'indices (une seule lecture, pas de blocs imposés)
    words = _index_words(data, base, first_vertex)
    bounds = [max(3, shape.vertex_count) for shape in shapes]
    runs = _segment_indices(words, bounds)
    if not runs:
        report.warnings.append(
            "Aucun triangle valide : tampon d'indices non reconnu (géométrie refusée).")
        return None

    # positions décodées **une fois par forme**, et seulement jusqu'à l'indice
    # réellement utilisé : décoder 2,4 M de sommets pour 80 000 faces était le
    # premier poste de dépense mesuré (1,8 s sur une armure).
    needed: dict[int, int] = {}
    for index, _, triangles in runs:
        highest = max(max(triangle) for triangle in triangles) + 1
        if highest > needed.get(index, 0):
            needed[index] = highest
    positions: dict[int, list[tuple[float, float, float]]] = {}
    for index, count in needed.items():
        shape = shapes[index]
        attribute = shape.attribute("position")
        if attribute is None:
            continue
        limit = min(count, shape.vertex_count or count)
        positions[index] = _decode_positions(data, shape.vertex_offset, limit,
                                             shape.stride, attribute.offset,
                                             attribute.format)
    diagonals = {index: _diagonal(values) for index, values in positions.items()}

    # --- validation de chaque suite d'indices, puis recollement des voisines
    kept: list[tuple[int, int, list]] = []
    dropped = 0
    for index, start, triangles in runs:
        values = positions.get(index)
        bound = len(values) if values else 0
        if bound < 3:
            dropped += 1
            continue
        inside = [triangle for triangle in triangles
                  if max(triangle) < bound]
        if len(inside) < max(4, len(triangles) // 2):
            dropped += 1
            continue
        diagonal = diagonals.get(index, 0.0)
        if _coherence(values, inside) > COHERENCE_MAX:
            dropped += 1
            continue
        if _edge_ratio(values, inside, diagonal) > EDGE_RATIO_MAX:
            dropped += 1
            continue
        cleaned = _drop_oversized(values, inside, diagonal)
        cleaned = _drop_outliers(values, cleaned)
        if len(cleaned) < max(4, len(inside) // 2):
            dropped += 1
            continue
        kept.append((index, start, cleaned))
    if dropped:
        report.dropped = dropped
        report.warnings.append(
            f"{dropped} suite(s) d'indices ignorée(s) faute de validation.")

    runs = _merge_runs(kept) if kept else []
    if not runs:
        report.warnings.append(
            "Aucun triangle validé : la géométrie n'a pas passé les contrôles.")
        return None

    # signale les formes dont plusieurs niveaux de détail se mélangent : la
    # géométrie reste juste, mais elle est plus dense que le LOD 0 seul
    for index in {run[0] for run in runs}:
        peaks = [max(max(triangle) for triangle in triangles)
                 for shape_index, _, triangles in runs if shape_index == index]
        peak = max(peaks)
        lower = sum(1 for value in peaks if value < peak * 0.75)
        if lower:
            report.warnings.append(
                f"forme {index} : {lower} sous-maillage(s) de LOD inférieur inclus "
                f"(pic d'indices {peak}).")

    meshes: list[MeshData] = []
    material = report.material_names[0] if report.material_names else ""
    counter = 0
    for index, _, triangles in runs:
        values = positions.get(index) or []
        highest = max(max(triangle) for triangle in triangles) + 1
        shape_positions = values[:min(highest, len(values))]
        if len(shape_positions) < 3:
            continue
        meshes.append(MeshData(name=f"{report.model_name or 'modele'}_{counter:02d}",
                               positions=shape_positions, faces=list(triangles),
                               material=material))
        counter += 1

    if meshes:
        # normales et UV : une seule lecture par forme, partagée entre les
        # morceaux qui utilisent le même tampon
        decoded: dict[int, tuple] = {}
        for shape_index in {run[0] for run in runs}:
            shape = shapes[shape_index]
            count = needed.get(shape_index, 0)
            sample = next((triangles[:2000] for index, _, triangles in runs
                           if index == shape_index), [])
            normals = _decode_normals(data, shape, count)
            uvs, label = _decode_uvs(data, shape, count,
                                     positions.get(shape_index, []), sample)
            decoded[shape_index] = (normals, uvs)
            if uvs and not report.uv:
                report.uv = True
                report.warnings.append(f"UV décodées ({label}).")
        for mesh, (index, _, _) in zip(meshes, runs):
            normals, uvs = decoded.get(index, ([], []))
            length = len(mesh.positions)
            if uvs and len(uvs) >= length:
                mesh.uvs = list(uvs[:length])
            if normals and len(normals) >= length:
                mesh.normals = list(normals[:length])
            elif not any("normales absentes" in warning for warning in report.warnings):
                # Comme le lecteur structuré, ne pas exporter comme source des
                # normales inventées ; le rendu calcule ses normales de face.
                report.warnings.append("Normales absentes ou indécodables ; elles ne sont pas exportées.")

    if not meshes:
        report.warnings.append("Géométrie vide après validation : refusée.")
        return None
    first_needed = max(len(mesh.positions) for mesh in meshes)
    report.vertex_count = max(report.vertex_count, first_needed)
    report.submeshes = len(meshes)
    return meshes, report
