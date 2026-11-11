"""Table des tailles de ressources (``ResourceSizeTable*.rsizetable``).

Le jeu réserve, pour chaque fichier du romfs, une taille maximale (la
« ResourceSizeTable »). Un fichier plus gros que sa réservation n'est pas
chargé : c'est la raison principale pour laquelle un mod (textures HD, modèle
plus lourd, police plus grande) « ne fait rien » sur Switch / Switch 2, et
Hookshot le documente explicitement — le mod doit fournir une table avec des
entrées agrandies.

Disposition du contenu (petit-boutiste) ::

    « RESTBL »            magic (6 octets)
    u32                   version
    u32                   longueur des noms des entrées nommées
    u32                   nombre d'entrées hachées
    u32                   nombre d'entrées nommées
    puis, par entrée hachée : u32 CRC-32 du chemin, u32 taille réservée
    puis, les entrées nommées (conservées telles quelles : format du jeu)

La clé est le **CRC-32 du chemin relatif au romfs**, suffixe ``.zs`` retiré
(``Model/Test.bfres.zs`` → ``Model/Test.bfres``). Les entrées hachées sont
triées par CRC : la recherche du jeu est dichotomique, l'ordre est donc
obligatoire après une insertion.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .util import FormatError, HookyError, human_size

MAGIC = b"RESTBL"
HEADER_SIZE = 22          # 6 (magic) + 4 + 4 + 4 + 4
ENTRY_SIZE = 8
TABLE_GLOB = "ResourceSizeTable.*"


def key_for(path: str) -> int:
    """CRC-32 du chemin, suffixe ``.zs`` retiré (c'est la clé du jeu)."""
    name = path.replace("\\", "/")
    while name.lower().endswith(".zs"):
        name = name[:-3]
    return zlib.crc32(name.encode("utf-8"))


@dataclass
class ResourceSizeTable:
    version: int = 0
    name_length: int = 0
    sizes: dict[int, int] = field(default_factory=dict)
    tail: bytes = b""                     # entrées nommées, telles quelles
    named_count: int = 0

    # ------------------------------------------------------------------ lecture
    @classmethod
    def parse(cls, data: bytes) -> "ResourceSizeTable":
        if len(data) < HEADER_SIZE or data[:6] != MAGIC:
            raise FormatError("Ce n'est pas une table RESTBL "
                              f"(magic {data[:6]!r}).")
        version, name_length, hashed, named = struct.unpack_from("<IIII", data, 6)
        end = HEADER_SIZE + ENTRY_SIZE * hashed
        if end > len(data):
            raise FormatError("Table RESTBL tronquée (entrées hachées).")
        sizes: dict[int, int] = {}
        for index in range(hashed):
            key, size = struct.unpack_from("<II", data, HEADER_SIZE + ENTRY_SIZE * index)
            sizes[key] = size
        return cls(version=version, name_length=name_length, sizes=sizes,
                   tail=bytes(data[end:]), named_count=named)

    @classmethod
    def load(cls, path: str | Path) -> "ResourceSizeTable":
        return cls.parse(Path(path).read_bytes())

    # ------------------------------------------------------------------ écriture
    def build(self) -> bytes:
        entries = b"".join(struct.pack("<II", key, size)
                           for key, size in sorted(self.sizes.items()))
        header = MAGIC + struct.pack("<IIII", self.version, self.name_length,
                                     len(self.sizes), self.named_count)
        return header + entries + self.tail

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.build())

    # ------------------------------------------------------------------ entrées
    def __len__(self) -> int:
        return len(self.sizes)

    def get(self, path: str) -> int | None:
        return self.sizes.get(key_for(path))

    def set(self, path: str, size: int) -> None:
        if size < 0 or size > 0xFFFFFFFF:
            raise HookyError(f"Taille hors limites pour {path} : {size}")
        self.sizes[key_for(path)] = int(size)

    def ensure(self, path: str, size: int) -> bool:
        """Agrandit l'entrée de ``path`` si ``size`` ne tient pas.

        Renvoie ``True`` si la table a changé (entrée créée ou agrandie),
        ``False`` si la taille réservée suffisait déjà.
        """
        key = key_for(path)
        current = self.sizes.get(key)
        if current is not None and current >= size:
            return False
        # une entrée nouvelle reçoit une petite marge, comme le jeu
        self.sizes[key] = int(size if current is not None else size + 64)
        return True

    # ------------------------------------------------------------------ diagnostic
    def summary(self) -> list[str]:
        total = sum(self.sizes.values())
        lines = [f"Table RESTBL v{self.version} : {len(self.sizes)} entrée(s) hachée(s), "
                 f"{self.named_count} nommée(s), {human_size(total)} réservés au total"]
        if self.tail:
            lines.append(f"  entrées nommées conservées : {human_size(len(self.tail))}")
        return lines

    def check(self, files: dict[str, int]) -> list[str]:
        """Chemins dont la taille dépasse la réservation (``{chemin: taille}``)."""
        too_big = []
        for path, size in files.items():
            reserved = self.get(path)
            if reserved is not None and size > reserved:
                too_big.append(f"{path} : {human_size(size)} > "
                               f"{human_size(reserved)} réservés")
        return too_big

    def grow_for(self, files: dict[str, int]) -> list[str]:
        """Agrandit les entrées nécessaires ; renvoie la liste des changements."""
        changes: list[str] = []
        for path, size in files.items():
            key = key_for(path)
            previous = self.sizes.get(key)
            if self.ensure(path, size):
                changes.append(f"{path} : {human_size(previous or 0)} → "
                               f"{human_size(self.sizes[key])}")
        return changes


# --------------------------------------------------------------------------- romfs
def find_tables(romfs: str | Path) -> list[Path]:
    """Tables présentes dans ``romfs/System/Resource`` (triées)."""
    folder = Path(romfs) / "System" / "Resource"
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if path.name.startswith("ResourceSizeTable.")
                  and ".rsizetable" in path.name)


def load_table(romfs: str | Path, dicts=None) -> tuple[ResourceSizeTable, Path, bytes]:
    """Charge la table du romfs ; renvoie ``(table, chemin, données brutes .zs)``.

    ``dicts`` (un :class:`hookymod.zs.ZstdDicts`) est utilisé pour décompresser
    la table quand elle est en ``.zs``. Sans lui, la table est lue via
    ``zstandard`` sans dictionnaire (échoue si le jeu en utilise un).
    """
    tables = find_tables(romfs)
    if not tables:
        raise HookyError("Aucune table de tailles "
                         "(romfs/System/Resource/ResourceSizeTable.*) trouvée.")
    path = tables[0]
    raw = path.read_bytes()
    if path.suffix.lower() == ".zs":
        if dicts is not None and len(dicts):
            from .zs import DictMissing
            try:
                data = dicts.decompress(raw)
            except DictMissing:
                data = _plain_zstd(raw, path)
        else:
            data = _plain_zstd(raw, path)
    else:
        data = raw
    return ResourceSizeTable.parse(data), path, raw


def _plain_zstd(raw: bytes, path: Path) -> bytes:
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover
        raise HookyError("module « zstandard » requis pour lire la table") from exc
    try:
        return zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    except zstandard.ZstdError as exc:
        raise HookyError(f"Table illisible sans son dictionnaire ({path.name}) : {exc}. "
                         "Ouvre d'abord le romfs pour charger ZsDic.pack.zs.") from exc
