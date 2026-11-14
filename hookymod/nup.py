"""Lecture des paquets ``.nup`` : dumps déchiffrés de jeux Nintendo Switch 2.

Un ``.nup`` est le format produit par les outils de dump Switch 2 : un seul
fichier contenant les titres du jeu, chacun avec son arborescence ``exefs/``
(main, rtld, subsdk, …) et ``romfs/`` (les assets). C'est exactement ce que le
portage PC **Hookshot** accepte à la place d'un couple ``romfs`` + ``exefs``.

Disposition (petit-boutiste), reconstituée à partir du fichier ::

    0x000  en-tête « NUP\\x01 »
             +0x08 u64 décalage de la TOC, u64 taille de la TOC
             +0x18 32 octets : SHA-256 de la TOC
             +0x38 u64 décalage du « blob » (début des données)
             +0x40 u64 taille totale déclarée du fichier
    0x080  TOC « TOC\\x01 »
             +0x04 u32 nombre de titres
             +0x08 u64 nombre de dossiers
             +0x10 u64 nombre de fichiers
             +0x18 u64 décalage de la table des noms (relatif à la TOC)
             +0x20 u64 taille de la table des noms
    puis   titres (0x40 octets) : u64 id, u32 racine, u32 exefs, u32 romfs,
           u32 version, … ; 0xFFFFFFFF = absent
    puis   dossiers (24 octets) : u32 nom (offset), u32 longueur, u32 premier
           enfant, u32 nombre d'enfants, u64 réservé
    puis   fichiers (80 octets) : u32 nom, u32 longueur, u64 taille réelle,
           u64 décalage des données, u64 taille stockée, 32 octets de SHA-256,
           u8 codec (0 = brut, 1 = zstd), u8 drapeaux, 2+12 octets réservés
    puis   liste d'enfants (u32) : index < dossiers → dossier, sinon fichier
    puis   noms, non terminés par NUL (offset + longueur)

Le contenu d'un fichier est stocké tel quel, ou dans une frame zstd quand le
codec vaut 1. Un fichier ``.zs`` du romfs est lui aussi une frame zstd : ce
n'est pas une compression du paquet, il ne faut donc pas le décompresser à
l'extraction (l'heuristique : une frame dont le contenu annoncé fait exactement
la taille du fichier est une compression du paquet).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .util import HookyError, FormatError, human_size, safe_child, write_atomic

NUP_MAGIC = b"NUP\x01"
TOC_MAGIC = b"TOC\x01"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
NO_NODE = 0xFFFFFFFF

CODEC_NAMES = {0: "brut", 1: "zstd"}


# --------------------------------------------------------------------------- zstd
def frame_content_size(head: bytes) -> int | None:
    """Taille de contenu annoncée par une frame zstd (``None`` si absente)."""
    if len(head) < 6 or head[:4] != ZSTD_MAGIC:
        return None
    descriptor = head[4]
    single_segment = (descriptor >> 5) & 1
    checksum = (descriptor >> 2) & 1
    dict_flag = descriptor & 3
    size_flag = descriptor >> 6
    at = 5 + (0 if single_segment else 1) + (0, 1, 2, 4)[dict_flag]
    if checksum:
        pass  # l'indicateur de checksum ne décale pas la taille de contenu
    if size_flag == 0:
        if not single_segment:
            return None
        return head[at] if len(head) > at else None
    width = {1: 2, 2: 4, 3: 8}[size_flag]
    if len(head) < at + width:
        return None
    size = int.from_bytes(head[at:at + width], "little")
    return size + 256 if width == 2 else size


def _zstd_decompress(data: bytes) -> bytes:
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - dépend de l'environnement
        raise HookyError("module « zstandard » requis pour lire un .nup compressé "
                         "(pip install zstandard)") from exc
    try:
        return zstandard.ZstdDecompressor().decompressobj().decompress(data)
    except zstandard.ZstdError as exc:
        raise FormatError(f"Frame zstd invalide : {exc}") from exc


# --------------------------------------------------------------------------- modèles
@dataclass
class NupTitle:
    index: int
    title_id: int
    version: int
    root: int
    exefs: int | None
    romfs: int | None

    @property
    def label(self) -> str:
        parts = [f"{self.title_id:016X}"]
        if self.version:
            parts.append(f"v{self.version}")
        return " · ".join(parts)

    def has(self, part: str) -> bool:
        return self.node(part) is not None

    def node(self, part: str) -> int | None:
        value = {"exefs": self.exefs, "romfs": self.romfs, "": self.root}.get(part)
        return None if value is None or value == NO_NODE else value


@dataclass
class NupDir:
    name_offset: int
    name_length: int
    first_child: int
    child_count: int


@dataclass
class NupFile:
    name_offset: int
    name_length: int
    size: int
    offset: int
    stored_size: int
    sha256: bytes
    codec: int
    flags: int
    name: str = ""
    packed: bool | None = None

    @property
    def codec_name(self) -> str:
        return CODEC_NAMES.get(self.codec, f"codec {self.codec}")


@dataclass
class ExtractResult:
    count: int = 0
    total_bytes: int = 0
    problems: list[str] = field(default_factory=list)
    heads: list[str] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.problems)


# --------------------------------------------------------------------------- paquet
class NupPackage:
    """Accès en lecture seule au contenu d'un ``.nup``."""

    def __init__(self, path: str | Path | None = None, data: bytes | None = None) -> None:
        if path is None and data is None:
            raise HookyError("NupPackage : chemin ou données requis.")
        self.path = Path(path) if path is not None else None
        self._buffer = data
        self._handle = None
        self.titles: list[NupTitle] = []
        try:
            self._parse()
        except Exception:
            self.close()
            raise

    # ------------------------------------------------------------------ accès bas niveau
    @classmethod
    def open(cls, path: str | Path) -> "NupPackage":
        path = Path(path)
        if not path.exists():
            raise HookyError(f"Paquet introuvable : {path}")
        return cls(path=path)

    @classmethod
    def from_bytes(cls, data: bytes) -> "NupPackage":
        return cls(data=data)

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def __enter__(self) -> "NupPackage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __len__(self) -> int:
        return len(self.titles)

    @property
    def physical_size(self) -> int:
        if self._buffer is not None:
            return len(self._buffer)
        assert self.path is not None
        return self.path.stat().st_size

    def _read_at(self, offset: int, size: int) -> bytes:
        if size <= 0:
            return b""
        if offset < 0 or offset + size > self.physical_size:
            raise FormatError(f"Lecture hors du paquet : {offset:#x}+{size:#x} "
                              f"(taille {self.physical_size:#x})")
        if self._buffer is not None:
            return self._buffer[offset:offset + size]
        if self._handle is None:
            assert self.path is not None
            self._handle = self.path.open("rb")
        self._handle.seek(offset)
        chunk = self._handle.read(size)
        if len(chunk) != size:
            raise FormatError("Paquet tronqué (lecture incomplète) : copie ou "
                              "téléchargement non terminé ?")
        return chunk

    # ------------------------------------------------------------------ analyse
    def _parse(self) -> None:
        if self.physical_size < 0x80:
            raise FormatError("Fichier trop court pour un paquet .nup.")
        header = self._read_at(0, 0x80)
        if header[:4] != NUP_MAGIC:
            raise FormatError(f"Ce n'est pas un paquet .nup (magic {header[:4]!r}).")
        self.declared_size = struct.unpack_from("<Q", header, 0x40)[0]
        self.toc_offset, self.toc_size = struct.unpack_from("<QQ", header, 0x08)
        self.toc_sha256 = header[0x18:0x38]
        self.blob_offset = struct.unpack_from("<Q", header, 0x38)[0]
        if self.declared_size and self.physical_size < self.declared_size:
            raise FormatError(
                f"Le .nup est incomplet : {human_size(self.physical_size)} sur "
                f"{human_size(self.declared_size)} annoncés. Retélécharge ou recopie "
                "le fichier entièrement.")
        if self.toc_size < 0x30:
            raise FormatError("TOC trop petite.")
        toc = self._read_at(self.toc_offset, self.toc_size)
        if toc[:4] != TOC_MAGIC:
            raise FormatError(f"Pas de TOC à {self.toc_offset:#x}.")
        title_count, self.dir_count, self.file_count, strings_offset, strings_size = \
            struct.unpack_from("<IQQQQ", toc, 4)

        titles_at = 0x30
        dirs_at = titles_at + 0x40 * title_count
        files_at = dirs_at + 24 * self.dir_count
        kids_at = files_at + 80 * self.file_count
        if kids_at > len(toc) or strings_offset > len(toc):
            raise FormatError("Tables de la TOC incohérentes (dépassement).")

        self.strings = toc[strings_offset:strings_offset + strings_size]

        for index in range(title_count):
            base = titles_at + 0x40 * index
            title_id, root, exefs, romfs, version = struct.unpack_from("<QIIII", toc, base)
            self.titles.append(NupTitle(
                index=index, title_id=title_id, version=version, root=root,
                exefs=None if exefs == NO_NODE else exefs,
                romfs=None if romfs == NO_NODE else romfs))

        self.dirs: list[NupDir] = []
        for index in range(self.dir_count):
            name_offset, name_length, first_child, child_count = \
                struct.unpack_from("<IIII", toc, dirs_at + 24 * index)
            self.dirs.append(NupDir(name_offset, name_length, first_child, child_count))

        self.files: list[NupFile] = []
        for index in range(self.file_count):
            base = files_at + 80 * index
            name_offset, name_length, size, offset, stored_size = \
                struct.unpack_from("<IIQQQ", toc, base)
            self.files.append(NupFile(
                name_offset=name_offset, name_length=name_length, size=size,
                offset=offset, stored_size=stored_size,
                sha256=toc[base + 32:base + 64], codec=toc[base + 64],
                flags=toc[base + 65]))

        if strings_offset < kids_at:
            raise FormatError("Table des noms avant la liste d'enfants.")
        count = (strings_offset - kids_at) // 4
        self.children = list(struct.unpack_from(f"<{count}I", toc, kids_at)) if count else []

    # ------------------------------------------------------------------ noms / parcours
    def name(self, offset: int, length: int) -> str:
        raw = self.strings[offset:offset + length]
        if len(raw) != length:
            raise FormatError("Nom hors de la table des noms.")
        return raw.decode("utf-8", "replace")

    def dir_name(self, index: int) -> str:
        entry = self.dirs[index]
        return self.name(entry.name_offset, entry.name_length)

    def file_name(self, entry: NupFile) -> str:
        if not entry.name:
            entry.name = self.name(entry.name_offset, entry.name_length)
        return entry.name

    def title(self, value: int | str | None = None) -> NupTitle:
        if not self.titles:
            raise FormatError("Le paquet ne contient aucun titre.")
        if value is None:
            return self.titles[0]
        if isinstance(value, int):
            for title in self.titles:
                if title.index == value:
                    return title
            raise HookyError(f"Titre #{value} absent (0 à {len(self.titles) - 1}).")
        wanted = str(value).lower().removeprefix("0x")
        for title in self.titles:
            if f"{title.title_id:016x}".endswith(wanted) or str(title.version) == wanted:
                return title
        raise HookyError(f"Aucun titre ne correspond à « {value} ».")

    def walk(self, node: int, prefix: str = "") -> Iterator[tuple[str, NupFile]]:
        """Parcourt récursivement un dossier ; renvoie ``(chemin, fichier)``."""
        seen: set[int] = set()
        stack: list[tuple[int, str]] = [(node, prefix)]
        while stack:
            current, base = stack.pop()
            if current in seen:
                raise FormatError("Boucle dans l'arborescence du paquet.")
            seen.add(current)
            if current >= len(self.dirs):
                raise FormatError("Index de dossier invalide.")
            entry = self.dirs[current]
            end = entry.first_child + entry.child_count
            if end > len(self.children):
                raise FormatError("Liste d'enfants incohérente.")
            for child in self.children[entry.first_child:end]:
                if child < self.dir_count:
                    stack.append((child, f"{base}{self.dir_name(child)}/"))
                elif child - self.dir_count < len(self.files):
                    item = self.files[child - self.dir_count]
                    yield f"{base}{self.file_name(item)}", item
                else:
                    raise FormatError("Index d'enfant hors table.")

    def walk_part(self, part: str, title: int | str | None = None) -> Iterator[tuple[str, NupFile]]:
        node = self.title(title).node(part)
        if node is None:
            return iter(())
        return self.walk(node, "" if part == "" else f"{part}/")

    def members(self, title: int | str | None = None,
                only: str | None = None) -> Iterator[tuple[str, NupFile]]:
        """Fichiers du titre, chemins préfixés par ``exefs/`` / ``romfs/``."""
        chosen = self.title(title)
        parts = [only] if only else ["exefs", "romfs"]
        for part in parts:
            node = chosen.node(part)
            if node is None:
                continue
            yield from self.walk(node, f"{part}/")

    # ------------------------------------------------------------------ lecture
    def raw(self, entry: NupFile) -> bytes:
        return self._read_at(entry.offset, entry.stored_size)

    def is_packed(self, entry: NupFile) -> bool:
        """Vrai si le paquet compresse lui-même ce fichier (frame zstd)."""
        if entry.packed is not None:
            return entry.packed
        packed = None
        if entry.codec == 1:
            packed = True
        elif entry.stored_size >= 8:
            head = self._read_at(entry.offset, min(18, entry.stored_size))
            if head[:4] == ZSTD_MAGIC:
                announced = frame_content_size(head)
                if announced is None:
                    try:
                        packed = len(_zstd_decompress(self.raw(entry))) == entry.size
                    except HookyError:
                        packed = False
                else:
                    packed = announced == entry.size
        entry.packed = bool(packed)
        return entry.packed

    def read(self, entry: NupFile) -> bytes:
        data = self.raw(entry)
        if self.is_packed(entry):
            data = _zstd_decompress(data)
            if entry.size and len(data) != entry.size:
                raise FormatError(f"Taille décompressée inattendue pour "
                                  f"« {self.file_name(entry)} » : {len(data)} au lieu de "
                                  f"{entry.size}.")
        elif entry.size and len(data) != entry.size:
            raise FormatError(f"Taille inattendue pour « {self.file_name(entry)} » : "
                              f"{len(data)} au lieu de {entry.size}.")
        return data

    def verify_sha256(self, entry: NupFile) -> bool:
        if not any(entry.sha256):
            return True
        return hashlib.sha256(self.read(entry)).digest() == entry.sha256

    # ------------------------------------------------------------------ contrôle
    def verify(self, title: int | str | None = None, deep: bool = False) -> list[str]:
        """Liste les anomalies détectées ; une empreinte absente ne peut être vérifiée."""
        problems: list[str] = []
        if any(self.toc_sha256):
            try:
                toc = self._read_at(self.toc_offset, self.toc_size)
            except (HookyError, OSError) as exc:
                problems.append(f"TOC illisible : {exc}")
            else:
                if hashlib.sha256(toc).digest() != self.toc_sha256:
                    problems.append("TOC : SHA-256 différent")
        titles = [self.title(title)] if title is not None else self.titles
        total = 0
        for chosen in titles:
            for part in ("exefs", "romfs"):
                if chosen.node(part) is None:
                    continue
                for rel, entry in self.walk(chosen.node(part), f"{part}/"):
                    total += 1
                    if entry.codec not in CODEC_NAMES:
                        problems.append(f"{rel} : codec inconnu {entry.codec}")
                    if entry.offset < self.blob_offset or entry.offset % 16:
                        problems.append(f"{rel} : décalage mal aligné")
                    if entry.offset + entry.stored_size > self.physical_size:
                        problems.append(f"{rel} : données hors du fichier")
                    if deep:
                        try:
                            data = self.read(entry)
                        except HookyError as exc:
                            problems.append(f"{rel} : illisible ({exc})")
                            continue
                        if not self.verify_sha256(entry):
                            problems.append(f"{rel} : SHA-256 différent")
                        if rel.endswith(".zs") and not data.startswith(ZSTD_MAGIC):
                            problems.append(f"{rel} : .zs dont l'en-tête n'est pas zstd")
        if not total:
            problems.append("aucun fichier trouvé pour ce titre")
        return problems

    # ------------------------------------------------------------------ extraction
    def extract(self, destination: str | Path, title: int | str | None = None,
                only: str | None = None,
                progress: Callable[[int, int], None] | None = None,
                skip_bad: bool = True) -> ExtractResult:
        """Écrit les fichiers du titre sous ``destination/<part>/<chemin>``.

        Les chemins refusés et les erreurs d'écriture sont ajoutés aux
        problèmes quand ``skip_bad`` est vrai ; sinon une erreur est levée.
        Les fichiers ordinaires déjà présents peuvent être remplacés, mais
        l'écrasement du paquet source est refusé. L'extraction n'est pas globale-
        ment transactionnelle : les fichiers précédents restent en cas d'erreur.
        """
        root = Path(destination)
        entries = list(self.members(title, only))
        try:
            source = self.path.resolve() if self.path is not None else None
        except (OSError, RuntimeError) as exc:
            raise HookyError(f"Chemin du paquet inaccessible : {exc}") from exc
        result = ExtractResult()
        total = len(entries)
        for index, (rel, entry) in enumerate(entries, 1):
            try:
                target = safe_child(root, rel)
                if target is None:
                    raise FormatError("Chemin de sortie refusé (hors du dossier d'extraction).")
                if source is not None and target == source:
                    raise FormatError("Le fichier extrait écraserait le paquet NUP d'origine.")
                data = self.read(entry)
                target.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(target, data)
            except (HookyError, OSError, ValueError, RuntimeError) as exc:
                result.problems.append(f"{rel} : {exc}")
                if not skip_bad:
                    if isinstance(exc, HookyError):
                        raise
                    raise HookyError(f"Extraction impossible pour « {rel} » : {exc}") from exc
                continue
            result.count += 1
            result.total_bytes += len(data)
            if rel.endswith(".zs") and not data.startswith(ZSTD_MAGIC):
                result.heads.append(rel)
            if progress is not None:
                progress(index, total)
        return result

    # ------------------------------------------------------------------ résumé
    def summary(self) -> list[str]:
        lines = [f"Paquet : {self.path or '<mémoire>'}",
                 f"Taille : {human_size(self.physical_size)} "
                 f"(déclarée {human_size(self.declared_size)})",
                 f"Dossiers : {self.dir_count} · fichiers : {self.file_count} · "
                 f"titres : {len(self.titles)}"]
        for title in self.titles:
            counts = []
            for part in ("exefs", "romfs"):
                if title.node(part) is None:
                    counts.append(f"{part} absent")
                    continue
                files = list(self.walk(title.node(part), ""))
                size = sum(item.size for _, item in files)
                counts.append(f"{part} : {len(files)} fichier(s), {human_size(size)}")
            lines.append(f"  • titre #{title.index} {title.label} — " + " · ".join(counts))
        return lines


def is_nup(data: bytes) -> bool:
    return data[:4] == NUP_MAGIC


def open_nup(path: str | Path) -> NupPackage:
    return NupPackage.open(path)
