"""Transport binaire borné de la géométrie pour l'aperçu WebGL local.

Aucun export intermédiaire ni décimation : positions, normales, UV et indices
sont ceux des maillages décodés. Une entrée invalide ou trop volumineuse est
refusée entièrement, pas réduite silencieusement.
"""
from __future__ import annotations

import json
import math
import struct
import sys
from array import array

from hookymod.util import HookyError

MAGIC = b"H3D1"
MAX_MESHES = 2048
MAX_VERTICES = 2_000_000
MAX_TRIANGLES = 4_000_000
MAX_BYTES = 128 * 1024 * 1024
MAX_HEADER = 4 * 1024 * 1024


def _packed(typecode: str, values) -> bytes:
    packed = array(typecode, values)
    if packed.itemsize != 4:
        raise HookyError("Format binaire de cette plateforme non pris en charge.")
    if sys.byteorder != "little":
        packed.byteswap()
    return packed.tobytes()


def encode(meshes, textures: dict | None = None) -> bytes:
    """Sérialise les maillages complets et leurs liaisons d'albédo non ambiguës."""
    textures = textures or {}
    if not meshes or len(meshes) > MAX_MESHES:
        raise HookyError("Nombre de maillages trop grand ou modèle vide pour la vue 3D.")
    vertices = sum(len(mesh.positions) for mesh in meshes)
    triangles = sum(len(mesh.faces) for mesh in meshes)
    if not vertices or not triangles or vertices > MAX_VERTICES or triangles > MAX_TRIANGLES:
        raise HookyError("Vue 3D refusée : limite de 2 millions de sommets ou 4 millions de triangles. "
                         "Aucune face n'a été retirée.")
    entries = []
    size = 0
    for mesh in meshes:
        count = len(mesh.positions)
        if not count or not mesh.faces:
            raise HookyError("Maillage vide : vue 3D refusée sans retirer de faces.")
        normals = mesh.normals is not None and len(mesh.normals) == count
        uvs = mesh.uvs is not None and len(mesh.uvs) == count
        if mesh.normals is not None and not normals or mesh.uvs is not None and not uvs:
            raise HookyError("Attribut de sommet incomplet : vue 3D refusée.")
        material = str(mesh.material or "")
        binding = textures.get(material) if uvs else None
        entries.append({"name": str(mesh.name), "material": material, "vertices": count,
                        "triangles": len(mesh.faces), "normals": normals, "uvs": uvs,
                        "texture": binding})
        size += count * (12 + (12 if normals else 0) + (8 if uvs else 0)) + len(mesh.faces) * 12
    header = json.dumps({"version": 1, "meshes": entries}, ensure_ascii=True,
                        separators=(",", ":")).encode("ascii")
    if len(header) > MAX_HEADER or size + len(header) + 12 > MAX_BYTES:
        raise HookyError("Vue 3D refusée : géométrie supérieure à 128 Mio. Aucune face n'a été retirée.")
    output = bytearray(MAGIC + struct.pack("<I", len(header)) + header)
    output.extend(b"\0" * (-len(output) % 4))
    for mesh in meshes:
        def flatten(values, width):
            for row in values:
                # array('f') peut convertir une valeur finie hors plage en
                # infini sans exception ; ne jamais l'envoyer ainsi au GPU.
                if len(row) != width or not all(math.isfinite(v) and abs(v) <= 3.402823466e38 for v in row):
                    raise HookyError("Attribut de sommet invalide : vue 3D refusée.")
                yield from row

        try:
            output.extend(_packed("f", flatten(mesh.positions, 3)))
            if mesh.normals is not None:
                output.extend(_packed("f", flatten(mesh.normals, 3)))
            if mesh.uvs is not None:
                output.extend(_packed("f", flatten(mesh.uvs, 2)))
            count = len(mesh.positions)
            def indices():
                for face in mesh.faces:
                    if len(face) != 3 or any(not isinstance(i, int) or i < 0 or i >= count for i in face):
                        raise HookyError("Triangle invalide : vue 3D refusée sans retirer de faces.")
                    yield from face
            output.extend(_packed("I", indices()))
        except (OverflowError, TypeError, ValueError) as exc:
            raise HookyError(f"Géométrie non représentable en 3D : {exc}") from exc
    return bytes(output)
