"""Génération de fichiers de test synthétiques (aucun asset du jeu requis)."""

from __future__ import annotations

import struct

import zstandard as zstd


def _w(off: int, size: int) -> int:
    return off


class Blob:
    """Tampon binaire avec écriture à des offsets absolus."""

    def __init__(self, size: int) -> None:
        self.data = bytearray(size)

    def put(self, offset: int, fmt: str, *values) -> int:
        struct.pack_into("<" + fmt, self.data, offset, *values)
        return offset + struct.calcsize("<" + fmt)

    def raw(self, offset: int, blob: bytes) -> int:
        self.data[offset:offset + len(blob)] = blob
        return offset + len(blob)

    def bytes(self) -> bytes:
        return bytes(self.data)


# --------------------------------------------------------------------------- SARC
def make_sarc(entries: list[tuple[str, bytes]], endian: str = "<") -> bytes:
    """SARC minimal (< 4 entrées) écrit indépendamment de hookymod.sarc."""
    mult = 0x65

    def hash_name(name: str) -> int:
        value = 0
        for byte in name.encode():
            value = (value * mult + byte) & 0xFFFFFFFF
        return value

    items = sorted(((hash_name(n), n, d) for n, d in entries))
    names = bytearray()
    name_offsets = []
    for _, n, _ in items:
        name_offsets.append(len(names) // 4)
        names += n.encode() + b"\0"
        names += b"\0" * (-len(names) % 4)

    data_off = 0x14 + 0x0C + 16 * len(items) + 8 + len(names)
    data_off = (data_off + 0x1FFF) // 0x2000 * 0x2000
    nodes = []
    pos = 0
    blobs = []
    for (h, n, d), name_off in zip(items, name_offsets):
        pos = (pos + 0x1FFF) // 0x2000 * 0x2000 if d[:4] in (b"FRES", b"BNTX") else pos
        start = pos
        pos += len(d)
        blobs.append((start, d))
        nodes.append((h, 0x01000000 | name_off, start, pos))

    total = data_off + pos
    out = bytearray()
    out += b"SARC"
    out += struct.pack(endian + "H", 0x14)
    out += b"\xff\xfe" if endian == "<" else b"\xfe\xff"
    out += struct.pack(endian + "II", total, data_off)
    out += struct.pack(endian + "HH", 0x100, 0)
    out += b"SFAT" + struct.pack(endian + "HHI", 0xC, len(nodes), mult)
    for node in nodes:
        out += struct.pack(endian + "IIII", *node)
    out += b"SFNT" + struct.pack(endian + "HH", 8, 0) + bytes(names)
    out += b"\0" * (data_off - len(out))
    for start, blob in blobs:
        out += b"\0" * (data_off + start - len(out))
        out += blob
    return bytes(out)


# --------------------------------------------------------------------------- zstd
def train_dict(samples: list[bytes], size: int = 1024) -> bytes:
    return zstd.train_dictionary(size, samples).as_bytes()


def compress(data: bytes, dictionary: bytes | None = None, level: int = 10) -> bytes:
    if dictionary:
        return zstd.ZstdCompressor(level=level,
                                   dict_data=zstd.ZstdCompressionDict(dictionary)).compress(data)
    return zstd.ZstdCompressor(level=level).compress(data)


# --------------------------------------------------------------------------- BNTX
def make_bntx(width: int, height: int, pixels: bytes, fmt: int = 0x0B01,
              tile_mode: int = 1, block_height_log2: int = 0,
              comp_sel=(2, 3, 4, 5)) -> bytes:
    """BNTX 4×N minimal (une texture, un mip, données déjà linéaires)."""
    str_block = 0x100
    infos = 0x200
    brti = 0x300
    info = 0x310
    ptrs = 0x400
    data_off = 0x800
    file_size = data_off + len(pixels)
    blob = Blob(file_size)

    # --- en-tête
    blob.raw(0x00, b"BNTX\0\0\0\0")
    blob.put(0x08, "I", 0x00040000)
    blob.raw(0x0C, b"\xff\xfe")
    blob.put(0x0E, "B", 0x0C)
    blob.put(0x0F, "B", 0x00)
    blob.put(0x10, "I", 0)          # fileNameAddr (corrigé après)
    blob.put(0x14, "H", 0)
    blob.put(0x16, "H", str_block)
    blob.put(0x18, "I", 0)          # relocAddr
    blob.put(0x1C, "I", file_size)

    # --- conteneur de textures
    blob.raw(0x20, b"NX  ")
    blob.put(0x24, "I", 1)
    blob.put(0x28, "Q", infos)
    blob.put(0x30, "Q", 0)
    blob.put(0x38, "Q", 0)
    blob.put(0x40, "Q", 0)
    blob.put(0x48, "Q", 0)
    blob.put(0x50, "I", 0)

    # --- bloc _STR
    blob.raw(str_block, b"_STR")
    blob.put(str_block + 4, "I", 0)
    blob.put(str_block + 8, "I", 0)
    str_pos = str_block + 16
    blob.put(str_pos, "I", 2)  # nombre de chaînes
    blob.put(str_pos + 4, "I", 0)
    name_pos = str_pos + 8
    blob.raw(name_pos, struct.pack("<H", 8) + b"test.bntx\0")
    tex_name_pos = name_pos + 2 + 8 + 1
    if tex_name_pos % 2:
        tex_name_pos += 1
    blob.raw(tex_name_pos, struct.pack("<H", 8) + b"TexName0\0")
    blob.put(0x10, "I", name_pos)

    # --- infos
    blob.put(infos, "Q", brti)
    blob.raw(brti, b"BRTI" + b"\0" * 12)

    blob.put(info + 0, "B", 0)         # flags
    blob.put(info + 1, "B", 2)         # dimension 2D
    blob.put(info + 2, "H", tile_mode)
    blob.put(info + 4, "H", 0)         # swizzle
    blob.put(info + 6, "H", 1)         # mips
    blob.put(info + 8, "H", 1)         # samples
    blob.put(info + 12, "I", fmt)
    blob.put(info + 16, "I", 0)
    blob.put(info + 20, "i", width)
    blob.put(info + 24, "i", height)
    blob.put(info + 28, "i", 1)
    blob.put(info + 32, "I", 1)
    blob.put(info + 36, "I", block_height_log2 & 7)
    blob.put(info + 40, "I", 0)
    blob.put(info + 64, "I", len(pixels))
    blob.put(info + 68, "I", 0x200)
    blob.raw(info + 72, bytes(comp_sel))
    blob.put(info + 76, "Q", tex_name_pos)
    blob.put(info + 84, "Q", 0)
    blob.put(info + 92, "Q", ptrs)
    for off in range(100, 140, 8):
        blob.put(info + off, "Q", 0)

    blob.put(ptrs, "Q", data_off)
    blob.raw(data_off, pixels)
    return blob.bytes()


def rgba8_pixels(width: int, height: int, seed: int = 0) -> bytes:
    out = bytearray()
    for y in range(height):
        for x in range(width):
            out += bytes(((x * 7 + seed) & 0xFF, (y * 11 + seed) & 0xFF,
                          ((x + y) * 3) & 0xFF, 255))
    return bytes(out)


# --------------------------------------------------------------------------- BFRES
def make_bfres() -> bytes:
    """BFRES Switch v10 minimal : 1 modèle, 1 forme, 1 mesh, 1 triangle."""
    buffer_base = 0x800
    blob = Blob(buffer_base + 0x40)

    # --- en-tête
    blob.raw(0x00, b"FRES")
    blob.put(0x04, "I", 0)
    blob.put(0x08, "I", 0x000A0002)  # version 10.0.2
    blob.raw(0x0C, b"\xff\xfe")
    blob.put(0x0E, "B", 0x0C)
    blob.put(0x0F, "B", 0x40)
    blob.put(0x10, "I", 0)
    blob.put(0x14, "H", 0)
    blob.put(0x16, "H", 0)
    blob.put(0x18, "I", 0)
    blob.put(0x1C, "I", len(blob.data))

    # --- chaînes (u16 longueur + NUL), zone 0x100
    cursor = 0x100

    def add_string(text: str) -> int:
        nonlocal cursor
        pos = cursor
        raw = text.encode() + b"\0"
        blob.raw(pos, struct.pack("<H", len(text)) + raw)
        cursor = pos + 2 + len(raw)
        if cursor % 2:
            cursor += 1
        return pos

    s_name = add_string("TestModel")
    s_shape = add_string("Shape0")
    s_pos = add_string("Position")

    # --- en-tête BFRES : offsets
    blob.put(0x20, "Q", 0)                      # nom racine
    blob.put(0x28, "Q", 0x1A8)                  # valeurs du dictionnaire (tableau d'offsets)
    blob.put(0x30, "Q", 0x180)                  # dictionnaire de modèles
    blob.put(0xA8, "Q", 0)                      # memory pool
    blob.put(0xB0, "Q", 0x480)                  # buffer info
    blob.put(0xD0, "Q", 0)                      # string table
    blob.put(0xD8, "I", 0)                      # string pool size
    blob.put(0xDC, "H", 1)                      # nombre de modèles

    def single_dict(dict_off: int, values_off: int, key: int, value_off_: int) -> None:
        blob.put(dict_off, "I", 0)
        blob.put(dict_off + 4, "I", 1)
        node = dict_off + 8 + 16
        blob.put(node, "I", 0xFFFFFFFF)
        blob.put(node + 4, "H", 0)
        blob.put(node + 6, "H", 0)
        blob.put(node + 8, "Q", key)
        blob.put(values_off, "Q", value_off_)

    single_dict(0x180, 0x1A8, s_name, 0x1B8)     # modèles

    # --- objet modèle (0x1B8)
    m = 0x1B8
    m = blob.put(m, "I", 0)          # flags
    m = blob.put(m, "Q", s_name)     # nom
    m = blob.put(m, "Q", 0)          # chemin
    m = blob.put(m, "Q", 0)          # squelette
    m = blob.put(m, "Q", 0x3C0)      # liste des FVTX
    m = blob.put(m, "Q", 0x268)      # valeurs du dictionnaire de formes
    m = blob.put(m, "Q", 0x240)      # dictionnaire de formes
    m = blob.put(m, "Q", 0)          # matériaux (valeurs)
    m = blob.put(m, "Q", 0)          # matériaux (dictionnaire)
    m = blob.put(m, "Q", 0)          # shader assign
    m = blob.put(m, "Q", 0)          # user data (valeurs)
    m = blob.put(m, "Q", 0)          # user data (dictionnaire)
    m = blob.put(m, "Q", 0)          # user pointer
    m = blob.put(m, "H", 1)          # nombre de FVTX
    m = blob.put(m, "H", 1)          # nombre de formes
    m = blob.put(m, "H", 0)          # nombre de matériaux
    m = blob.put(m, "H", 0)          # shader assign count
    m = blob.put(m, "H", 0)          # user data count
    m = blob.put(m, "H", 0)          # padding
    blob.put(m, "I", 0)

    single_dict(0x240, 0x268, s_shape, 0x280)    # formes

    # --- objet forme (0x280)
    s = 0x280
    s = blob.put(s, "I", 0)          # flags
    s = blob.put(s, "Q", s_shape)    # nom
    s = blob.put(s, "Q", 0x360)      # FVTX
    s = blob.put(s, "Q", 0x2E0)      # liste des mesh
    s = blob.put(s, "Q", 0)          # index des os
    s = blob.put(s, "Q", 0)          # key shapes (valeurs)
    s = blob.put(s, "Q", 0)          # key shapes (dictionnaire)
    s = blob.put(s, "Q", 0)          # bounding boxes
    s = blob.put(s, "Q", 0)          # rayons
    s = blob.put(s, "q", 0)          # user pointer
    s = blob.put(s, "H", 0)          # idx
    s = blob.put(s, "H", 0)          # matériau
    s = blob.put(s, "H", 0)          # os
    s = blob.put(s, "H", 0)          # index FVTX
    s = blob.put(s, "H", 0)          # nombre d'os
    s = blob.put(s, "B", 0)          # os par sommet
    s = blob.put(s, "B", 1)          # nombre de mesh
    s = blob.put(s, "B", 0)          # key shapes
    s = blob.put(s, "B", 0)          # attributs cibles
    blob.put(s, "H", 0)

    # --- tableau des mesh (0x2E0)
    blob.put(0x2E0, "Q", 0x400)

    single_dict(0x300, 0x328, s_pos, 0x340)      # attributs

    # --- attribut (0x340)
    blob.put(0x340, "Q", s_pos)      # nom
    blob.raw(0x348, b"\x05\x18")     # format 3x32 float (big endian)
    blob.put(0x34A, "H", 0)          # padding
    blob.put(0x34C, "H", 0)          # offset dans le sommet
    blob.put(0x34E, "H", 0)          # index du tampon

    # --- FVTX (0x360)
    v = 0x360
    v = blob.put(v, "I", 0)          # flags
    v = blob.put(v, "Q", 0x328)      # attributs (valeurs)
    v = blob.put(v, "Q", 0x300)      # attributs (dictionnaire)
    v = blob.put(v, "Q", 0)          # memory pool
    v = blob.put(v, "Q", 0)          # inconnu
    v = blob.put(v, "Q", 0)          # inconnu 2
    v = blob.put(v, "Q", 0x3E0)      # tailles
    v = blob.put(v, "Q", 0x3D0)      # strides
    v = blob.put(v, "Q", 0)          # padding
    v = blob.put(v, "i", 8)          # offset du tampon (relatif)
    v = blob.put(v, "B", 1)          # nombre d'attributs
    v = blob.put(v, "B", 1)          # nombre de tampons
    v = blob.put(v, "H", 0)          # idx
    v = blob.put(v, "I", 3)          # nombre de sommets
    v = blob.put(v, "H", 0)          # os par sommet
    blob.put(v, "H", 8)              # alignement GPU

    # --- tableau des FVTX (0x3C0)
    blob.put(0x3C0, "Q", 0x360)
    # strides (0x3D0) : 16 octets
    blob.put(0x3D0, "I", 12)
    blob.put(0x3D4, "I", 0)
    blob.put(0x3D8, "Q", 0)
    # tailles (0x3E0) : 16 octets
    blob.put(0x3E0, "I", 36)
    blob.put(0x3E4, "I", 0)
    blob.put(0x3E8, "Q", 0)
    # sous-maillages (0x3F0)
    blob.put(0x3F0, "I", 0)
    blob.put(0x3F4, "I", 3)

    # --- mesh (0x400)
    mm = 0x400
    mm = blob.put(mm, "Q", 0x3F0)    # tableau des sous-maillages
    mm = blob.put(mm, "Q", 0)        # memory pool
    mm = blob.put(mm, "Q", 0)        # tampon
    mm = blob.put(mm, "I", 6)        # taille des index
    mm = blob.put(mm, "I", 0)        # drapeaux
    mm += 40                         # BufferSize (padding)
    mm = blob.put(mm, "I", 0)        # offset du tampon d'index
    mm = blob.put(mm, "I", 3)        # TRIANGLES
    mm = blob.put(mm, "I", 1)        # index u16
    mm = blob.put(mm, "I", 3)        # nombre d'index
    mm = blob.put(mm, "I", 0)        # premier sommet
    mm = blob.put(mm, "H", 1)        # nombre de sous-maillages
    blob.put(mm, "H", 0)

    # --- BufferInfo (0x480)
    blob.put(0x480, "I", 34)
    blob.put(0x484, "I", 0)
    blob.put(0x488, "Q", buffer_base)
    blob.raw(0x490, b"\0" * 16)

    # --- données : index puis sommets
    blob.raw(buffer_base, struct.pack("<3H", 0, 1, 2))
    blob.raw(buffer_base + 8, struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    return blob.bytes()


def make_restbl(entries: dict[str, int], version: int = 3,
                named: bytes = b"", named_count: int = 0) -> bytes:
    """Table RESTBL synthétique (``{chemin: taille réservée}``)."""
    import struct
    import zlib

    body = b"".join(
        struct.pack("<II", zlib.crc32(path.encode()) & 0xFFFFFFFF, size)
        for path, size in sorted(entries.items(), key=lambda kv: zlib.crc32(kv[0].encode())))
    header = b"RESTBL" + struct.pack("<IIII", version, 64, len(entries), named_count)
    return header + body + named
