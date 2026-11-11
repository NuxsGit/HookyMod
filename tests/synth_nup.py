"""Générateur de paquets ``.nup`` synthétiques (aucun fichier du jeu).

La disposition suit celle décrite dans :mod:`hookymod.nup` : en-tête ``NUP\\x01``,
TOC ``TOC\\x01``, tables de titres / dossiers / fichiers / enfants / noms, puis
les données alignées sur 16 octets.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Any

import synth

ALIGN = 16


def _align(value: int, multiple: int = ALIGN) -> int:
    return (value + multiple - 1) // multiple * multiple


class _Builder:
    def __init__(self) -> None:
        self.names = bytearray()
        self.name_offsets: dict[str, tuple[int, int]] = {}

    def name(self, text: str) -> tuple[int, int]:
        if text not in self.name_offsets:
            offset = len(self.names)
            raw = text.encode("utf-8")
            self.names += raw
            self.name_offsets[text] = (offset, len(raw))
        return self.name_offsets[text]


def make_nup(tree: dict[str, Any], pack: frozenset[str] = frozenset(),
             titles: list[dict[str, Any]] | None = None,
             hash_members: bool = True) -> bytes:
    """Construit un ``.nup`` valide à partir d'une arborescence de ``bytes``.

    ``tree`` : ``{"exefs": {"main": b"..."}, "romfs": {...}}``.
    ``pack`` : chemins (``"romfs/Model/x.bfres"``) stockés en frame zstd (codec 1).
    ``titles`` : liste de dicts ``{"id": int, "version": int}`` ; un seul titre
    par défaut.
    """
    builder = _Builder()
    dirs: list[tuple[str, list[int]]] = []      # (nom, enfants déjà résolus)
    files: list[tuple[str, bytes]] = []         # (chemin complet, contenu)
    index_of: dict[str, int] = {}

    def walk(node: dict[str, Any], name: str, prefix: str, parents_kids: list):
        index = len(dirs)
        dirs.append((name, []))
        index_of[prefix.rstrip("/")] = index
        kids: list[int] = dirs[index][1]
        for child_name in sorted(node):
            value = node[child_name]
            path = f"{prefix}{child_name}"
            if isinstance(value, dict):
                child_index = walk(value, child_name, path + "/", kids)
                kids.append(("dir", child_index))
            else:
                file_index = len(files)
                files.append((path, value))
                kids.append(("file", file_index))
        return index

    walk(tree, "", "", [])
    # les index de fichiers sont connus : on aplatit les listes d'enfants
    children: list[int] = []
    dir_records: list[tuple[int, int, int, int]] = []
    for name, kids in dirs:
        offset, length = builder.name(name)
        dir_records.append((offset, length, len(children), len(kids)))
        for kind, value in kids:
            children.append(value if kind == "dir" else len(dirs) + value)

    file_records: list[dict[str, Any]] = []
    for path, data in files:
        name = path.rsplit("/", 1)[-1]
        offset, length = builder.name(name)
        stored = synth.compress(data) if path in pack else data
        file_records.append({
            "name_offset": offset, "name_length": length, "size": len(data),
            "stored": stored, "codec": 1 if path in pack else 0,
            "sha": hashlib.sha256(data).digest() if hash_members else bytes(32),
            "data_offset": 0,
        })

    title_list = titles or [{"id": 0x0100_0000_0000_0001, "version": 1}]
    title_count = len(title_list)

    def toc_size() -> int:
        return (0x30 + 0x40 * title_count + 24 * len(dirs) + 80 * len(file_records)
                + 4 * len(children) + len(builder.names))

    blob_offset = _align(0x80 + toc_size())
    position = blob_offset
    for record in file_records:
        position = _align(position)
        record["data_offset"] = position
        position += len(record["stored"])
    total_size = position

    strings_offset = (0x30 + 0x40 * title_count + 24 * len(dirs)
                      + 80 * len(file_records) + 4 * len(children))
    toc = bytearray()
    toc += b"TOC\x01"
    toc += struct.pack("<IQQQQ", title_count, len(dirs), len(file_records),
                       strings_offset, len(builder.names))
    toc += bytes(0x30 - 0x28)
    for item in title_list:
        exefs = index_of.get("exefs")
        romfs = index_of.get("romfs")
        toc += struct.pack("<QIIII", item["id"], 0,
                           exefs if exefs is not None else 0xFFFFFFFF,
                           romfs if romfs is not None else 0xFFFFFFFF,
                           item.get("version", 0))
        toc += bytes(0x40 - 24)
    for record in dir_records:
        toc += struct.pack("<IIII", *record) + bytes(8)
    for record in file_records:
        toc += struct.pack("<IIQQQ", record["name_offset"], record["name_length"],
                           record["size"], record["data_offset"],
                           len(record["stored"]))
        toc += record["sha"]
        toc += struct.pack("<BBH", record["codec"], 0, 0) + bytes(12)
    for child in children:
        toc += struct.pack("<I", child)
    toc += builder.names
    assert len(toc) == toc_size(), (len(toc), toc_size())

    blob = bytearray()
    for record in file_records:
        blob += bytes(record["data_offset"] - blob_offset - len(blob))
        blob += record["stored"]
    while len(blob) < total_size - blob_offset:
        blob += b"\x00"

    header = bytearray(0x80)
    header[0:4] = b"NUP\x01"
    struct.pack_into("<QQ", header, 0x08, 0x80, len(toc))
    header[0x18:0x38] = hashlib.sha256(bytes(toc)).digest()
    struct.pack_into("<Q", header, 0x38, blob_offset)
    struct.pack_into("<Q", header, 0x40, total_size)
    gap = blob_offset - 0x80 - len(toc)
    assert gap >= 0, "TOC plus grande que prévu"
    return bytes(header) + bytes(toc) + bytes(gap) + bytes(blob)


def make_sample_nup(compress_members: bool = True) -> bytes:
    """Petit paquet type Switch 2 : exefs + romfs avec BFRES, BNTX.zs, SARC."""
    import synth as _synth

    bfres = _synth.make_bfres()
    bntx = _synth.make_bntx(4, 4, _synth.rgba8_pixels(4, 4))
    pack = _synth.make_sarc([("a.bin", b"AAA"), ("b.bin", b"BBB")])
    tree = {
        "exefs": {"main": b"NSO0" + bytes(28), "main.npdm": bytes(0x40)},
        "romfs": {
            "Model": {"test.bfres": bfres},
            "Textures": {"test.bntx.zs": synth.compress(bntx)},
            "Pack": {"Data.pack": pack},
            "readme.txt": b"sample romfs\n",
        },
    }
    pack_paths = frozenset({"romfs/Pack/Data.pack"}) if compress_members else frozenset()
    return make_nup(tree, pack=pack_paths)
