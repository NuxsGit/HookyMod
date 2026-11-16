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

import copy
import json
import os
import shutil
import struct
import sys
import tempfile
from array import array
from pathlib import Path
from urllib.parse import quote

from .mesh import MeshData, export_obj, triangulated
from .util import HookyError, write_atomic

NO_UV_WARNING = "Certaines formes sans UV n'ont pas de texture liée dans GLB/glTF ; PNG à vérifier."

#: note laissée dans le dossier exporté (utile pour un moddeur pressé)
IMPORT_NOTE = """\
Export HookyMod — import dans Blender
=====================================

GLB autonome (géométrie, matériaux et images associées intégrées) :
  Fichier ▸ Importer ▸ glTF 2.0 → choisir {glb}
  Les PNG non associés restent séparés dans le dossier.

glTF séparé (garder le .bin et les PNG à côté) :
  Fichier ▸ Importer ▸ glTF 2.0 → choisir {gltf}

OBJ (statique, garder le .mtl et les PNG à côté) :
  Fichier ▸ Importer ▸ Wavefront (.obj) → choisir {obj}, pas le .mtl.
  Si Blender demande : Forward = -Z, Up = Y (réglage par défaut).
  OBJ/MTL ne stockent ni squelette ni animation.

{animation_note}

Les cartes reconnues et disponibles peuvent être exportées en PNG. Leurs
rôles sont déduits des noms et doivent être vérifiés :
  _Alb  → Base Color          (sRGB)
  _Nrm  → Normal Map          (Non-Color, nœud Normal Map)
  _Rgh  → Roughness           (Non-Color)
  _Mtl  → Metallic            (Non-Color)
  _AO   → Occlusion / facteur (Non-Color)
  _Emi  → Emission            (sRGB)
  _Spc, _Msk → pas d'équivalent glTF, à régler à la main
  *_MetalRough.png → emballage glTF : G = rugosité, B = métal

Le glTF peut relier baseColor, normal, metallicRoughness, occlusion et
emissive si l'association est connue. Le MTL référence la couleur (map_Kd) et
la normale via map_Bump : Blender l'importe comme carte de normales, mais
certains logiciels peuvent l'interpréter comme relief. Vérifier le branchement ;
les autres cartes sont à raccorder manuellement. Les cartes manquantes ou
ambiguës ne sont pas reliées arbitrairement. Consulter TEXTURES.md
et RAPPORT_MODELE.txt s'ils sont présents, ainsi que le rapport de l'application.
Les noms et matériaux non reconnus peuvent être approximatifs.

Orientation des UV (V) : chaque format suit sa spécification, donc les deux
fichiers ne portent pas le même V — c'est normal, ne « corrige » pas l'un avec
l'autre :
  * .gltf : V d'origine (0 = haut de l'image, comme le jeu) ;
  * .obj  : V retourné (0 = bas de l'image, convention OpenGL).

Si une texture semble à l'envers dans Blender : ouvre « Vérifier les UV » dans
HookyCreator (le maillage tracé sur l'atlas montre tout de suite l'orientation),
plutôt que de retourner l'image dans un éditeur.

L'import produit un objet par maillage exporté. Les triangles dégénérés
(indices répétés) restent dans les fichiers ; certains logiciels les écartent à
l'import. Des parties ou attributs du fichier d'origine peuvent manquer selon
la version et la méthode de décodage.
"""


# --------------------------------------------------------------------------- util
def _finish_meshes(meshes: list[MeshData]) -> list[MeshData]:
    """Vérifie et complète ce qui peut l'être sans mentir sur le reste."""
    out: list[MeshData] = []
    for index, mesh in enumerate(meshes):
        if not mesh.faces:
            continue
        if not mesh.positions:
            raise HookyError(f"Maillage {mesh.name} sans sommets : export refusé.")
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
    """``Weapon_Sword_Example_Alb`` → ``Weapon_Sword_Example`` (et variantes)."""
    for suffix in ALBEDO_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _normalised_textures(textures: dict[str, str] | None) -> dict[str, str]:
    """Indexe les PNG par nom de fichier **et** par nom sans suffixe.

    Le nom d'un matériau (``Mt_Blade``) ne contient presque jamais celui de la
    texture (``Weapon_Sword_Example_Alb``) : sans cette normalisation, le glTF
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


def _build_gltf(meshes: list[MeshData], path: Path,
                textures: dict[str, str] | None = None,
                scale: float = 1.0,
                maps: dict[str, str] | None = None,
                material_maps: dict[str, dict[str, str]] | None = None) -> tuple[dict, _Buffer]:
    """``textures`` : albédo par matériau ; ``maps`` : {rôle: fichier} du modèle.

    Les rôles reconnus par glTF : ``albedo`` (baseColor), ``normal``,
    ``metalrough`` (G = rugosité, B = métal), ``occlusion``, ``emissive``.
    """
    textures = textures or {}
    maps = {role: Path(name).name for role, name in (maps or {}).items() if name}
    binary = _Buffer()
    accessors: list[dict] = []
    images: list[dict] = []
    gl_textures: list[dict] = []
    materials: list[dict] = []
    material_index: dict[tuple[str, bool], int] = {}
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

    # cartes du modèle (normale, metallicRoughness, occlusion, émission…) :
    # elles sont communes aux matériaux, contrairement à l'albédo qui peut
    # changer d'un matériau à l'autre
    map_index: dict[str, int] = {}
    for role in ("normal", "metalrough", "occlusion", "emissive"):
        name = maps.get(role)
        if not name:
            continue
        source = path.parent / name
        if not source.is_file():
            continue
        if name not in copied:
            try:
                if source.resolve() != (path.parent / name).resolve():
                    shutil.copyfile(source, path.parent / name)
            except OSError:
                pass
            copied[name] = name
            images.append({"uri": name})
            gl_textures.append({"source": len(images) - 1, "sampler": 0})
        map_index[role] = [image["uri"] for image in images].index(name)

    # La voie par matériau ne retombe jamais sur la première carte d'un autre
    # matériau. Les images sont enregistrées une seule fois, même partagées.
    per_material: dict[str, dict[str, int]] = {}
    if material_maps is not None:
        for material, roles in material_maps.items():
            chosen: dict[str, int] = {}
            for role, source in roles.items():
                if role not in ("albedo", "normal", "metalrough", "occlusion", "emissive"):
                    continue
                name = Path(source).name
                if not (path.parent / name).is_file():
                    continue
                if name not in copied:
                    copied[name] = name
                    images.append({"uri": name})
                    gl_textures.append({"source": len(images) - 1, "sampler": 0})
                chosen[role] = next(i for i, image in enumerate(images) if image["uri"] == name)
            per_material[material] = chosen

    single_albedo = None
    if material_maps is None and len({str(path) for path in textures.values()}) == 1:
        single_albedo = next(iter(textures.values()))

    def material_for(name: str, albedo: str | None, has_uv: bool) -> int:
        material_name = name or "Material"
        key = (material_name, has_uv)
        if key in material_index:
            return material_index[key]
        entry: dict = {
            "name": material_name,
            "doubleSided": True,
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.8,
            },
        }
        # Une primitive sans UV ne peut pas utiliser TEXCOORD_0 : conserver un
        # matériau distinct sans texture plutôt que produire un glTF invalide.
        chosen = ((per_material.get(material_name, {}) if material_maps is not None else map_index)
                  if has_uv else {})
        if material_maps is not None and "albedo" in chosen:
            entry["pbrMetallicRoughness"]["baseColorTexture"] = {"index": chosen["albedo"]}
        elif material_maps is None and has_uv:
            if albedo is None:
                albedo = textures.get(material_name) or textures.get(_base_name(material_name)) or single_albedo
            if albedo and Path(albedo).name in copied:
                texture_name = Path(albedo).name
                for index, image in enumerate(images):
                    if image["uri"] == texture_name:
                        entry["pbrMetallicRoughness"]["baseColorTexture"] = {"index": index}
                        break
        if "metalrough" in chosen:
            # les facteurs multiplient la texture : 1.0 = « c'est la carte qui
            # décide », sinon Blender assombrit ou rend mat malgré l'image
            entry["pbrMetallicRoughness"]["metallicRoughnessTexture"] = {
                "index": chosen["metalrough"]}
            entry["pbrMetallicRoughness"]["metallicFactor"] = 1.0
            entry["pbrMetallicRoughness"]["roughnessFactor"] = 1.0
        if "normal" in chosen:
            entry["normalTexture"] = {"index": chosen["normal"], "scale": 1.0}
        if "occlusion" in chosen:
            entry["occlusionTexture"] = {"index": chosen["occlusion"], "strength": 1.0}
        if "emissive" in chosen:
            entry["emissiveTexture"] = {"index": chosen["emissive"]}
            entry["emissiveFactor"] = [1.0, 1.0, 1.0]
        materials.append(entry)
        material_index[key] = len(materials) - 1
        return material_index[key]

    for index, mesh in enumerate(meshes):
        # une seule mise à l'échelle, un seul tableau binaire (array), et les
        # bornes calculées une fois : la version précédente reconstruisait les
        # positions deux fois et appelait _bounds() deux fois par maillage
        flat = array("f", [value * scale for point in mesh.positions
                           for value in point])
        positions_bytes = _little_bytes(flat)
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
            view = binary.add(_little_bytes(flat), 34962)
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
            view = binary.add(_little_bytes(flat), 34962)
            accessors.append({"bufferView": view, "componentType": 5126,
                              "count": len(mesh.uvs), "type": "VEC2"})
            attributes["TEXCOORD_0"] = len(accessors) - 1

        triangles = triangulated(mesh)
        if not triangles:
            continue
        largest = max(max(tri) for tri in triangles)
        # 0xffff est réservé au primitive restart et interdit dans un
        # accessor glTF uint16 ; promouvoir même quand il est le maximum.
        wide = largest >= 0xFFFF
        indices = array("I" if wide else "H",
                        [value for tri in triangles for value in tri])
        view = binary.add(_little_bytes(indices), 34963)
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
            "material": material_for(mesh.material or mesh.name, albedo, bool(mesh.uvs)),
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
    return document, binary


def _little_bytes(values: array) -> bytes:
    """glTF est toujours little-endian, quelle que soit la plateforme hôte."""
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def _write_gltf_files(document: dict, binary: _Buffer, path: Path) -> None:
    # Les URI glTF sont des URI, non des noms bruts : les accents, espaces,
    # « # » et « % » doivent survivre au chargement dans d'autres logiciels.
    external = copy.deepcopy(document)
    external["buffers"][0]["uri"] = quote(gltf_bin_path(path).name, safe="-._~")
    external["buffers"][0]["byteLength"] = len(binary.data)
    for image in external.get("images", []):
        image["uri"] = quote(image["uri"], safe="-._~")
    write_atomic(path, json.dumps(external, indent=1, allow_nan=False).encode("utf-8"))
    write_atomic(gltf_bin_path(path), bytes(binary.data))


def _write_glb(document: dict, binary: _Buffer, path: Path) -> None:
    """GLB 2.0 autonome : un tampon unique, images incluses et alignées sur 4."""
    embedded = copy.deepcopy(document)
    embedded["buffers"][0].pop("uri", None)
    size = len(binary.data)
    images = []
    for image in embedded.get("images", []):
        source = path.parent / image.pop("uri")
        with source.open("rb") as stream:
            signature = stream.read(8)
        if signature.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif signature.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        else:
            raise HookyError(f"Image GLB non prise en charge : {source.name} (PNG ou JPEG attendu).")
        length = source.stat().st_size
        if not length:
            raise HookyError(f"Image GLB vide : {source.name}.")
        size += -size % 4
        view = {"buffer": 0, "byteOffset": size, "byteLength": length}
        image.update({"name": source.name, "bufferView": len(embedded["bufferViews"]), "mimeType": mime})
        embedded["bufferViews"].append(view)
        images.append((source, size, length))
        size += length
    embedded["buffers"][0]["byteLength"] = size
    json_bytes = json.dumps(embedded, ensure_ascii=True, separators=(",", ":"),
                            allow_nan=False).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    bin_size = size + (-size % 4)
    total = 12 + 8 + len(json_bytes) + 8 + bin_size
    if total > 0xFFFFFFFF:
        raise HookyError("GLB trop volumineux (limite du format : 4 Gio).")
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".hookymod-glb-")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(struct.pack("<4sII", b"glTF", 2, total))
            output.write(struct.pack("<I4s", len(json_bytes), b"JSON"))
            output.write(json_bytes)
            output.write(struct.pack("<I4s", bin_size, b"BIN\0"))
            output.write(binary.data)
            cursor = len(binary.data)
            for source, offset, length in images:
                output.write(b"\0" * (offset - cursor))
                with source.open("rb") as stream:
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise HookyError(f"Image modifiée pendant l'export GLB : {source.name}.")
                        output.write(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        raise HookyError(f"Image modifiée pendant l'export GLB : {source.name}.")
                cursor = offset + length
            output.write(b"\0" * (bin_size - cursor))
        from .util import _replace_atomic
        _replace_atomic(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def gltf_bin_path(path) -> Path:
    """Chemin du ``.bin`` associé à un ``.gltf`` (même nom, autre extension)."""
    path = Path(path)
    return path.parent / (path.with_suffix("").name + ".bin")


# --------------------------------------------------------------------------- API
def export_bundle(meshes: list[MeshData], directory, name: str = "modele",
                  textures: dict[str, str] | None = None,
                  scale: float = 1.0, formats: tuple[str, ...] = ("gltf", "obj"),
                  maps: dict[str, str] | None = None,
                  material_maps: dict[str, dict[str, str]] | None = None,
                  warnings: list[str] | None = None,
                  animation: dict | None = None) -> dict[str, Path]:
    """Écrit un dossier prêt pour Blender et renvoie les fichiers créés.

    :param meshes: maillages à exporter (positions, faces, UV, normales).
    :param textures: ``{matériau: chemin du PNG}`` de l'albédo.
    :param scale: facteur appliqué à la géométrie (1.0 = unités du jeu).
    :param maps: ``{rôle: nom du PNG}`` pour les autres cartes (normale,
        metallicRoughness, occlusion, émission) — branchées sur les matériaux.
    """
    meshes = _finish_meshes(list(meshes))
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    if animation is not None and not ({"gltf", "glb"} & set(formats)):
        raise HookyError("Un clip animé nécessite un export glTF ou GLB ; OBJ reste statique.")
    if {"gltf", "glb"} & set(formats):
        gltf = folder / f"{name}.gltf"
        document, binary = _build_gltf(meshes, gltf, textures, scale,
                                       maps=maps, material_maps=material_maps)
        if animation is not None:
            from . import gltf_animation
            gltf_animation.add_to_gltf(document, binary, meshes, animation, scale)
        if "glb" in formats:
            written["glb"] = folder / f"{name}.glb"
            _write_glb(document, binary, written["glb"])
        if "gltf" in formats:
            _write_gltf_files(document, binary, gltf)
            written["gltf"] = gltf
            written["bin"] = gltf_bin_path(gltf)

    if "obj" in formats:
        albedos = ({name: roles["albedo"] for name, roles in material_maps.items() if roles.get("albedo")}
                   if material_maps is not None else
                   {material: Path(source).name for material, source in (textures or {}).items() if source})
        normals = ({name: roles["normal"] for name, roles in material_maps.items() if roles.get("normal")}
                   if material_maps is not None else (maps or {}).get("normal", ""))
        written["obj"] = export_obj(meshes, folder / f"{name}.obj", scale=scale,
                                    albedo_map=albedos, normal_map=normals,
                                    strict_materials=material_maps is not None)
        written["mtl"] = written["obj"].with_suffix(".mtl")

    for material_name, source in (textures or {}).items():
        if source and Path(source).is_file():
            try:
                shutil.copyfile(source, folder / Path(source).name)
            except OSError:
                pass

    written["note"] = folder / "IMPORT_BLENDER.txt"
    animation_note = (
        "Le clip sélectionné et le squelette sont présents dans GLB/glTF. "
        "Conversion expérimentale à 30 images/s par convention : courbes cubiques "
        "échantillonnées et rotations Euler converties en quaternions ; vérifier le mouvement dans l'outil cible. "
        "La boucle se règle dans le logiciel de lecture."
        if animation is not None else
        "Aucun clip n'a été demandé : GLB et glTF sont statiques pour cet export.")
    if animation is not None and animation.get("repeated_keys"):
        animation_note += (" Des images-clés partagent un même instant ; un saut éventuel "
                           "est approché par une très courte transition en glTF. Certains logiciels "
                           "peuvent fusionner des clés trop proches.")
    write_atomic(written["note"], IMPORT_NOTE.format(
        glb=f"{name}.glb" if "glb" in formats else "(non exporté)",
        gltf=f"{name}.gltf" if "gltf" in formats else "(non exporté)",
        obj=f"{name}.obj" if "obj" in formats else "(non exporté)",
        animation_note=animation_note,
    ).encode("utf-8"))
    if warnings is not None:
        # Le ZIP doit conserver les limites de décodage après téléchargement.
        notes = list(warnings[:100])
        if len(warnings) > 100:
            notes.append(f"{len(warnings) - 100} remarque(s) supplémentaires omises.")
        if (any(not mesh.uvs for mesh in meshes) and (textures or maps or material_maps)
                and NO_UV_WARNING not in notes):
            notes.append(NO_UV_WARNING)
        lines = ["Rapport de l'export du modèle", "",
                 "Vérifier les matériaux, la géométrie et le résultat dans l'application cible.",
                 "La réussite de l'export ne garantit pas la fidélité ni le fonctionnement en jeu.", ""]
        lines += [f"- {note}" for note in notes] if notes else ["Aucune remarque particulière disponible."]
        written["report"] = folder / "RAPPORT_MODELE.txt"
        write_atomic(written["report"], ("\n".join(lines) + "\n").encode("utf-8"))
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
