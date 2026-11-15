"""Cache disque de la géométrie décodée (positions, faces, UV, normales).

Décoder un BFRES 10.x coûte quelques dixièmes de seconde par modèle et par
affichage : cliquer sur un modèle, changer d'onglet, rouvrir l'atelier, tout
repayait le décodage. Les maillages sont donc écrits sur disque dans un format
binaire minimal (des ``array`` brutes, une en-tête JSON), puis relus d'un bloc.

Le fichier ne contient **que** ce que le décodage a validé : un
``<modele>.geo`` absent ou d'une autre version est simplement ignoré, jamais
deviné. La clé contient la taille et la date du fichier source, comme les
vignettes.
"""

from __future__ import annotations

import json
import struct
from array import array
from pathlib import Path

MAGIC = b"HOOKYGEO1"
VERSION = 3  # v3 : ne pas réutiliser les normales synthétiques de l'ancien cache


def _array_bytes(type_code: str) -> bytes:
    values = array(type_code)
    return bytes(values.tobytes())


def write(path, meshes, meta: dict | None = None) -> int:
    """Écrit ``meshes`` dans ``path`` et renvoie le nombre d'octets écrits."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "version": VERSION,
        "meta": meta or {},
        "meshes": [
            {
                "name": mesh.name,
                "material": mesh.material,
                "positions": len(mesh.positions),
                "faces": len(mesh.faces),
                "uvs": len(mesh.uvs) if mesh.uvs else 0,
                "normals": len(mesh.normals) if mesh.normals else 0,
            }
            for mesh in meshes
        ],
    }
    blob = bytearray(MAGIC)
    payload = json.dumps(header, separators=(",", ":")).encode("utf-8")
    blob += struct.pack("<I", len(payload))
    blob += payload
    body = bytearray()
    for mesh in meshes:
        flat = array("f")
        for x, y, z in mesh.positions:
            flat.append(x)
            flat.append(y)
            flat.append(z)
        body += flat.tobytes()
        indices = array("I")
        for face in mesh.faces:
            indices.append(face[0])
            indices.append(face[1])
            indices.append(face[2])
        body += indices.tobytes()
        if mesh.uvs:
            flat = array("f")
            for u, v in mesh.uvs:
                flat.append(u)
                flat.append(v)
            body += flat.tobytes()
        if mesh.normals:
            flat = array("f")
            for x, y, z in mesh.normals:
                flat.append(x)
                flat.append(y)
                flat.append(z)
            body += flat.tobytes()
    blob += body
    from hookymod.util import write_atomic

    # Noms temporaires uniques : deux processus peuvent décoder le même modèle.
    write_atomic(path, bytes(blob))
    return len(blob)


def read(path):
    """Relit un cache de géométrie ; ``None`` s'il est absent ou illisible."""
    from hookymod.mesh import MeshData

    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < len(MAGIC) + 4 or raw[:len(MAGIC)] != MAGIC:
        return None
    offset = len(MAGIC)
    (size,) = struct.unpack_from("<I", raw, offset)
    offset += 4
    try:
        header = json.loads(raw[offset:offset + size].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if int(header.get("version", 0)) != VERSION:
        return None
    offset += size
    meshes = []
    try:
        for entry in header.get("meshes", []):
            count = int(entry["positions"])
            faces = int(entry["faces"])
            uvs = int(entry.get("uvs", 0))
            normals = int(entry.get("normals", 0))

            flat = array("f")
            flat.frombytes(raw[offset:offset + count * 12])
            offset += count * 12
            positions = [(flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2])
                         for i in range(count)]

            indices = array("I")
            indices.frombytes(raw[offset:offset + faces * 12])
            offset += faces * 12
            triangles = [(indices[i * 3], indices[i * 3 + 1], indices[i * 3 + 2])
                         for i in range(faces)]

            mesh = MeshData(name=entry.get("name") or "mesh",
                            material=entry.get("material") or "",
                            positions=positions, faces=triangles)
            if uvs:
                flat = array("f")
                flat.frombytes(raw[offset:offset + uvs * 8])
                offset += uvs * 8
                mesh.uvs = [(flat[i * 2], flat[i * 2 + 1]) for i in range(uvs)]
            if normals:
                flat = array("f")
                flat.frombytes(raw[offset:offset + normals * 12])
                offset += normals * 12
                mesh.normals = [(flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2])
                                for i in range(normals)]
            meshes.append(mesh)
    except (struct.error, ValueError, IndexError):
        return None
    return meshes, header.get("meta", {})


def stamp(path: Path) -> tuple[int, int]:
    """(taille, date) du fichier source, pour la clé de cache."""
    from .cache import file_stamp

    return file_stamp(path)
