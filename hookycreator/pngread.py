"""Lecture d'images pour l'import de textures (PNG, JPEG si Pillow est là).

Pillow est utilisé quand il est présent : c'est le cas le plus courant chez un
moddeur, et il gère tous les formats. Sinon on décode le PNG soi-même (8 bits,
RGB/RGBA, non entrelacé), ce qui suffit pour un aller-retour depuis un éditeur.
"""

from __future__ import annotations

import struct
import zlib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _pil_decode(data: bytes):
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    import io

    with Image.open(io.BytesIO(data)) as image:
        rgba = image.convert("RGBA")
        return rgba.width, rgba.height, rgba.tobytes()


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _png_decode(data: bytes):
    if not data.startswith(PNG_MAGIC):
        return None
    offset = 8
    width = height = depth = colour = interlace = 0
    idat = bytearray()
    palette = b""
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        payload = data[offset + 8:offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", payload)
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break
    if not width or depth != 8 or interlace or colour not in (2, 6, 3):
        return None
    channels = {2: 3, 6: 4, 3: 1}[colour]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    previous = bytearray(stride)
    pixels = bytearray(stride * height)
    position = 0
    for row in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position:position + stride])
        position += stride
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            up_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif filter_type == 4:
                line[i] = (line[i] + _paeth(left, up, up_left)) & 0xFF
        pixels[row * stride:(row + 1) * stride] = line
        previous = line
    rgba = bytearray(width * height * 4)
    for index in range(width * height):
        if colour == 6:
            rgba[index * 4:index * 4 + 4] = pixels[index * 4:index * 4 + 4]
        elif colour == 2:
            r, g, b = pixels[index * 3:index * 3 + 3]
            rgba[index * 4:index * 4 + 4] = bytes((r, g, b, 255))
        else:
            entry = pixels[index] * 3
            r, g, b = palette[entry:entry + 3] if entry + 3 <= len(palette) else (0, 0, 0)
            rgba[index * 4:index * 4 + 4] = bytes((r, g, b, 255))
    return width, height, bytes(rgba)


def decode_image(data: bytes) -> tuple[int, int, bytes]:
    """Décode une image en ``(largeur, hauteur, RGBA)``.

    :raises ValueError: image illisible (message prêt à afficher).
    """
    if not data:
        raise ValueError("Image vide.")
    result = _pil_decode(data)
    if result is None:
        result = _png_decode(data)
    if result is None:
        raise ValueError(
            "Image non lisible : installe Pillow (pip install Pillow) pour "
            "importer autre chose qu'un PNG 8 bits."
        )
    width, height, rgba = result
    if width <= 0 or height <= 0 or len(rgba) != width * height * 4:
        raise ValueError("Image incohérente.")
    return width, height, rgba
