"""Import borné de géométrie OBJ et glTF 2.0, sans accès réseau ni dépendance.

Les données importées servent à une réécriture limitée d'un modèle existant ;
les matériaux, textures et animations du fichier externe ne sont pas réinjectés.
Les formats comprimés, maillages déformés et accessors sparse sont refusés plutôt
que d'écrire une géométrie incorrecte. Aucun URI externe n'est chargé depuis le
système de fichiers : seuls les tampons fournis explicitement sont accessibles.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from .mesh import MeshData, triangle_has_area
from .util import HookyError

MAX_BYTES = 64 * 1024 * 1024
MAX_VERTICES = 1_000_000
MAX_TRIANGLES = 2_000_000
MAX_NODES = 20_000
MAX_POLYGON = 1024


@dataclass
class ModelImport:
    meshes: list[MeshData]
    warnings: list[str] = field(default_factory=list)
    # Deux matériaux glTF peuvent porter le même nom (ou aucun nom).
    material_slots: int = 0
    # L'aperçu peut les ignorer, mais une injection ne doit jamais les perdre.
    unsupported_attributes: list[str] = field(default_factory=list)


def _finite(values) -> bool:
    return all(math.isfinite(value) for value in values)


def _float_values(parts, count: int) -> tuple[float, ...]:
    try:
        values = tuple(float(item) for item in parts[:count])
    except (TypeError, ValueError, OverflowError):
        raise HookyError("Coordonnée numérique invalide.") from None
    if len(values) != count or not _finite(values):
        raise HookyError("Coordonnée manquante ou non finie.")
    return values


def _index(text: str, length: int) -> int:
    try:
        value = int(text)
    except ValueError:
        raise HookyError("Indice OBJ invalide.") from None
    result = value - 1 if value > 0 else length + value
    if value == 0 or result < 0 or result >= length:
        raise HookyError("Indice OBJ hors limites.")
    return result


def read_obj(data: bytes) -> ModelImport:
    """Lit les indices séparés v/vt/vn, y compris les coutures et indices négatifs."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise HookyError("L'OBJ doit être encodé en UTF-8.") from None
    positions, uvs, normals = [], [], []
    groups: dict[tuple[str, str], dict] = {}
    name, material = "Objet", ""
    triangles = 0
    vertex_total = 0
    polygons = False
    for raw in text.splitlines():
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        parts = line.split()
        tag, args = parts[0], parts[1:]
        if tag == "v":
            if len(args) not in (3, 4) or (len(args) == 4 and _float_values(args[3:], 1)[0] != 1):
                raise HookyError("Coordonnée OBJ homogène non prise en charge.")
            positions.append(_float_values(args, 3))
            if len(positions) > MAX_VERTICES:
                raise HookyError("OBJ : trop de sommets.")
        elif tag == "vt":
            u, v = _float_values(args, 2)
            # OBJ a une origine V en bas ; les UV internes suivent glTF.
            uvs.append((u, 1.0 - v))
            if len(uvs) > MAX_VERTICES * 2:
                raise HookyError("OBJ : trop de coordonnées UV.")
        elif tag == "vn":
            normals.append(_float_values(args, 3))
            if len(normals) > MAX_VERTICES * 2:
                raise HookyError("OBJ : trop de normales.")
        elif tag in ("o", "g"):
            name = " ".join(args)[:256] or "Objet"
        elif tag == "usemtl":
            material = " ".join(args)[:256]
        elif tag in {"l", "p", "curv", "curv2", "surf"}:
            raise HookyError("L'OBJ contient des lignes, points ou surfaces non triangulaires non pris en charge.")
        elif tag == "f":
            if len(args) < 3 or len(args) > MAX_POLYGON:
                raise HookyError("Face OBJ invalide ou trop complexe.")
            group = groups.setdefault((name, material), {
                "mesh": MeshData(name=name, material=material), "vertices": {},
                "uvs": [], "normals": [], "has_uv": None, "has_normal": None,
            })
            mesh = group["mesh"]
            face = []
            for token in args:
                values = token.split("/")
                if len(values) > 3 or not values[0]:
                    raise HookyError("Face OBJ invalide.")
                p = _index(values[0], len(positions))
                u = _index(values[1], len(uvs)) if len(values) > 1 and values[1] else None
                n = _index(values[2], len(normals)) if len(values) > 2 and values[2] else None
                for key, present in (("has_uv", u is not None), ("has_normal", n is not None)):
                    if group[key] is None:
                        group[key] = present
                    elif group[key] != present:
                        raise HookyError("Face OBJ avec UV ou normales partiellement manquantes.")
                identity = (p, u, n)
                if identity not in group["vertices"]:
                    group["vertices"][identity] = len(mesh.positions)
                    mesh.positions.append(positions[p])
                    if u is not None:
                        group["uvs"].append(uvs[u])
                    if n is not None:
                        group["normals"].append(normals[n])
                    vertex_total += 1
                    if vertex_total > MAX_VERTICES:
                        raise HookyError("OBJ : trop de sommets après séparation des UV.")
                face.append(group["vertices"][identity])
            polygons |= len(face) > 3
            for index in range(1, len(face) - 1):
                tri = (face[0], face[index], face[index + 1])
                if len(set(tri)) != 3:
                    raise HookyError("Face OBJ dégénérée.")
                mesh.faces.append(tri)
                triangles += 1
                if triangles > MAX_TRIANGLES:
                    raise HookyError("OBJ : trop de triangles.")
    meshes = []
    for item in groups.values():
        mesh = item["mesh"]
        mesh.uvs = item["uvs"] if item["has_uv"] else None
        mesh.normals = item["normals"] if item["has_normal"] else None
        if mesh.faces:
            meshes.append(mesh)
    if not meshes:
        raise HookyError("OBJ vide ou sans faces.")
    warnings = (["Polygones OBJ triangulés en éventail : vérifie les formes concaves."]
                if polygons else [])
    return ModelImport(meshes, warnings, material_slots=len({mesh.material for mesh in meshes}))


def _safe_buffer_name(uri: str) -> str:
    if not isinstance(uri, str) or len(uri) > 2048 or "\\" in uri:
        raise HookyError("URI de tampon glTF invalide.")
    parsed = urlsplit(uri)
    name = unquote(parsed.path)
    pieces = PurePosixPath(name).parts
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not pieces
            or name.startswith("/") or "\\" in name or "\x00" in name
            or any(piece in (".", "..") for piece in name.split("/"))):
        raise HookyError("URI de tampon glTF externe ou hors dossier refusé.")
    return pieces[-1]


def _json_document(data: bytes) -> dict:
    try:
        doc = json.loads(data.decode("utf-8-sig").rstrip("\x00 \t\r\n"))
    except (ValueError, UnicodeError, RecursionError):
        raise HookyError("Document glTF JSON invalide.") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("asset"), dict) \
            or str(doc["asset"].get("version", "")).split(".")[0] != "2":
        raise HookyError("Seul glTF 2.0 est pris en charge.")
    required = doc.get("extensionsRequired", [])
    if not isinstance(required, list) or required:
        raise HookyError("Extension glTF obligatoire non prise en charge.")
    return doc


def _glb(data: bytes) -> tuple[dict, bytes | None]:
    if len(data) < 20:
        raise HookyError("GLB tronqué.")
    magic, version, length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise HookyError("En-tête GLB invalide.")
    chunks, offset = [], 12
    while offset < length:
        if offset + 8 > length:
            raise HookyError("Chunk GLB tronqué.")
        size, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        if size % 4 or offset + size > length:
            raise HookyError("Taille de chunk GLB invalide.")
        chunks.append((kind, data[offset:offset + size]))
        offset += size
    if not chunks or chunks[0][0] != 0x4E4F534A:
        raise HookyError("Chunk JSON GLB absent.")
    binary = [chunk for kind, chunk in chunks[1:] if kind == 0x004E4942]
    if len(binary) > 1:
        raise HookyError("Plusieurs tampons GLB internes ne sont pas pris en charge.")
    return _json_document(chunks[0][1]), binary[0] if binary else None


def _list(doc: dict, name: str, limit: int = MAX_NODES) -> list:
    value = doc.get(name, [])
    if not isinstance(value, list) or len(value) > limit:
        raise HookyError(f"Table glTF « {name} » invalide ou trop grande.")
    return value


def _integer(value, what: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise HookyError(f"{what} glTF invalide.")
    return value


def _entry(items: list, index, what: str) -> dict:
    index = _integer(index, what)
    if index >= len(items) or not isinstance(items[index], dict):
        raise HookyError(f"{what} glTF introuvable.")
    return items[index]


def _buffers(doc: dict, embedded: bytes | None, files: dict[str, bytes]) -> list[bytes]:
    buffers = []
    total = 0
    for index, entry in enumerate(_list(doc, "buffers", 16)):
        if not isinstance(entry, dict):
            raise HookyError("Tampon glTF invalide.")
        expected = _integer(entry.get("byteLength"), "Taille du tampon")
        total += expected
        if expected > MAX_BYTES or total > MAX_BYTES:
            raise HookyError("Tampons glTF trop grands au total.")
        uri = entry.get("uri")
        if uri is None:
            if index != 0 or embedded is None:
                raise HookyError("Tampon GLB intégré absent.")
            data = embedded
        elif isinstance(uri, str) and uri.startswith("data:"):
            header, sep, payload = uri.partition(",")
            if not sep or not header.lower().endswith(";base64") or len(payload) > (MAX_BYTES * 4 // 3 + 8):
                raise HookyError("Tampon glTF intégré invalide ou trop grand.")
            try:
                data = base64.b64decode(payload, validate=True)
            except (ValueError, binascii.Error):
                raise HookyError("Tampon glTF base64 invalide.") from None
        else:
            name = _safe_buffer_name(uri)
            if name not in files:
                raise HookyError(f"Tampon glTF manquant : {name}. Sélectionne aussi le fichier .bin.")
            data = files[name]
        if len(data) < expected:
            raise HookyError("Tampon glTF tronqué.")
        buffers.append(data[:expected])
    return buffers


_COMPONENTS = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
               5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _accessor(doc: dict, buffers: list[bytes], index, kind: str, *, indices=False, semantic=""):
    entry = _entry(_list(doc, "accessors", 100_000), index, "Accessor")
    if "sparse" in entry or "bufferView" not in entry:
        raise HookyError("Accessor glTF sparse ou sans tampon non pris en charge.")
    if entry.get("type") != kind:
        raise HookyError(f"Accessor glTF : {kind} attendu.")
    component = entry.get("componentType")
    if component not in _COMPONENTS or (indices and component not in (5121, 5123, 5125)):
        raise HookyError("Type d'accessor glTF non pris en charge.")
    count = _integer(entry.get("count"), "Nombre d'éléments", 1)
    if count > (MAX_TRIANGLES * 3 if indices else MAX_VERTICES):
        raise HookyError("Accessor glTF trop grand.")
    view = _entry(_list(doc, "bufferViews", 100_000), entry["bufferView"], "Vue de tampon")
    buffer_index = _integer(view.get("buffer"), "Tampon")
    if buffer_index >= len(buffers):
        raise HookyError("Tampon glTF introuvable.")
    buf = buffers[buffer_index]
    start = _integer(view.get("byteOffset", 0), "Offset de vue")
    length = _integer(view.get("byteLength"), "Taille de vue")
    offset = _integer(entry.get("byteOffset", 0), "Offset d'accessor")
    fmt, unit = _COMPONENTS[component]
    size = unit * _WIDTHS[kind]
    stride = _integer(view.get("byteStride", size), "Pas d'accessor", 1)
    if (start + length > len(buf) or stride < size or stride > 252 or stride % unit
            or (start + offset) % unit or offset + (count - 1) * stride + size > length):
        raise HookyError("Accessor glTF hors limites ou mal aligné.")
    normalized = entry.get("normalized", False)
    if not isinstance(normalized, bool) or (normalized and (indices or component == 5126)):
        raise HookyError("Normalisation d'accessor glTF invalide.")
    if semantic in ("POSITION", "NORMAL") and component != 5126:
        raise HookyError(f"Format {semantic} glTF non standard ou quantifié non pris en charge.")
    if semantic == "TEXCOORD_0" and not (component == 5126 or (component in (5121, 5123) and normalized)):
        raise HookyError("Format UV glTF non standard ou quantifié non pris en charge.")
    unpack = struct.Struct("<" + fmt * _WIDTHS[kind]).unpack_from
    result = []
    for number in range(count):
        values = unpack(buf, start + offset + number * stride)
        if normalized:
            divisor = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535, 5125: 4294967295}[component]
            values = tuple(max(-1.0, value / divisor) for value in values)
        if not _finite(values):
            raise HookyError("Accessor glTF contient une valeur non finie.")
        result.append(values[0] if indices else values)
    return result


_IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)


def _matmul(a, b):
    return tuple(sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
                 for r in range(4) for c in range(4))


def _node_matrix(node: dict):
    if "matrix" in node:
        if any(key in node for key in ("translation", "rotation", "scale")):
            raise HookyError("Transformations glTF matrix et TRS mélangées.")
        value = node["matrix"]
        if not isinstance(value, list) or len(value) != 16:
            raise HookyError("Matrice glTF invalide.")
        v = _float_values(value, 16)
        matrix = tuple(v[c * 4 + r] for r in range(4) for c in range(4))
    else:
        def vector(key, default):
            value = node.get(key, default)
            if not isinstance(value, list) or len(value) != len(default):
                raise HookyError(f"Transformation glTF « {key} » invalide.")
            return _float_values(value, len(default))
        tx, ty, tz = vector("translation", [0, 0, 0])
        x, y, z, w = vector("rotation", [0, 0, 0, 1])
        sx, sy, sz = vector("scale", [1, 1, 1])
        norm = math.sqrt(x*x + y*y + z*z + w*w)
        if norm < 1e-12:
            raise HookyError("Rotation glTF nulle.")
        x, y, z, w = x/norm, y/norm, z/norm, w/norm
        matrix = ((1-2*(y*y+z*z))*sx, 2*(x*y-z*w)*sy, 2*(x*z+y*w)*sz, tx,
                  2*(x*y+z*w)*sx, (1-2*(x*x+z*z))*sy, 2*(y*z-x*w)*sz, ty,
                  2*(x*z-y*w)*sx, 2*(y*z+x*w)*sy, (1-2*(x*x+y*y))*sz, tz,
                  0., 0., 0., 1.)
    if any(abs(matrix[i]) > 1e-9 for i in (12, 13, 14)) or abs(matrix[15]-1) > 1e-9:
        raise HookyError("Matrice glTF non affine.")
    return matrix


def _transform(mesh: MeshData, matrix) -> MeshData:
    a,b,c,tx, d,e,f,ty, g,h,i,tz, _,_,_,_ = matrix
    determinant = a*(e*i-f*h) - b*(d*i-f*g) + c*(d*h-e*g)
    if abs(determinant) < 1e-15 or not math.isfinite(determinant):
        raise HookyError("Transformation glTF singulière.")
    mesh.positions = [(a*x+b*y+c*z+tx, d*x+e*y+f*z+ty, g*x+h*y+i*z+tz)
                      for x, y, z in mesh.positions]
    if mesh.normals:
        # Comatrice / déterminant : transpose(inverse(M)) pour les normales.
        co = (e*i-f*h, f*g-d*i, d*h-e*g, c*h-b*i, a*i-c*g,
              b*g-a*h, b*f-c*e, c*d-a*f, a*e-b*d)
        output = []
        for x, y, z in mesh.normals:
            nx = (co[0]*x+co[1]*y+co[2]*z)/determinant
            ny = (co[3]*x+co[4]*y+co[5]*z)/determinant
            nz = (co[6]*x+co[7]*y+co[8]*z)/determinant
            length = math.sqrt(nx*nx+ny*ny+nz*nz)
            if length < 1e-12 or not math.isfinite(length):
                raise HookyError("Normale glTF nulle ou invalide.")
            output.append((nx/length, ny/length, nz/length))
        mesh.normals = output
    if determinant < 0:
        mesh.faces = [(a, c, b) for a, b, c in mesh.faces]
    if not all(_finite(point) for point in mesh.positions):
        raise HookyError("Transformation glTF produit des coordonnées non finies.")
    return mesh


def read_gltf(data: bytes, files: dict[str, bytes] | None = None, *, binary=False) -> ModelImport:
    doc, embedded = _glb(data) if binary else (_json_document(data), None)
    buffers = _buffers(doc, embedded, files or {})
    source_meshes = _list(doc, "meshes")
    nodes = _list(doc, "nodes")
    materials = _list(doc, "materials")
    scenes = _list(doc, "scenes")
    if doc.get("skins"):
        raise HookyError("Maillage glTF avec squelette non pris en charge pour l'injection.")
    warnings = []
    if doc.get("animations"):
        warnings.append("Animations glTF ignorées ; seule la pose statique est importée.")
    if scenes:
        scene = _entry(scenes, doc.get("scene", 0), "Scène")
        roots = scene.get("nodes", [])
    else:
        children = set()
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("children", []), list):
                raise HookyError("Nœud glTF invalide.")
            children.update(child for child in node.get("children", []) if type(child) is int)
        roots = [index for index in range(len(nodes)) if index not in children]
    if not isinstance(roots, list) or not roots:
        raise HookyError("Scène glTF vide ou invalide.")
    output = []
    material_slots = set()
    unsupported_attributes: set[str] = set()
    vertex_total = triangle_total = visits = 0

    def visit(index, parent, ancestors):
        nonlocal vertex_total, triangle_total, visits
        index = _integer(index, "Nœud")
        if index in ancestors or visits >= MAX_NODES:
            raise HookyError("Cycle ou trop de nœuds glTF.")
        node = _entry(nodes, index, "Nœud")
        visits += 1
        matrix = _matmul(parent, _node_matrix(node))
        if "skin" in node or "weights" in node:
            raise HookyError("Déformation glTF non prise en charge pour l'injection.")
        if "mesh" in node:
            source = _entry(source_meshes, node["mesh"], "Maillage")
            primitives = source.get("primitives")
            if not isinstance(primitives, list) or not primitives or len(primitives) > MAX_NODES:
                raise HookyError("Primitives glTF absentes ou trop nombreuses.")
            for primitive in primitives:
                if not isinstance(primitive, dict) or primitive.get("mode", 4) != 4:
                    raise HookyError("Seules les primitives glTF en triangles sont prises en charge.")
                if primitive.get("targets"):
                    raise HookyError("Morph targets glTF non pris en charge pour l'injection.")
                attributes = primitive.get("attributes")
                if not isinstance(attributes, dict) or "POSITION" not in attributes:
                    raise HookyError("Positions glTF absentes ou comprimées.")
                if any(key.startswith(("JOINTS_", "WEIGHTS_")) for key in attributes):
                    raise HookyError("Poids de squelette glTF non pris en charge pour l'injection.")
                accessors = _list(doc, "accessors", 100_000)
                position_count = _integer(_entry(accessors, attributes["POSITION"], "Positions").get("count"),
                                          "Nombre de sommets", 1)
                index_count = (_integer(_entry(accessors, primitive["indices"], "Indices").get("count"),
                                        "Nombre d'indices", 1)
                               if "indices" in primitive else position_count)
                if vertex_total + position_count > MAX_VERTICES or triangle_total + index_count // 3 > MAX_TRIANGLES:
                    raise HookyError("Scène glTF trop grande.")
                for semantic in ("NORMAL", "TEXCOORD_0"):
                    if semantic in attributes and _entry(accessors, attributes[semantic], semantic).get("count") != position_count:
                        raise HookyError("Attributs glTF de longueurs différentes.")
                extra_attributes = set(attributes) - {"POSITION", "NORMAL", "TEXCOORD_0"}
                unsupported_attributes.update(extra_attributes)
                positions = _accessor(doc, buffers, attributes["POSITION"], "VEC3", semantic="POSITION")
                normals = (_accessor(doc, buffers, attributes["NORMAL"], "VEC3", semantic="NORMAL")
                           if "NORMAL" in attributes else None)
                uvs = (_accessor(doc, buffers, attributes["TEXCOORD_0"], "VEC2", semantic="TEXCOORD_0")
                       if "TEXCOORD_0" in attributes else None)
                if (normals is not None and len(normals) != len(positions)) or \
                        (uvs is not None and len(uvs) != len(positions)):
                    raise HookyError("Attributs glTF de longueurs différentes.")
                indices = (_accessor(doc, buffers, primitive["indices"], "SCALAR", indices=True)
                           if "indices" in primitive else list(range(len(positions))))
                if len(indices) % 3 or not indices or any(value >= len(positions) for value in indices):
                    raise HookyError("Indices glTF invalides ou hors limites.")
                faces = [tuple(indices[i:i+3]) for i in range(0, len(indices), 3)]
                if any(len(set(face)) != 3 for face in faces):
                    raise HookyError("Triangle glTF dégénéré.")
                vertex_total += len(positions)
                triangle_total += len(faces)
                if vertex_total > MAX_VERTICES or triangle_total > MAX_TRIANGLES:
                    raise HookyError("Scène glTF trop grande.")
                material = ""
                if "material" in primitive:
                    material_entry = _entry(materials, primitive["material"], "Matériau")
                    material_slots.add(primitive["material"])
                    material = str(material_entry.get("name", ""))[:256]
                else:
                    material_slots.add(None)
                mesh = MeshData(name=str(node.get("name") or source.get("name") or "Objet")[:256],
                                material=material, positions=positions, normals=normals, uvs=uvs, faces=faces)
                output.append(_transform(mesh, matrix))
        children = node.get("children", [])
        if not isinstance(children, list):
            raise HookyError("Enfants glTF invalides.")
        for child in children:
            visit(child, matrix, ancestors | {index})

    for root in roots:
        visit(root, _IDENTITY, set())
    if not output:
        raise HookyError("Scène glTF sans triangles.")
    unsupported = sorted(unsupported_attributes)
    if unsupported:
        names = ", ".join(name[:80] for name in unsupported[:8])
        if len(unsupported) > 8:
            names += ", …"
        warnings.append(f"Attributs glTF non conservables ({names}) : aperçu seulement, injection refusée.")
    return ModelImport(output, warnings, material_slots=len(material_slots),
                       unsupported_attributes=unsupported)


def read_model(filename: str, data: bytes, files: dict[str, bytes] | None = None, *,
               axis: str = "keep", scale: float = 1.0) -> ModelImport:
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise HookyError("Fichier vide ou trop volumineux.")
    if axis not in ("keep", "z_up") or not math.isfinite(scale) or not 0.001 <= scale <= 1000:
        raise HookyError("Axes ou échelle d'import invalides.")
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix == ".obj":
        result = read_obj(data)
    elif suffix in (".gltf", ".glb"):
        result = read_gltf(data, files, binary=suffix == ".glb")
    else:
        raise HookyError("Choisis un fichier .obj, .gltf ou .glb.")
    for mesh in result.meshes:
        if axis == "z_up":
            mesh.positions = [(x, z, -y) for x, y, z in mesh.positions]
            if mesh.normals:
                mesh.normals = [(x, z, -y) for x, y, z in mesh.normals]
        if scale != 1:
            mesh.positions = [(x*scale, y*scale, z*scale) for x, y, z in mesh.positions]
    if not all(_finite(point) for mesh in result.meshes for point in mesh.positions):
        raise HookyError("Coordonnées importées non finies.")
    for mesh in result.meshes:
        if any(not triangle_has_area(*(mesh.positions[index] for index in face)) for face in mesh.faces):
            raise HookyError("Triangle source dégénéré ou d'aire nulle.")
    return result
