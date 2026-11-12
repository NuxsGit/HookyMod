"""BFRES version 10 (Switch 2) : lecture de la géométrie.

Il n'existe **aucune documentation publique** du BFRES 10.x : ce module ne suit
pas un cahier des charges, il applique ce qui a été **mesuré** sur un vrai
fichier du jeu (papillon, build 1.1.1), puis **vérifie** chaque hypothèse avant
de livrer une géométrie.

Structure observée
------------------
* en-tête : `FRES`, version `0x000A0400`, nom (offset de chaîne) à `0x20`,
  pointeur du modèle à `0x28`, dictionnaire de modèles à `0x30` ; la base des
  tampons GPU est un `u64` à `0x170` (ici `0x4000`) ;
* `FMDL` : nom `+0x08`, squelette `+0x18`, `FVTX` `+0x20`, formes `+0x28`,
  matériaux `+0x38` ;
* `FVTX` : nombre de tampons `+0x04`, listes d'attributs `+0x08`/`+0x10` ;
  l'offset du tampon de sommets (relatif à la base) est dans le bloc, on le
  retrouve par validation (voir :func:`_find_vertex_offset`) ;
* tampon de sommets : enregistrements de **24 octets**
  (`position = 3 × f16` à `+0`, `UV = 2 × u16 / 65535` à `+16`) ;
  le reste (`+8`, `+12`, `+20`) n'est pas encore décodé : on n'invente rien ;
* tampon d'indices : `u16`, découpé en sous-maillages alignés sur `0x1000`
  octets, chacun terminé par du remplissage (triangles dégénérés).

Comme rien n'est garanti par un document, chaque étape a une porte de sortie :
si la vérification échoue, la fonction renvoie ``None`` et l'appelant explique
pourquoi. Une géométrie douteuse ne doit jamais arriver jusqu'à Blender.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from .mesh import MeshData

#: enregistrements mesurés dans le tampon de sommets
STRIDE = 24
POSITION_OFFSET = 0
UV_OFFSET = 16
#: découpage des sous-maillages dans le tampon d'indices
SUBMESH_ALIGN = 0x1000


@dataclass
class V10Report:
    """Ce que la lecture a réellement trouvé (affiché, jamais deviné)."""

    version: int = 0            # u32 complet (0x000A0400)
    major: int = 0
    minor: int = 0
    model_name: str = ""
    material_names: list[str] = field(default_factory=list)
    buffer_base: int = 0
    vertex_offset: int = 0
    stride: int = STRIDE
    vertex_count: int = 0
    index_offset: int = 0
    index_count: int = 0
    submeshes: int = 0
    uv: bool = False
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = [
            f"BFRES {self.major}.{self.minor} « {self.model_name or '(sans nom)'} »",
            f"  base des tampons : 0x{self.buffer_base:X}",
            f"  sommets          : {self.vertex_count} × {self.stride} o à 0x{self.vertex_offset:X}",
            f"  indices          : {self.index_count} u16 à 0x{self.index_offset:X}",
            f"  sous-maillages   : {self.submeshes}",
            f"  UV               : {'oui (u16/65535)' if self.uv else 'non (non décodées)'}",
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


# --------------------------------------------------------------------------- tampons
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
        for i, j in ((a, b), (b, c)):
            local += math.dist(positions[i], positions[j])
            pairs += 1
    if not pairs:
        return 1.0
    local /= pairs
    far = total = 0.0
    stride = max(1, count // 200)
    for i in range(0, count, stride):
        j = (i * 7 + 13) % count
        far += math.dist(positions[i], positions[j])
        total += 1
    far /= max(1, total)
    return local / far if far > 0 else 1.0


def _decode_positions(data: bytes, start: int, count: int, stride: int):
    positions = []
    for index in range(count):
        try:
            x, y, z = struct.unpack_from("<3e", data, start + index * stride)
        except struct.error:
            return []
        positions.append((x, y, z))
    return positions


def _decode_uvs(data: bytes, start: int, count: int, stride: int):
    uvs = []
    for index in range(count):
        u, v = struct.unpack_from("<2H", data, start + index * stride + UV_OFFSET)
        uvs.append((u / 65535.0, v / 65535.0))
    return uvs


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
        pa, pb, pc = positions[a], positions[b], positions[c]
        u = [pb[i] - pa[i] for i in range(3)]
        v = [pc[i] - pa[i] for i in range(3)]
        cx, cy, cz = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                      u[0] * v[1] - u[1] * v[0])
        area3 = 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
        (ax, ay), (bx, by), (cx2, cy2) = uvs[a], uvs[b], uvs[c]
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


#: nombre de sommets regardés pour valider un candidat (rapide, suffisant)
_PROBE_VERTICES = 4000


def _candidate_offsets(data: bytes, vertex_info: int, base: int) -> list[int]:
    """Offsets absolus plausibles du tampon de sommets, lus dans ``FVTX``.

    Le bloc mélange des ``u32`` et des ``u64`` : on lit les deux, on ne garde
    que ce qui tombe dans le fichier, aligné, après la base des tampons.
    """
    found: list[int] = []
    # Le bloc FVTX s'arrête à la section suivante : au-delà, les valeurs lues
    # sont des pointeurs de matériaux (ils ressemblent à des offsets de tampon,
    # d'où la nécessité de borner).
    end = min(vertex_info + 0x240, len(data) - 8)
    for magic in (b"FMAT", b"FSHP", b"FSKL", b"_STR"):
        found_at = data.find(magic, vertex_info + 8)
        if found_at != -1:
            end = min(end, found_at)
    for offset in range(vertex_info, end, 2):
        values = []
        if offset % 8 == 0:
            values.append(_u64(data, offset))
        values.append(_u32(data, offset))
        for value in values:
            absolute = base + value
            if (value and absolute % 8 == 0 and base < absolute
                    and absolute + STRIDE * 8 < len(data) and absolute not in found):
                found.append(absolute)
    return found


def _find_vertex_offset(data: bytes, vertex_info: int, base: int,
                        vertex_count_hint: int = _PROBE_VERTICES) -> int:
    """Offset du tampon de sommets, cherché dans le bloc ``FVTX`` puis validé.

    On teste chaque ``u64`` du bloc : le bon candidat est celui qui donne un
    tampon de sommets dont les positions passent la cohérence. Aucun candidat
    n'est accepté sur la seule foi de sa place dans la structure.
    """
    if not _valid_pointer(vertex_info, data):
        return 0
    best, best_score = 0, 1.0
    for absolute in _candidate_offsets(data, vertex_info, base):
        count = min(vertex_count_hint, (len(data) - absolute) // STRIDE)
        positions = _decode_positions(data, absolute, count, STRIDE)
        if len(positions) < 32 or not all(all(map(math.isfinite, p)) for p in positions):
            continue
        # Un vrai modèle occupe un volume : des positions quasi identiques
        # signifient qu'on lit autre chose (des index, du remplissage…).
        spread = max(abs(v) for p in positions for v in p)
        if not (1e-3 < spread < 1e4):
            continue
        if len({(round(p[0], 6), round(p[1], 6), round(p[2], 6)) for p in positions}) < 50:
            continue
        groups = _index_triangles(data, base, absolute, count)
        if not groups or len(groups[0]) < 20:
            continue
        score = _coherence(positions, groups[0])
        if 1e-3 < score < best_score:
            best, best_score = absolute, score
    return best if best_score < 0.35 else 0


def _detect_stride(data: bytes, start: int, end: int, fallback: int = STRIDE) -> int:
    """Pas d'enregistrement des sommets, par autocorrélation des octets."""
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
    # Un pas de 24 donne aussi un pic à 48 : on garde le plus petit multiple
    # qui explique presque aussi bien les répétitions.
    for lag in range(8, best_lag):
        if best_lag % lag == 0 and scores[lag] >= best - 0.02:
            return lag
    return best_lag


# --------------------------------------------------------------------------- lecture
def _index_triangles(data: bytes, start: int, end: int, vertex_count: int):
    """Triangles du tampon d'indices, découpés en sous-maillages alignés.

    Chaque sous-maillage se termine par du remplissage : ses triangles
    dégénérés servent de sentinelle, on s'arrête là.
    """
    groups: list[list[tuple[int, int, int]]] = []
    for block in range(start, end, SUBMESH_ALIGN):
        limit = min(block + SUBMESH_ALIGN, end)
        triangles: list[tuple[int, int, int]] = []
        offset = block
        while offset + 6 <= limit:
            a, b, c = struct.unpack_from("<3H", data, offset)
            offset += 6
            if max(a, b, c) >= vertex_count:
                break
            if a == b or b == c or a == c:      # remplissage / fin de sous-maillage
                break
            triangles.append((a, b, c))
        if triangles:
            groups.append(triangles)
    return groups


def parse(data: bytes) -> tuple[list[MeshData], V10Report] | None:
    """Maillages d'un BFRES 10.x, ou ``None`` si la structure n'est pas reconnue.

    Le retour est volontairement « tout ou rien » : soit les tampons vérifient
    leurs hypothèses, soit on ne renvoie rien et l'appelant dit pourquoi.
    """
    if data[:4] != b"FRES" or len(data) < 0x200:
        return None
    report = V10Report(version=_u32(data, 0x08))
    report.major = (report.version >> 16) & 0xFFFF
    report.minor = (report.version >> 8) & 0xFF
    if report.major != 10:
        return None

    model_offset = _u64(data, 0x28)
    if not _valid_pointer(model_offset, data) or data[model_offset:model_offset + 4] != b"FMDL":
        report.warnings.append("Pointeur FMDL invalide : structure 10.x non reconnue.")
        return None
    base = _u64(data, 0x170)
    if not _valid_pointer(base, data, 8):
        report.warnings.append("Base des tampons GPU (0x170) invalide.")
        return None
    report.buffer_base = base
    report.model_name = _string_at(data, _u64(data, model_offset + 0x08))

    vertex_info = _u64(data, model_offset + 0x20)
    materials_offset = _u64(data, model_offset + 0x38)
    if _valid_pointer(materials_offset, data):
        report.material_names.append(_string_at(data, _u64(data, materials_offset + 0x08)))

    vertex_start = _find_vertex_offset(data, vertex_info, base)
    if not vertex_start:
        report.warnings.append("Tampon de sommets introuvable dans FVTX.")
        return None
    report.vertex_offset = vertex_start
    report.index_offset = base

    stride = _detect_stride(data, vertex_start, len(data))
    report.stride = stride
    vertex_count = (len(data) - vertex_start) // stride
    report.vertex_count = vertex_count
    report.index_count = (vertex_start - base) // 2

    positions = _decode_positions(data, vertex_start, vertex_count, stride)
    if not positions or not all(all(map(math.isfinite, p)) for p in positions):
        report.warnings.append("Positions illisibles (f16) : géométrie refusée.")
        return None

    groups = _index_triangles(data, base, vertex_start, vertex_count)
    groups = [g for g in groups if len(g) >= 8]      # fragments = bruit de découpage
    if not groups:
        report.warnings.append("Aucun triangle valide dans le tampon d'indices.")
        return None

    score = _coherence(positions, groups[0])
    if score > 0.5:
        report.warnings.append(
            f"Géométrie incohérente (score {score:.2f}) : refusée plutôt que fausse."
        )
        return None

    uvs = _decode_uvs(data, vertex_start, vertex_count, stride)
    report.uv = _uv_plausible(positions, uvs, groups[0])
    if not report.uv:
        report.warnings.append("UV non reconnues : export sans texture (pas de fausse UV).")
        uvs = []

    meshes: list[MeshData] = []
    material = report.material_names[0] if report.material_names else ""
    for index, triangles in enumerate(groups):
        mesh = MeshData(name=f"{report.model_name or 'modele'}_{index:02d}",
                        positions=list(positions), faces=list(triangles),
                        material=material)
        if report.uv:
            mesh.uvs = list(uvs)
        mesh.ensure_normals()
        meshes.append(mesh)
    report.submeshes = len(meshes)
    return meshes, report
