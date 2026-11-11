"""Rendu 3D hors interface : un PNG (ou une planche de vues) sans Tkinter.

L'interface graphique suffit à elle seule quand Tkinter est installé ; ce module
sert aux machines où ce n'est pas le cas (Windows sans tcl/tk, serveur, CI) et
à l'export d'images : ``python -m hookymod.cli view modele.bfres -o apercu.png``.

Le rendu réutilise exactement la même géométrie, la même caméra et le même
éclairage que la fenêtre 3D (:mod:`hookymod.viewer`), mais rasterise les
triangles dans un tampon mémoire puis encode un PNG avec :mod:`hookymod.util`.
Aucune dépendance externe.
"""

from __future__ import annotations

from .mesh import MeshData
from .util import HookyError, png_bytes
from .viewer import (LIGHT, Projector, build_render_data, fit_view,
                     order_visible_faces, shade_color, view_scale)

#: Fond par défaut (proche du thème sombre de l'application).
BACKGROUND = (0x14, 0x16, 0x1C, 0xFF)


def _hex_rgb(color: str) -> tuple[int, int, int]:
    """``"#rrggbb"`` → triplet d'octets (les appels internes sont fiables)."""
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def render_rgba(meshes, width: int = 800, height: int = 600,
                yaw: float = 0.6, pitch: float = -0.3, zoom: float = 1.0,
                cull: bool = True, max_faces: int | None = None,
                background: tuple[int, int, int, int] = BACKGROUND,
                supersample: int = 1) -> tuple[int, int, bytes]:
    """Rasterise les maillages et retourne ``(largeur, hauteur, rgba)``.

    La projection est orthographique et la profondeur vient d'un tampon ``z`` :
    les faces qui se croisent sont donc correctement départagées, contrairement
    au simple tri arrière→avant de la version interactive.
    """
    width = max(16, int(width))
    height = max(16, int(height))
    supersample = max(1, min(4, int(supersample)))
    rw, rh = width * supersample, height * supersample

    if isinstance(meshes, MeshData):
        meshes = [meshes]
    data = build_render_data(list(meshes))
    center, radius = fit_view(data.positions)
    scale = view_scale(rw, rh, radius, zoom)
    projector = Projector(data.positions, center, yaw, pitch, scale,
                          rw / 2.0, rh / 2.0)

    total = len(data.faces)
    budget = total if max_faces is None else max(1, min(total, int(max_faces)))
    visible = order_visible_faces(data.faces, projector, budget, cull=cull,
                                  min_area=0.0)
    if not visible:
        raise HookyError("Aucune face visible sous cet angle "
                         "(essaie --yaw / --pitch ou désactive le culling).")

    pixels = bytearray(bytes(background) * (rw * rh))
    # La profondeur croît vers la caméra (cf. Projector) : le plus grand z est
    # le plus proche, comme dans le viewer.
    zbuf = [float("-inf")] * (rw * rh)
    stride = rw * 4

    for index in visible:
        a, b, c = data.faces[index]
        p0, p1, p2 = projector(a), projector(b), projector(c)
        area = ((p1[0] - p0[0]) * (p2[1] - p0[1])
                - (p2[0] - p0[0]) * (p1[1] - p0[1]))
        if abs(area) < 1e-9:
            continue
        r, g, bl = _hex_rgb(shade_color(data.colors[index], data.normals[index],
                                        yaw, pitch))
        xmin = max(0, int(min(p0[0], p1[0], p2[0])) - 1)
        xmax = min(rw - 1, int(max(p0[0], p1[0], p2[0])) + 1)
        ymin = max(0, int(min(p0[1], p1[1], p2[1])) - 1)
        ymax = min(rh - 1, int(max(p0[1], p1[1], p2[1])) + 1)
        if xmin > xmax or ymin > ymax:
            continue
        ax, ay, az = p0
        bx, by, bz = p1
        cx, cy, cz = p2
        inv = 1.0 / area
        for y in range(ymin, ymax + 1):
            py = y + 0.5
            row = y * stride
            row_slot = y * rw
            for x in range(xmin, xmax + 1):
                px = x + 0.5
                # poids barycentriques (les aires signées se normalisent)
                w0 = ((bx - px) * (cy - py) - (cx - px) * (by - py)) * inv
                w1 = ((cx - px) * (ay - py) - (ax - px) * (cy - py)) * inv
                w2 = 1.0 - w0 - w1
                if w0 < 0.0 or w1 < 0.0 or w2 < 0.0:
                    continue
                z = w0 * az + w1 * bz + w2 * cz
                slot = row_slot + x
                if z <= zbuf[slot]:
                    continue
                zbuf[slot] = z
                offset = row + x * 4
                pixels[offset] = r
                pixels[offset + 1] = g
                pixels[offset + 2] = bl
                pixels[offset + 3] = 255

    if supersample > 1:
        pixels = _downsample(pixels, rw, rh, width, height, supersample,
                             background)
        rw, rh = width, height
    return rw, rh, bytes(pixels)


def _downsample(pixels: bytearray, rw: int, rh: int, width: int, height: int,
                factor: int, background) -> bytearray:
    """Réduction par moyenne (anti-aliasing) ; le fond reste opaque."""
    out = bytearray(width * height * 4)
    count = factor * factor
    for y in range(height):
        for x in range(width):
            r = g = b = 0
            for dy in range(factor):
                base = ((y * factor + dy) * rw + x * factor) * 4
                for dx in range(factor):
                    offset = base + dx * 4
                    r += pixels[offset]
                    g += pixels[offset + 1]
                    b += pixels[offset + 2]
            offset = (y * width + x) * 4
            out[offset] = r // count
            out[offset + 1] = g // count
            out[offset + 2] = b // count
            out[offset + 3] = 255
    return out


def render_png(meshes, width: int = 800, height: int = 600,
               yaw: float = 0.6, pitch: float = -0.3, zoom: float = 1.0,
               cull: bool = True, max_faces: int | None = None,
               supersample: int = 1) -> bytes:
    """Comme :func:`render_rgba`, mais renvoie directement les octets PNG."""
    w, h, rgba = render_rgba(meshes, width, height, yaw, pitch, zoom, cull,
                             max_faces, supersample=supersample)
    return png_bytes(w, h, rgba)


def turntable_png(meshes, views: int = 4, width: int = 480, height: int = 480,
                  pitch: float = -0.3, zoom: float = 1.0,
                  cull: bool = True) -> bytes:
    """Planche de ``views`` rotations (de 0 à 360°) côte à côte.

    Pratique pour juger un modèle d'un coup d'œil sans interface graphique.
    """
    views = max(1, min(12, int(views)))
    frames = []
    rendered = 0
    for i in range(views):
        yaw = 6.283185307179586 * i / views
        try:
            frames.append(render_rgba(meshes, width, height, yaw, pitch, zoom,
                                      cull))
            rendered += 1
        except HookyError:
            # vue sans face visible (modèle plat vu de dos) : on garde la place
            frames.append((width, height,
                           bytes(BACKGROUND) * (width * height)))
    if not rendered:
        raise HookyError("Aucune vue n'a pu être rendue sous ces angles.")
    total_width = width * len(frames)
    strip = bytearray(total_width * height * 4)
    for i, (fw, fh, rgba) in enumerate(frames):
        for y in range(min(height, fh)):
            source = y * fw * 4
            target = (y * total_width + i * width) * 4
            strip[target:target + width * 4] = rgba[source:source + width * 4]
    return png_bytes(total_width, height, bytes(strip))


def light_vector() -> tuple[float, float, float]:
    """Direction de la lumière utilisée par le rendu (utile aux tests)."""
    return LIGHT
