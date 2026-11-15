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

from . import native
from .sarc import SarcArchive, is_sarc
from .util import DictMissing, FormatError, HookyError

try:  # pragma: no cover - dépendance externe
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
ZSTD_MAGIC_SKIP = b"\x50\x2a\x4d\x18"
DEFAULT_LEVEL = 16


class DecompressionLimit(FormatError):
    """La sortie dépasse le plafond demandé avant son allocation complète."""


def zstd_available() -> bool:
    return zstd is not None


def decoder_backend() -> dict:
    """Diagnostic du chemin ``ZstdDicts``, sans prétendre que toute l'app est Rust."""
    if zstd is None:
        return {"name": "missing", "label": "module zstandard absent",
                "hint": "Le module zstandard reste nécessaire à HookyCreator."}
    if native.available():
        return {"name": "rust", "label": "Rust si compatible, sinon zstandard",
                "hint": "Lecture directe sans dictionnaire et compression : zstandard."}
    return {"name": "zstandard", "label": "zstandard",
            "hint": "Décompression et compression par le module zstandard."}


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


#: Identifiants que le jeu code dans ses frames zstd.  Les dictionnaires sont
#: des dictionnaires « bruts » (``zs.zsdic``…) : zstandard leur attribue l'ID 0,
#: donc l'identifiant se déduit du **nom** du fichier, pas de son contenu.
DICT_IDS = {"zs": 1, "bkprb": 2, "bcett.byml": 3, "pack": 4}


def dict_name_id(name: str) -> int:
    """Identifiant de dictionnaire d'après son nom (0 si inconnu)."""
    base = Path(name).name.strip().lower()
    for suffix in (".zsdic", ".zs", ".dict", ".bin"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return DICT_IDS.get(base, 0)


class ZstdDicts:
    """Collection de dictionnaires zstd, indexés par identifiant de contenu.

    ``require_complete`` refuse les frames tronquées, y compris en fin de flux
    concaténé. Par défaut le repli garde le comportement historique du lecteur.
    """

    def __init__(self, *, require_complete: bool = False) -> None:
        self.by_id: dict[int, tuple[str, "zstd.ZstdCompressionDict"]] = {}
        self.by_name: dict[str, "zstd.ZstdCompressionDict"] = {}
        self.sources: list[Path] = []
        self.loaded_files: list[str] = []
        self.require_complete = require_complete

    # ------------------------------------------------------------------ état
    def __len__(self) -> int:
        return len(self.by_name)

    def __bool__(self) -> bool:
        return bool(self.by_name)

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
        except Exception:
            return False
        base = Path(name).name
        stem = base
        for suffix in (".zsdic", ".zs", ".dict", ".bin"):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
        is_new = stem not in self.by_name
        self.by_name[stem] = dictionary
        did = dictionary.dict_id() or DICT_IDS.get(stem.lower(), 0)
        if did and did not in self.by_id:
            self.by_id[did] = (name, dictionary)
        return is_new

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
        """Dictionnaire d'un identifiant de frame (par contenu, sinon par nom)."""
        if not dict_id:
            return None
        found = self.by_id.get(dict_id)
        if found:
            return found[1]
        for base, known in DICT_IDS.items():
            if known == dict_id and base in self.by_name:
                return self.by_name[base]
        return None

    def decompress(self, raw: bytes, dict_id: int | None = None,
                   allow_try_all: bool = True, *, max_output_size: int | None = None) -> bytes:
        """Décompresse une frame zstd, en trouvant le bon dictionnaire.

        Avec ``max_output_size``, les frames complètes sont vérifiées et la
        sortie est bornée, y compris lorsque sa taille n'est pas annoncée.
        """
        require_zstd()
        if max_output_size is not None and (type(max_output_size) is not int or max_output_size < 1):
            raise ValueError("max_output_size doit être un entier strictement positif")
        if not is_zstd(raw):
            raise FormatError("Ce fichier n'est pas une frame zstd (.zs).")
        did, size = frame_info(raw)
        if max_output_size is not None and size is not None and size > max_output_size:
            raise DecompressionLimit(f"Flux zstd trop volumineux (limite : {max_output_size} octets).")
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
                    return self._run(raw, candidate, max_output_size=max_output_size)
                except DecompressionLimit:
                    raise
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
        return self._run(raw, dictionary, max_output_size=max_output_size)

    def _run(self, raw: bytes, dictionary, *, max_output_size: int | None = None) -> bytes:
        require_zstd()
        # Le chemin natif a sa propre limite globale, pas celle demandée ici.
        if max_output_size is None and native.available():
            # Forcer le dictionnaire choisi pour les éventuelles trames sans
            # id, comme le décodeur zstandard C. Un dictionnaire brut (id 0)
            # ne doit pas être renommé silencieusement si la frame annonce un
            # id non nul : le décodeur C rejetterait ce couple.
            did, _ = frame_info(raw)
            dictionary_id = dictionary.dict_id() if dictionary else 0
            if not dictionary or not did or dictionary_id:
                dict_bytes = dictionary.as_bytes() if dictionary else None
                decoded = native.zstd_decompress(raw, dict_bytes, dictionary_id,
                                                 force_dictionary=bool(dictionary))
                if decoded is not None:
                    return decoded
        # Une limite native, une fonctionnalité non prise en charge ou un
        # moteur absent conservent exactement le comportement existant.
        dctx = (zstd.ZstdDecompressor(dict_data=dictionary)
                if dictionary else zstd.ZstdDecompressor())
        try:
            if self.require_complete or max_output_size is not None:
                # stream_reader accepte silencieusement une fin tronquée et
                # peut rendre des octets partiels. Vérifier la fin de chaque
                # frame, y compris les frames skippables et concaténées.
                out = bytearray()
                offset = 0
                while offset < len(raw):
                    decoder = dctx.decompressobj()
                    while offset < len(raw):
                        # Un petit bloc limite l'allocation intermédiaire lorsque
                        # quelques octets se décompressent en un grand tampon.
                        chunk = raw[offset:offset + (1 << 10)]
                        offset += len(chunk)
                        decoded = decoder.decompress(chunk)
                        if max_output_size is not None and len(decoded) > max_output_size - len(out):
                            raise DecompressionLimit(
                                f"Flux zstd trop volumineux (limite : {max_output_size} octets).")
                        out += decoded
                        if decoder.eof:
                            offset -= len(decoder.unused_data)
                            break
                    if not decoder.eof:
                        raise FormatError("Flux zstd tronqué : fin de frame absente.")
                return bytes(out)
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


#: noms sous lesquels le jeu range ses dictionnaires zstd
DICTIONARY_NAMES = ("ZsDic.pack.zs", "ZsDic.pack", "Zsdic.pack.zs", "ZSDic.pack.zs")


def find_dictionaries_near(path) -> list[Path]:
    """Dictionnaires posés à côté d'un asset (ou un dossier au-dessus).

    Un moddeur travaille sur deux ou trois fichiers extraits, pas sur un romfs
    complet : on regarde donc le dossier du fichier puis ses parents, sans
    descendre dans l'arborescence (pas de scan de 26 Go pour ouvrir un modèle).
    """
    path = Path(path)
    start = path if path.is_dir() else path.parent
    found: list[Path] = []
    # deux niveaux au-dessus suffisent : romfs/Pack/ZsDic.pack.zs, ou le
    # dossier de travail du moddeur. Au-delà, on ramasserait n'importe quoi.
    for folder in [start, *start.parents[:2]]:
        for name in DICTIONARY_NAMES:
            candidate = folder / name
            if candidate.is_file() and candidate not in found:
                found.append(candidate)
        packed = folder / "Pack" / "ZsDic.pack.zs"
        if packed.is_file() and packed not in found:
            found.append(packed)
        if found:
            break
    return found


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
