"""Lecture et reconstruction d'archives SARC (little-endian).

Format (rappel) ::

    0x00  "SARC"
    0x04  uint16 header size (0x14)
    0x06  uint16 BOM (0xFFFE = little endian, 0xFEFF = big endian)
    0x08  uint32 taille totale du fichier
    0x0C  uint32 offset des données
    0x10  uint16 version (0x0100)
    0x12  uint16 réservé
    0x14  "SFAT" uint16 header size uint16 nombre de nœuds uint32 multiplicateur
          nœuds : uint32 hash, uint32 attributs, uint32 début, uint32 fin
    ...   "SFNT" uint16 header size uint16 réservé, table des noms
    ...   données des fichiers (offsets relatifs à l'offset des données)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import native
from .util import FormatError, align, cstring

MAGIC = b"SARC"
SFAT = b"SFAT"
SFNT = b"SFNT"

#: Extensions dont les données doivent être alignées (GPU / mmap).
BIG_ALIGN_EXTS = (".bfres", ".sbfres", ".bntx", ".bnsh", ".bnch", ".pack")
BIG_ALIGN_MAGICS = (b"FRES", b"BNTX", b"BNSH", b"FRES")


def is_sarc(data: bytes) -> bool:
    return data[:4] == MAGIC and len(data) >= 0x20


def backend() -> dict:
    """Chemin SARC utilisé par l'application, avec son repli historique."""
    if native.available():
        return {"name": "rust", "label": "Rust si compatible, sinon Python"}
    return {"name": "python", "label": "Python"}


def sarc_hash(name: str, mult: int = 0x65) -> int:
    """Hash SFAT d'un nom (comme ``sarc_tool``)."""
    value = 0
    for byte in name.encode("utf-8", "replace"):
        value = (value * mult + byte) & 0xFFFFFFFF
    return value


@dataclass
class SarcEntry:
    """Un fichier contenu dans une archive."""

    name: str
    data: bytes
    alignment: int = 4
    hash: int = 0
    hash_valid: bool = True

    def __post_init__(self) -> None:
        if not self.hash:
            self.hash = sarc_hash(self.name)

    @property
    def named(self) -> bool:
        return not self.name.startswith("0x") or not self.name.endswith(".bin")


@dataclass
class SarcArchive:
    """Archive SARC modifiable en mémoire."""

    entries: list[SarcEntry] = field(default_factory=list)
    endian: str = "<"
    hash_mult: int = 0x65
    version: int = 0x0100
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ lecture
    @classmethod
    def parse(cls, data: bytes) -> "SarcArchive":
        # Garder le chemin historique pour les sous-classes et les tampons
        # mutables : leurs types et leurs surcharges font partie du comportement
        # Python. Le natif peut aussi décliner les archives volumineuses.
        if cls is SarcArchive and type(data) is bytes and native.available():
            parsed = native.sarc_parse(data)
            if parsed is not None:
                endian, version, mult, metadata, warnings = parsed
                entries = [SarcEntry(name=name, data=data[start:start + size],
                                     alignment=alignment, hash=node_hash, hash_valid=True)
                           for name, start, size, alignment, node_hash in metadata]
                return cls(entries=entries, endian=endian, hash_mult=mult,
                           version=version, warnings=warnings)
        if data[:4] != MAGIC:
            raise FormatError("Ce n'est pas une archive SARC (magic « SARC » absent).")
        if len(data) < 0x20:
            raise FormatError("Archive SARC tronquée (en-tête incomplet).")
        bom = data[6:8]
        if bom == b"\xfe\xff":
            endian = ">"
        elif bom == b"\xff\xfe":
            endian = "<"
        else:
            raise FormatError("Archive SARC : BOM inconnu.")
        try:
            header_size = struct.unpack_from(endian + "H", data, 4)[0]
            data_off = struct.unpack_from(endian + "I", data, 0x0C)[0]
            version = struct.unpack_from(endian + "H", data, 0x10)[0]
        except struct.error as exc:
            raise FormatError("Archive SARC : en-tête illisible.") from exc
        if header_size < 0x14 or header_size + 0x0C > len(data):
            raise FormatError(f"Archive SARC : taille d'en-tête invalide (0x{header_size:X}).")
        if data_off > len(data):
            raise FormatError("Archive SARC : offset de données hors du fichier.")

        if data[header_size:header_size + 4] != SFAT:
            raise FormatError("Archive SARC : bloc SFAT absent.")
        (sfat_size, count, mult) = struct.unpack_from(endian + "HHI", data, header_size + 4)
        if sfat_size < 0x0C:
            raise FormatError("Archive SARC : bloc SFAT invalide.")
        nodes_off = header_size + sfat_size
        names_block = nodes_off + 16 * count
        if names_block + 8 > len(data):
            raise FormatError("Archive SARC : table des nœuds tronquée.")
        if data[names_block:names_block + 4] != SFNT:
            raise FormatError("Archive SARC : bloc SFNT absent.")
        names_off = names_block + 8

        entries: list[SarcEntry] = []
        warnings: list[str] = []
        for i in range(count):
            try:
                node_hash, attrs, start, end = struct.unpack_from(endian + "IIII", data, nodes_off + 16 * i)
            except struct.error as exc:
                raise FormatError(f"Archive SARC : nœud {i} illisible.") from exc
            named = bool(attrs & 0x01000000)
            if named:
                name_off = names_off + (attrs & 0x00FFFFFF) * 4
                if name_off >= len(data):
                    warnings.append(f"nœud {i} : nom hors fichier, renommé.")
                    name = f"0x{node_hash:08x}.bin"
                    named = False
                else:
                    name = cstring(data, name_off).replace("\\", "/")
                    if not name:
                        warnings.append(f"nœud {i} : nom vide, renommé.")
                        name = f"0x{node_hash:08x}.bin"
                        named = False
            else:
                name = f"0x{node_hash:08x}.bin"
            if start > end or data_off + end > len(data):
                warnings.append(f"« {name} » : bornes invalides (0x{start:X}-0x{end:X}), ignoré.")
                continue
            blob = data[data_off + start:data_off + end]
            alignment = cls._guess_alignment(blob, name, data_off + start)
            entries.append(SarcEntry(name=name, data=blob, alignment=alignment,
                                     hash=node_hash, hash_valid=True))
        return cls(entries=entries, endian=endian, hash_mult=mult, version=version,
                   warnings=warnings)

    @staticmethod
    def _guess_alignment(blob: bytes, name: str, abs_offset: int) -> int:
        """Devine l'alignement utile d'une entrée (GPU vs données simples)."""
        lower = name.lower()
        if blob[:4] in BIG_ALIGN_MAGICS or lower.endswith(BIG_ALIGN_EXTS):
            base = 0x2000
        else:
            base = 4
        # Si l'offset d'origine était plus aligné que ça, on le conserve.
        if abs_offset:
            detected = abs_offset & (-abs_offset)
            if detected > base:
                base = min(detected, 0x10000)
        return base

    # ------------------------------------------------------------------ accès
    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __getitem__(self, index):
        return self.entries[index]

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def get(self, name: str) -> SarcEntry | None:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def find(self, needle: str) -> list[SarcEntry]:
        needle = needle.lower()
        return [e for e in self.entries if needle in e.name.lower()]

    def index_of(self, entry: SarcEntry) -> int:
        for i, item in enumerate(self.entries):
            if item is entry:
                return i
        raise ValueError("entrée absente de cette archive")

    def replace(self, index: int, data: bytes) -> None:
        entry = self.entries[index]
        entry.data = data
        entry.alignment = self._guess_alignment(data, entry.name, 0)

    def add(self, name: str, data: bytes) -> SarcEntry:
        if self.get(name):
            raise ValueError(f"« {name} » existe déjà dans l'archive.")
        entry = SarcEntry(name=name, data=data,
                          alignment=self._guess_alignment(data, name, 0))
        self.entries.append(entry)
        return entry

    def remove(self, index: int) -> None:
        del self.entries[index]

    # ------------------------------------------------------------------ écriture
    def build(self, verify: bool = True) -> bytes:
        """Reconstruit l'archive complète.

        Le résultat est relu juste après pour vérifier qu'aucune entrée n'a été
        perdue ou corrompue (protection contre une reconstruction bancale).
        """
        if not self.entries:
            raise FormatError("Impossible de construire une archive SARC vide.")
        if type(self) is SarcArchive and native.available():
            result = native.sarc_build(self.entries, self.endian, self.hash_mult,
                                       self.version, verify=verify)
            if result is not None:
                return result

        e = self.endian
        if e not in ("<", ">"):
            e = "<"

        # 1. nœuds triés par hash (puis nom, pour les collisions)
        items = sorted(self.entries, key=lambda it: (sarc_hash(it.name, self.hash_mult), it.name))

        # 2. table des noms + offsets en mots de 4 octets
        names = bytearray()
        name_offsets: list[int] = []
        for item in items:
            name_offsets.append(len(names) // 4)
            raw = item.name.encode("utf-8", "replace") + b"\x00"
            names += raw
            names += b"\x00" * (-len(names) % 4)

        # 3. positions des données
        max_align = max([it.alignment for it in items] + [4])
        header_size = 0x14
        sfat_size = 0x0C
        nodes_size = 16 * len(items)
        names_size = 8 + len(names)
        data_off = align(header_size + sfat_size + nodes_size + names_size, max_align)

        nodes: list[tuple[int, int, int, int]] = []
        blobs: list[tuple[int, bytes]] = []
        pos = 0
        for item, name_off in zip(items, name_offsets):
            pos = align(pos, item.alignment)
            start = pos
            pos += len(item.data)
            blobs.append((start, item.data))
            attrs = 0x01000000 | (name_off & 0x00FFFFFF)
            nodes.append((sarc_hash(item.name, self.hash_mult), attrs, start, pos))

        total = data_off + pos
        out = bytearray()
        out += MAGIC
        out += struct.pack(e + "H", header_size)
        out += b"\xff\xfe" if e == "<" else b"\xfe\xff"
        out += struct.pack(e + "II", total, data_off)
        out += struct.pack(e + "HH", self.version, 0)
        out += SFAT
        out += struct.pack(e + "HHI", sfat_size, len(nodes), self.hash_mult)
        for node in nodes:
            out += struct.pack(e + "IIII", *node)
        out += SFNT
        out += struct.pack(e + "HH", 8, 0)
        out += bytes(names)
        if len(out) > data_off:
            raise FormatError("Erreur interne : en-tête SARC plus grand que prévu.")
        out += b"\x00" * (data_off - len(out))
        for start, blob in blobs:
            if len(out) < data_off + start:
                out += b"\x00" * (data_off + start - len(out))
            out += blob
        result = bytes(out)

        if verify:
            check = SarcArchive.parse(result)
            if len(check.entries) != len(items):
                raise FormatError("Reconstruction SARC incohérente (nombre d'entrées).")
            for expected, got in zip(items, check.entries):
                if expected.name != got.name or expected.data != got.data:
                    raise FormatError(
                        f"Reconstruction SARC incohérente pour « {expected.name} »."
                    )
        return result


def unpack_sarc(data: bytes) -> SarcArchive:
    return SarcArchive.parse(data)
