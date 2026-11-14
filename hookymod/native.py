"""Passerelle facultative vers le cœur Rust, sans dépendance Python externe.

La bibliothèque est chargée uniquement depuis le dossier du projet. Elle ne
contient ni données de jeu ni API Python : une ABI C versionnée permet de
l'utiliser avec toutes les versions de Python, Windows compris. Chaque fonction
renvoie ``None`` si le moteur n'est pas disponible ou refuse l'entrée ; les
appelants gardent alors leur implémentation Python d'origine.

``HOOKYMOD_DISABLE_RUST=1`` permet de comparer le repli sans désinstaller le
moteur. Après compilation, redémarrer l'application pour charger la bibliothèque.
"""

from __future__ import annotations

import ctypes
import itertools
import os
import struct
import sys
from array import array
from pathlib import Path

_ABI = 5
_U32_MAX = (1 << 32) - 1
_U64_MAX = (1 << 64) - 1
_BFRES_SHAPE_INPUT_LIMIT = 1024 * 1024 * 1024
_BFRES_SHAPE_METADATA_LIMIT = 64 * 1024 * 1024
_BFRES_SHAPE_COUNT_LIMIT = 250_000
_SARC_INPUT_LIMIT = 1024 * 1024 * 1024
_SARC_METADATA_LIMIT = 128 * 1024 * 1024
_SARC_BUILD_INPUT_LIMIT = 512 * 1024 * 1024
_SARC_BUILD_OUTPUT_LIMIT = 512 * 1024 * 1024
_RESTBL_INPUT_LIMIT = 1024 * 1024 * 1024
_RESTBL_METADATA_LIMIT = 64 * 1024 * 1024
_RESTBL_OUTPUT_LIMIT = 512 * 1024 * 1024
_RESTBL_ENTRY_LIMIT = 4_000_000
_BFRES_BUFFER_LIMIT = 64 * 1024 * 1024
_ZSTD_INPUT_LIMIT = 1024 * 1024 * 1024
_ZSTD_OUTPUT_LIMIT = 512 * 1024 * 1024
_ZSTD_DICTIONARY_LIMIT = 16 * 1024 * 1024
_LIB_DIR = Path(__file__).resolve().parent / "_native_lib"


class _SarcBuildInput(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("name_len", ctypes.c_size_t),
                ("data", ctypes.c_char_p), ("data_len", ctypes.c_size_t),
                ("alignment", ctypes.c_uint32)]


if sys.platform == "win32":
    _LIB_NAME = "hookymod_core.dll"
elif sys.platform == "darwin":
    _LIB_NAME = "libhookymod_core.dylib"
elif sys.platform.startswith("linux"):
    _LIB_NAME = "libhookymod_core.so"
else:
    _LIB_NAME = ""

_lib = None
_reason = ""


def _load():
    global _reason
    if os.environ.get("HOOKYMOD_DISABLE_RUST", "").lower() in {"1", "true", "yes"}:
        _reason = "Désactivé par HOOKYMOD_DISABLE_RUST."
        return None
    if not _LIB_NAME:
        _reason = "Plateforme non prise en charge par le chargeur Rust."
        return None
    path = _LIB_DIR / _LIB_NAME
    if not path.is_file():
        _reason = "Moteur Rust non compilé ; lancer python tools/build_rust.py."
        return None
    try:
        library = ctypes.CDLL(str(path))
        library.hookymod_core_abi.argtypes = []
        library.hookymod_core_abi.restype = ctypes.c_uint32
        if library.hookymod_core_abi() != _ABI:
            _reason = "Version du moteur Rust incompatible ; le recompiler."
            return None
        u8p = ctypes.c_void_p
        size = ctypes.c_size_t
        u32 = ctypes.c_uint32
        library.hookymod_core_zstd_decompress.argtypes = [
            u8p, size, u8p, size, u32, ctypes.c_uint8, size,
            ctypes.POINTER(u8p), ctypes.POINTER(size), ctypes.POINTER(u8p),
        ]
        library.hookymod_core_zstd_decompress.restype = ctypes.c_int32
        library.hookymod_core_zstd_free.argtypes = [u8p]
        library.hookymod_core_zstd_free.restype = None
        library.hookymod_core_buffer_free.argtypes = [u8p]
        library.hookymod_core_buffer_free.restype = None
        library.hookymod_core_sarc_parse.argtypes = [
            u8p, size, ctypes.POINTER(u8p), ctypes.POINTER(size), ctypes.POINTER(u8p),
        ]
        library.hookymod_core_sarc_parse.restype = ctypes.c_int32
        library.hookymod_core_sarc_build.argtypes = [
            u8p, size, ctypes.c_uint8, ctypes.c_uint16, u32, ctypes.c_uint8,
            ctypes.POINTER(u8p), ctypes.POINTER(size), ctypes.POINTER(u8p),
        ]
        library.hookymod_core_sarc_build.restype = ctypes.c_int32
        library.hookymod_core_restbl_parse.argtypes = [
            u8p, size, ctypes.POINTER(u8p), ctypes.POINTER(size), ctypes.POINTER(u8p),
        ]
        library.hookymod_core_restbl_parse.restype = ctypes.c_int32
        library.hookymod_core_restbl_build.argtypes = [
            u8p, size, u8p, size, u32, u32, u32,
            ctypes.POINTER(u8p), ctypes.POINTER(size), ctypes.POINTER(u8p),
        ]
        library.hookymod_core_restbl_build.restype = ctypes.c_int32
        library.hookymod_core_bfres_find_shapes.argtypes = [
            u8p, size, ctypes.c_uint64,
            ctypes.POINTER(u8p), ctypes.POINTER(size), ctypes.POINTER(u8p),
        ]
        library.hookymod_core_bfres_find_shapes.restype = ctypes.c_int32
        library.hookymod_core_deswizzle.argtypes = [
            u8p, size, u8p, size, u32, u32, u32, u32, u32, u32,
            ctypes.c_int32, ctypes.c_uint8, ctypes.c_uint8,
        ]
        library.hookymod_core_deswizzle.restype = ctypes.c_int32
        library.hookymod_core_swizzle.argtypes = library.hookymod_core_deswizzle.argtypes
        library.hookymod_core_swizzle.restype = ctypes.c_int32
        library.hookymod_core_decode_bc.argtypes = [
            u8p, size, u8p, size, u32, u32, u32, ctypes.c_uint8,
        ]
        library.hookymod_core_decode_bc.restype = ctypes.c_int32
        library.hookymod_core_encode_pixels.argtypes = [
            u8p, size, u8p, size, u32, u32, u32,
        ]
        library.hookymod_core_encode_pixels.restype = ctypes.c_int32
        library.hookymod_core_downsample_mip.argtypes = [
            u8p, size, u8p, size, u32, u32,
        ]
        library.hookymod_core_downsample_mip.restype = ctypes.c_int32
        library.hookymod_core_segment_indices.argtypes = [
            u8p, size, u8p, size, u8p, size, u32,
        ]
        library.hookymod_core_segment_indices.restype = ctypes.c_int32
        library.hookymod_core_decode_positions.argtypes = [
            u8p, size, u8p, size, u32, u32, u32, u32, ctypes.c_uint8,
        ]
        library.hookymod_core_decode_positions.restype = ctypes.c_int32
        library.hookymod_core_filter_triangles.argtypes = [
            u8p, size, u8p, size, u8p, size, ctypes.c_double,
        ]
        library.hookymod_core_filter_triangles.restype = ctypes.c_int32
        library.hookymod_core_rasterize.argtypes = [
            u8p, size, u8p, size, u32, u32, u32, u32,
        ]
        library.hookymod_core_rasterize.restype = ctypes.c_int32
        return library
    except AttributeError:
        _reason = "Version du moteur Rust incomplète ou incompatible ; le recompiler."
        return None
    except (OSError, ValueError) as exc:
        _reason = f"Moteur Rust impossible à charger : {exc}"
        return None


_lib = _load()


def available() -> bool:
    """Le binaire de la bonne version a été chargé dans ce processus."""
    return _lib is not None


def status() -> dict:
    return {"available": available(), "abi": _ABI, "reason": _reason}


def _valid_u32(*values) -> bool:
    return all(isinstance(x, int) and 0 <= x <= _U32_MAX for x in values)


def _source(data, *, owned: bool = False):
    """Pointeur et propriétaire conservé jusqu'au retour de la fonction native.

    ctypes libère le GIL : un tampon mutable fourni par l'extérieur pourrait
    être modifié simultanément. On le copie donc, sauf s'il vient d'être créé
    dans cette fonction et n'est partagé avec aucun autre fil.
    """
    if owned and isinstance(data, bytearray) and data:
        owner = (ctypes.c_ubyte * len(data)).from_buffer(data)
    else:
        owner = ctypes.c_char_p(bytes(data))
    return ctypes.cast(owner, ctypes.c_void_p), owner


def _destination(size: int):
    buffer = bytearray(size)
    if size:
        owner = (ctypes.c_ubyte * size).from_buffer(buffer)
        return buffer, ctypes.cast(owner, ctypes.c_void_p), owner
    return buffer, ctypes.c_void_p(), None


def zstd_decompress(data: bytes, dictionary: bytes | None = None, dictionary_id: int = 0,
                    *, force_dictionary: bool = False,
                    max_output: int = _ZSTD_OUTPUT_LIMIT) -> bytes | None:
    """Décode avec Rust ou renvoie ``None`` pour laisser le repli traiter l'entrée.

    La mémoire de sortie appartient à Rust et est libérée même si sa copie
    vers Python échoue. Aucun état de décodeur n'est partagé entre les fils.
    Les tampons mutables sont copiés avant que ctypes ne libère le GIL.
    """
    library = _lib
    if (library is None or type(data) not in (bytes, bytearray)
            or (dictionary is not None and type(dictionary) not in (bytes, bytearray))
            or not _valid_u32(dictionary_id)
            or type(max_output) is not int or not 0 <= max_output <= _ZSTD_OUTPUT_LIMIT
            or len(data) > _ZSTD_INPUT_LIMIT
            or (dictionary is not None and len(dictionary) > _ZSTD_DICTIONARY_LIMIT)
            or (force_dictionary and not dictionary)):
        return None
    source, source_owner = _source(data)
    if dictionary:
        dict_ptr, dict_owner = _source(dictionary)
        dict_len = len(dictionary)
    else:
        dict_ptr, dict_owner, dict_len = ctypes.c_void_p(), None, 0
    output_ptr = ctypes.c_void_p()
    output_len = ctypes.c_size_t()
    handle = ctypes.c_void_p()
    result = library.hookymod_core_zstd_decompress(
        source, len(data), dict_ptr, dict_len, dictionary_id, bool(force_dictionary),
        max_output, ctypes.byref(output_ptr), ctypes.byref(output_len), ctypes.byref(handle))
    del source_owner, dict_owner
    try:
        if (result != 0 or not handle.value or output_len.value > max_output
                or (output_len.value and not output_ptr.value)):
            return None
        return ctypes.string_at(output_ptr, output_len.value)
    finally:
        if handle.value:
            library.hookymod_core_zstd_free(handle)


def _owned_result(library, function, arguments: tuple, limit: int) -> bytes | None:
    """Appel commun : le handle Rust est toujours libéré après copie."""
    output_ptr = ctypes.c_void_p()
    output_len = ctypes.c_size_t()
    handle = ctypes.c_void_p()
    result = function(*arguments, ctypes.byref(output_ptr),
                      ctypes.byref(output_len), ctypes.byref(handle))
    try:
        if (result != 0 or not handle.value or output_len.value > limit
                or (output_len.value and not output_ptr.value)):
            return None
        return ctypes.string_at(output_ptr, output_len.value)
    finally:
        if handle.value:
            library.hookymod_core_buffer_free(handle)


def bfres_find_shapes(data: bytes, base: int):
    """Métadonnées des formes BFRES 10.x, ou ``None`` pour le repli Python.

    Les noms sont des offsets dans l'entrée immuable : leurs bornes et celles
    des autres champs sont revérifiées avant de construire des objets Python.
    """
    library = _lib
    if (library is None or type(data) is not bytes or len(data) > _BFRES_SHAPE_INPUT_LIMIT
            or type(base) is not int or not 0 <= base <= _U64_MAX):
        return None
    source, owner = _source(data)
    metadata = _owned_result(library, library.hookymod_core_bfres_find_shapes,
                             (source, len(data), base), _BFRES_SHAPE_METADATA_LIMIT)
    del owner
    if metadata is None:
        return None
    try:
        header = struct.Struct("<4sI")
        shape_record = struct.Struct("<QQIQQQII")
        attribute_record = struct.Struct("<IIIII")
        magic, count = header.unpack_from(metadata)
        if (magic != b"FSP1" or count > _BFRES_SHAPE_COUNT_LIMIT
                or count > (len(metadata) - header.size) // (shape_record.size + 2 * attribute_record.size)):
            return None
        shapes = []
        pos = header.size
        for _ in range(count):
            offset, stride, relative, vertex, vertices, data_end, flags, attr_count = \
                shape_record.unpack_from(metadata, pos)
            pos += shape_record.size
            declared = flags & 0xff
            if (offset % 4 or offset + 0x50 > len(data) or data[offset:offset + 4] != b"FVTX"
                    or not relative or vertex != base + relative or vertex >= len(data)
                    or stride < 4 or stride % 4 or not 2 <= attr_count <= 32
                    or (declared and attr_count != declared)
                    or (shapes and vertex <= shapes[-1][3])):
                return None
            attributes = []
            for _ in range(attr_count):
                start, length, fmt, attr_offset, size = attribute_record.unpack_from(metadata, pos)
                pos += attribute_record.size
                end = start + length
                if (start < 2 or not 1 <= length < 64 or end > len(data)
                        or any(byte < 0x20 or byte > 0x7e for byte in data[start:end])
                        or not size):
                    return None
                if attributes and (attr_offset <= attributes[-1][2]
                                   or attributes[-1][3] != attr_offset - attributes[-1][2]):
                    return None
                attributes.append((data[start:end].decode("ascii"), fmt, attr_offset, size))
            calculated = max(attr_offset + size for _, _, attr_offset, size in attributes)
            if stride != (calculated + 3) // 4 * 4:
                return None
            shapes.append((offset, stride, relative, vertex, vertices, data_end, flags, attributes))
        if pos != len(metadata):
            return None
        for index, shape in enumerate(shapes):
            next_vertex = shapes[index + 1][3] if index + 1 < len(shapes) else len(data)
            if shape[4] != (next_vertex - shape[3]) // shape[1]:
                return None
        return shapes
    except (struct.error, UnicodeDecodeError, OverflowError, ValueError):
        return None


def sarc_parse(data: bytes):
    """Métadonnées d'un SARC, ou ``None`` si Python doit prendre le relais.

    Les contenus restent dans l'entrée immuable : Rust ne renvoie que des
    offsets, que l'on vérifie à nouveau avant de créer les objets Python.
    """
    library = _lib
    if library is None or type(data) is not bytes or len(data) > _SARC_INPUT_LIMIT:
        return None
    source, owner = _source(data)
    metadata = _owned_result(library, library.hookymod_core_sarc_parse,
                            (source, len(data)), _SARC_METADATA_LIMIT)
    del owner
    if metadata is None:
        return None
    try:
        header = struct.Struct("<4sBHIII")
        entry = struct.Struct("<IIIQQ")
        magic, endian, version, multiplier, count, warning_count = header.unpack_from(metadata)
        if magic != b"HSP1" or endian not in (0, 1) or count > 65535 or warning_count > 131070:
            return None
        pos = header.size
        entries = []
        for _ in range(count):
            name_len, alignment, node_hash, start, size = entry.unpack_from(metadata, pos)
            pos += entry.size
            name_end = pos + name_len
            if name_end > len(metadata) or start + size > len(data):
                return None
            name = metadata[pos:name_end].decode("utf-8")
            entries.append((name, start, size, alignment, node_hash))
            pos = name_end
        warnings = []
        for _ in range(warning_count):
            (length,) = struct.unpack_from("<I", metadata, pos)
            pos += 4
            end = pos + length
            if end > len(metadata):
                return None
            warnings.append(metadata[pos:end].decode("utf-8"))
            pos = end
        if pos != len(metadata):
            return None
        return (">" if endian else "<", version, multiplier, entries, warnings)
    except (struct.error, UnicodeDecodeError, OverflowError):
        return None


def sarc_build(entries, endian, multiplier, version, *, verify=True) -> bytes | None:
    """Reconstruit les entrées simples, sinon conserve le chemin Python.

    Les valeurs et les types exotiques (notamment les sous-classes et chaînes
    contenant des surrogates) sont laissés au constructeur historique. Les noms
    encodés et contenus sont des octets immuables gardés vivants jusqu'au
    retour de l'appel, même si ctypes libère le GIL.
    """
    from .sarc import SarcEntry

    library = _lib
    if (library is None or type(entries) is not list or not entries or len(entries) > 65535
            or type(endian) is not str or type(multiplier) is not int
            or type(version) is not int or type(verify) is not bool
            or not _valid_u32(multiplier) or not 0 <= version <= 0xffff):
        return None
    # Capturer chaque référence une seule fois : même si une autre tâche
    # remplace une entrée, les pointeurs transmis restent valides et bornés.
    original_entries = tuple(entries)
    if not original_entries or len(original_entries) > 65535:
        return None
    items = []
    total = 0
    name_total = 0
    try:
        for item in original_entries:
            if type(item) is not SarcEntry:
                return None
            original_name, data, alignment = item.name, item.data, item.alignment
            if (type(original_name) is not str or type(data) is not bytes
                    or type(alignment) is not int or not _valid_u32(alignment)):
                return None
            name = original_name.encode("utf-8")
            name_total += len(name) + 4
            total += 16 + len(name) + len(data)
            if total > _SARC_BUILD_INPUT_LIMIT or name_total > 64 * 1024 * 1024:
                return None
            items.append((name, alignment, data))
    except UnicodeEncodeError:
        return None
    descriptors = (_SarcBuildInput * len(items))()
    for index, (name, alignment, data) in enumerate(items):
        descriptors[index] = _SarcBuildInput(name, len(name), data, len(data), alignment)
    result = _owned_result(library, library.hookymod_core_sarc_build,
                          (descriptors, len(items), endian == ">", version, multiplier, verify),
                          _SARC_BUILD_OUTPUT_LIMIT)
    # Les pointeurs ne doivent pas survivre aux propriétaires des octets.
    del descriptors, items
    return result


def restbl_parse(data: bytes):
    """Lit la table, en conservant l'ordre et la dernière valeur des doublons.

    Rust valide l'en-tête, repère les clés répétées et prépare les paires ;
    la passerelle les convertit en dictionnaire Python sans modifier la queue.
    """
    library = _lib
    if library is None or type(data) is not bytes or len(data) > _RESTBL_INPUT_LIMIT:
        return None
    source, owner = _source(data)
    metadata = _owned_result(library, library.hookymod_core_restbl_parse,
                             (source, len(data)), _RESTBL_METADATA_LIMIT)
    del owner
    if metadata is None:
        return None
    try:
        header = struct.Struct("<4sIIIIIQB")
        magic, version, name_length, named, original, unique, end, mode = header.unpack_from(metadata)
        if (magic != b"RSP1" or mode not in (0, 1) or original > _RESTBL_ENTRY_LIMIT
                or unique > original or end != 22 + original * 8 or end > len(data)):
            return None
        if mode == 0:
            if unique != original or len(metadata) != header.size:
                return None
            pairs = memoryview(data)[22:end]
        else:
            if len(metadata) != header.size + unique * 8:
                return None
            pairs = memoryview(metadata)[header.size:]
        sizes = dict(struct.iter_unpack("<II", pairs))
        if len(sizes) != unique:
            return None
        return version, name_length, sizes, bytes(data[end:]), named
    except (struct.error, OverflowError, ValueError):
        return None


def restbl_build(sizes, version, name_length, named_count, tail) -> bytes | None:
    """Trie et écrit en Rust les tables aux types Python ordinaires.

    La liste des paires et le tableau de conversion sont privés, de sorte que
    ctypes puisse libérer le GIL sans observer de mutation simultanée.
    """
    library = _lib
    if (library is None or type(sizes) is not dict or type(tail) is not bytes
            or not all(type(value) is int for value in (version, name_length, named_count))
            or not _valid_u32(version, name_length, named_count)
            or len(sizes) > _RESTBL_ENTRY_LIMIT
            or 22 + len(sizes) * 8 + len(tail) > _RESTBL_OUTPUT_LIMIT):
        return None
    items = list(sizes.items())
    if (len(items) > _RESTBL_ENTRY_LIMIT
            or 22 + len(items) * 8 + len(tail) > _RESTBL_OUTPUT_LIMIT
            or any(type(key) is not int or type(size) is not int
                   or not 0 <= key <= _U32_MAX or not 0 <= size <= _U32_MAX
                   for key, size in items)):
        return None
    pairs = array("I", itertools.chain.from_iterable(items))
    if pairs.itemsize != 4:
        return None
    if sys.byteorder != "little":
        pairs.byteswap()
    pair_len = len(pairs) * pairs.itemsize
    if pair_len:
        pair_owner = (ctypes.c_ubyte * pair_len).from_buffer(pairs)
        pair_ptr = ctypes.cast(pair_owner, ctypes.c_void_p)
    else:
        pair_ptr, pair_owner = ctypes.c_void_p(), None
    tail_ptr, tail_owner = _source(tail)
    result = _owned_result(library, library.hookymod_core_restbl_build,
                           (pair_ptr, pair_len, tail_ptr, len(tail),
                            version, name_length, named_count), _RESTBL_OUTPUT_LIMIT)
    del pair_owner, tail_owner
    return result


def deswizzle(width: int, height: int, blk_w: int, blk_h: int, bpp: int,
              tile_mode: int, block_height_log2: int, data: bytes,
              round_pitch: bool = True, orin: bool = False) -> bytes | None:
    if (_lib is None or not _valid_u32(width, height, blk_w, blk_h, bpp, tile_mode)
            or not blk_w or not blk_h or not bpp
            or not isinstance(block_height_log2, int)):
        return None
    wb = (width + blk_w - 1) // blk_w
    hb = (height + blk_h - 1) // blk_h
    length = wb * hb * bpp
    if length > sys.maxsize:
        return None
    if not length:
        return b""
    source, source_owner = _source(data)
    out, destination, destination_owner = _destination(length)
    result = _lib.hookymod_core_deswizzle(
        source, len(data), destination, length, width, height, blk_w, blk_h, bpp,
        tile_mode, max(0, min(5, block_height_log2)), bool(round_pitch), bool(orin))
    # Les propriétaires doivent vivre au-delà de l'appel FFI.
    del source_owner, destination_owner
    return bytes(out) if result == 0 else None


def swizzle(width: int, height: int, blk_w: int, blk_h: int, bpp: int,
            tile_mode: int, block_height_log2: int, data: bytes,
            round_pitch: bool = True, orin: bool = False) -> bytes | None:
    if (_lib is None or not _valid_u32(width, height, blk_w, blk_h, bpp, tile_mode)
            or not blk_w or not blk_h or not bpp
            or not isinstance(block_height_log2, int)):
        return None
    wb = (width + blk_w - 1) // blk_w
    hb = (height + blk_h - 1) // blk_h
    row = wb * bpp
    needed = row * hb
    if len(data) < needed:
        return None                  # garder le comportement du repli tronqué
    if not needed:
        return b""
    height_log2 = max(0, min(5, block_height_log2))
    if tile_mode == 1:
        pitch = ((row + 31) // 32) * 32 if round_pitch else row
        length = pitch * hb
    else:
        gob_rows = 8 * (1 << height_log2)
        length = ((row + 63) // 64) * 64 * ((hb + gob_rows - 1) // gob_rows) * gob_rows
    if length > sys.maxsize:
        return None
    source, source_owner = _source(data)
    out, destination, destination_owner = _destination(length)
    result = _lib.hookymod_core_swizzle(
        source, len(data), destination, length, width, height, blk_w, blk_h, bpp,
        tile_mode, height_log2, bool(round_pitch), bool(orin))
    del source_owner, destination_owner
    return bytes(out) if result == 0 else None


def decode_bc(name: str, data: bytes, width: int, height: int) -> bytes | None:
    if _lib is None or not _valid_u32(width, height):
        return None
    fmt = next((n for n in range(1, 6) if name.startswith(f"BC{n}")), None)
    if fmt is None:
        return None
    length = width * height * 4
    if length > sys.maxsize:
        return None
    if not length:
        return b""
    source, source_owner = _source(data)
    out, destination, destination_owner = _destination(length)
    result = _lib.hookymod_core_decode_bc(source, len(data), destination, length,
                                          width, height, fmt, "SNORM" in name)
    del source_owner, destination_owner
    return bytes(out) if result == 0 else None


_ENCODE_FORMATS = {
    "BC1": 1, "BC3": 3, "BC4": 4, "BC5": 5,
    "R8": 10, "RG8": 11, "RGBA8": 12, "BGRA8": 13,
    "RGB565": 14, "BGR565": 15, "RGBA4": 16, "BGRA4": 17,
    "RGB5A1": 18, "BGR5A1": 19,
}


def encode_pixels(kind: str, rgba: bytes, width: int, height: int) -> bytes | None:
    """Encode les formats déjà pris en charge par l'écriture BNTX Python."""
    if _lib is None or not _valid_u32(width, height) or kind not in _ENCODE_FORMATS:
        return None
    if len(rgba) < width * height * 4:
        return None
    if kind in ("BC1", "BC3", "BC4", "BC5"):
        stride = 8 if kind in ("BC1", "BC4") else 16
        length = ((width + 3) // 4) * ((height + 3) // 4) * stride
    else:
        stride = {"R8": 1, "RG8": 2, "RGBA8": 4, "BGRA8": 4}.get(kind, 2)
        length = width * height * stride
    if length > sys.maxsize:
        return None
    if not length:
        return b""
    source, source_owner = _source(rgba)
    out, destination, destination_owner = _destination(length)
    result = _lib.hookymod_core_encode_pixels(
        source, len(rgba), destination, length, width, height, _ENCODE_FORMATS[kind])
    del source_owner, destination_owner
    return bytes(out) if result == 0 else None


def downsample_mip(rgba: bytes, width: int, height: int) -> bytes | None:
    if (_lib is None or not _valid_u32(width, height) or not width or not height
            or len(rgba) < width * height * 4):
        return None
    length = max(1, width // 2) * max(1, height // 2) * 4
    if length > sys.maxsize:
        return None
    source, source_owner = _source(rgba)
    out, destination, destination_owner = _destination(length)
    result = _lib.hookymod_core_downsample_mip(
        source, len(rgba), destination, length, width, height)
    del source_owner, destination_owner
    return bytes(out) if result == 0 else None


def segment_indices(words, bounds, minimum: int = 4) -> list | None:
    """Découpe les indices u16 sans changer l'ordre ni les critères du lecteur.

    Une copie privée du tampon est utilisée pour que ctypes puisse libérer le
    GIL sans observer de mutation simultanée. Les types exotiques et les gros
    tampons restent au lecteur Python.
    """
    if (_lib is None or type(words) is not array or words.typecode != "H"
            or words.itemsize != 2 or sys.byteorder != "little"
            or type(bounds) is not list or type(minimum) is not int
            or not _valid_u32(len(words), len(bounds), minimum)
            or any(type(b) is not int or not 0 <= b <= _U32_MAX for b in bounds)):
        return None
    max_runs = len(words) // 3
    output_size = 4 + max_runs * 12
    if len(words) * 2 + len(bounds) * 4 + output_size > _BFRES_BUFFER_LIMIT:
        return None
    raw = words.tobytes()
    bound_data = struct.pack(f"<{len(bounds)}I", *bounds)
    word_ptr, word_owner = _source(raw)
    bound_ptr, bound_owner = _source(bound_data)
    output, output_ptr, output_owner = _destination(output_size)
    result = _lib.hookymod_core_segment_indices(
        word_ptr, len(raw), bound_ptr, len(bound_data), output_ptr, output_size, minimum)
    del word_owner, bound_owner, output_owner
    if result != 0:
        return None
    count = struct.unpack_from("<I", output)[0]
    if count > max_runs:
        return None
    runs = []
    view = memoryview(raw)
    for shape, start, triangles in struct.iter_unpack("<III", memoryview(output)[4:4 + count * 12]):
        end = start + triangles * 3
        if shape >= len(bounds) or end > len(words):
            return None
        runs.append((shape, start, list(struct.iter_unpack("<3H", view[start * 2:end * 2]))))
    return runs


def decode_positions(data: bytes, start: int, count: int, stride: int,
                     offset: int, fmt: int) -> list | None:
    """Lit les positions BFRES f32/f16, en gardant les arrêts de sécurité."""
    if (_lib is None or type(data) is not bytes
            or not all(type(v) is int for v in (start, count, stride, offset, fmt))
            or not _valid_u32(start, count, stride, offset, fmt) or not stride):
        return None
    is_f32 = fmt == 0x1805
    size = 12 if is_f32 else 6
    fast = (is_f32 and stride % 4 == 0 and offset % 4 == 0
            and stride >= 12 and count >= 1 and start + stride <= len(data))
    if fast:
        capacity = min(count, (len(data) - start) // stride)
    else:
        first_end = start + offset + size
        capacity = min(count, 1 + (len(data) - first_end) // stride) if first_end <= len(data) else 0
    output_size = 4 + capacity * 24
    if output_size > _BFRES_BUFFER_LIMIT:
        return None
    source, source_owner = _source(data)
    output, target, target_owner = _destination(output_size)
    result = _lib.hookymod_core_decode_positions(
        source, len(data), target, output_size, start, count, stride, offset, is_f32)
    del source_owner, target_owner
    if result != 0:
        return None
    written = struct.unpack_from("<I", output)[0]
    if written > capacity:
        return None
    return list(struct.iter_unpack("<3d", memoryview(output)[4:4 + written * 24]))


def filter_triangles(positions, triangles, limit2: float) -> list | None:
    """Même filtre BFRES que Python, avec des f64 et indices i64 vérifiés.

    Pour les petits lots, types inhabituels ou données trop volumineuses on
    conserve le repli. Cela évite de convertir des entiers Python arbitraires
    en flottants et de modifier silencieusement la géométrie.
    """
    if _lib is None or len(triangles) < 128 or type(limit2) is not float:
        return None
    if (len(positions) + len(triangles)) * 24 + len(triangles) > _BFRES_BUFFER_LIMIT:
        return None
    points = bytearray(len(positions) * 24)
    faces = bytearray(len(triangles) * 24)
    pack_point = struct.Struct("<3d").pack_into
    pack_face = struct.Struct("<3q").pack_into
    try:
        for i, p in enumerate(positions):
            if type(p) not in (list, tuple) or len(p) < 3:
                return None
            x, y, z = p[0], p[1], p[2]
            if type(x) is not float or type(y) is not float or type(z) is not float:
                return None
            pack_point(points, i * 24, x, y, z)
        for i, face in enumerate(triangles):
            if type(face) not in (list, tuple) or len(face) != 3:
                return None
            a, b, c = face
            if type(a) is not int or type(b) is not int or type(c) is not int:
                return None
            pack_face(faces, i * 24, a, b, c)
    except (IndexError, OverflowError, TypeError, ValueError, struct.error):
        return None
    point_ptr, point_owner = _source(points, owned=True)
    face_ptr, face_owner = _source(faces, owned=True)
    mask, mask_ptr, mask_owner = _destination(len(triangles))
    result = _lib.hookymod_core_filter_triangles(
        point_ptr, len(points), face_ptr, len(faces), mask_ptr, len(mask), limit2)
    del point_owner, face_owner, mask_owner
    if result != 0:
        return None
    return [(face[0], face[1], face[2]) for face, keep in zip(triangles, mask) if keep]


def rasterize(width: int, height: int, supersample: int, triangles: bytearray,
              background: tuple[int, int, int, int], *, _owned: bool = False) -> bytes | None:
    """Dessine des triangles projetés (9 f64 + 4 octets RGBA par triangle).

    ``_owned`` n'est utilisé que lorsque l'appelant vient de créer le tampon et
    garantit qu'aucun autre fil ne peut le modifier pendant l'appel natif.
    """
    if (_lib is None or not _valid_u32(width, height, supersample)
            or not 1 <= supersample <= 4 or len(triangles) % 76):
        return None
    length = width * height * 4
    if length > sys.maxsize:
        return None
    if not length:
        return b""
    bg = bytes(background)  # même validation que le rastériseur Python
    if len(bg) != 4:
        return None
    source, source_owner = _source(triangles, owned=_owned)
    out, destination, destination_owner = _destination(length)
    result = _lib.hookymod_core_rasterize(
        source, len(triangles), destination, length, width, height, supersample,
        int.from_bytes(bg, "little"))
    del source_owner, destination_owner
    return bytes(out) if result == 0 else None
