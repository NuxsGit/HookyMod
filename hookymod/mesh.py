"""Géométrie : maillages, normales, export OBJ (utilisé par le viewer et Blender)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .util import HookyError, write_atomic

Vec3 = tuple[float, float, float]
Vec2 = tuple[float, float]


def triangle_has_area(a: Vec3, b: Vec3, c: Vec3) -> bool:
    """Teste une aire non nulle sans débordement pour des coordonnées finies.

    Les arêtes sont mises à l'échelle avant le produit vectoriel : une unité
    minuscule ne doit pas être confondue avec une face d'aire nulle.
    """
    edges = tuple(b[i] - a[i] for i in range(3)) + tuple(c[i] - a[i] for i in range(3))
    if not all(math.isfinite(value) for value in edges):
        return False
    scale = max(abs(value) for value in edges)
    if scale == 0:
        return False
    ux, uy, uz, vx, vy, vz = (value / scale for value in edges)
    return (uy * vz - uz * vy != 0 or uz * vx - ux * vz != 0
            or ux * vy - uy * vx != 0)


@dataclass
class MeshData:
    """Un maillage triangulaire prêt à afficher / exporter."""

    name: str = "mesh"
    positions: list[Vec3] = field(default_factory=list)
    faces: list[tuple[int, ...]] = field(default_factory=list)
    uvs: list[Vec2] | None = None
    normals: list[Vec3] | None = None
    colors: list[tuple[float, float, float]] | None = None
    material: str = ""

    # ------------------------------------------------------------------ infos
    def __len__(self) -> int:
        return len(self.faces)

    def bounds(self) -> tuple[Vec3, Vec3]:
        if not self.positions:
            raise HookyError("Maillage vide.")
        xs, ys, zs = zip(*self.positions)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def center_radius(self) -> tuple[Vec3, float]:
        lo, hi = self.bounds()
        center = tuple((lo[i] + hi[i]) / 2 for i in range(3))
        radius = max(hi[i] - lo[i] for i in range(3)) / 2 or 1.0
        return center, radius  # type: ignore[return-value]

    def face_normal(self, face: tuple[int, ...]) -> Vec3:
        try:
            a = self.positions[face[0]]
            b = self.positions[face[1]]
            c = self.positions[face[2]]
        except IndexError:
            return (0.0, 0.0, 1.0)
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        return (nx / length, ny / length, nz / length)

    def ensure_normals(self) -> None:
        """Calcule des normales lissées si le modèle n'en fournit pas."""
        if self.normals and len(self.normals) == len(self.positions):
            return
        acc: list[list[float]] = [[0.0, 0.0, 0.0] for _ in self.positions]
        for face in self.faces:
            normal = self.face_normal(face)
            for idx in face:
                if 0 <= idx < len(acc):
                    acc[idx][0] += normal[0]
                    acc[idx][1] += normal[1]
                    acc[idx][2] += normal[2]
        normals: list[Vec3] = []
        for nx, ny, nz in acc:
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            normals.append((nx / length, ny / length, nz / length))
        self.normals = normals

    def scale(self, factor: float) -> None:
        self.positions = [(x * factor, y * factor, z * factor) for x, y, z in self.positions]

    def merged(self, other: "MeshData") -> "MeshData":
        offset = len(self.positions)
        positions = self.positions + other.positions
        faces = self.faces + [tuple(i + offset for i in f) for f in other.faces]
        normals = None
        if self.normals and other.normals and len(other.normals) == len(other.positions):
            normals = self.normals + other.normals
        uvs = None
        if self.uvs and other.uvs and len(other.uvs) == len(other.positions):
            uvs = self.uvs + other.uvs
        colors = None
        if self.colors and other.colors:
            colors = self.colors + other.colors
        return MeshData(name=f"{self.name}+{other.name}", positions=positions,
                        faces=faces, uvs=uvs, normals=normals, colors=colors)


def iter_triangles(mesh: "MeshData"):
    """Triangles, avec éventuel découpage en éventail, sans perte silencieuse.

    Un triangle dégénéré reste présent dans les fichiers exportés, même si un
    logiciel peut l'ignorer à l'import. Une face invalide refuse tout l'export.
    """
    count = len(mesh.positions)
    for face in mesh.faces:
        if len(face) < 3 or any(not isinstance(i, int) or not 0 <= i < count for i in face):
            raise HookyError(f"Face invalide dans {mesh.name} : export refusé sans retirer de faces.")
        for i in range(1, len(face) - 1):
            yield (face[0], face[i], face[i + 1])


def triangulated(mesh: "MeshData") -> list[tuple[int, int, int]]:
    """Construit la liste des triangles vérifiés d'un maillage."""
    return list(iter_triangles(mesh))


def merge_meshes(meshes: list[MeshData], name: str = "model") -> MeshData:
    if not meshes:
        raise HookyError("Aucun maillage à fusionner.")
    result = MeshData(name=name)
    for mesh in meshes:
        if not mesh.faces:
            continue
        result = result.merged(mesh) if result.positions else mesh
    return result


def _fmt(value: float) -> str:
    return f"{value:.9g}"


def export_obj(meshes: list[MeshData], path, mtl: bool = True,
               scale: float = 1.0, up_axis: str = "y",
               albedo_map: dict[str, str] | None = None,
               normal_map: str | dict[str, str] = "",
               strict_materials: bool = False) -> Path:
    """Écrit un fichier OBJ (+ MTL) lisible par Blender, Maya, etc.

    L'OBJ est un format d'échange ouvert par de nombreux logiciels 3D.

    La version précédente appelait un formateur **trois fois par sommet** :
    15 millions d'appels pour une armure, 13,7 s d'écriture. Ici un sommet
    coûte une seule chaîne formatée, et les faces sont formatées en bloc.

    :param albedo_map: ``{matériau: fichier PNG}`` → ``map_Kd`` dans le MTL.
    :param normal_map: fichier PNG de normale → ``map_Bump`` dans le MTL.
        Blender l'interprète comme carte de normales ; les autres logiciels
        peuvent l'interpréter comme carte de relief, à vérifier après import.
    """
    path = Path(path)
    chunks: list[str] = ["# Exporté par HookyMod / Asset Explorer", ""]
    mtl_lines: list[str] = ["# Matériaux générés par HookyMod", ""]
    use_mtl = mtl and path.suffix.lower() == ".obj"
    if use_mtl:
        chunks.append(f"mtllib {path.with_suffix('.mtl').name}")
        chunks.append("")

    vertex_offset = 0
    uv_offset = 0
    normal_offset = 0
    palette = [(0.82, 0.82, 0.85), (0.85, 0.72, 0.45), (0.55, 0.75, 0.55),
               (0.72, 0.55, 0.75), (0.55, 0.7, 0.85), (0.9, 0.6, 0.55)]
    z_up = up_axis == "z"

    for index, mesh in enumerate(meshes):
        if not mesh.positions:
            if mesh.faces:
                raise HookyError(f"Maillage {mesh.name} sans sommets : export refusé.")
            continue
        name = (mesh.name or f"mesh_{index}").replace(" ", "_")
        chunks.append(f"g {name}")
        material = mesh.material or mesh.name or f"mat_{index}"
        color = palette[index % len(palette)]
        if use_mtl:
            chunks.append(f"usemtl {material}")
            albedo_file = ((albedo_map or {}).get(material, "") if strict_materials
                           else _albedo_for(material, albedo_map or {}))
            # Kd multiplie map_Kd : garder le blanc, sans teinter l'image.
            if albedo_file:
                color = (1., 1., 1.)
            mtl_lines += [f"newmtl {material}",
                          f"Kd {color[0]:.3f} {color[1]:.3f} {color[2]:.3f}",
                          "Ka 0.1 0.1 0.1", "Ks 0.0 0.0 0.0", "d 1.0",
                          "illum 2"]
            if albedo_file:
                mtl_lines.append(f"map_Kd {albedo_file}")
            normal_file = normal_map.get(material, "") if isinstance(normal_map, dict) else normal_map
            if normal_file:
                # Blender ignore « norm » ; il relie map_Bump à Normal Map.
                # D'autres lecteurs peuvent y voir un relief : documenté dans
                # la notice, à vérifier dans le logiciel cible.
                mtl_lines.append(f"map_Bump {normal_file}")
            mtl_lines.append("")

        if z_up:
            for x, y, z in mesh.positions:
                chunks.append(f"v {x * scale:.9g} {-z * scale:.9g} {y * scale:.9g}")
        else:
            for x, y, z in mesh.positions:
                chunks.append(f"v {x * scale:.9g} {y * scale:.9g} {z * scale:.9g}")
        has_uv = bool(mesh.uvs) and len(mesh.uvs) == len(mesh.positions)
        has_n = bool(mesh.normals) and len(mesh.normals) == len(mesh.positions)
        if has_uv:
            chunks += [f"vt {u:.9g} {1.0 - v:.9g}" for u, v in mesh.uvs]
        if has_n:
            if z_up:
                chunks += [f"vn {nx:.9g} {-nz:.9g} {ny:.9g}"
                           for nx, ny, nz in mesh.normals]
            else:
                chunks += [f"vn {nx:.9g} {ny:.9g} {nz:.9g}"
                           for nx, ny, nz in mesh.normals]

        vbase = vertex_offset + 1
        ubase = uv_offset + 1
        nbase = normal_offset + 1
        triangles = iter_triangles(mesh)
        if has_uv and has_n:
            for f0, f1, f2 in triangles:
                chunks.append(
                    "f %d/%d/%d %d/%d/%d %d/%d/%d" % (
                        f0 + vbase, f0 + ubase, f0 + nbase,
                        f1 + vbase, f1 + ubase, f1 + nbase,
                        f2 + vbase, f2 + ubase, f2 + nbase))
        elif has_uv:
            for f0, f1, f2 in triangles:
                chunks.append("f %d/%d %d/%d %d/%d" % (
                    f0 + vbase, f0 + ubase, f1 + vbase, f1 + ubase,
                    f2 + vbase, f2 + ubase))
        elif has_n:
            for f0, f1, f2 in triangles:
                chunks.append("f %d//%d %d//%d %d//%d" % (
                    f0 + vbase, f0 + nbase, f1 + vbase, f1 + nbase,
                    f2 + vbase, f2 + nbase))
        else:
            for f0, f1, f2 in triangles:
                chunks.append("f %d %d %d" % (f0 + vbase, f1 + vbase,
                                              f2 + vbase))
        chunks.append("")
        vertex_offset += len(mesh.positions)
        uv_offset += len(mesh.uvs) if has_uv else 0
        normal_offset += len(mesh.normals) if has_n else 0

    write_atomic(path, ("\n".join(chunks) + "\n").encode("utf-8"))
    if use_mtl:
        write_atomic(path.with_suffix(".mtl"), ("\n".join(mtl_lines) + "\n").encode("utf-8"))
    return path


def _albedo_for(material: str, albedo_map: dict[str, str]) -> str:
    """Fichier d'albédo d'un matériau : nom exact, nom sans suffixe, ou l'unique."""
    if not albedo_map:
        return ""
    if material in albedo_map:
        return albedo_map[material]
    unique = {value for value in albedo_map.values()}
    return next(iter(unique)) if len(unique) == 1 else ""


def load_obj(path_or_bytes) -> MeshData:
    """Charge un fichier OBJ (aperçu 3D sans Blender)."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        text = bytes(path_or_bytes).decode("utf-8", "replace")
        name = "objet.obj"
    else:
        path = Path(path_or_bytes)
        text = path.read_text(errors="ignore")
        name = path.name
    positions: list[Vec3] = []
    normals: list[Vec3] = []
    uvs: list[Vec2] = []
    faces: list[tuple[int, ...]] = []
    for line in text.splitlines():
        if line.startswith("v "):
            parts = line.split()
            try:
                positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except (IndexError, ValueError):
                continue
        elif line.startswith("vt "):
            parts = line.split()
            try:
                uvs.append((float(parts[1]), float(parts[2]) if len(parts) > 2 else 0.0))
            except (IndexError, ValueError):
                uvs.append((0.0, 0.0))
        elif line.startswith("vn "):
            parts = line.split()
            try:
                normals.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except (IndexError, ValueError):
                continue
        elif line.startswith("f "):
            tokens = line.split()[1:]
            face: list[int] = []
            for token in tokens:
                pieces = token.split("/")
                try:
                    vertex = int(pieces[0])
                except ValueError:
                    continue
                vertex = vertex - 1 if vertex > 0 else len(positions) + vertex
                face.append(vertex)
            if len(face) >= 3:
                for i in range(1, len(face) - 1):
                    faces.append((face[0], face[i], face[i + 1]))
    if not positions or not faces:
        raise HookyError("OBJ vide ou illisible.")
    mesh = MeshData(name=name, positions=positions, faces=faces)
    if normals and len(normals) == len(positions):
        mesh.normals = normals
    if uvs and len(uvs) == len(positions):
        mesh.uvs = uvs
    mesh.ensure_normals()
    return mesh
