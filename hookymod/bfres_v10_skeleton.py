"""Placement statique borné des formes rigides d'un squelette BFRES 10.x.

Une forme à zéro influence utilise l'os de FSHP ; à une influence, chaque
sommet utilise une entrée de la table de matrices rigides FSKL. Les formes à
plusieurs influences restent dans leur espace de référence et ne sont pas
animées. On ne reconstruit ni rig ni animation pour l'export.

Pour ne pas inventer une pose avec les variantes non maîtrisées, ce lecteur
n'accepte que les chaînes d'os à échelle unitaire, sans billboard. Lorsque des
matrices inverses de repos sont présentes, elles servent aussi à vérifier la
composition de la hiérarchie avant d'appliquer quoi que ce soit.
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
        raise SkeletonError("mode de rotation inconnu")
    bones, mapping, inverse = struct.unpack_from("<3Q", data, offset + 0x10)
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
        _region(data, inverse, smooth * 48)

    parents: list[int] = []
    locals_: list[tuple[float, ...]] = []
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
        if (bone_flags & 0x70000 or (bone_flags & 0x7000) != rotation_mode
                or any(abs(v - 1.0) > 1e-5 for v in scale)):
            raise SkeletonError("billboard, rotation ou échelle d'os non prise en charge")
        r = _rotation(rotation, rotation_mode)
        locals_.append((r[0], r[1], r[2], translation[0],
                        r[3], r[4], r[5], translation[1],
                        r[6], r[7], r[8], translation[2]))
        parents.append(parent)
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
            world[child] = locals_[child] if parent == -1 else _mul(world[parent], locals_[child])  # type: ignore[arg-type]

    matrices = tuple(world)  # toutes les entrées ont été calculées
    identity = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0.)
    for index in range(smooth):
        inv = struct.unpack_from("<12f", data, inverse + index * 48)
        if not all(math.isfinite(v) for v in inv):
            raise SkeletonError("matrice inverse non finie")
        combined = _mul(matrices[matrix_to_bone[index]], inv)  # type: ignore[arg-type]
        # Les grandes translations ne doivent pas masquer une rotation
        # incorrecte ; leur tolérance relative ne s'applique qu'à la colonne T.
        translation_tolerance = 0.003 * max(1.0, *(abs(inv[i]) for i in (3, 7, 11)))
        if any(abs(a - b) > (translation_tolerance if i % 4 == 3 else 0.003)
               for i, (a, b) in enumerate(zip(combined, identity))):
            raise SkeletonError("pose et matrices inverses incompatibles")
    return Skeleton(matrices, tuple(matrix_to_bone), smooth)  # type: ignore[arg-type]


def transform(matrix: tuple[float, ...], vector: tuple[float, ...], *, normal: bool = False) -> tuple[float, float, float]:
    """Applique la rotation seule à une normale, la rotation+translation à un point."""
    result = tuple(sum(matrix[4 * row + col] * vector[col] for col in range(3))
                   + (0.0 if normal else matrix[4 * row + 3]) for row in range(3))
    return result  # type: ignore[return-value]
