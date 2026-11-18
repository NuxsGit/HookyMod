"""Encodage BNTX : PNG (RGBA) → pixels BNTX, avec swizzle et mips.

Deux modes :

* **remplacement dans un BNTX existant** (:func:`replace_texture`) : le plus sûr
  pour un mod — on garde l'en-tête, la version, la disposition, le format et les
  mips du fichier d'origine, on ne réécrit que les pixels. Le fichier garde
  exactement sa taille.
* **création d'un BNTX** (:func:`build_bntx`) : fichier complet autonome
  (texture non compressée par défaut, ou BC1/BC3), utile pour une texture
  ajoutée par un mod.

Formats encodables : R8, RG8, RGBA8 (et sRGB), BGRA8, RGB565, BGR565, RGBA4,
BGRA4, RGB5A1, BGR5A1, BC1, BC3, BC4, BC5. Le cœur Rust facultatif reproduit
les mêmes octets pour accélérer l'encodage et la génération des mips.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import bntx as bntx_mod
from . import native
from .util import FormatError, HookyError, align, decode_png, png_bytes, write_atomic

# --------------------------------------------------------------------------- formats
# code BNTX -> nom court utilisé par les encodeurs
ENCODABLE = {
    0x0201: "R8",
    0x0901: "RG8",
    0x0B01: "RGBA8", 0x0B06: "RGBA8",
    0x0C01: "BGRA8", 0x0C06: "BGRA8",
    0x0701: "RGB565", 0x0801: "BGR565",
    0x0301: "RGBA4", 0x0401: "BGRA4",
    0x0501: "RGB5A1", 0x3B01: "BGR5A1",
    0x1A01: "BC1", 0x1A06: "BC1",
    0x1C01: "BC3", 0x1C06: "BC3",
    0x1D01: "BC4",
    0x1E01: "BC5",
}

# nom lisible -> code (pour build_bntx)
NAME_TO_CODE = {
    "R8": 0x0201, "RG8": 0x0901,
    "RGBA8": 0x0B01, "SRGB8": 0x0B06,
    "BGRA8": 0x0C01,
    "RGB565": 0x0701, "BGR565": 0x0801,
    "RGBA4": 0x0301, "BGRA4": 0x0401,
    "RGB5A1": 0x0501, "BGR5A1": 0x3B01,
    "BC1": 0x1A01, "BC1_SRGB": 0x1A06,
    "BC3": 0x1C01, "BC3_SRGB": 0x1C06,
    "BC4": 0x1D01, "BC5": 0x1E01,
}


def can_encode_format(code: int) -> bool:
    return code in ENCODABLE


def format_hint(code: int) -> str:
    return ENCODABLE.get(code, "")


# --------------------------------------------------------------------------- couleurs
def _clamp8(value: float) -> int:
    return 0 if value <= 0 else (255 if value >= 255 else int(value + 0.5))


def _rgb_to_565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _565_to_rgb(value: int) -> tuple[int, int, int]:
    r = (value >> 11) & 0x1F
    g = (value >> 5) & 0x3F
    b = value & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _pixel(rgba: bytes, width: int, x: int, y: int) -> tuple[int, int, int, int]:
    at = (y * width + x) * 4
    return rgba[at], rgba[at + 1], rgba[at + 2], rgba[at + 3]


# --------------------------------------------------------------------------- formats simples
def _encode_linear(rgba: bytes, width: int, height: int, kind: str) -> bytes:
    out = bytearray()
    for y in range(height):
        for x in range(width):
            r, g, b, a = _pixel(rgba, width, x, y)
            if kind == "R8":
                out.append(r)
            elif kind == "RG8":
                out += bytes((r, g))
            elif kind == "RGBA8":
                out += bytes((r, g, b, a))
            elif kind == "BGRA8":
                out += bytes((b, g, r, a))
            elif kind == "RGB565":
                out += struct.pack("<H", _rgb_to_565(r, g, b))
            elif kind == "BGR565":
                out += struct.pack("<H", _rgb_to_565(b, g, r))
            elif kind == "RGBA4":
                out += struct.pack("<H", ((r >> 4) << 12) | ((g >> 4) << 8)
                                   | ((b >> 4) << 4) | (a >> 4))
            elif kind == "BGRA4":
                out += struct.pack("<H", ((b >> 4) << 12) | ((g >> 4) << 8)
                                   | ((r >> 4) << 4) | (a >> 4))
            elif kind == "RGB5A1":
                out += struct.pack("<H", ((r >> 3) << 11) | ((g >> 3) << 6)
                                   | ((b >> 3) << 1) | (1 if a >= 128 else 0))
            elif kind == "BGR5A1":
                out += struct.pack("<H", ((b >> 3) << 11) | ((g >> 3) << 6)
                                   | ((r >> 3) << 1) | (1 if a >= 128 else 0))
            else:  # pragma: no cover - garde-fou
                raise HookyError(f"Format non encodable : {kind}")
    return bytes(out)


# --------------------------------------------------------------------------- BC4 / BC5
def _bc4_block(values: list[int]) -> bytes:
    """Bloc BC4 (canal unique, 8 octets) : rampe 8 valeurs."""
    low, high = min(values), max(values)
    if low == high:
        return bytes((high, high) + (0,) * 6)
    palette = [high, low]
    if high > low:
        for index in range(1, 7):
            palette.append(_clamp8(high + (low - high) * index / 7.0))
        bits = 0
        for i, value in enumerate(values):
            best = min(range(8), key=lambda k: abs(palette[k] - value))
            bits |= best << (3 * i)
    else:  # pragma: no cover - high > low toujours vrai ici
        for index in range(1, 5):
            palette.append(_clamp8(high + (low - high) * index / 5.0))
        bits = 0
    return bytes((high, low)) + struct.pack("<Q", bits)[:6]


def _encode_bc4(rgba: bytes, width: int, height: int, channel: int = 0) -> bytes:
    out = bytearray()
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            values = []
            for y in range(4):
                for x in range(4):
                    px = min(width - 1, bx + x)
                    py = min(height - 1, by + y)
                    values.append(_pixel(rgba, width, px, py)[channel])
            out += _bc4_block(values)
    return bytes(out)


def _encode_bc5(rgba: bytes, width: int, height: int) -> bytes:
    red = _encode_bc4(rgba, width, height, 0)
    green = _encode_bc4(rgba, width, height, 1)
    # BC5 entrelace les blocs rouges et verts par bloc
    out = bytearray()
    blocks = len(red) // 8
    for index in range(blocks):
        out += red[index * 8:index * 8 + 8]
        out += green[index * 8:index * 8 + 8]
    return bytes(out)


# --------------------------------------------------------------------------- BC1 / BC3
def _bc1_colors(block: list[tuple[int, int, int, int]],
                allow_transparent: bool) -> tuple[int, int, int]:
    """Retourne (couleur0, couleur1, bits) pour un bloc 4×4."""
    opaque = [px for px in block if px[3] >= 128] or block
    if allow_transparent and any(px[3] < 128 for px in block):
        mode412 = True
    else:
        mode412 = False

    # endpoints = min/max selon la luminance (méthode simple et stable)
    def lum(px):
        return px[0] * 299 + px[1] * 587 + px[2] * 114

    ordered = sorted(opaque, key=lum)
    low, high = ordered[0], ordered[-1]
    c0 = _rgb_to_565(*high[:3])
    c1 = _rgb_to_565(*low[:3])
    if c0 == c1:
        c1 = max(0, c0 - 1)
    if c0 < c1:
        c0, c1 = c1, c0

    palette = [_565_to_rgb(c0), _565_to_rgb(c1)]
    if mode412:
        palette.append(tuple((2 * a + b) // 3 for a, b in zip(palette[0], palette[1])))
        palette.append((0, 0, 0))
    else:
        palette.append(tuple((2 * a + b) // 3 for a, b in zip(palette[0], palette[1])))
        palette.append(tuple((a + 2 * b) // 3 for a, b in zip(palette[0], palette[1])))

    bits = 0
    for index, px in enumerate(block):
        if mode412 and px[3] < 128:
            choice = 3
        else:
            choice = min(range(len(palette)),
                         key=lambda k: sum((palette[k][c] - px[c]) ** 2 for c in range(3)))
            if mode412 and choice == 3:
                choice = 0
        bits |= choice << (2 * index)
    return c0, c1, bits


def _encode_bc1(rgba: bytes, width: int, height: int, allow_alpha: bool = True) -> bytes:
    out = bytearray()
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            block = []
            for y in range(4):
                for x in range(4):
                    px = min(width - 1, bx + x)
                    py = min(height - 1, by + y)
                    block.append(_pixel(rgba, width, px, py))
            c0, c1, bits = _bc1_colors(block, allow_alpha)
            out += struct.pack("<HHI", c0, c1, bits & 0xFFFFFFFF)
    return bytes(out)


def _encode_bc3(rgba: bytes, width: int, height: int) -> bytes:
    alpha = _encode_bc4(rgba, width, height, 3)
    color = _encode_bc1(rgba, width, height, allow_alpha=False)
    out = bytearray()
    for index in range(len(color) // 8):
        out += alpha[index * 8:index * 8 + 8]
        out += color[index * 8:index * 8 + 8]
    return bytes(out)


def encode_pixels(rgba: bytes, width: int, height: int, format_code: int) -> bytes:
    """Encode un buffer RGBA (linéaire) dans le format BNTX demandé."""
    kind = ENCODABLE.get(format_code)
    if not kind:
        name = bntx_mod.format_info(format_code)[0]
        raise HookyError(f"Format non encodable par HookyMod : {name} "
                         f"(0x{format_code:04X}). Formats gérés : "
                         + ", ".join(sorted(set(ENCODABLE.values()))
                                     if False else sorted(set(ENCODABLE.values()))))
    expected = width * height * 4
    if len(rgba) < expected:
        raise HookyError(f"Pixels RGBA trop courts : {len(rgba)} < {expected}.")
    if native.available():
        encoded = native.encode_pixels(kind, rgba, width, height)
        if encoded is not None:
            return encoded
    if kind in ("BC1", "BC3", "BC4", "BC5", "RGB565", "BGR565", "RGBA4", "BGRA4",
                "RGB5A1", "BGR5A1", "R8", "RG8", "RGBA8", "BGRA8"):
        if kind == "BC1":
            return _encode_bc1(rgba, width, height)
        if kind == "BC3":
            return _encode_bc3(rgba, width, height)
        if kind == "BC4":
            return _encode_bc4(rgba, width, height, 0)
        if kind == "BC5":
            return _encode_bc5(rgba, width, height)
        return _encode_linear(rgba, width, height, kind)
    raise HookyError(f"Format non encodable : {kind}")  # pragma: no cover


# --------------------------------------------------------------------------- mips
def downsample(rgba: bytes, width: int, height: int) -> tuple[int, int, bytes]:
    """Réduit de moitié par moyenne 2×2 (filtre boîte)."""
    new_w = max(1, width // 2)
    new_h = max(1, height // 2)
    if native.available():
        result = native.downsample_mip(rgba, width, height)
        if result is not None:
            return new_w, new_h, result
    out = bytearray(new_w * new_h * 4)
    for y in range(new_h):
        for x in range(new_w):
            total = [0, 0, 0, 0]
            samples = 0
            for dy in (0, 1):
                for dx in (0, 1):
                    sx = min(width - 1, x * 2 + dx)
                    sy = min(height - 1, y * 2 + dy)
                    px = _pixel(rgba, width, sx, sy)
                    for c in range(4):
                        total[c] += px[c]
                    samples += 1
            at = (y * new_w + x) * 4
            for c in range(4):
                out[at + c] = _clamp8(total[c] / samples)
    return new_w, new_h, bytes(out)


def build_mips(rgba: bytes, width: int, height: int,
               levels: int) -> list[tuple[int, int, bytes]]:
    chain = [(width, height, rgba)]
    for _ in range(max(0, levels - 1)):
        w, h, data = chain[-1]
        if w <= 1 and h <= 1:
            break
        chain.append(downsample(data, w, h))
    return chain


def mip_levels_for(width: int, height: int) -> int:
    levels = 1
    while width > 1 or height > 1:
        width = max(1, width // 2)
        height = max(1, height // 2)
        levels += 1
    return levels


# --------------------------------------------------------------------------- remplacement
@dataclass
class ReplaceReport:
    texture: str
    width: int
    height: int
    format_name: str
    mips: int
    size: int

    def __str__(self) -> str:
        return (f"{self.texture or 'texture'} : {self.width}×{self.height} "
                f"{self.format_name}, {self.mips} mip(s), {self.size} octets")


def _shifted_block_height(texture: "bntx_mod.BntxTexture", level: int) -> int:
    return texture._shifted_block_height(level)


def replace_texture(template: bytes, rgba: bytes, width: int, height: int,
                    name: str = "", index: int | None = None,
                    generate_mips: bool = True) -> tuple[bytes, ReplaceReport]:
    """Réécrit les pixels d'une texture dans un BNTX existant (taille constante).

    Le format, la disposition (swizzle), les dimensions et le nombre de mips
    viennent du fichier d'origine : on ne remplace que les octets de pixels.
    """
    file = bntx_mod.BntxFile.parse(template)
    texture = None
    if name:
        texture = file.get(name)
        if texture is None:
            for candidate in file.textures:
                if candidate.name.lower() == name.lower():
                    texture = candidate
                    break
    if texture is None and index is not None:
        if 0 <= index < len(file.textures):
            texture = file.textures[index]
    if texture is None:
        if not name and index is None and len(file.textures) == 1:
            texture = file.textures[0]
        else:
            names = ", ".join(file.texture_names)
            raise HookyError(f"Texture « {name or index} » introuvable. "
                             f"Textures du fichier : {names}")
    if (width, height) != (texture.width, texture.height):
        raise HookyError(
            f"Dimensions différentes de l'original ({texture.width}×{texture.height} "
            f"vs {width}×{height}). Pour changer la taille d'une texture, il faut "
            "reconstruire le BNTX (build_bntx) : le jeu lit la taille dans l'en-tête "
            "et les autres offsets du fichier ne bougeraient pas.")
    if not can_encode_format(texture.format_code):
        raise HookyError(
            f"Le format d'origine ({texture.format_name}, 0x{texture.format_code:04X}) "
            "n'est pas encodable par HookyMod. Formats gérés : "
            + ", ".join(sorted(set(ENCODABLE.values())))
            + ". Réexporte la texture en RGBA8/BC1/BC3 depuis le jeu, ou utilise "
              "un BNTX d'origine dans un de ces formats.")

    out = bytearray(template)
    base = getattr(texture, "data_offset", 0) or (len(template) - len(texture.data))
    image_end = min(len(template), base + max(texture.image_size, len(texture.data)))
    levels = texture.mip_count if generate_mips else 1
    levels = max(1, min(levels, mip_levels_for(width, height)))
    chain = build_mips(rgba, width, height, levels)
    written = 0
    for level, (level_w, level_h, pixels) in enumerate(chain):
        encoded = encode_pixels(pixels, level_w, level_h, texture.format_code)
        surface = bntx_mod._surface_size(level_w, level_h, texture.blk_w,
                                         texture.blk_h, texture.bpp)
        if len(encoded) < surface:
            encoded = encoded + b"\x00" * (surface - len(encoded))
        padded = bntx_mod.surface_size(level_w, level_h, texture.blk_w, texture.blk_h,
                                       texture.bpp, texture.tile_mode,
                                       _shifted_block_height(texture, level))
        offset = base + (texture.mip_offsets[level]
                         if level < len(texture.mip_offsets) else 0)
        if level + 1 < len(texture.mip_offsets):
            limit = base + texture.mip_offsets[level + 1]
        else:
            limit = image_end
        space = max(0, min(limit, len(out)) - offset)
        if space < surface // 2:
            raise HookyError(f"Mip {level} hors du fichier : le BNTX d'origine est "
                             "incohérent (ou tronqué).")
        round_pitch = space >= padded
        swizzled = bntx_mod.swizzle(level_w, level_h, texture.blk_w, texture.blk_h,
                                    texture.bpp, texture.tile_mode,
                                    _shifted_block_height(texture, level), encoded,
                                    round_pitch=round_pitch, orin=texture.orin)
        chunk = swizzled[:space]
        out[offset:offset + len(chunk)] = chunk
        written += len(chunk)
    report = ReplaceReport(texture.name, width, height, texture.format_name,
                           len(chain), written)
    return bytes(out), report


# --------------------------------------------------------------------------- création
def _string_table(entries: list[str]) -> bytes:
    """Entrées d'une table ``_STR`` : ``u16 longueur`` + texte + NUL, alignées.

    Le bloc complet (magic ``_STR``, taille, nombre d'entrées) est écrit par
    :func:`build_bntx` : les entrées commencent 24 octets après le magic.
    """
    payload = bytearray()
    for entry in entries:
        raw = entry.encode("utf-8")
        payload += struct.pack("<H", len(raw)) + raw + b"\x00"
        if len(payload) % 2:
            payload += b"\x00"
    return bytes(payload)


def build_bntx(name: str, width: int, height: int, rgba: bytes,
               format_name: str = "RGBA8", mips: int | None = None,
               version: int | None = None, alignment_shift: int = 12,
               texture_name: str = "", orin: bool = False) -> bytes:
    """Construit un fichier BNTX complet à une texture.

    Le résultat est autonome (une texture, ``mips`` niveaux générés). Pour un
    mod, préfère :func:`replace_texture` quand la texture existe déjà : les
    en-têtes du jeu (version, drapeaux, alignement) restent alors intacts.

    ``orin=True`` produit un fichier 0.4.x en disposition Orin ;
    par défaut le fichier est en disposition X1 (version 0.3).
    """
    if version is None:
        version = 0x00040100 if orin else 0x00030000
    code = NAME_TO_CODE.get(format_name.upper())
    if code is None:
        raise HookyError(f"Format inconnu : {format_name}. Choix : "
                         + ", ".join(sorted(NAME_TO_CODE)))
    if decode_png is None:  # pragma: no cover - garde-fou
        raise HookyError("Décodage PNG indisponible.")
    levels = mip_levels_for(width, height) if mips in (None, 0) else max(1, int(mips))
    chain = build_mips(rgba, width, height, levels)
    format_code = code
    _, bpp, blk_w, blk_h = bntx_mod.format_info(format_code)
    compressed = blk_w > 1 or blk_h > 1
    tile_mode = 0 if compressed else 1
    block_height_log2 = 4 if compressed else 0
    surfaces: list[bytes] = []
    shift = 0
    for level_w, level_h, pixels in chain:
        encoded = encode_pixels(pixels, level_w, level_h, format_code)
        blocks_w = bntx_mod.div_round_up(level_w, blk_w)
        blocks_h = bntx_mod.div_round_up(level_h, blk_h)
        surface = blocks_w * blocks_h * bpp
        if len(encoded) < surface:
            encoded += b"\x00" * (surface - len(encoded))
        # Même règle que le décodeur : le blockHeight effectif baisse sur les
        # petites surfaces (moins de 8 * 2^log2 lignes de blocs).
        if compressed:
            lines_per = (1 << block_height_log2) * 8
            if bntx_mod.pow2_round_up(bntx_mod.div_round_up(level_h, blk_h)) < lines_per:
                shift += 1
            effective = max(0, block_height_log2 - shift)
        else:
            effective = 0
        swizzled = bntx_mod.swizzle(level_w, level_h, blk_w, blk_h, bpp, tile_mode,
                                    effective, encoded, orin=orin)
        padded = bntx_mod.surface_size(level_w, level_h, blk_w, blk_h, bpp,
                                       tile_mode, effective)
        surfaces.append(swizzled if len(swizzled) >= padded else
                        swizzled + b"\x00" * (padded - len(swizzled)))

    texture_label = texture_name or (name.rsplit(".", 1)[0] if name else "texture")
    display_name = name or (texture_label + ".bntx")

    count = 1
    header_size = 0x20
    container_size = 0x38
    ptr_array = header_size + container_size          # 0x58
    brti_at = align(ptr_array + 8 * count, 8)
    brti_struct = 0x78
    struct_at = brti_at + 0x10
    mip_ptr_at = align(struct_at + brti_struct * count, 8)
    mip_ptr_size = 8 * len(surfaces)
    strings_at = mip_ptr_at + mip_ptr_size
    strings_payload = _string_table([display_name, texture_label])
    str_block = strings_at
    # _STR : magic (4) + 8 octets réservés + u32 nombre + u32 réservé
    #        + entrées (chacune : u16 longueur, texte, NUL)
    str_header = 24
    str_block_size = str_header + len(strings_payload)
    name_pos = str_block + str_header
    # position de la 2ᵉ chaîne (nom de la texture), avec l'alignement pair
    tex_name_pos = name_pos + 2 + len(display_name.encode("utf-8")) + 1
    if (tex_name_pos - name_pos) % 2:
        tex_name_pos += 1
    data_at = align(str_block + str_block_size, 1 << max(4, alignment_shift))
    total = data_at + sum(len(surface) for surface in surfaces)

    out = bytearray(total)
    # --- en-tête
    struct.pack_into("<8sIHBB", out, 0, b"BNTX", version, 0xFEFF, alignment_shift, 6)
    struct.pack_into("<I", out, 0x10, name_pos)        # nom du fichier dans _STR
    struct.pack_into("<HH", out, 0x14, 0, str_block)   # flags, premier bloc
    struct.pack_into("<I", out, 0x18, 0)               # table de relocalisation
    struct.pack_into("<I", out, 0x1C, total)
    # --- conteneur
    out[0x20:0x24] = b"NX  "
    struct.pack_into("<I", out, 0x24, count)
    struct.pack_into("<Q", out, 0x28, ptr_array)       # tableau des BTI
    struct.pack_into("<Q", out, 0x30, data_at)         # début des données
    struct.pack_into("<Q", out, 0x38, 0)               # dictionnaire
    struct.pack_into("<Q", out, ptr_array, brti_at)

    # --- structure de texture
    out[brti_at:brti_at + 4] = b"BRTI"
    at = struct_at
    out[at] = 0x01                    # flags : blockHeightLog2 présent
    out[at + 1] = 1                   # dim = 2D
    struct.pack_into("<H", out, at + 2, tile_mode)
    struct.pack_into("<H", out, at + 4, 1)             # swizzle = 1
    struct.pack_into("<H", out, at + 6, len(surfaces))
    struct.pack_into("<H", out, at + 8, 1)             # samples
    struct.pack_into("<I", out, at + 12, format_code)
    struct.pack_into("<i", out, at + 20, width)
    struct.pack_into("<i", out, at + 24, height)
    struct.pack_into("<i", out, at + 28, 1)
    struct.pack_into("<I", out, at + 32, 1)            # arrayLength
    layout = block_height_log2 & 7
    struct.pack_into("<I", out, at + 36, layout)
    struct.pack_into("<Q", out, at + 76, tex_name_pos)   # nom de la texture
    struct.pack_into("<I", out, at + 64, sum(len(s) for s in surfaces))
    struct.pack_into("<I", out, at + 68, 1 << max(4, alignment_shift))
    struct.pack_into("<BBBB", out, at + 72, 0, 1, 2, 3)
    struct.pack_into("<Q", out, at + 92, mip_ptr_at)

    # --- pointeurs de mips
    offset = data_at
    for level, surface in enumerate(surfaces):
        struct.pack_into("<Q", out, mip_ptr_at + 8 * level, offset)
        out[offset:offset + len(surface)] = surface
        offset += len(surface)

    # --- bloc de chaînes
    out[str_block:str_block + 4] = b"_STR"
    struct.pack_into("<I", out, str_block + 4, str_block_size)
    struct.pack_into("<I", out, str_block + 16, 2)     # nombre de chaînes
    out[name_pos:name_pos + len(strings_payload)] = strings_payload
    return bytes(out)


# --------------------------------------------------------------------------- API simple
def png_to_bntx(png: bytes, template: bytes | None = None, texture: str = "",
                index: int | None = None, format_name: str = "RGBA8",
                mips: int | None = None, name: str = "",
                orin: bool = False) -> tuple[bytes, ReplaceReport]:
    """Convertit un PNG en BNTX (remplacement si ``template`` est fourni).

    Sans gabarit, ``orin=True`` produit un fichier 0.4.x en disposition Orin ;
    avec un gabarit, c'est la disposition du fichier d'origine qui s'applique.
    """
    try:
        width, height, rgba = decode_png(png)
    except Exception as exc:  # noqa: BLE001 - message destiné à l'utilisateur
        raise FormatError(f"PNG illisible : {exc}") from exc
    if template:
        return replace_texture(template, rgba, width, height, texture, index)
    data = build_bntx(name or "texture.bntx", width, height, rgba, format_name, mips,
                      texture_name=texture, orin=orin)
    report = ReplaceReport(texture, width, height, bntx_mod.format_info(
        NAME_TO_CODE[format_name.upper()])[0], mips or mip_levels_for(width, height),
        len(data))
    return data, report


def png_to_bntx_file(png_path, out_path, template: bytes | None = None, **kwargs):
    from pathlib import Path
    png = Path(png_path).read_bytes()
    data, report = png_to_bntx(png, template, **kwargs)
    write_atomic(Path(out_path), data)
    return report


def bntx_to_png(data: bytes, name: str = "", index: int | None = None,
                level: int = 0) -> bytes:
    file = bntx_mod.BntxFile.parse(data)
    texture = file.get(name) if name else None
    if texture is None and index is not None and 0 <= index < len(file.textures):
        texture = file.textures[index]
    if texture is None:
        if len(file.textures) != 1:
            raise HookyError(f"Texture « {name or index} » introuvable (le fichier "
                             f"en contient {len(file.textures)}).")
        texture = file.textures[0]
    width, height, rgba = texture.decode(level)
    return png_bytes(width, height, rgba)
