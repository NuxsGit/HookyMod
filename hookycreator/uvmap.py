"""Vérificateur d'UV : la preuve visuelle que la texture tombe juste.

Le problème constaté : une épée exportée pour Blender avait sa texture à
l'envers — il fallait retourner l'image dans Paint. Une orientation ne se
« devine » pas dans les octets : il faut **la voir**. Ce module produit donc une
image où les triangles UV du modèle sont dessinés **par-dessus la texture** :

* si le contour du maillage épouse les zones utiles de l'atlas (la lame sur la
  lame, la garde sur la garde), les UV sont bons ;
* si tout est décalé ou retourné, on le voit immédiatement, sans exporter ;
* un second rendu (`flip=True`) retourne V, pour trancher quand les deux se
  ressemblent.

En plus de l'image, ``summarise()`` donne les chiffres qui trahissent un vrai
défaut : UV hors de [0, 1] (le modèle se répète), triangles d'aire UV nulle
(texture étirée en un point), et écart de densité de texels entre triangles
(texture étirée sur une face voisine). Aucune de ces valeurs n'est inventée :
elles sont calculées sur les UV réellement décodés.
"""

from __future__ import annotations

import math

from hookymod import util

#: côté de l'image produite (assez grand pour lire un atlas, assez léger pour
#: s'afficher dans l'inspecteur)
SIZE = 640
#: nombre maximal de triangles dessinés (au-delà, le tracé masque la texture
#: qu'on cherche justement à voir)
MAX_TRIANGLES = 1200
#: nombre minimal de triangles par maillage, même sur un modèle très découpé
MIN_PER_MESH = 24

#: couleurs du tracé, une par matériau (lisible sur une texture claire ou sombre)
EDGE_COLORS = ((0.10, 0.85, 0.35), (0.95, 0.35, 0.55), (0.30, 0.65, 1.00),
               (1.00, 0.75, 0.20), (0.75, 0.45, 1.00), (0.20, 0.95, 0.95))


# --------------------------------------------------------------------------- image
def _put(buf: bytearray, width: int, height: int, x: int, y: int, rgb,
         mix: float = 1.0) -> None:
    """Écrit un pixel ; ``mix`` < 1 mélange avec la couleur déjà là.

    Le tracé reste ainsi translucide : la texture qu'on vérifie ne disparaît pas
    sous le maillage.
    """
    if 0 <= x < width and 0 <= y < height:
        index = (y * width + x) * 4
        if mix >= 0.999:
            buf[index], buf[index + 1], buf[index + 2] = rgb[0], rgb[1], rgb[2]
        else:
            keep = 1.0 - mix
            buf[index] = int(buf[index] * keep + rgb[0] * mix)
            buf[index + 1] = int(buf[index + 1] * keep + rgb[1] * mix)
            buf[index + 2] = int(buf[index + 2] * keep + rgb[2] * mix)
        buf[index + 3] = 255


def _line(buf: bytearray, width: int, height: int, a, b, rgb,
          mix: float = 1.0) -> None:
    """Trait de Bresenham (integer, sans dépendance)."""
    x0, y0 = a
    x1, y1 = b
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        _put(buf, width, height, x0, y0, rgb, mix)
        if x0 == x1 and y0 == y1:
            return
        twice = 2 * error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy


def _scaled_texture(png: bytes | None, size: int) -> bytearray:
    """Texture réduite/agrandie en ``size × size`` (damier neutre sans texture)."""
    buf = bytearray(size * size * 4)
    if not png:
        for cell_y in range(size):
            for cell_x in range(size):
                light = ((cell_x // 16) + (cell_y // 16)) % 2 == 0
                index = (cell_y * size + cell_x) * 4
                value = 60 if light else 44
                buf[index:index + 4] = bytes((value, value + 4, value + 10, 255))
        return buf

    from hookycreator.pngread import decode_image

    width, height, pixels = decode_image(png)
    if width < 1 or height < 1 or len(pixels) < width * height * 4:
        return _scaled_texture(None, size)
    for y in range(size):
        top = (y * height) // size
        bottom = max(top + 1, ((y + 1) * height) // size)
        for x in range(size):
            left = (x * width) // size
            right = max(left + 1, ((x + 1) * width) // size)
            red = green = blue = count = 0
            for sy in range(top, bottom):
                row = sy * width * 4
                for sx in range(left, right):
                    offset = row + sx * 4
                    alpha = pixels[offset + 3]
                    if alpha == 0:
                        continue
                    red += pixels[offset]
                    green += pixels[offset + 1]
                    blue += pixels[offset + 2]
                    count += 1
            index = (y * size + x) * 4
            if count:
                buf[index] = red // count
                buf[index + 1] = green // count
                buf[index + 2] = blue // count
            else:                       # transparent : on montre le damier
                light = ((x // 16) + (y // 16)) % 2 == 0
                value = 60 if light else 44
                buf[index] = value
                buf[index + 1] = value + 4
                buf[index + 2] = value + 10
            buf[index + 3] = 255
    return buf


def overlay(png: bytes | None, meshes, size: int = SIZE,
            flip: bool = False, max_triangles: int = MAX_TRIANGLES) -> bytes:
    """Rend la texture avec les triangles UV du modèle tracés dessus.

    :param png: albédo décodé (``None`` = damier, pour juger la place des UV seuls)
    :param meshes: maillages décodés (``MeshData`` avec ``uvs`` et ``faces``)
    :param flip: retourne V (V = 1 - v) pour comparer les deux orientations
    """
    size = max(128, min(1600, int(size)))
    buf = _scaled_texture(png, size)

    # repères discrets : bordure et quadrillage tous les quarts
    border = (255, 255, 255)
    for i in range(size):
        _put(buf, size, size, i, 0, border)
        _put(buf, size, size, i, size - 1, border)
        _put(buf, size, size, 0, i, border)
        _put(buf, size, size, size - 1, i, border)
    for quarter in (1, 2, 3):
        position = size * quarter // 4
        for i in range(0, size, 4):          # pointillé
            _put(buf, size, size, position, i, (255, 255, 255))
            _put(buf, size, size, i, position, (255, 255, 255))

    drawn = 0
    # une couleur par **matériau** : deux morceaux du même matériau se lisent
    # comme une seule zone, comme dans le rendu du modèle
    slots: dict[str, int] = {}
    per_mesh = max(MIN_PER_MESH, max_triangles // max(1, len(meshes)))
    for index, mesh in enumerate(meshes):
        uvs = mesh.uvs if mesh.uvs and len(mesh.uvs) == len(mesh.positions) else None
        if not uvs or not mesh.faces:
            continue
        slot = slots.setdefault(mesh.material or f"#{index}", len(slots))
        rgb = tuple(int(255 * c) for c in EDGE_COLORS[slot % len(EDGE_COLORS)])
        step = max(1, len(mesh.faces) // per_mesh)
        for face_index in range(0, len(mesh.faces), step):
            face = mesh.faces[face_index]
            if len(face) < 3:
                continue
            points = []
            for corner in face[:3]:
                if corner >= len(uvs):
                    points = []
                    break
                u, v = uvs[corner]
                if flip:
                    v = 1.0 - v
                points.append((int(round(u * (size - 1))), int(round(v * (size - 1)))))
            if len(points) != 3:
                continue
            for first in range(3):
                _line(buf, size, size, points[first], points[(first + 1) % 3], rgb,
                      0.55)
            drawn += 1
            if drawn >= max_triangles:
                break
        if drawn >= max_triangles:
            break
    return util.png_bytes(size, size, bytes(buf))


# --------------------------------------------------------------------------- chiffres
def _uv_area(a, b, c) -> float:
    return 0.5 * abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))


def _triangle_area3(pa, pb, pc) -> float:
    ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
    vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def summarise(meshes, texture_name: str = "") -> dict:
    """Chiffres d'un jeu d'UV : bornes, débordements, faces dégénérées, étirement."""
    faces = degenerate = outside = mapped_meshes = 0
    total_meshes = len([mesh for mesh in meshes if mesh.faces])
    low_u = low_v = math.inf
    high_u = high_v = -math.inf
    densities: list[float] = []

    for mesh in meshes:
        if not mesh.faces:
            continue
        uvs = mesh.uvs if mesh.uvs and len(mesh.uvs) == len(mesh.positions) else None
        if not uvs:
            continue
        mapped_meshes += 1
        for u, v in uvs:
            if u < low_u:
                low_u = u
            if u > high_u:
                high_u = u
            if v < low_v:
                low_v = v
            if v > high_v:
                high_v = v
            if not (-0.02 <= u <= 1.02 and -0.02 <= v <= 1.02):
                outside += 1
        step = max(1, len(mesh.faces) // 400)          # échantillon suffisant
        for index in range(0, len(mesh.faces), step):
            face = mesh.faces[index]
            if len(face) < 3 or max(face[:3]) >= len(uvs):
                continue
            faces += 1
            a, b, c = (uvs[face[0]], uvs[face[1]], uvs[face[2]])
            area_uv = _uv_area(a, b, c)
            if area_uv < 1e-9:
                degenerate += 1
                continue
            try:
                area_3d = _triangle_area3(mesh.positions[face[0]],
                                         mesh.positions[face[1]],
                                         mesh.positions[face[2]])
            except IndexError:
                continue
            if area_3d > 0:
                densities.append(area_3d / area_uv)

    warnings: list[str] = []
    stretch = 0.0
    if densities:
        densities.sort()
        median = densities[len(densities) // 2]
        if median > 0:
            # 95ᵉ centile et non maximum : un seul triangle minuscule ne doit pas
            # faire croire que tout le modèle est étiré
            stretch = densities[int(len(densities) * 0.95)] / median
    if not mapped_meshes:
        warnings.append("aucune coordonnée de texture dans ce modèle : "
                        "la texture ne peut pas être plaquée")
    if outside:
        warnings.append(f"{outside} sommet(s) hors de [0, 1] : la texture se répète "
                        "(voulu sur certains tissus, suspect ailleurs)")
    if degenerate:
        warnings.append(f"{degenerate} triangle(s) d'aire UV nulle : la texture y est "
                        "réduite à un point (couleur unie)")
    if stretch > 8:
        warnings.append(f"densité de texels irrégulière (×{stretch:.0f}) : la texture "
                        "est étirée sur une partie du modèle")

    return {
        "has_uvs": bool(mapped_meshes),
        "meshes": total_meshes,
        "mapped_meshes": mapped_meshes,
        "faces": faces,
        "u": [round(low_u, 4), round(high_u, 4)] if mapped_meshes else [0, 0],
        "v": [round(low_v, 4), round(high_v, 4)] if mapped_meshes else [0, 0],
        "outside": outside,
        "degenerate": degenerate,
        "stretch": round(stretch, 2),
        "texture": texture_name,
        "warnings": warnings,
        "conventions": {
            "bfres": "V = 0 en haut de l'image (comme la texture du jeu)",
            "gltf": "V = 0 en haut (spécification glTF) : UV écrites telles quelles",
            "obj": "V = 0 en bas (OpenGL) : UV écrites avec V retourné",
        },
    }
