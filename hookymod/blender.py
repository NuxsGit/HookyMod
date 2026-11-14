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
from array import array
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

Orientation des UV (V) : chaque format suit sa spécification, donc les deux
fichiers ne portent pas le même V — c'est normal, ne « corrige » pas l'un avec
l'autre :
  * .gltf : V d'origine (0 = haut de l'image, comme le jeu) ;
  * .obj  : V retourné (0 = bas de l'image, convention OpenGL).

Si une texture semble à l'envers dans Blender : ouvre « Vérifier les UV » dans
HookyCreator (le maillage tracé sur l'atlas montre tout de suite l'orientation),
plutôt que de retourner l'image dans un éditeur.

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


#: suffixes des textures du jeu (albédo) que l'on sait reconnaître
ALBEDO_SUFFIXES = ("_Alb", "_Albedo", "_Alb_0", "_BC", "_BaseColor", "_Dif")


def _base_name(name: str) -> str:
    """``Weapon_Sword_Kokiri_Alb`` → ``Weapon_Sword_Kokiri`` (et variantes)."""
    for suffix in ALBEDO_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _normalised_textures(textures: dict[str, str] | None) -> dict[str, str]:
    """Indexe les PNG par nom de fichier **et** par nom sans suffixe.

    Le nom d'un matériau (``Mt_Blade``) ne contient presque jamais celui de la
    texture (``Weapon_Sword_Kokiri_Alb``) : sans cette normalisation, le glTF
    sortait sans aucune texture alors que les PNG étaient bien exportés.
    """
    if not textures:
        return {}
    out: dict[str, str] = {}
    for key, source in textures.items():
        if not source:
            continue
        stem = Path(source).stem
        for name in (key, stem, _base_name(stem)):
            if name:
                out.setdefault(name, source)
    return out


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

    for material_name, source in _normalised_textures(textures).items():
        if not source or not Path(source).is_file():
            continue
        name = Path(source).name
        if name in copied:
            continue                    # une seule image par fichier
        target_image = path.parent / name
        try:
            # les PNG arrivent souvent déjà dans le dossier d'export : les
            # recopier sur eux-mêmes levait SameFileError, et le glTF sortait
            # sans aucune texture alors que les images étaient là
            if target_image.resolve() != Path(source).resolve():
                shutil.copyfile(source, target_image)
        except OSError:
            continue
        copied[name] = name
        images.append({"uri": name})
        gl_textures.append({"source": len(images) - 1, "sampler": 0})

    single_albedo = None
    if len({str(path) for path in textures.values()}) == 1:
        single_albedo = next(iter(textures.values()))

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
        if albedo is None:
            albedo = (textures.get(key) or textures.get(_base_name(key))
                      or single_albedo)
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
        # une seule mise à l'échelle, un seul tableau binaire (array), et les
        # bornes calculées une fois : la version précédente reconstruisait les
        # positions deux fois et appelait _bounds() deux fois par maillage
        flat = array("f", [value * scale for point in mesh.positions
                           for value in point])
        positions_bytes = flat.tobytes()
        view = binary.add(positions_bytes, 34962)
        lo, hi = mesh.bounds()
        accessors.append({
            "bufferView": view,
            "componentType": 5126,
            "count": len(mesh.positions),
            "type": "VEC3",
            "min": [value * scale for value in lo],
            "max": [value * scale for value in hi],
        })
        position_accessor = len(accessors) - 1
        attributes = {"POSITION": position_accessor}

        if mesh.normals:
            flat = array("f", [value for normal in mesh.normals for value in normal])
            view = binary.add(flat.tobytes(), 34962)
            accessors.append({"bufferView": view, "componentType": 5126,
                              "count": len(mesh.normals), "type": "VEC3"})
            attributes["NORMAL"] = len(accessors) - 1

        if mesh.uvs:
            # glTF : l'origine (0,0) est le **coin supérieur gauche** de l'image
            # (spécification glTF 2.0, §3.8.3), exactement comme les UV du jeu.
            # Retourner V ici produisait une texture à l'envers dans Blender :
            # l'importeur glTF de Blender applique déjà la conversion vers son
            # propre repère (V vers le haut).
            flat = array("f")
            for u, v in mesh.uvs:
                flat.append(u)
                flat.append(v)
            view = binary.add(flat.tobytes(), 34962)
            accessors.append({"bufferView": view, "componentType": 5126,
                              "count": len(mesh.uvs), "type": "VEC2"})
            attributes["TEXCOORD_0"] = len(accessors) - 1

        triangles = triangulated(mesh)
        if not triangles:
            continue
        largest = max(max(tri) for tri in triangles)
        wide = largest > 0xFFFF
        indices = array("I" if wide else "H",
                        [value for tri in triangles for value in tri])
        view = binary.add(indices.tobytes(), 34963)
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
    binary_path = path.parent / bin_name
    binary_path.write_bytes(bytes(binary.data))
    return path


def gltf_bin_path(path) -> Path:
    """Chemin du ``.bin`` associé à un ``.gltf`` (même nom, autre extension)."""
    path = Path(path)
    return path.parent / (path.with_suffix("").name + ".bin")


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
        gltf = _write_gltf(meshes, folder / f"{name}.gltf", textures, scale)
        written["gltf"] = gltf
        binary = gltf_bin_path(gltf)
        if binary.is_file():
            written["bin"] = binary

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
