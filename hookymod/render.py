"""Rendu 3D hors interface : un PNG (ou une planche de vues) sans Tkinter.

L'interface graphique suffit à elle seule quand Tkinter est installé ; ce module
sert aux machines où ce n'est pas le cas (Windows sans tcl/tk, serveur, CI) et
à l'export d'images : ``python -m hookymod.cli view modele.bfres -o apercu.png``.

Le rendu réutilise exactement la même géométrie, la même caméra et le même
éclairage que la fenêtre 3D (:mod:`hookymod.viewer`), mais rasterise les
triangles dans un tampon mémoire puis encode un PNG avec :mod:`hookymod.util`.
Le cœur Rust facultatif accélère les pixels ; le repli n'a aucune dépendance
externe.
"""

from __future__ import annotations

import math
import struct

from . import native
from .mesh import MeshData
from .util import HookyError, png_bytes
from .viewer import (LIGHT, Projector, build_render_data, fit_view,
                     order_visible_faces, shade_rgb, view_scale)

#: Fond par défaut (proche du thème sombre de l'application).
BACKGROUND = (0x14, 0x16, 0x1C, 0xFF)


def _hex_rgb(color: str) -> tuple[int, int, int]:
    """``"#rrggbb"`` → triplet d'octets (les appels internes sont fiables)."""
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def render_rgba(meshes, width: int = 800, height: int = 600,
                yaw: float = 0.6, pitch: float = -0.3, zoom: float = 1.0,
                cull: bool = True, max_faces: int | None = None,
                background: tuple[int, int, int, int] = BACKGROUND,
                supersample: int = 1, frustum: bool = True,
                data=None) -> tuple[int, int, bytes]:
    """Rasterise les maillages et retourne ``(largeur, hauteur, rgba)``.

    La projection est orthographique et la profondeur vient d'un tampon ``z`` :
    les faces qui se croisent sont donc correctement départagées, contrairement
    au simple tri arrière→avant de la version interactive.
    """
    width = max(16, int(width))
    height = max(16, int(height))
    supersample = max(1, min(4, int(supersample)))
    rw, rh = width * supersample, height * supersample

    if data is None:
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
                                  min_area=0.0,
                                  screen=(rw, rh) if frustum else None)
    if not visible:
        raise HookyError("Aucune face visible sous cet angle "
                         "(essaie --yaw / --pitch ou désactive le culling).")

    trig = (math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch))
    if native.available() and len(visible) * 76 <= 64 * 1024 * 1024:
        # Même géométrie, même ordre et même éclairage que le repli Python ;
        # seuls le z-buffer, les pixels et la réduction AA passent en Rust.
        # Le passage par ctypes libère le GIL pendant le rendu natif.
        record = struct.Struct("<9d4B")
        triangles = bytearray(len(visible) * record.size)
        offset = 0
        for index in visible:
            a, b, c = data.faces[index]
            p0, p1, p2 = projector(a), projector(b), projector(c)
            area = ((p1[0] - p0[0]) * (p2[1] - p0[1])
                    - (p2[0] - p0[0]) * (p1[1] - p0[1]))
            if -1e-9 < area < 1e-9:
                continue
            r, g, bl = shade_rgb(data.colors[index], data.normals[index], trig)
            record.pack_into(triangles, offset, *p0, *p1, *p2, r, g, bl, 255)
            offset += record.size
        del triangles[offset:]
        result = native.rasterize(width, height, supersample, triangles, background,
                                  _owned=True)
        del triangles
        if result is not None:
            return width, height, result

    pixels = bytearray(bytes(background) * (rw * rh))
    # La profondeur croît vers la caméra (cf. Projector) : le plus grand z est
    # le plus proche, comme dans le viewer.
    zbuf = [float("-inf")] * (rw * rh)

    # Balayage de lignes par triangle : les trois sommets sont triés par y, on
    # parcourt les deux arêtes gauches/droites et on avance en z par addition.
    zbuffer = zbuf
    for index in visible:
        a, b, c = data.faces[index]
        p0 = projector(a)
        p1 = projector(b)
        p2 = projector(c)
        area = ((p1[0] - p0[0]) * (p2[1] - p0[1])
                - (p2[0] - p0[0]) * (p1[1] - p0[1]))
        if -1e-9 < area < 1e-9:
            continue
        r, g, bl = shade_rgb(data.colors[index], data.normals[index], trig)
        color = bytes((r, g, bl, 255))

        # tri par y croissant
        if p0[1] > p1[1]:
            p0, p1 = p1, p0
        if p1[1] > p2[1]:
            p1, p2 = p2, p1
        if p0[1] > p1[1]:
            p0, p1 = p1, p0
        ay, ax, az = p0[1], p0[0], p0[2]
        by, bx, bz = p1[1], p1[0], p1[2]
        cy, cx, cz = p2[1], p2[0], p2[2]

        # py = y + 0.5 doit rester dans [ay, cy] : sinon l'interpolation
        # extrapole et une arête de 0,01 unité dessine une traînée de 100 px
        ylo = max(0, -(-int((ay - 0.5) * 2) // 2))
        yhi = min(rh - 1, int(cy - 0.5))
        if ylo > yhi:
            continue
        # bornes en x : l'interpolation reste **dans** le triangle (il est
        # convexe) ; sur une arête quasi horizontale, une erreur d'arrondi
        # suffisait sinon à envoyer la ligne de l'autre côté de l'écran
        x_min3 = min(ax, bx, cx)
        x_max3 = max(ax, bx, cx)

        span_ac = cy - ay
        if span_ac < 1e-12:
            continue
        dx_ac = (cx - ax) / span_ac
        dz_ac = (cz - az) / span_ac
        span_ab = by - ay
        span_bc = cy - by
        dx_ab = (bx - ax) / span_ab if span_ab > 1e-12 else 0.0
        dz_ab = (bz - az) / span_ab if span_ab > 1e-12 else 0.0
        dx_bc = (cx - bx) / span_bc if span_bc > 1e-12 else 0.0
        dz_bc = (cz - bz) / span_bc if span_bc > 1e-12 else 0.0

        for y in range(ylo, yhi + 1):
            py = y + 0.5
            if py < by:
                x_left = ax + (py - ay) * dx_ab
                z_left = az + (py - ay) * dz_ab
            else:
                x_left = bx + (py - by) * dx_bc
                z_left = bz + (py - by) * dz_bc
            x_right = ax + (py - ay) * dx_ac
            z_right = az + (py - ay) * dz_ac
            if x_left < x_min3:
                x_left = x_min3
            elif x_left > x_max3:
                x_left = x_max3
            if x_right > x_max3:
                x_right = x_max3
            elif x_right < x_min3:
                x_right = x_min3
            if x_left > x_right:
                x_left, x_right = x_right, x_left
                z_left, z_right = z_right, z_left
            span_width = x_right - x_left
            if span_width < 1e-9:
                continue
            start_x = int(x_left + 0.999)
            end_x = int(x_right)
            if start_x < 0:
                start_x = 0
            if end_x > rw - 1:
                end_x = rw - 1
            if start_x > end_x:
                continue
            step = (z_right - z_left) / span_width
            z = z_left + (start_x + 0.5 - x_left) * step
            slot = y * rw + start_x
            offset = slot * 4
            for x in range(start_x, end_x + 1):
                if z > zbuffer[slot]:
                    zbuffer[slot] = z
                    pixels[offset:offset + 4] = color
                z += step
                slot += 1
                offset += 4

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
               supersample: int = 1, frustum: bool = True,
               data=None) -> bytes:
    """Comme :func:`render_rgba`, mais renvoie directement les octets PNG."""
    w, h, rgba = render_rgba(meshes, width, height, yaw, pitch, zoom, cull,
                             max_faces, supersample=supersample,
                             frustum=frustum, data=data)
    return png_bytes(w, h, rgba)


def turntable_png(meshes, views: int = 4, width: int = 480, height: int = 480,
                  pitch: float = -0.3, zoom: float = 1.0,
                  cull: bool = True, frustum: bool = True,
                  supersample: int = 1, data=None,
                  max_faces: int | None = None) -> bytes:
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
                                      cull, max_faces=max_faces, supersample=supersample,
                                      frustum=frustum, data=data))
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
