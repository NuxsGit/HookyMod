"""Modèle « fichier ouvert » : zstd -> SARC -> entrées -> … (récursif)."""

from __future__ import annotations

from pathlib import Path

from .sarc import SarcArchive, is_sarc
from .util import FormatError, HookyError, ascii_strings, hexdump, human_size
from .zs import DEFAULT_LEVEL, ZstdDicts, frame_info, is_zstd

MAGICS = (
    (b"FRES", "BFRES (modèle, animation, matériaux)"),
    (b"BNTX", "BNTX (textures)"),
    (b"BNSH", "BNSH (shader)"),
    (b"BNCH", "BNCH (shader cache)"),
    (b"BYML", "BYML (données)"),
    (b"YB  ", "BYML (données, big endian)"),
    (b"AAMP", "AAMP (paramètres)"),
    (b"Yaz0", "Yaz0 (compressé)"),
    (b"FRES", "BFRES"),
)


def detect_kind(data: bytes) -> str:
    """Nom lisible du type de données."""
    if not data:
        return "vide"
    if is_zstd(data):
        return "zstd (.zs)"
    if is_sarc(data):
        return "archive SARC"
    for magic, label in MAGICS:
        if data.startswith(magic):
            return label
    if data[:4] == b"RIFF":
        return "RIFF (audio)"
    if data[:4] in (b"OggS",):
        return "Ogg (audio)"
    if data[:4] == b"KCL ":
        return "KCL (collision)"
    return "données binaires"


def is_navigable(data: bytes) -> bool:
    """Peut-on « entrer » dans ces données (double-clic) ?"""
    return is_zstd(data) or is_sarc(data)


class Level:
    """Un fichier ouvert, éventuellement compressé et/ou archive SARC."""

    def __init__(self, name: str, raw: bytes, dicts: ZstdDicts, rel: str | None = None,
                 parent: "Level | None" = None, parent_index: int | None = None,
                 compress_level: int = DEFAULT_LEVEL) -> None:
        self.name = name
        self.dicts = dicts
        self.parent = parent
        self.parent_index = parent_index
        self.rel = rel or name
        self.compress_level = compress_level
        self.original_raw = raw
        self.compressed = is_zstd(raw)
        self.dict_id = 0
        if self.compressed:
            self.dict_id, _ = frame_info(raw)
            self.data = dicts.decompress(raw, self.dict_id)
        else:
            self.data = raw
        self.archive: SarcArchive | None = None
        self.modified = False
        if is_sarc(self.data):
            try:
                self.archive = SarcArchive.parse(self.data)
            except FormatError:
                self.archive = None  # archive illisible : on la garde en binaire
        self.warnings: list[str] = list(self.archive.warnings) if self.archive else []

    # ------------------------------------------------------------------ état
    @property
    def entries(self):
        return self.archive.entries if self.archive else None

    def __len__(self) -> int:
        return len(self.archive) if self.archive else 0

    def path_label(self) -> str:
        parts = []
        node: Level | None = self
        while node is not None:
            parts.append(node.name)
            node = node.parent
        return " › ".join(reversed(parts))

    def describe(self) -> str:
        """Résumé texte (type, taille, hexdump, chaînes, entrées)."""
        lines = [
            f"Fichier : {self.name}",
            f"Chemin  : {self.rel}",
            f"Taille  : {human_size(len(self.data))} ({len(self.data):,} octets)",
            f"Type    : {detect_kind(self.data)}",
        ]
        if self.compressed:
            lines.append(f"Compressé : zstd, dictionnaire id {self.dict_id or '(aucun)'}")
        if self.archive is not None:
            lines.append(f"Archive : {len(self.archive)} entrée(s)")
        if self.modified:
            lines.append("État    : MODIFIÉ (pas encore enregistré)")
        if self.warnings:
            lines.append("")
            lines += [f"⚠ {w}" for w in self.warnings[:8]]
        lines += ["", "--- Aperçu hexadécimal (1 Ko) ---"]
        lines += hexdump(self.data, 1024)
        strings = ascii_strings(self.data, limit=60)
        if strings:
            lines += ["", "--- Chaînes ASCII ---"]
            lines += strings
        return "\n".join(lines)

    # ------------------------------------------------------------------ entrées
    def entry_blob(self, index: int) -> bytes:
        if not self.archive:
            raise HookyError("Ce fichier n'est pas une archive.")
        return self.archive.entries[index].data

    def entry_name(self, index: int) -> str:
        if not self.archive:
            raise HookyError("Ce fichier n'est pas une archive.")
        return self.archive.entries[index].name

    def entry_kind(self, index: int) -> str:
        return detect_kind(self.entry_blob(index))

    def can_open_entry(self, index: int) -> bool:
        return is_navigable(self.entry_blob(index))

    def open_entry(self, index: int) -> "Level":
        """Ouvre une entrée (décompression + SARC éventuelle) comme un enfant."""
        name = self.entry_name(index)
        child = Level(name=name, raw=self.entry_blob(index), dicts=self.dicts,
                      rel=f"{self.rel}!/{name}", parent=self, parent_index=index,
                      compress_level=self.compress_level)
        return child

    def replace_entry(self, index: int, data: bytes) -> None:
        if not self.archive:
            raise HookyError("Ce fichier n'est pas une archive.")
        self.archive.replace(index, data)
        self.modified = True

    def mark_modified(self) -> None:
        self.modified = True

    # ------------------------------------------------------------------ écriture
    def packed_data(self) -> bytes:
        """Données décompressées à jour (SARC reconstruit si nécessaire)."""
        if self.archive is not None and self.modified:
            return self.archive.build()
        return self.data

    def pack(self) -> bytes:
        """Octets finaux, recompressés comme à l'origine si nécessaire."""
        if not self.modified:
            return self.original_raw
        data = self.packed_data()
        if self.compressed:
            return self.dicts.compress(data, self.dict_id, self.compress_level)
        return data

    def save_to(self, path) -> None:
        from .util import write_atomic
        write_atomic(Path(path), self.pack())


class Document:
    """Une pile de :class:`Level` représentant la navigation d'un fichier ouvert."""

    def __init__(self, root: Level) -> None:
        self.stack: list[Level] = [root]
        self.dicts = root.dicts

    # ------------------------------------------------------------------ navigation
    @property
    def root(self) -> Level:
        return self.stack[0]

    @property
    def top(self) -> Level:
        return self.stack[-1]

    def push(self, index: int) -> Level:
        child = self.top.open_entry(index)
        self.stack.append(child)
        return child

    def pop(self) -> Level | None:
        if len(self.stack) <= 1:
            return None
        child = self.stack.pop()
        if not child.modified:
            # Rien n'a changé : on ne touche pas au parent (sinon le document
            # serait marqué « modifié » simplement en naviguant).
            return child
        parent = self.stack[-1]
        parent.replace_entry(child.parent_index, child.pack())
        return child

    def collapse(self) -> Level:
        while len(self.stack) > 1:
            self.pop()
        return self.root

    # ------------------------------------------------------------------ état
    def modified(self) -> bool:
        """Le document contient-il des modifications (à n'importe quel niveau) ?"""
        return bool(self.root.modified) or any(lv.modified for lv in self.stack[1:])

    def pack(self) -> bytes:
        self.collapse()
        return self.root.pack()

    def title(self) -> str:
        return self.top.path_label()

    def status(self) -> str:
        parts = [self.top.path_label()]
        if self.top.compressed:
            parts.append(f"zstd · dict {self.top.dict_id or '-'}")
        if self.modified():
            parts.append("modifié")
        return "   ·   ".join(parts)


def open_document(name: str, raw: bytes, dicts: ZstdDicts, rel: str | None = None) -> Document:
    return Document(Level(name=name, raw=raw, dicts=dicts, rel=rel))
