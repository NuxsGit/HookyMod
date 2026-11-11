"""Gestion des fichiers ``.zs`` (zstd) et des dictionnaires du jeu.

Sur Switch, la plupart des assets sont compressés en zstd **avec un dictionnaire
partagé** livré dans ``Pack/ZsDic.pack.zs``.  Sans ce dictionnaire, impossible de
lire les assets : ce module le détecte, le charge et l'utilise automatiquement.

Il sait aussi se rabattre sur les autres dictionnaires du pack si l'identifiant
du dictionnaire d'un fichier ne correspond pas exactement (utile sur les dumps
modifiés).
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from .sarc import SarcArchive, is_sarc
from .util import DictMissing, FormatError, HookyError

try:  # pragma: no cover - dépendance externe
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
ZSTD_MAGIC_SKIP = b"\x50\x2a\x4d\x18"
DEFAULT_LEVEL = 16


def zstd_available() -> bool:
    return zstd is not None


def require_zstd() -> None:
    if zstd is None:  # pragma: no cover - dépend de l'environnement
        raise HookyError(
            "Le module « zstandard » est manquant.\n\n"
            "Installe-le avec :\n    pip install zstandard"
        )


def is_zstd(data: bytes) -> bool:
    return data[:4] == ZSTD_MAGIC


def is_dict_file(data: bytes) -> bool:
    """Heuristique : est-ce un dictionnaire zstd brut (et non un asset) ?"""
    if len(data) < 8 or is_zstd(data) or is_sarc(data):
        return False
    if data[:4] in (b"FRES", b"BNTX", b"BNSH", b"BYML", b"YB  "):
        return False
    return True


def frame_info(data: bytes) -> tuple[int, int | None]:
    """Retourne ``(dict_id, content_size)`` d'une frame zstd."""
    require_zstd()
    try:
        params = zstd.get_frame_parameters(data)
    except Exception as exc:  # zstd.ZstdError
        raise FormatError(f"Frame zstd invalide : {exc}") from exc
    size = params.content_size
    return params.dict_id, (None if size in (0, 0xFFFFFFFFFFFFFFFF) else size)


class ZstdDicts:
    """Collection de dictionnaires zstd, indexés par identifiant de contenu."""

    def __init__(self) -> None:
        self.by_id: dict[int, tuple[str, "zstd.ZstdCompressionDict"]] = {}
        self.sources: list[Path] = []
        self.loaded_files: list[str] = []

    # ------------------------------------------------------------------ état
    def __len__(self) -> int:
        return len(self.by_id)

    def __bool__(self) -> bool:
        return bool(self.by_id)

    def describe(self) -> list[str]:
        return [f"{name} (id {did})" for did, (name, _) in sorted(self.by_id.items())]

    # ------------------------------------------------------------------ chargement
    def add(self, name: str, raw: bytes) -> bool:
        """Ajoute un dictionnaire brut.  Retourne ``True`` si nouveau."""
        require_zstd()
        if not raw:
            return False
        try:
            dictionary = zstd.ZstdCompressionDict(raw)
            did = dictionary.dict_id()
        except Exception:
            return False
        if did in self.by_id:
            return False
        self.by_id[did] = (name, dictionary)
        return True

    def load_file(self, path) -> int:
        """Charge un ``.zsdic``, un ``ZsDic.pack`` ou ``ZsDic.pack.zs``."""
        require_zstd()
        path = Path(path)
        raw = path.read_bytes()
        count = 0
        if is_zstd(raw):
            raw = self.decompress(raw, allow_try_all=False)
        if is_sarc(raw):
            archive = SarcArchive.parse(raw)
            pending = []
            for entry in archive:
                blob = entry.data
                if is_zstd(blob):
                    pending.append((entry.name, blob))
                    continue
                if self.add(entry.name, blob):
                    count += 1
            # Deuxième passe : entrées compressées (nécessite les dictionnaires ci-dessus)
            for name, blob in pending:
                try:
                    plain = self.decompress(blob)
                except HookyError:
                    continue
                if self.add(name, plain):
                    count += 1
        elif self.add(path.name, raw):
            count += 1
        if count:
            self.sources.append(path)
            self.loaded_files.append(str(path))
        return count

    def load_romfs(self, root) -> tuple[int, Path | None]:
        """Charge tous les dictionnaires trouvés dans un dossier romfs."""
        files, romfs_root = find_dict_files(root)
        count = 0
        errors: list[str] = []
        for path in files:
            try:
                count += self.load_file(path)
            except HookyError as exc:
                errors.append(f"{path.name} : {exc}")
        if errors and not count:
            raise HookyError("Dictionnaires illisibles :\n" + "\n".join(errors))
        return count, romfs_root

    # ------------------------------------------------------------------ (dé)compression
    def _dict_for(self, dict_id: int):
        if not dict_id:
            return None
        found = self.by_id.get(dict_id)
        return found[1] if found else None

    def decompress(self, raw: bytes, dict_id: int | None = None,
                   allow_try_all: bool = True) -> bytes:
        """Décompresse une frame zstd, en trouvant le bon dictionnaire."""
        require_zstd()
        if not is_zstd(raw):
            raise FormatError("Ce fichier n'est pas une frame zstd (.zs).")
        did, _ = frame_info(raw)
        if dict_id is None:
            dict_id = did

        dictionary = self._dict_for(dict_id)
        if dictionary is None and dict_id:
            if not allow_try_all:
                raise DictMissing(
                    f"Dictionnaire zstd id {dict_id} introuvable.\n\n"
                    "Ouvre le dossier romfs (bouton « Choisir romfs… ») : "
                    "Pack/ZsDic.pack.zs est détecté automatiquement."
                )
            # On tente chaque dictionnaire connu (zstd vérifie l'ID, donc pas de
            # décompression silencieusement fausse).
            last_error: Exception | None = None
            for _, candidate in self.by_id.values():
                try:
                    return self._run(raw, candidate)
                except Exception as exc:  # noqa: BLE001 - on essaie le suivant
                    last_error = exc
            if not self.by_id:
                raise DictMissing(
                    f"Ce fichier est compressé avec un dictionnaire zstd (id {dict_id}) "
                    "mais aucun dictionnaire n'est chargé.\n\n"
                    "Clique sur « Choisir romfs… » et sélectionne ton dossier romfs : "
                    "Pack/ZsDic.pack.zs sera trouvé et chargé automatiquement."
                ) from last_error
            raise DictMissing(
                f"Aucun dictionnaire ne correspond à la frame zstd (id {dict_id}).\n"
                "Le dump est peut-être incomplet ou issu d'une autre version du jeu."
            ) from last_error
        return self._run(raw, dictionary)

    @staticmethod
    def _run(raw: bytes, dictionary) -> bytes:
        require_zstd()
        dctx = (zstd.ZstdDecompressor(dict_data=dictionary)
                if dictionary else zstd.ZstdDecompressor())
        try:
            with dctx.stream_reader(io.BytesIO(raw), read_across_frames=True) as reader:
                out = bytearray()
                while True:
                    chunk = reader.read(1 << 20)
                    if not chunk:
                        break
                    out += chunk
                return bytes(out)
        except zstd.ZstdError as exc:
            raise FormatError(f"Flux zstd invalide : {exc}") from exc

    def compress(self, data: bytes, dict_id: int = 0, level: int = DEFAULT_LEVEL) -> bytes:
        """Compresse avec le dictionnaire correspondant (même id que l'original)."""
        require_zstd()
        dictionary = self._dict_for(dict_id)
        if dict_id and dictionary is None:
            raise DictMissing(
                f"Impossible de recompresser : dictionnaire zstd id {dict_id} absent."
            )
        try:
            compressor = (zstd.ZstdCompressor(level=level, dict_data=dictionary)
                          if dictionary else zstd.ZstdCompressor(level=level))
            return compressor.compress(data)
        except zstd.ZstdError as exc:  # pragma: no cover
            raise FormatError(f"Compression zstd impossible : {exc}") from exc

    def roundtrip_ok(self, data: bytes, dict_id: int = 0, level: int = DEFAULT_LEVEL) -> bool:
        try:
            return self.decompress(self.compress(data, dict_id, level)) == data
        except HookyError:
            return False


# --------------------------------------------------------------------------- recherche
_DICT_NAMES = ("zsdic.pack", "zsdic")


def find_dict_files(root) -> tuple[list[Path], Path | None]:
    """Trouve les dictionnaires zstd d'un dump.

    Retourne ``(fichiers, racine_romfs_detectee)``.  La racine détectée est le
    dossier qui contient ``Pack/`` (l'utilisateur peut sélectionner le dossier
    parent du romfs : on gère les deux cas).
    """
    root = Path(root)
    if not root.exists():
        return [], None

    # 1. cas standard : <romfs>/Pack/ZsDic.pack.zs
    for base in (root, root / "romfs"):
        for variant in ("ZsDic.pack.zs", "ZsDic.pack", "zsdic.pack.zs", "zsdic.pack"):
            candidate = base / "Pack" / variant
            if candidate.is_file():
                return [candidate], base

    # 2. recherche limitée en profondeur (dumps rangés différemment)
    hits: list[Path] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - root_depth
        if depth > 4:
            dirnames[:] = []
        for filename in filenames:
            low = filename.lower()
            if low.endswith(".zsdic") or any(low.startswith(n) for n in _DICT_NAMES) \
                    or low == "zsdic.pack.zs":
                path = Path(dirpath) / filename
                if path not in hits:
                    hits.append(path)
        if hits:
            break
    if not hits:
        return [], None
    romfs_root: Path | None = None
    for hit in hits:
        for parent in hit.parents:
            if parent.name.lower() == "romfs":
                romfs_root = parent
                break
            if (parent / "Pack").is_dir():
                romfs_root = parent
                break
        if romfs_root:
            break
    return hits, romfs_root or root


def guess_romfs_root(folder) -> Path | None:
    """Détermine le vrai dossier romfs à partir du dossier choisi par l'utilisateur."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    if (folder / "Pack").is_dir() or (folder / "System").is_dir() or (folder / "Model").is_dir():
        return folder
    sub = folder / "romfs"
    if sub.is_dir():
        return sub
    try:
        children = [p for p in folder.iterdir() if p.is_dir()]
    except OSError:
        return None
    for child in children:
        if (child / "Pack").is_dir() or (child / "System").is_dir():
            return child
    return None
