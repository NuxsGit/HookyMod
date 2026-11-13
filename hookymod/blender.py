"""Passage de la géométrie vers Blender (OBJ + MTL + PNG, ou glTF 2.0).

Le but est simple : ce que HookyMod lit (BFRES, OBJ) doit arriver dans Blender
avec ses matériaux et ses textures, sans add-on propriétaire et sans dépendance
externe. Deux sorties sont proposées :

* **glTF 2.0** (``.gltf`` + ``.bin`` + PNG) : format standard, importé par
  Blender avec ``Fichier ▸ Importer ▸ glTF 2.0``, textures et matériaux
  compris ;
* **OBJ + MTL** pour les outils qui ne lisent que ça, avec les mêmes PNG.

Les coordonnées sont laissées **telles quelles, Y vers le haut** : c'est la
convention de BFRES *et* de glTF, et les importateurs Blender (OBJ comme glTF)
convertissent en Z-haut tout seuls. Un cylindre posé sur le sol reste donc
debout.

Aucune donnée n'est inventée : si un maillage n'a pas d'UV ou pas de normale,
le champ correspondant est simplement absent de l'export, pour que Blender le
signale au lieu d'afficher une géométrie fausse.
"""

from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path

from .mesh import MeshData, export_obj, triangulated
from .util import HookyError, write_atomic

#: note laissée dans le dossier exporté (utile pour un moddeur pressé)
IMPORT_NOTE = """\
Export HookyMod — import dans Blender
=====================================

glTF (recommandé, garde matériaux + textures) :
  Fichier ▸ Importer ▸ glTF 2.0 → choisir {gltf}

OBJ (plus simple, mêmes PNG à côté) :
  Fichier ▸ Importer ▸ Wavefront (.obj) → choisir {obj}
  puis, si Blender demande : Forward = -Z, Up = Y (réglage par défaut).

Les textures sont les PNG de ce dossier, déjà branchées sur les matériaux
(base color pour l'albedo). Les noms de matériaux viennent du modèle, ce qui
permet de retrouver à quoi correspond chaque partie.

Rien n'est trié ni fusionné : un objet Blender par maillage, comme dans le
fichier d'origine.
"""


# --------------------------------------------------------------------------- util
def _finish_meshes(meshes: list[MeshData]) -> list[MeshData]:
    """Vérifie et complète ce qui peut l'être sans mentir sur le reste."""
    out: list[MeshData] = []
    for index, mesh in enumerate(meshes):
        if not mesh.positions or not mesh.faces:
            continue
        if not mesh.name:
            mesh.name = f"mesh_{index}"
        if mesh.uvs is not None and len(mesh.uvs) != len(mesh.positions):
            mesh.uvs = None
        if mesh.normals is not None and len(mesh.normals) != len(mesh.positions):
            mesh.normals = None
        out.append(mesh)
    if not out:
        raise HookyError("Aucun maillage exploitable : rien à envoyer vers Blender.")
    return out


def _bounds(mesh: MeshData) -> tuple[list[float], list[float]]:
    lo, hi = mesh.bounds()
    return list(lo), list(hi)


# --------------------------------------------------------------------------- glTF
class _Buffer:
    """Accumule les données binaires d'un glTF (un seul tampon, simple)."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.views: list[dict] = []

    def add(self, payload: bytes, target: int | None = None) -> int:
        while len(self.data) % 4:
            self.data.append(0)
        view = {
            "buffer": 0,
            "byteOffset": len(self.data),
            "byteLength": len(payload),
        }
        if target is not None:
            view["target"] = target
        self.views.append(view)
        self.data.extend(payload)
        return len(self.views) - 1


def _write_gltf(meshes: list[MeshData], path: Path,
                textures: dict[str, str] | None = None,
                scale: float = 1.0) -> Path:
    textures = textures or {}
    binary = _Buffer()
    accessors: list[dict] = []
    images: list[dict] = []
    gl_textures: list[dict] = []
    materials: list[dict] = []
    material_index: dict[str, int] = {}
    nodes: list[dict] = []
    gl_meshes: list[dict] = []
    copied: dict[str, str] = {}

    for material_name, source in textures.items():
        if not source or not Path(source).is_file():
            continue
        name = Path(source).name
        if name not in copied:
            try:
                shutil.copyfile(source, path.parent / name)
            except OSError:
                continue
            copied[name] = name
        images.append({"uri": copied[name]})
        gl_textures.append({"source": len(images) - 1, "sampler": 0})

    def material_for(name: str, albedo: str | None) -> int:
        key = name or "Material"
        if key in material_index:
            return material_index[key]
        entry: dict = {
            "name": key,
            "doubleSided": True,
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.8,
            },
        }
        if albedo and Path(albedo).name in copied:
            texture_name = Path(albedo).name
            for index, image in enumerate(images):
                if image["uri"] == texture_name:
                    entry["pbrMetallicRoughness"]["baseColorTexture"] = {"index": index}
                    break
        materials.append(entry)
        material_index[key] = len(materials) - 1
        return material_index[key]

    for index, mesh in enumerate(meshes):
        scale_factor = scale
        positions = [(x * scale_factor, y * scale_factor, z * scale_factor)
                     for x, y, z in mesh.positions]
        flat = bytearray()
        for x, y, z in positions:
            flat += struct.pack("<3f", x, y, z)
        view = binary.add(bytes(flat), 34962)
        accessors.append({
            "bufferView": view,
            "componentType": 5126,
            "count": len(positions),
            "type": "VEC3",
            "min": _bounds(mesh)[0],
            "max": _bounds(mesh)[1],
        })
        position_accessor = len(accessors) - 1
        attributes = {"POSITION": position_accessor}

        if mesh.normals:
            flat = bytearray()
            for nx, ny, nz in mesh.normals:
                flat += struct.pack("<3f", nx, ny, nz)
            view = binary.add(bytes(flat), 34962)
            accessors.append({"bufferView": view, "componentType": 5126,
                              "count": len(mesh.normals), "type": "VEC3"})
            attributes["NORMAL"] = len(accessors) - 1

        if mesh.uvs:
            flat = bytearray()
            for u, v in mesh.uvs:
                flat += struct.pack("<2f", u, 1.0 - v)   # glTF : V vers le haut
            view = binary.add(bytes(flat), 34962)
            accessors.append({"bufferView": view, "componentType": 5126,
                              "count": len(mesh.uvs), "type": "VEC2"})
            attributes["TEXCOORD_0"] = len(accessors) - 1

        triangles = triangulated(mesh)
        if not triangles:
            continue
        indices = bytearray()
        largest = max(max(tri) for tri in triangles)
        wide = largest > 0xFFFF
        for tri in triangles:
            if wide:
                indices += struct.pack("<3I", *tri)
            else:
                indices += struct.pack("<3H", *tri)
        view = binary.add(bytes(indices), 34963)
        accessors.append({
            "bufferView": view,
            "componentType": 5125 if wide else 5123,
            "count": len(triangles) * 3,
            "type": "SCALAR",
        })
        albedo = textures.get(mesh.material or mesh.name) or textures.get(mesh.name)
        primitive = {
            "attributes": attributes,
            "indices": len(accessors) - 1,
            "material": material_for(mesh.material or mesh.name, albedo),
            "mode": 4,
        }
        gl_meshes.append({"name": mesh.name, "primitives": [primitive]})
        nodes.append({"name": mesh.name, "mesh": len(gl_meshes) - 1})

    if not gl_meshes:
        raise HookyError("Aucun triangle à exporter vers Blender.")

    stem = path.with_suffix("")
    bin_name = stem.name + ".bin"
    document = {
        "asset": {"version": "2.0", "generator": "HookyMod"},
        "scene": 0,
        "scenes": [{"name": stem.name, "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gl_meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": binary.views,
        "buffers": [{"uri": bin_name, "byteLength": len(binary.data)}],
    }
    if images:
        document["images"] = images
        document["textures"] = gl_textures
        document["samplers"] = [{"magFilter": 9729, "minFilter": 9987,
                                 "wrapS": 10497, "wrapT": 10497}]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(document, indent=1).encode("utf-8"))
    (path.parent / bin_name).write_bytes(bytes(binary.data))
    return path


# --------------------------------------------------------------------------- API
def export_bundle(meshes: list[MeshData], directory, name: str = "modele",
                  textures: dict[str, str] | None = None,
                  scale: float = 1.0, formats: tuple[str, ...] = ("gltf", "obj")) -> dict[str, Path]:
    """Écrit un dossier prêt pour Blender et renvoie les fichiers créés.

    :param meshes: maillages à exporter (positions, faces, UV, normales).
    :param textures: ``{matériau: chemin du PNG}`` de l'albédo.
    :param scale: facteur appliqué à la géométrie (1.0 = unités du jeu).
    """
    meshes = _finish_meshes(list(meshes))
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    if "gltf" in formats:
        written["gltf"] = _write_gltf(meshes, folder / f"{name}.gltf", textures, scale)

    if "obj" in formats:
        written["obj"] = export_obj(meshes, folder / f"{name}.obj", scale=scale)

    for material_name, source in (textures or {}).items():
        if source and Path(source).is_file():
            try:
                shutil.copyfile(source, folder / Path(source).name)
            except OSError:
                pass

    written["note"] = folder / "IMPORT_BLENDER.txt"
    write_atomic(written["note"], IMPORT_NOTE.format(
        gltf=f"{name}.gltf" if "gltf" in formats else "(non exporté)",
        obj=f"{name}.obj" if "obj" in formats else "(non exporté)",
    ).encode("utf-8"))
    return written


def textures_from_folder(folder, prefix: str = "") -> dict[str, str]:
    """Retrouve les PNG d'albédo déjà décodés à côté d'un modèle.

    On ne devine rien : seuls les fichiers dont le nom contient ``_Alb``
    (ou ``albedo``/``basecolor``) sont proposés comme couleur de base.
    """
    folder = Path(folder)
    found: dict[str, str] = {}
    if not folder.is_dir():
        return found
    for candidate in sorted(folder.glob("*.png")):
        stem = candidate.stem
        if prefix and not stem.startswith(prefix):
            continue
        low = stem.lower()
        if "_alb" in low or "albedo" in low or "basecolor" in low:
            found[stem.replace("_Alb", "")] = str(candidate)
    return found
