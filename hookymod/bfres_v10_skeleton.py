"""Placement statique borné des formes rigides d'un squelette BFRES 10.x.

Une forme à zéro influence utilise l'os de FSHP ; à une influence, chaque
sommet utilise une entrée de la table de matrices rigides FSKL. Les formes à
plusieurs influences restent dans leur espace de référence et ne sont pas
animées par ce lecteur. L'export animé expérimental utilise séparément les
informations validées de skin et de clip.

Les rotations Euler/quaternion par os et les échelles inversibles sont prises
en charge, ainsi que la compensation d'échelle Maya par segment. Les
billboards et le mode d'échelle Softimage restent refusés. Les matrices
inverses de repos présentes servent à vérifier la hiérarchie avant placement.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

MAX_BONES = 4096


class SkeletonError(ValueError):
    """Squelette absent, incohérent ou variante non prise en charge."""


def _region(data: bytes, offset: int, size: int) -> None:
    if offset <= 0 or size < 0 or offset + size > len(data):
        raise SkeletonError("table hors limites")


def _mul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    """Compose deux matrices affines 3×4, en ordre parent puis enfant."""
    return tuple(
        sum(a[4 * r + k] * b[4 * k + c] for k in range(3))
        + (a[4 * r + 3] if c == 3 else 0.0)
        for r in range(3) for c in range(4)
    )


def inverse(matrix: tuple[float, ...]) -> tuple[float, ...]:
    """Inverse affine complète, y compris échelle non uniforme et cisaillement.

    Normaliser d'abord le bloc 3×3 évite de confondre une petite échelle
    uniforme avec une matrice singulière. Les transformations trop mal
    conditionnées restent refusées plutôt que d'inventer une pose de skin.
    """
    linear = tuple(matrix[i] for i in (0, 1, 2, 4, 5, 6, 8, 9, 10))
    magnitude = max(abs(v) for v in linear)
    if not math.isfinite(magnitude) or magnitude == 0:
        raise SkeletonError("matrice d'os singulière (échelle nulle)")
    a, b, c, d, e, f, g, h, i = (v / magnitude for v in linear)
    determinant = a*(e*i-f*h) - b*(d*i-f*g) + c*(d*h-e*g)
    if not math.isfinite(determinant) or abs(determinant) < 1e-12:
        raise SkeletonError("matrice d'os singulière ou échelle trop déséquilibrée")
    factor = 1.0 / determinant / magnitude
    r = ((e*i-f*h)*factor, (c*h-b*i)*factor, (b*f-c*e)*factor,
         (f*g-d*i)*factor, (a*i-c*g)*factor, (c*d-a*f)*factor,
         (d*h-e*g)*factor, (b*g-a*h)*factor, (a*e-b*d)*factor)
    t = (matrix[3], matrix[7], matrix[11])
    result = (r[0], r[1], r[2], -sum(r[j] * t[j] for j in range(3)),
              r[3], r[4], r[5], -sum(r[3+j] * t[j] for j in range(3)),
              r[6], r[7], r[8], -sum(r[6+j] * t[j] for j in range(3)))
    if not all(math.isfinite(v) for v in result):
        raise SkeletonError("matrice inverse d'os non finie")
    return result


def normal_matrix(matrix: tuple[float, ...]) -> tuple[float, ...]:
    """Inverse transposée du bloc linéaire, pour les normales de repos."""
    inv = inverse(matrix)
    return (inv[0], inv[4], inv[8], 0., inv[1], inv[5], inv[9], 0., inv[2], inv[6], inv[10], 0.)


def _rotation(values: tuple[float, ...], mode: int) -> tuple[float, ...]:
    x, y, z, w = values
    if mode == 0x1000:  # Euler XYZ : Rz × Ry × Rx sur des vecteurs colonnes.
        cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
        return (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx,
                sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx,
                -sy, cy * sx, cy * cx)
    if mode == 0:  # quaternion (x, y, z, w)
        length = math.sqrt(x*x + y*y + z*z + w*w)
        if not 0.999 <= length <= 1.001:
            raise SkeletonError("quaternion non unitaire")
        x, y, z, w = x / length, y / length, z / length, w / length
        return (1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w),
                2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w),
                2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y))
    raise SkeletonError("mode de rotation inconnu")


@dataclass(frozen=True)
class Skeleton:
    matrices: tuple[tuple[float, ...], ...]
    inverse_matrices: tuple[tuple[float, ...], ...]
    rotation_modes: tuple[str, ...]
    matrix_to_bone: tuple[int, ...]
    smooth_count: int

    def rigid_matrix(self, index: int) -> tuple[float, ...]:
        if not self.smooth_count <= index < len(self.matrix_to_bone):
            raise SkeletonError("indice de matrice rigide hors limites")
        return self.matrices[self.matrix_to_bone[index]]

    def bone_matrix(self, index: int) -> tuple[float, ...]:
        if not 0 <= index < len(self.matrices):
            raise SkeletonError("indice d'os hors limites")
        return self.matrices[index]


def read(data: bytes, offset: int) -> Skeleton:
    _region(data, offset, 0x40)
    if data[offset:offset + 4] != b"FSKL":
        raise SkeletonError("signature FSKL absente")
    flags = struct.unpack_from("<I", data, offset + 4)[0]
    rotation_mode = flags & 0x7000
    if rotation_mode not in (0, 0x1000):
        raise SkeletonError(f"mode de rotation du squelette inconnu (0x{rotation_mode:x})")
    if flags & 0x300 == 0x300:
        raise SkeletonError("mode d'échelle Softimage non pris en charge")
    bones, mapping, inverse_ptr = struct.unpack_from("<3Q", data, offset + 0x10)
    count, smooth, rigid = struct.unpack_from("<3H", data, offset + 0x38)
    if not 1 <= count <= MAX_BONES or smooth + rigid > count * 2:
        raise SkeletonError("nombre d'os ou de matrices invalide")
    _region(data, bones, count * 0x58)  # enregistrement observé en BFRES 10.x
    if smooth + rigid:
        _region(data, mapping, (smooth + rigid) * 2)
        matrix_to_bone = struct.unpack_from(f"<{smooth + rigid}H", data, mapping)
        if any(index >= count for index in matrix_to_bone):
            raise SkeletonError("table des matrices invalide")
    else:
        matrix_to_bone = ()
    if smooth:
        _region(data, inverse_ptr, smooth * 48)

    parents: list[int] = []
    scales: list[tuple[float, ...]] = []
    rotations: list[tuple[float, ...]] = []
    translations: list[tuple[float, ...]] = []
    compensations: list[bool] = []
    rotation_modes: list[str] = []
    bone_mappings = []
    for index in range(count):
        at = bones + index * 0x58
        stored, parent, smooth_index, rigid_index = struct.unpack_from("<Hhhh", data, at + 0x20)
        bone_flags = struct.unpack_from("<I", data, at + 0x2C)[0]
        scale = struct.unpack_from("<3f", data, at + 0x30)
        rotation = struct.unpack_from("<4f", data, at + 0x3C)
        translation = struct.unpack_from("<3f", data, at + 0x4C)
        if (stored != index or parent < -1 or parent >= count or parent == index
                or not all(math.isfinite(v) for v in (*scale, *rotation, *translation))):
            raise SkeletonError("hiérarchie ou valeurs d'os invalides")
        if bone_flags & 0x70000:
            raise SkeletonError(f"os n°{index} : billboard non pris en charge (0x{bone_flags & 0x70000:x})")
        bone_rotation = bone_flags & 0x7000
        if bone_rotation not in (0, 0x1000):
            raise SkeletonError(f"os n°{index} : mode de rotation inconnu (0x{bone_rotation:x})")
        # Certains lecteurs 10.4 traitent un mode d'os nul comme « hériter du
        # squelette », alors que d'autres le lisent comme quaternion explicite.
        # Ne choisir Euler qu'en cas non ambigu : quatrième valeur Euler == 1
        # et quaternion hors de la tolérance unitaire, ou identité commune.
        if bone_rotation == 0 and rotation_mode == 0x1000 and rotation[3] == 1.0:
            length = math.hypot(*rotation)
            if rotation[:3] == (0., 0., 0.) or not 0.999 <= length <= 1.001:
                bone_rotation = 0x1000
            else:
                raise SkeletonError(f"os n°{index} : mode de rotation ambigu entre Euler et quaternion")
        if any(abs(value) < 1e-8 for value in scale):
            raise SkeletonError(f"os n°{index} : échelle nulle ou trop petite pour le skin")
        try:
            r = _rotation(rotation, bone_rotation)
        except SkeletonError as exc:
            raise SkeletonError(f"os n°{index} : {exc}") from exc
        parents.append(parent)
        scales.append(scale)
        rotations.append(r)
        translations.append(translation)
        compensations.append(bool(bone_flags & 0x00800000))
        rotation_modes.append("euler" if bone_rotation == 0x1000 else "quaternion")
        bone_mappings.append((smooth_index, rigid_index))
    for index, bone in enumerate(matrix_to_bone):
        expected = bone_mappings[bone][0 if index < smooth else 1]
        if expected != index:
            raise SkeletonError("correspondance matrice/os incohérente")

    # Le parent n'a pas besoin de précéder l'enfant dans la table. Une boucle
    # est refusée sans récursion pour rester borné même avec un fichier hostile.
    world: list[tuple[float, ...] | None] = [None] * count
    for index in range(count):
        chain: list[int] = []
        seen: set[int] = set()
        current = index
        while current != -1 and world[current] is None:
            if current in seen:
                raise SkeletonError("cycle dans la hiérarchie")
            seen.add(current)
            chain.append(current)
            current = parents[current]
        while chain:
            child = chain.pop()
            parent = parents[child]
            r, s, t = rotations[child], scales[child], translations[child]
            # Maya SSC : T × inverse(échelle locale du parent) × R × S.
            # La translation de l'enfant reste donc affectée par son parent.
            p = scales[parent] if parent >= 0 and compensations[child] else (1., 1., 1.)
            local = (r[0]*s[0]/p[0], r[1]*s[1]/p[0], r[2]*s[2]/p[0], t[0],
                     r[3]*s[0]/p[1], r[4]*s[1]/p[1], r[5]*s[2]/p[1], t[1],
                     r[6]*s[0]/p[2], r[7]*s[1]/p[2], r[8]*s[2]/p[2], t[2])
            world[child] = local if parent == -1 else _mul(world[parent], local)  # type: ignore[arg-type]
            if not all(math.isfinite(value) for value in world[child]):
                raise SkeletonError(f"os n°{child} : matrice de repos non finie")

    matrices = tuple(world)  # toutes les entrées ont été calculées
    inverses = []
    for index, matrix in enumerate(matrices):
        try:
            inverses.append(inverse(matrix))  # type: ignore[arg-type]
        except SkeletonError as exc:
            raise SkeletonError(f"os n°{index} : {exc}") from exc
    identity = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0.)
    for index in range(smooth):
        inv = struct.unpack_from("<12f", data, inverse_ptr + index * 48)
        if not all(math.isfinite(v) for v in inv):
            raise SkeletonError("matrice inverse non finie")
        combined = _mul(matrices[matrix_to_bone[index]], inv)  # type: ignore[arg-type]
        # Les grandes translations ne doivent pas masquer une rotation
        # incorrecte ; leur tolérance relative ne s'applique qu'à la colonne T.
        translation_tolerance = 0.003 * max(1.0, *(abs(inv[i]) for i in (3, 7, 11)))
        if any(abs(a - b) > (translation_tolerance if i % 4 == 3 else 0.003)
               for i, (a, b) in enumerate(zip(combined, identity))):
            raise SkeletonError("pose et matrices inverses incompatibles")
    return Skeleton(matrices, tuple(inverses), tuple(rotation_modes), tuple(matrix_to_bone), smooth)  # type: ignore[arg-type]


def transform_normal(matrix: tuple[float, ...], vector: tuple[float, ...]) -> tuple[float, float, float]:
    """Applique une inverse transposée déjà calculée et normalise le résultat."""
    result = tuple(sum(matrix[4 * row + col] * vector[col] for col in range(3)) for row in range(3))
    length = math.hypot(*result)
    if not math.isfinite(length) or length < 1e-30:
        raise SkeletonError("normale d'os invalide")
    return tuple(value / length for value in result)  # type: ignore[return-value]


def transform(matrix: tuple[float, ...], vector: tuple[float, ...], *, normal: bool = False) -> tuple[float, float, float]:
    """Transforme un point ou une normale avec la matrice affine appropriée."""
    if normal:
        return transform_normal(normal_matrix(matrix), vector)
    return tuple(sum(matrix[4 * row + col] * vector[col] for col in range(3))
                 + matrix[4 * row + 3] for row in range(3))  # type: ignore[return-value]
