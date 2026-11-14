"""Budget mémoire d'un aperçu 3D : estimation, dégradation, refus expliqué.

HookyCreator rend les modèles sur le processeur : il n'y a pas de VRAM à
proprement parler, mais la mémoire consommée par un aperçu suit les mêmes
règles qu'un moteur graphique — géométrie copiée, faces projetées, couleur et
profondeur par pixel. Ce module chiffre ce coût **avant** de rendre, pour tenir
le budget choisi dans les réglages (6 Go par défaut, jusqu'à 25 Go).

Ordre de dégradation (jamais silencieux) :

1. réduire l'anticrénelage ;
2. limiter le nombre de faces dessinées ;
3. si même cela ne rentre pas, refuser avec les chiffres exacts.
"""

from __future__ import annotations

from dataclasses import dataclass

from hookymod.mesh import MeshData

from .settings import Settings

#: Coût mémoire d'un sommet : tuple Python (3 doubles) + son pointeur de liste.
VERTEX_BYTES = 96
#: Coût d'une face : tuple d'indices + la couleur et la normale calculées pour
#: le rendu (deux tuples de plus).
FACE_BYTES = 112
#: Tampon de couleurs (RGBA) par pixel.
PIXEL_BYTES = 4
#: Tampon de profondeur (un flottant Python par pixel).
DEPTH_BYTES = 8

#: En dessous de ce nombre de faces, un aperçu n'a plus de sens : on refuse.
MIN_RENDER_FACES = 400


class BudgetError(RuntimeError):
    """L'aperçu ne rentre pas dans le budget, même dégradé."""

    def __init__(self, message: str, *, needed: int, budget: int) -> None:
        super().__init__(message)
        self.needed = needed
        self.budget = budget


@dataclass
class Plan:
    """Ce qui sera réellement rendu, et pourquoi ce n'est pas le maximum."""

    supersample: int
    face_limit: int | None
    geometry_bytes: int
    frame_bytes: int
    budget_bytes: int
    notes: list[str]

    @property
    def total_bytes(self) -> int:
        return self.geometry_bytes + self.frame_bytes

    @property
    def fits(self) -> bool:
        return self.total_bytes <= self.budget_bytes

    @property
    def fill(self) -> float:
        """Part du budget utilisée (0 → 1), pour la jauge de l'interface."""
        if not self.budget_bytes:
            return 1.0
        return min(1.0, self.total_bytes / self.budget_bytes)

    def as_json(self) -> dict:
        return {
            "supersample": self.supersample,
            "face_limit": self.face_limit,
            "geometry_bytes": self.geometry_bytes,
            "frame_bytes": self.frame_bytes,
            "total_bytes": self.total_bytes,
            "budget_bytes": self.budget_bytes,
            "fill": round(self.fill, 4),
            "fits": self.fits,
            "notes": list(self.notes),
        }


def geometry_bytes(meshes) -> tuple[int, int]:
    """``(sommets, octets)`` de la géométrie telle qu'elle sera copiée."""
    vertices = faces = 0
    for mesh in meshes:
        vertices += len(mesh.positions)
        faces += len(mesh.faces)
    return faces, vertices * VERTEX_BYTES + faces * FACE_BYTES


def frame_bytes(width: int, height: int, supersample: int) -> int:
    """Couleur + profondeur pour une image de ``width × height`` sur-échantillonnée."""
    scale = max(1, int(supersample)) ** 2
    return int(width) * int(height) * scale * (PIXEL_BYTES + DEPTH_BYTES)


def plan(settings: Settings, meshes, width: int, height: int,
         face_limit: int | None = None) -> Plan:
    """Décide quoi rendre pour tenir dans le budget, sans jamais dépasser.

    ``face_limit`` est un plafond venant de l'appelant (interface) ; il est
    respecté même s'il est plus généreux que le budget, mais descendu si le
    budget l'exige.
    """
    settings = settings.clamp()
    faces, geom = geometry_bytes(meshes)
    notes: list[str] = []
    limit = settings.max_faces if face_limit is None else face_limit
    if limit is not None:
        limit = max(MIN_RENDER_FACES, min(int(limit), FACES_CEILING))
        if limit < faces:
            faces = limit
            geom = _geom_for(meshes, limit)

    supersample = settings.supersample
    budget = settings.budget_bytes
    current = Plan(supersample, limit, geom, frame_bytes(width, height, supersample),
                   budget, notes)
    if current.fits:
        return current

    if not settings.adaptive:
        raise BudgetError(
            f"Aperçu trop lourd : {_gb(current.total_bytes)} pour un budget de "
            f"{_gb(budget)}. Monte le budget mémoire dans les réglages "
            f"(jusqu'à 25 Go) ou active la dégradation automatique.",
            needed=current.total_bytes, budget=budget)

    # 1. anticrénelage
    if supersample > 1:
        notes.append(f"anticrénelage ramené de ×{supersample} à ×1 pour tenir "
                     f"dans {_gb(budget)}")
        supersample = 1
        current = Plan(supersample, limit, geom, frame_bytes(width, height, 1),
                       budget, notes)
        if current.fits:
            return current

    # 2. nombre de faces dessinées — en tenant compte des sommets qu'elles
    #    gardent avec elles (un maillage dense coûte plus cher par face)
    room = budget - current.frame_bytes
    per_face = _cost_per_face(meshes)
    allowed = max(0, room) // per_face
    if allowed < MIN_RENDER_FACES:
        raise BudgetError(
            f"Même à {MIN_RENDER_FACES} faces et sans anticrénelage, l'aperçu "
            f"demanderait {_gb(current.frame_bytes + MIN_RENDER_FACES * FACE_BYTES)} "
            f"pour un budget de {_gb(budget)}. Monte le budget dans les réglages.",
            needed=current.frame_bytes + MIN_RENDER_FACES * FACE_BYTES, budget=budget)
    if limit is not None and allowed >= limit:
        raise BudgetError(
            f"L'aperçu ({_gb(current.total_bytes)}) dépasse le budget de {_gb(budget)} "
            f"alors que le plafond de faces est déjà atteint. Monte le budget.",
            needed=current.total_bytes, budget=budget)

    faces = int(allowed)
    notes.append(f"faces limitées à {faces} sur {_total_faces(meshes)} pour tenir "
                 f"dans {_gb(budget)}")
    return Plan(supersample, faces, faces * per_face, current.frame_bytes,
                budget, notes)


FACES_CEILING = 4_000_000


def _total_faces(meshes) -> int:
    return sum(len(mesh.faces) for mesh in meshes)


def _cost_per_face(meshes) -> int:
    """Coût d'une face *avec* les sommets qu'elle traîne (ratio du modèle)."""
    total_faces = _total_faces(meshes)
    total_vertices = sum(len(mesh.positions) for mesh in meshes)
    if total_faces <= 0:
        return FACE_BYTES
    return FACE_BYTES + int(VERTEX_BYTES * total_vertices / total_faces)


def _vertex_share(meshes, face_limit: int) -> int:
    """Sommets gardés quand on ne dessine que ``face_limit`` faces."""
    total_faces = _total_faces(meshes)
    total_vertices = sum(len(mesh.positions) for mesh in meshes)
    if total_faces <= 0 or face_limit >= total_faces:
        return total_vertices
    keep = 0
    remaining = face_limit
    for mesh in meshes:
        if remaining <= 0:
            break
        if len(mesh.faces) <= remaining:
            keep += len(mesh.positions)
            remaining -= len(mesh.faces)
        else:
            # maillage partiellement dessiné : on compte ses sommets au prorata,
            # bornés par le nombre de sommets réellement référencés
            share = len(mesh.faces) and remaining / len(mesh.faces) or 0.0
            keep += int(len(mesh.positions) * share)
            remaining = 0
    return max(1, keep)


def _geom_for(meshes, face_limit: int) -> int:
    faces = min(face_limit, _total_faces(meshes))
    return _vertex_share(meshes, face_limit) * VERTEX_BYTES + faces * FACE_BYTES


def trim(meshes, face_limit: int | None) -> list[MeshData]:
    """Garde les premières ``face_limit`` faces, sommets remappés.

    Les modèles sont décodés bloc par bloc : garder les premiers blocs conserve
    donc une partie cohérente du modèle au lieu d'un semis de triangles.
    """
    if face_limit is None:
        return list(meshes)
    total = _total_faces(meshes)
    if face_limit >= total:
        return list(meshes)
    kept: list[MeshData] = []
    remaining = max(1, int(face_limit))
    for mesh in meshes:
        if remaining <= 0:
            break
        if len(mesh.faces) <= remaining:
            kept.append(mesh)
            remaining -= len(mesh.faces)
            continue
        faces = mesh.faces[:remaining]
        used = sorted({index for face in faces for index in face})
        remap = {old: new for new, old in enumerate(used)}
        trimmed = MeshData(
            name=mesh.name,
            positions=[mesh.positions[index] for index in used],
            faces=[tuple(remap[index] for index in face) for face in faces],
            uvs=([mesh.uvs[index] for index in used]
                 if mesh.uvs and len(mesh.uvs) == len(mesh.positions) else None),
            normals=([mesh.normals[index] for index in used]
                     if mesh.normals and len(mesh.normals) == len(mesh.positions) else None),
            colors=(mesh.colors if mesh.colors and len(mesh.colors) == len(mesh.positions)
                    else None),
            material=mesh.material,
        )
        kept.append(trimmed)
        remaining = 0
    return kept or list(meshes)


def _gb(value: int) -> str:
    return f"{value / 1024 ** 3:.2f} Go" if value >= 1024 ** 3 else f"{value / 1024 ** 2:.0f} Mo"
