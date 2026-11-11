"""BNTX (textures Switch) : lecture, désentrelacement et décodage RGBA.

Le format BNTX stocke les textures « swizzlées » (Tegra block linear) et
compressées en BCn / ASTC.  Ce module les décode en RGBA afin de les afficher
ou de les exporter en PNG.

Référence de format : documentation publique BNTX (Syroot / BfresLibrary).
Les décodeurs BCn utilisent ``texture2ddecoder`` s'il est installé, sinon un
décodage Python pur (BC1/BC3/BC4/BC5).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .util import FormatError, HookyError, cstring, u16, u32, u64, png_bytes

try:  # accélérateur optionnel (BCn/ASTC en C)
    import texture2ddecoder as _t2d  # type: ignore
except Exception:  # pragma: no cover
    _t2d = None

MAGIC = b"BNTX"

# --------------------------------------------------------------------------- formats
# (nom, octets par bloc/pixel, largeur de bloc, hauteur de bloc)
FORMATS: dict[int, tuple[str, int, int, int]] = {
    0x0101: ("R4_G4_UNORM", 1, 1, 1),
    0x0201: ("R8_UNORM", 1, 1, 1),
    0x0301: ("R4_G4_B4_A4_UNORM", 2, 1, 1),
    0x0401: ("A4_B4_G4_R4_UNORM", 2, 1, 1),
    0x0501: ("R5_G5_B5_A1_UNORM", 2, 1, 1),
    0x0601: ("A1_B5_G5_R5_UNORM", 2, 1, 1),
    0x0701: ("R5_G6_B5_UNORM", 2, 1, 1),
    0x0801: ("B5_G6_R5_UNORM", 2, 1, 1),
    0x0901: ("R8_G8_UNORM", 2, 1, 1),
    0x0B01: ("R8_G8_B8_A8_UNORM", 4, 1, 1),
    0x0B06: ("R8_G8_B8_A8_SRGB", 4, 1, 1),
    0x0C01: ("B8_G8_R8_A8_UNORM", 4, 1, 1),
    0x0C06: ("B8_G8_R8_A8_SRGB", 4, 1, 1),
    0x0E01: ("R10_G10_B10_A2_UNORM", 4, 1, 1),
    0x1A01: ("BC1_UNORM", 8, 4, 4),
    0x1A06: ("BC1_SRGB", 8, 4, 4),
    0x1B01: ("BC2_UNORM", 16, 4, 4),
    0x1B06: ("BC2_SRGB", 16, 4, 4),
    0x1C01: ("BC3_UNORM", 16, 4, 4),
    0x1C06: ("BC3_SRGB", 16, 4, 4),
    0x1D01: ("BC4_UNORM", 8, 4, 4),
    0x1D02: ("BC4_SNORM", 8, 4, 4),
    0x1E01: ("BC5_UNORM", 16, 4, 4),
    0x1E02: ("BC5_SNORM", 16, 4, 4),
    0x1F05: ("BC6_FLOAT", 16, 4, 4),
    0x1F0A: ("BC6_UFLOAT", 16, 4, 4),
    0x2001: ("BC7_UNORM", 16, 4, 4),
    0x2006: ("BC7_SRGB", 16, 4, 4),
    0x3B01: ("B5_G5_R5_A1_UNORM", 2, 1, 1),
}
ASTC_BLOCKS = {
    0x2D: (4, 4), 0x2E: (5, 4), 0x2F: (5, 5), 0x30: (6, 5), 0x31: (6, 6),
    0x32: (8, 5), 0x33: (8, 6), 0x34: (8, 8), 0x35: (10, 5), 0x36: (10, 6),
    0x37: (10, 8), 0x38: (10, 10), 0x39: (12, 10), 0x3A: (12, 12),
}
for _code, (_bw, _bh) in ASTC_BLOCKS.items():
    FORMATS[(_code << 8) | 0x01] = (f"ASTC_{_bw}x{_bh}_UNORM", 16, _bw, _bh)
    FORMATS[(_code << 8) | 0x06] = (f"ASTC_{_bw}x{_bh}_SRGB", 16, _bw, _bh)

PIXEL_FORMATS = {"R8_UNORM", "R8_G8_UNORM", "R4_G4_UNORM", "R4_G4_B4_A4_UNORM",
                 "A4_B4_G4_R4_UNORM", "R5_G5_B5_A1_UNORM", "A1_B5_G5_R5_UNORM",
                 "R5_G6_B5_UNORM", "B5_G6_R5_UNORM", "R8_G8_B8_A8_UNORM",
                 "R8_G8_B8_A8_SRGB", "B8_G8_R8_A8_UNORM", "B8_G8_R8_A8_SRGB",
                 "R10_G10_B10_A2_UNORM", "B5_G5_R5_A1_UNORM"}


def format_info(fmt: int) -> tuple[str, int, int, int]:
    info = FORMATS.get(fmt)
    if info is None:
        # Variantes sRGB/SNORM non listées : on retombe sur la famille.
        family = FORMATS.get((fmt & 0xFF00) | 0x01)
        if family is None:
            raise FormatError(f"Format de texture BNTX inconnu : 0x{fmt:04X}.")
        return family
    return info


def is_supported(fmt: int) -> bool:
    return fmt in FORMATS


def supports_astc() -> bool:
    return _t2d is not None


# --------------------------------------------------------------------------- swizzle
def div_round_up(n: int, d: int) -> int:
    return (n + d - 1) // d


def align_up(x: int, y: int) -> int:
    return ((x - 1) | (y - 1)) + 1


def pow2_round_up(x: int) -> int:
    x = max(1, x) - 1
    x |= x >> 1
    x |= x >> 2
    x |= x >> 4
    x |= x >> 8
    x |= x >> 16
    return x + 1


def gob_offset(x: int, y: int, orin: bool = False) -> int:
    """Décalage d'un octet (x, y) à l'intérieur d'un GOB de 512 octets.

    Deux dispositions existent :

    * **Tegra X1** (Switch 1, TRM v1.3 p. 1218) : les secteurs de 16 octets sont
      groupés deux rangées à la fois ;
    * **Orin** (Tegra T239, Switch 2, TRM Orin) : ils sont groupés **quatre**
      rangées à la fois (128 octets par groupe au lieu de 64). C'est le
      « sector swap » que Switch-Toolbox appelle ``is_orin``/``orin_sector_swap``.
    """
    if orin:
        return (((x % 64) // 32) * 256 + ((y % 8) // 4) * 128
                + ((x % 32) // 16) * 64 + (y % 4) * 16 + (x % 16))
    return (((x % 64) // 32) * 256 + ((y % 8) // 2) * 64
            + ((x % 32) // 16) * 32 + (y % 2) * 16 + (x % 16))


def get_addr_block_linear(x: int, y: int, image_width: int, bytes_per_pixel: int,
                          base_address: int, block_height: int,
                          orin: bool = False) -> int:
    """Adresse d'un bloc/pixel dans une surface block-linear (X1 ou Orin)."""
    image_width_in_gobs = div_round_up(image_width * bytes_per_pixel, 64)
    gob = (base_address
           + (y // (8 * block_height)) * 512 * block_height * image_width_in_gobs
           + (x * bytes_per_pixel // 64) * 512 * block_height
           + ((y % (8 * block_height)) // 8) * 512)
    return gob + gob_offset(x * bytes_per_pixel, y, orin)


def _surface_size(width: int, height: int, blk_w: int, blk_h: int, bpp: int) -> int:
    return div_round_up(width, blk_w) * div_round_up(height, blk_h) * bpp


def surface_size(width: int, height: int, blk_w: int, blk_h: int, bpp: int,
                 tile_mode: int, block_height_log2: int) -> int:
    """Taille réellement occupée par un mip sur disque (alignements inclus)."""
    blocks_w = div_round_up(width, blk_w)
    blocks_h = div_round_up(height, blk_h)
    if tile_mode == 1:
        return align_up(blocks_w * bpp, 32) * blocks_h
    block_height = 1 << max(0, min(5, block_height_log2))
    return align_up(blocks_w * bpp, 64) * align_up(blocks_h, block_height * 8)


def deswizzle(width: int, height: int, blk_w: int, blk_h: int, bpp: int,
              tile_mode: int, block_height_log2: int, data: bytes,
              round_pitch: bool = True, orin: bool = False) -> bytes:
    """Transforme une surface swizzlée en données linéaires (bloc/pixel).

    ``orin=True`` lit la disposition Switch 2 (Tegra T239) au lieu de la X1.
    """
    wb = div_round_up(width, blk_w)
    hb = div_round_up(height, blk_h)
    needed = wb * hb * bpp
    out = bytearray(needed)
    if needed == 0:
        return bytes(out)

    if tile_mode == 1:  # linéaire (avec pitch aligné sur 32 octets)
        pitch = wb * bpp
        if round_pitch:
            pitch = align_up(pitch, 32)
        row = wb * bpp
        for y in range(hb):
            src = y * pitch
            dst = y * row
            chunk = data[src:src + min(row, max(0, len(data) - src))]
            out[dst:dst + len(chunk)] = chunk
        return bytes(out)

    block_height = 1 << max(0, min(5, block_height_log2))
    # Une unité contiguë fait 16 octets (plusieurs pixels pour les petits formats).
    chunk_bytes = 16
    per_chunk = max(1, 16 // bpp)
    size = len(data)
    for y in range(hb):
        row_dst = y * wb * bpp
        row_end = row_dst + wb * bpp
        for x in range(0, wb, per_chunk):
            src = get_addr_block_linear(x, y, wb, bpp, 0, block_height, orin)
            if src >= size:
                break
            dst = row_dst + x * bpp
            # Une unité contiguë ne doit pas dépasser la ligne (ni le tampon).
            count = min(chunk_bytes, (wb - x) * bpp, row_end - dst, needed - dst, size - src)
            if count > 0:
                out[dst:dst + count] = data[src:src + count]
    return bytes(out)


def swizzle(width: int, height: int, blk_w: int, blk_h: int, bpp: int,
            tile_mode: int, block_height_log2: int, data: bytes,
            round_pitch: bool = True, orin: bool = False) -> bytes:
    """Inverse de :func:`deswizzle` (pour réécrire une texture dans un BNTX).

    Le tampon retourné a la taille réelle de la surface swizzlée (souvent plus
    grande que la taille linéaire, à cause de l'alignement des GOB).
    """
    wb = div_round_up(width, blk_w)
    hb = div_round_up(height, blk_h)
    linear_size = wb * hb * bpp
    if linear_size == 0:
        return b""
    if tile_mode == 1:
        pitch = align_up(wb * bpp, 32) if round_pitch else wb * bpp
        out = bytearray(pitch * hb)
        row = wb * bpp
        for y in range(hb):
            out[y * pitch:y * pitch + row] = data[y * row:y * row + row]
        return bytes(out)

    block_height = 1 << max(0, min(5, block_height_log2))
    pitch = align_up(wb * bpp, 64)
    surface = pitch * align_up(hb, block_height * 8)
    out = bytearray(surface)
    chunk_bytes = 16
    per_chunk = max(1, 16 // bpp)
    for y in range(hb):
        row_src = y * wb * bpp
        for x in range(0, wb, per_chunk):
            dst = get_addr_block_linear(x, y, wb, bpp, 0, block_height, orin)
            count = min(chunk_bytes, (wb - x) * bpp, linear_size - (row_src + x * bpp))
            if count <= 0 or dst >= surface:
                break
            count = min(count, surface - dst)
            out[dst:dst + count] = data[row_src + x * bpp:row_src + x * bpp + count]
    return bytes(out)


# --------------------------------------------------------------------------- décodage BCn
def _rgb565(value: int) -> tuple[int, int, int]:
    r = (value >> 11) & 0x1F
    g = (value >> 5) & 0x3F
    b = value & 0x1F
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def _bgra_to_rgba(buf: bytes) -> bytes:
    out = bytearray(buf)
    out[0::4] = buf[2::4]
    out[2::4] = buf[0::4]
    return bytes(out)


def _decode_bc1(data: bytes, width: int, height: int, allow_alpha: bool = True) -> bytes:
    if _t2d is not None:
        return _bgra_to_rgba(_t2d.decode_bc1(data, width, height))
    bw, bh = div_round_up(width, 4), div_round_up(height, 4)
    out = bytearray(width * height * 4)
    for by in range(bh):
        for bx in range(bw):
            off = (by * bw + bx) * 8
            if off + 8 > len(data):
                break
            c0, c1, bits = struct.unpack_from("<HHI", data, off)
            r0, g0, b0 = _rgb565(c0)
            r1, g1, b1 = _rgb565(c1)
            palette = [(r0, g0, b0, 255), (r1, g1, b1, 255)]
            if c0 > c1:
                palette += [((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
                            ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255)]
            else:
                palette += [((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255),
                            (0, 0, 0, 0)]
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    break
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        break
                    idx = (bits >> (2 * (py * 4 + px))) & 3
                    r, g, b, a = palette[idx]
                    pos = (y * width + x) * 4
                    out[pos:pos + 4] = bytes((r, g, b, a))
    return bytes(out)


def _decode_bc4(data: bytes, width: int, height: int, signed: bool = False) -> bytes:
    """Décode un canal (BC4).  Retourne RGBA opaque."""
    bw, bh = div_round_up(width, 4), div_round_up(height, 4)
    out = bytearray(width * height * 4)
    for by in range(bh):
        for bx in range(bw):
            off = (by * bw + bx) * 8
            if off + 8 > len(data):
                break
            r0, r1 = data[off], data[off + 1]
            bits = int.from_bytes(data[off + 2:off + 8], "little")
            if signed:
                r0 = r0 - 256 if r0 > 127 else r0
                r1 = r1 - 256 if r1 > 127 else r1
                if r0 > r1:
                    values = [r0, r1] + [((8 - k) * r0 + (k - 1) * r1) // 7 for k in range(2, 8)]
                else:
                    values = [r0, r1] + [((6 - k) * r0 + (k - 1) * r1) // 5 for k in range(2, 6)] + [-128, 127]
                values = [max(0, min(255, v + 128)) for v in values]
            else:
                if r0 > r1:
                    values = [r0, r1] + [((8 - k) * r0 + (k - 1) * r1) // 7 for k in range(2, 8)]
                else:
                    values = [r0, r1] + [((6 - k) * r0 + (k - 1) * r1) // 5 for k in range(2, 6)] + [0, 255]
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    break
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        break
                    idx = (bits >> (3 * (py * 4 + px))) & 7
                    v = values[idx]
                    pos = (y * width + x) * 4
                    out[pos:pos + 4] = bytes((v, v, v, 255))
    return bytes(out)


def _compose_bc5(data: bytes, width: int, height: int, signed: bool = False) -> bytes:
    """BC5 = deux blocs BC4 entrelacés par bloc 4×4 (R puis G)."""
    blocks = len(data) // 16
    red_raw = b"".join(data[i * 16:i * 16 + 8] for i in range(blocks))
    green_raw = b"".join(data[i * 16 + 8:i * 16 + 16] for i in range(blocks))
    red = _decode_bc4(red_raw, width, height, signed)
    green = _decode_bc4(green_raw, width, height, signed)
    out = bytearray(red)
    out[1::4] = green[0::4]
    out[3::4] = b"\xff" * (width * height)
    return bytes(out)


def _decode_bcn(name: str, data: bytes, width: int, height: int,
                blk_w: int, blk_h: int) -> bytes:
    if name.startswith(("BC1", "BC2", "BC3")):
        if _t2d is not None:
            if name.startswith("BC1"):
                return _bgra_to_rgba(_t2d.decode_bc1(data, width, height))
            if name.startswith("BC3"):
                return _bgra_to_rgba(_t2d.decode_bc3(data, width, height))
        if name.startswith("BC1"):
            return _decode_bc1(data, width, height)
        if name.startswith("BC2"):
            # BC2 = alpha 4 bits + bloc couleur BC1 (mode 4 couleurs)
            color = _decode_bc1(data[8:], width, height)
            out = bytearray(color)
            bw, bh = div_round_up(width, 4), div_round_up(height, 4)
            for by in range(bh):
                for bx in range(bw):
                    off = (by * bw + bx) * 16
                    if off + 8 > len(data):
                        break
                    alphas = int.from_bytes(data[off:off + 8], "little")
                    for py in range(4):
                        y = by * 4 + py
                        if y >= height:
                            break
                        for px in range(4):
                            x = bx * 4 + px
                            if x >= width:
                                break
                            a = (alphas >> (4 * (py * 4 + px))) & 0xF
                            out[(y * width + x) * 4 + 3] = a * 17
            return bytes(out)
        # BC3
        color = _decode_bc1(data[8:], width, height)
        alpha = _decode_bc4(data[:8], width, height)
        out = bytearray(color)
        out[3::4] = alpha[0::4]
        return bytes(out)
    if name.startswith("BC4"):
        if _t2d is not None:
            ch = _bgra_to_rgba(_t2d.decode_bc4(data, width, height))
            out = bytearray(width * height * 4)
            out[0::4] = ch[0::4]
            out[1::4] = ch[0::4]
            out[2::4] = ch[0::4]
            out[3::4] = b"\xff" * (width * height)
            return bytes(out)
        return _decode_bc4(data, width, height, "SNORM" in name)
    if name.startswith("BC5"):
        if _t2d is not None:
            rg = _t2d.decode_bc5(data, width, height)
            out = bytearray(width * height * 4)
            out[0::4] = rg[2::4]
            out[1::4] = rg[1::4]
            out[3::4] = b"\xff" * (width * height)
            return bytes(out)
        return _compose_bc5(data, width, height, "SNORM" in name)
    if name.startswith("BC6"):
        if _t2d is None:
            raise HookyError("BC6 non supporté sans le paquet « texture2ddecoder » "
                             "(pip install texture2ddecoder).")
        rgba = _bgra_to_rgba(_t2d.decode_bc6(data, width, height))
        return rgba
    if name.startswith("BC7"):
        if _t2d is None:
            raise HookyError("BC7 non supporté sans le paquet « texture2ddecoder » "
                             "(pip install texture2ddecoder).")
        return _bgra_to_rgba(_t2d.decode_bc7(data, width, height))
    if name.startswith("ASTC"):
        if _t2d is None:
            raise HookyError("ASTC non supporté sans le paquet « texture2ddecoder » "
                             "(pip install texture2ddecoder).")
        return _bgra_to_rgba(_t2d.decode_astc(data, width, height, blk_w, blk_h))
    raise HookyError(f"Format compressé non géré : {name}")


# --------------------------------------------------------------------------- formats non compressés
def _expand_u16(buf: bytes, table: list[bytes], count: int) -> bytes:
    """Convertit ``count`` valeurs 16 bits via une table de correspondance."""
    count = max(0, min(count, len(buf) // 2))
    if count == 0:
        return b""
    values = struct.unpack_from("<%dH" % count, buf, 0)
    return b"".join([table[v] for v in values])


def _decode_pixels(name: str, data: bytes, width: int, height: int) -> bytes:
    count = width * height
    if name.startswith("R8_UNORM"):
        table = [bytes((v, v, v, 255)) for v in range(256)]
        return b"".join([table[b] for b in data[:count]])

    if name.startswith("R8_G8_UNORM"):
        table = [bytes((v & 0xFF, v >> 8, v >> 8, 255)) for v in range(65536)]
        return _expand_u16(data, table, count)

    if name.startswith("R4_G4_UNORM"):
        out = bytearray(count * 4)
        for i in range(count):
            byte = data[i] if i < len(data) else 0
            v = (byte & 0xF) * 17
            g = (byte >> 4) * 17
            out[i * 4:i * 4 + 4] = bytes((v, g, g, 255))
        return bytes(out)

    if name.startswith("R4_G4_B4_A4"):
        table = []
        for v in range(65536):
            table.append(bytes(((v & 0xF) * 17, ((v >> 4) & 0xF) * 17,
                                ((v >> 8) & 0xF) * 17, ((v >> 12) & 0xF) * 17)))
        return _expand_u16(data, table, count)

    if name.startswith("A4_B4_G4_R4"):
        table = []
        for v in range(65536):
            table.append(bytes((((v >> 12) & 0xF) * 17, ((v >> 8) & 0xF) * 17,
                                ((v >> 4) & 0xF) * 17, (v & 0xF) * 17)))
        return _expand_u16(data, table, count)

    if name.startswith("R5_G5_B5_A1"):
        table = []
        for v in range(65536):
            r = (v & 0x1F) << 3
            g = ((v >> 5) & 0x1F) << 3
            b = ((v >> 10) & 0x1F) << 3
            a = 255 if (v >> 15) & 1 else 0
            table.append(bytes((r | r >> 5, g | g >> 5, b | b >> 5, a)))
        return _expand_u16(data, table, count)

    if name.startswith("A1_B5_G5_R5"):
        table = []
        for v in range(65536):
            a = 255 if v & 1 else 0
            b = ((v >> 1) & 0x1F) << 3
            g = ((v >> 6) & 0x1F) << 3
            r = ((v >> 11) & 0x1F) << 3
            table.append(bytes((r | r >> 5, g | g >> 5, b | b >> 5, a)))
        return _expand_u16(data, table, count)

    if name.startswith("B5_G5_R5_A1"):
        table = []
        for v in range(65536):
            b = (v & 0x1F) << 3
            g = ((v >> 5) & 0x1F) << 3
            r = ((v >> 10) & 0x1F) << 3
            a = 255 if (v >> 15) & 1 else 0
            table.append(bytes((r | r >> 5, g | g >> 5, b | b >> 5, a)))
        return _expand_u16(data, table, count)

    if name.startswith("R5_G6_B5"):
        table = []
        for v in range(65536):
            r = (v & 0x1F) << 3
            g = ((v >> 5) & 0x3F) << 2
            b = ((v >> 11) & 0x1F) << 3
            table.append(bytes((r | r >> 5, g | g >> 6, b | b >> 5, 255)))
        return _expand_u16(data, table, count)

    if name.startswith("B5_G6_R5"):
        table = []
        for v in range(65536):
            b = (v & 0x1F) << 3
            g = ((v >> 5) & 0x3F) << 2
            r = ((v >> 11) & 0x1F) << 3
            table.append(bytes((r | r >> 5, g | g >> 6, b | b >> 5, 255)))
        return _expand_u16(data, table, count)

    if name.startswith("R10_G10_B10_A2"):
        out = bytearray(count * 4)
        for i in range(count):
            v = u32(data, i * 4) if i * 4 + 4 <= len(data) else 0
            r = (v & 0x3FF) >> 2
            g = ((v >> 10) & 0x3FF) >> 2
            b = ((v >> 20) & 0x3FF) >> 2
            a = ((v >> 30) & 0x3) * 85
            out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
        return bytes(out)

    if name.startswith("R8_G8_B8_A8"):
        out = bytearray(data[:count * 4])
        if len(out) < count * 4:
            out += b"\x00" * (count * 4 - len(out))
        return bytes(out)

    if name.startswith("B8_G8_R8_A8"):
        out = bytearray(count * 4)
        src = data[:count * 4]
        if len(src) < count * 4:
            src += b"\x00" * (count * 4 - len(src))
        out[0::4] = src[2::4]
        out[1::4] = src[1::4]
        out[2::4] = src[0::4]
        out[3::4] = src[3::4]
        return bytes(out)

    raise HookyError(f"Format de pixel non géré : {name}")


def apply_component_select(rgba: bytes, comp_sel) -> bytes:
    """Applique la sélection de canaux GX2 (0=zéro, 1=un, 2=R, 3=G, 4=B, 5=A)."""
    sel = list(comp_sel or [])
    if len(sel) != 4 or not all(0 <= v <= 5 for v in sel):
        return rgba
    if sorted(sel) != [2, 3, 4, 5]:
        return rgba  # sélection non conventionnelle : on garde l'ordre naturel
    if sel == [2, 3, 4, 5]:
        return rgba
    src_index = {2: 0, 3: 1, 4: 2, 5: 3}
    out = bytearray(len(rgba))
    for i, channel in enumerate(sel):
        out[i::4] = rgba[src_index[channel]::4]
    return bytes(out)


# --------------------------------------------------------------------------- BNTX
class StringTable:
    """Table de chaînes BNTX (bloc ``_STR``)."""

    def __init__(self) -> None:
        self.by_pos: dict[int, str] = {}

    def load(self, data: bytes, offset: int) -> None:
        if offset + 8 > len(data):
            return
        count = u32(data, offset)
        pos = offset + 8
        for _ in range(min(count, 100000)):
            if pos + 2 > len(data):
                break
            size = u16(data, pos)
            text = cstring(data, pos + 2, limit=max(1, size))
            self.by_pos[pos] = text
            pos += 2 + size + 1
            if pos % 2:
                pos += 1

    def get(self, pos: int) -> str:
        return self.by_pos.get(pos, "")


@dataclass
class BntxTexture:
    name: str
    index: int
    width: int
    height: int
    depth: int
    mip_count: int
    array_length: int
    format_code: int
    format_name: str
    bpp: int
    blk_w: int
    blk_h: int
    tile_mode: int
    block_height_log2: int
    swizzle_value: int
    image_size: int
    data: bytes
    mip_offsets: list[int] = field(default_factory=list)
    comp_sel: tuple = (0, 0, 0, 0)
    warnings: list[str] = field(default_factory=list)
    data_offset: int = 0        # position absolue des données dans le fichier
    orin: bool = False          # disposition Switch 2 (Tegra T239) au lieu de la X1
    file_version: int = 0      # version du BNTX qui contient la texture

    # ------------------------------------------------------------------ décodage
    def mip_size(self, level: int = 0) -> tuple[int, int]:
        return (max(1, self.width >> level), max(1, self.height >> level))

    def _shifted_block_height(self, level: int) -> int:
        lines_per_block_height = (1 << self.block_height_log2) * 8
        shift = 0
        for mip in range(level + 1):
            w, h = self.mip_size(mip)
            blocks = pow2_round_up(div_round_up(h, self.blk_h))
            if blocks < lines_per_block_height:
                shift += 1
        return max(0, self.block_height_log2 - shift)

    def decode(self, level: int = 0, max_size: int | None = None) -> tuple[int, int, bytes]:
        """Décode un mip en RGBA.  ``max_size`` limite la résolution retournée."""
        level = max(0, min(level, max(0, self.mip_count - 1)))
        # On choisit un mip assez petit si demandé (aperçu rapide).
        if max_size:
            while level < self.mip_count - 1 and max(self.mip_size(level)) > max_size:
                level += 1
        width, height = self.mip_size(level)
        if not width or not height:
            raise HookyError("Texture de taille nulle.")

        bpp = self.bpp
        surface = _surface_size(width, height, self.blk_w, self.blk_h, bpp)
        padded = surface_size(width, height, self.blk_w, self.blk_h, bpp,
                              self.tile_mode, self._shifted_block_height(level))
        offset = self.mip_offsets[level] if level < len(self.mip_offsets) else 0
        available = len(self.data) - offset
        if level + 1 < len(self.mip_offsets):
            available = min(available, self.mip_offsets[level + 1] - offset)
        round_pitch = available >= padded
        read_size = padded if round_pitch else surface
        raw = self.data[offset:offset + read_size]
        if len(raw) < read_size:
            if len(raw) == 0:
                raise HookyError(f"« {self.name} » : données de texture absentes.")
            # Texture tronquée : complète avec des zéros pour ne pas planter.
            raw = raw + b"\x00" * (read_size - len(raw))

        self.warnings = []
        if self.swizzle_value not in (0, 1):
            self.warnings.append(f"swizzle inconnu ({self.swizzle_value})")
        linear = deswizzle(width, height, self.blk_w, self.blk_h, bpp,
                           self.tile_mode, self._shifted_block_height(level), raw,
                           round_pitch=round_pitch, orin=self.orin)
        if self.format_name in PIXEL_FORMATS:
            rgba = _decode_pixels(self.format_name, linear, width, height)
        else:
            rgba = _decode_bcn(self.format_name, linear, width, height,
                               self.blk_w, self.blk_h)
        rgba = apply_component_select(rgba, self.comp_sel)
        return width, height, rgba

    @property
    def swizzle_label(self) -> str:
        """« Orin (Switch 2) » ou « X1 (Switch 1) » tel qu'utilisé au décodage."""
        if self.tile_mode == 1:
            return "linéaire"
        return "Orin (Switch 2)" if self.orin else "X1 (Switch 1)"

    def to_png(self, level: int = 0) -> bytes:
        width, height, rgba = self.decode(level)
        return png_bytes(width, height, rgba)

    def save_png(self, path, level: int = 0) -> None:
        from .util import write_atomic
        write_atomic(Path(path), self.to_png(level))

    def info(self) -> str:
        lines = [
            f"Texture : {self.name or f'#{self.index}'}",
            f"Taille  : {self.width} × {self.height}"
            + (f" × {self.depth}" if self.depth > 1 else ""),
            f"Format  : {self.format_name} (0x{self.format_code:04X})",
            f"Mips    : {self.mip_count}   ·   Tableau : {self.array_length}",
            f"Tile    : {'linéaire' if self.tile_mode == 1 else 'block-linear'}"
            f"   ·   blockHeightLog2 : {self.block_height_log2}"
            f"   ·   swizzle : {self.swizzle_label}",
            f"Taille des données : {self.image_size:,} octets",
        ]
        if self.warnings:
            lines += [""] + [f"⚠ {w}" for w in self.warnings]
        return "\n".join(lines)


class BntxFile:
    """Fichier BNTX contenant une ou plusieurs textures.

    Les jeux Switch 2 écrivent des BNTX **0.4.x** (« Ounc ») dont les pixels
    utilisent la disposition *Orin* : la version décide donc du désentrelacement,
    et ``orin`` peut être forcé par l'appelant si un fichier est atypique.
    """

    #: Version à partir de laquelle Nintendo utilise la disposition Orin.
    ORIN_VERSION = 0x00040000

    def __init__(self, data: bytes, textures: list[BntxTexture], name: str = "",
                 version: int = 0, orin: bool = False,
                 warnings: list[str] | None = None) -> None:
        self.data = data
        self.textures = textures
        self.name = name
        self.version = version
        self.orin = orin
        self.warnings = warnings or []

    def __len__(self) -> int:
        return len(self.textures)

    def __iter__(self):
        return iter(self.textures)

    @property
    def texture_names(self) -> list[str]:
        return [t.name or f"#{t.index}" for t in self.textures]

    def get(self, name: str) -> BntxTexture | None:
        for tex in self.textures:
            if tex.name == name:
                return tex
        return None

    @classmethod
    def parse(cls, data: bytes, name: str = "", orin: bool | None = None) -> "BntxFile":
        """Analyse un BNTX.

        ``orin=None`` (défaut) choisit la disposition des pixels d'après la
        version du fichier (0.4.x et au-delà = Switch 2) ; ``True``/``False``
        force la disposition, ce qui permet de rattraper un fichier atypique
        depuis l'interface ou la ligne de commande.
        """
        if data[:4] != MAGIC:
            raise FormatError("Ce n'est pas un fichier BNTX (magic « BNTX » absent).")
        if len(data) < 0x40:
            raise FormatError("Fichier BNTX tronqué.")
        bom = data[0x0C:0x0E]
        if bom == b"\xff\xfe":
            e = "<"
        elif bom == b"\xfe\xff":
            e = ">"
        else:
            raise FormatError(
                "Fichier BNTX : BOM inconnu (0x0C : "
                f"{bom.hex()}). Ce fichier n'est peut-être pas un BNTX Nintendo.")
        try:
            version = u32(data, 0x08, e)
            first_block = u16(data, 0x16, e)
            name_offset = u32(data, 0x10, e)
            count = u32(data, 0x24, e)
            info_ptrs = u64(data, 0x28, e)
        except struct.error as exc:
            raise FormatError("Fichier BNTX : en-tête illisible.") from exc

        warnings: list[str] = []
        use_orin = (version >= cls.ORIN_VERSION) if orin is None else bool(orin)

        strings = StringTable()
        try:
            strings.load(data, first_block + 16)
        except (struct.error, IndexError):
            warnings.append("table des chaînes illisible : noms de textures perdus")
        file_name = strings.get(name_offset)
        if not file_name:
            # Le nom du fichier est indexé comme les autres chaînes.
            for pos, value in strings.by_pos.items():
                if value.lower().endswith((".bntx", ".bftex")):
                    file_name = value
                    break

        pointers, pointer_warning = cls._texture_pointers(data, e, count, info_ptrs)
        if pointer_warning:
            warnings.append(pointer_warning)

        textures: list[BntxTexture] = []
        for i, ptr in enumerate(pointers):
            if ptr + 0x10 > len(data):
                warnings.append(f"BTI {i} hors du fichier (ignoré)")
                continue
            if data[ptr:ptr + 4] != b"BRTI":
                warnings.append(f"BTI {i} : signature « BRTI » absente (ignoré)")
                continue
            try:
                tex = cls._parse_texture(data, ptr + 0x10, e, i, strings)
            except FormatError as exc:
                warnings.append(f"texture {i} : {exc}")
                continue
            tex.orin = use_orin
            tex.file_version = version
            textures.append(tex)
        if not textures:
            raise FormatError(
                "Fichier BNTX : aucune texture lisible."
                + ("\n\n" + "\n".join(warnings[:6]) if warnings else ""))
        if use_orin:
            warnings.append(
                f"BNTX {version >> 24}.{(version >> 16) & 0xFF}.{(version >> 8) & 0xFF}"
                " (Switch 2) : disposition Orin utilisée pour les pixels")
        return cls(data=data, textures=textures, name=name or file_name,
                   version=version, orin=use_orin, warnings=warnings)

    @staticmethod
    def _texture_pointers(data: bytes, e: str, count: int,
                          info_ptrs: int) -> tuple[list[int], str]:
        """Table des pointeurs de textures, avec repli pour les BNTX atypiques.

        Les jeux Switch 2 utilisent des BNTX 0.4.x : si l'en-tête lu ne donne
        rien d'exploitable (compteur absurde, pointeurs hors fichier, table
        déplacée), on retrouve les blocs ``BRTI`` en balayant le fichier — mieux
        vaut ouvrir une texture que refuser tout le fichier.
        """
        plausible = 0 < count <= 4096
        pointers: list[int] = []
        if plausible and info_ptrs and info_ptrs + 8 * min(count, 4096) <= len(data):
            for i in range(count):
                try:
                    ptr = u64(data, info_ptrs + 8 * i, e)
                except struct.error:
                    pointers = []
                    break
                if ptr + 0x10 > len(data) or data[ptr:ptr + 4] != b"BRTI":
                    pointers = []
                    break
                pointers.append(ptr)
            if len(pointers) == count:
                return pointers, ""

        # --- repli : balayage des signatures « BRTI » (fichier aligné sur 8/16)
        scanned: list[int] = []
        position = data.find(b"BRTI")
        while position != -1 and len(scanned) < 4096:
            scanned.append(position)
            position = data.find(b"BRTI", position + 4)
        # élimine les faux positifs (un « BRTI » de données doit avoir un nom/pointeurs valides)
        scanned = [p for p in scanned if p + 0x80 <= len(data)]
        if not scanned:
            raise FormatError(
                "Fichier BNTX : aucune table de textures ni bloc « BRTI » trouvé."
                f"\nTaille du fichier : {len(data):,} octets")
        return scanned, ("table des textures illisible : blocs « BRTI » retrouvés "
                         f"par balayage ({len(scanned)})")

    @staticmethod
    def _parse_texture(data: bytes, off: int, e: str, index: int,
                       strings: StringTable) -> BntxTexture:
        try:
            flags = data[off]
            dim = data[off + 1]
            tile_mode = u16(data, off + 2, e)
            swizzle_value = u16(data, off + 4, e)
            mip_count = u16(data, off + 6, e)
            # numSamples u16 @ off+8
            format_code = u32(data, off + 12, e)
            # accessFlags u32 @ off+16
            width = struct.unpack_from(e + "i", data, off + 20)[0]
            height = struct.unpack_from(e + "i", data, off + 24)[0]
            depth = struct.unpack_from(e + "i", data, off + 28)[0]
            array_length = u32(data, off + 32, e)
            texture_layout = u32(data, off + 36, e)
            image_size = u32(data, off + 64, e)
            comp_sel = (data[off + 72], data[off + 73], data[off + 74], data[off + 75])
            name_addr = u64(data, off + 76, e)
            ptrs_addr = u64(data, off + 92, e)
        except (struct.error, IndexError) as exc:
            raise FormatError(f"BTI {index} : structure illisible.") from exc

        if width <= 0 or height <= 0:
            raise FormatError(f"BTI {index} : dimensions invalides ({width}×{height}).")
        try:
            format_name, bpp, blk_w, blk_h = format_info(format_code)
        except FormatError:
            format_name, bpp, blk_w, blk_h = (f"0x{format_code:04X}", 0, 1, 1)

        block_height_log2 = texture_layout & 7 if flags & 1 else 0

        # offsets des mips (pointeurs absolus vers les données)
        mip_offsets: list[int] = [0]
        mip_count = max(1, mip_count)
        base = 0
        if ptrs_addr and ptrs_addr + 8 <= len(data):
            base = u64(data, ptrs_addr, e)
            if base >= len(data):
                base = 0
            for mip in range(1, mip_count):
                ptr_pos = ptrs_addr + 8 * mip
                if ptr_pos + 8 > len(data):
                    break
                ptr = u64(data, ptr_pos, e)
                mip_offsets.append(max(0, ptr - base) if ptr >= base else 0)

        payload = data[base:] if base else b""

        texture = BntxTexture(
            name=strings.get(name_addr) or "",
            index=index,
            width=width,
            height=height,
            depth=max(1, depth),
            mip_count=mip_count,
            array_length=max(1, array_length),
            format_code=format_code,
            format_name=format_name,
            bpp=bpp,
            blk_w=blk_w,
            blk_h=blk_h,
            tile_mode=tile_mode,
            block_height_log2=block_height_log2,
            swizzle_value=swizzle_value,
            image_size=image_size,
            data=payload,
            mip_offsets=mip_offsets,
            data_offset=base,
            comp_sel=comp_sel,
        )
        return texture

    @property
    def oriented_label(self) -> str:
        """Résumé lisible de la disposition utilisée."""
        if not self.textures:
            return "sans texture"
        tiles = {t.tile_mode for t in self.textures}
        if tiles == {1}:
            return "pixels linéaires"
        return "swizzle Orin (Switch 2)" if self.orin else "swizzle X1 (Switch 1)"

    @property
    def version_label(self) -> str:
        """Version lisible : Nintendo numérote « 0.4.1.x » les BNTX Switch 2."""
        v = self.version
        return f"{v >> 24}.{(v >> 16) & 0xFF}.{(v >> 8) & 0xFF}.{v & 0xFF}"

    def info(self) -> str:
        lines = [f"Fichier BNTX : {self.name or '(sans nom)'}",
                 f"Version : {self.version_label} (0x{self.version:08X})"
                 f"   ·   swizzle par défaut : "
                 f"{'Orin (Switch 2)' if self.orin else 'X1 (Switch 1)'}",
                 f"Textures : {len(self.textures)}", ""]
        for tex in self.textures:
            lines.append(f"  • {tex.name or f'#{tex.index}'} — {tex.width}×{tex.height} "
                         f"— {tex.format_name} — {tex.mip_count} mip(s)"
                         f" — {tex.swizzle_label}")
        if self.warnings:
            lines += [""] + [f"⚠ {w}" for w in self.warnings]
        return "\n".join(lines)


def is_bntx(data: bytes) -> bool:
    return data[:4] == MAGIC


def parse_bntx(data: bytes, name: str = "") -> BntxFile:
    return BntxFile.parse(data, name)
