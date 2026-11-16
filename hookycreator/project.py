"""HookyCreator — le cœur : projet, assets, textures, modèles, mobs, export de mod.

L'idée est celle d'un « MCreator » pour ce jeu : on ouvre un dossier (romfs ou
assets extraits), on voit ce qu'il contient, on modifie des textures, on crée
des variantes de mobs, et on sort un dossier de mod prêt à charger.

Rien n'est magique : chaque opération écrit dans un dossier de travail
(``work/``) et n'écrase **jamais** l'asset d'origine. L'export copie les
fichiers modifiés dans ``mods/<Nom>/romfs/`` avec leur chemin d'origine, ce que
le jeu attend (layeredFS).
"""

from __future__ import annotations

import bisect
import copy
import json
import os
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from hookymod import animation as animation_mod
from hookymod import audio as audio_mod
from hookymod import audio_codec as audio_codec_mod
from hookymod import video as video_mod
from hookymod import bfres as bfres_mod
from hookymod import bntx as bntx_mod
from hookymod import bntx_encode, blender, native, render, zs
from hookymod import texturemaps as texture_maps
from hookymod.texturemaps import ROLE_LABELS, ROLE_ORDER
from hookymod.blender import _base_name
from hookymod.mesh import MeshData
from hookymod.util import HookyError, safe_filename_stem, write_atomic

from .asset_names import is_animation_bfres, is_auxiliary_bfres
from .settings import Settings, SettingsError
from . import cache as cache_mod
from . import export_folders
from . import geo as geo_mod
from . import vram

MODEL_SUFFIXES = (".bfres.zs", ".bfres")
TEXTURE_SUFFIXES = (".bntx.zs", ".bntx")
SOUND_SUFFIXES = (".bwav.zs", ".bwav")
VIDEO_SUFFIXES = (".webm",)
# Garde-fou configurable : 0 désactive le plafond. Une troncature est signalée.
MAX_SCAN_FILES = 200_000
# Cache mémoire de bundles complets glTF / OBJ.
EXPORT_CACHE_BYTES = 64 * 1024 * 1024
# Des instances Project distinctes peuvent partager un dossier de travail.
_EXPORT_FLIGHTS = cache_mod.KeyedLocks()


@dataclass
class Asset:
    """Un fichier du projet, tel qu'il apparaît dans le navigateur."""

    rel: str
    kind: str                       # model | animation | auxiliary | texture | bytecode | archive
    size: int
    compressed: bool = False
    mtime: int = 0                  # sert de version dans les URL de vignettes
    role: str = ""                  # texture seulement : albedo, normal, roughness…

    def as_json(self) -> dict:
        # Les chemins du scan sont déjà normalisés avec « / » sur toutes les
        # plateformes. Éviter des dizaines de milliers d'objets Path au boot.
        data = {"rel": self.rel, "kind": self.kind, "size": self.size,
                "compressed": self.compressed, "name": self.rel.rsplit("/", 1)[-1],
                "mtime": self.mtime}
        if self.role:
            data["role"] = self.role
        return data

    @property
    def stem(self) -> str:
        name = self.rel.rsplit("/", 1)[-1]
        for suffix in (".zs",):
            if name.lower().endswith(suffix):
                name = name[: -len(suffix)]
        return name.rsplit(".", 1)[0]


def _model_export_stem(rel: str) -> str:
    """Nom portable commun aux exports individuels et aux sélections."""
    name = rel.rsplit("/", 1)[-1].split(".", 1)[0]
    return safe_filename_stem(name, fallback="modele")


def _model_export_names(assets: list[Asset]) -> dict[str, str]:
    """Attribue des dossiers distincts, même si un suffixe ressemble à un nom réel."""
    entries = [(asset.rel, _model_export_stem(asset.rel)) for asset in assets]
    counts: dict[str, int] = {}
    for _rel, stem in entries:
        key = stem.casefold()
        counts[key] = counts.get(key, 0) + 1
    # Réserver d'abord tous les noms naturels, y compris les groupes de
    # doublons, afin qu'un suffixe de hachage ne les usurpe pas.
    reserved = set(counts)
    result = {rel: stem for rel, stem in entries if counts[stem.casefold()] == 1}
    used = {stem.casefold() for stem in result.values()}
    for rel, stem in entries:
        if rel in result:
            continue
        base = f"{stem}--{cache_mod.digest(rel)[:8]}"
        name, number = base, 2
        while name.casefold() in reserved or name.casefold() in used:
            name, number = f"{base}-{number}", number + 1
        used.add(name.casefold())
        result[rel] = name
    return result


#: suffixes que le jeu ajoute au nom du modèle pour nommer ses animations
ANIMATION_SUFFIXES = ("_player_animation", "_animation", "_anim")


def _animation_key(name: str) -> str:
    """``Creature_Alpha_Animation.anim.bfres.zs`` → ``creature_alpha``."""
    base = name.split(".")[0].lower()
    for suffix in ANIMATION_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


#: préfixes d'espace de noms qu'un dump ajoute devant les noms
NAMESPACE_TOKENS = ("mt", "tex", "mtx", "texture")

_TOKEN_SPLIT = __import__("re").compile(r"[_\-.\s]+")


def _tokens(text: str) -> list[str]:
    """Découpe un nom en jetons minuscules (``Mt_Armor_Alb.0`` → armor, alb)."""
    return [token for token in _TOKEN_SPLIT.split(text.lower()) if token]


def _texture_base(name: str, role: str | None = None) -> str:
    """Nom d'une texture **sans** son espace de noms ni son suffixe de rôle.

    ``Mt_Weapon_Sword_Example_Alb`` → ``weapon_sword_example`` : c'est la clé
    qui doit rencontrer le nom du modèle, et elle se calcule une seule fois par
    fichier au lieu d'être recalculée pour chaque modèle.
    """
    tokens = _tokens(_png_stem(name))
    while tokens and tokens[0] in NAMESPACE_TOKENS:
        tokens.pop(0)
    if len(tokens) > 1 and (role or texture_maps.classify(name)) != "autre":
        tokens.pop()                     # le dernier jeton est le rôle (Alb, Nrm…)
    return "_".join(tokens)


def _subsample(width: int, height: int, rgba: bytes, limit: int) -> tuple[int, int, bytes]:
    """Réduit une image RGBA par pas entier, sans rééchantillonnage lourd.

    On garde un pixel sur ``n`` : pour une vignette, c'est invisible, et c'est
    mille fois moins coûteux qu'une moyenne en Python pur. Les rangées sont
    copiées par tranches, donc le travail se fait côté C.
    """
    step = max(1, -(-max(width, height) // limit))     # division entière, au-dessus
    # range(0, n, step) contient ceil(n / step) pixels, pas floor.
    new_w = max(1, -(-width // step))
    new_h = max(1, -(-height // step))
    if step == 1:
        return width, height, rgba
    stride = width * 4
    rows: list[bytes] = []
    for y in range(0, height, step):
        row = rgba[y * stride:(y + 1) * stride]
        if step > 1:
            # un pixel sur n, quatre octets par pixel
            row = b"".join(row[x * 4:x * 4 + 4] for x in range(0, width, step))
        rows.append(row)
    return new_w, new_h, b"".join(rows)


class _PrefixIndex:
    """Table « nom → assets » avec recherche par préfixe, sans parcours complet.

    Deux dictionnaires suffisaient avant, mais chaque recherche parcourait
    **toutes** les textures du projet : 600 modèles × 3 600 fichiers coûtaient
    4 s pour afficher la vue Ensembles, et un vrai dump ne s'affichait plus.
    Ici on paie une passe à l'ouverture, puis chaque modèle coûte un
    ``bisect`` — et la table est triée une fois, donc les préfixes se trouvent
    par intervalle et non par comparaison.
    """

    def __init__(self) -> None:
        self.exact: dict[str, list] = {}
        self.keys: list[str] = []
        self._sorted = False

    def add(self, key: str, asset) -> None:
        if not key:
            return
        self.exact.setdefault(key, []).append(asset)
        self._sorted = False

    def build(self) -> None:
        self.keys = sorted(self.exact)
        self._sorted = True

    def get(self, key: str) -> list:
        return self.exact.get(key, [])

    def starting_with(self, prefix: str) -> list:
        """Tous les assets dont la clé commence par ``prefix`` **suivi** d'un ``_``."""
        if not self._sorted:
            self.build()
        if not prefix:
            return []
        start = prefix + "_"
        index = bisect.bisect_left(self.keys, start)
        found: list = []
        while index < len(self.keys) and self.keys[index].startswith(start):
            found.extend(self.exact[self.keys[index]])
            index += 1
        return found


def _png_stem(stem: str) -> str:
    """``Mt_Epee_Alb.0`` → ``Mt_Epee_Alb`` (le numéro de variante ne sert à rien)."""
    head, _, tail = stem.rpartition(".")
    return head if head and tail.isdigit() else stem


def _display_name(stem: str) -> str:
    """``Creature_Alpha.Creature_Alpha`` → ``Creature_Alpha`` (certains fichiers doublent le nom)."""
    head, _, tail = stem.partition(".")
    return head if head and head == tail else stem


#: côté de la table d'échantillonnage (assez pour une couleur par face)
SAMPLER_SIZE = 48


def _sampler_table_python(width: int, height: int, pixels: bytes, size: int) -> bytes:
    """Référence Python : moyenne entière par cellule, alpha nul ignoré."""
    table = bytearray(size * size * 3)
    for y in range(size):
        top = (y * height) // size
        bottom = max(top + 1, ((y + 1) * height) // size)
        for x in range(size):
            left = (x * width) // size
            right = max(left + 1, ((x + 1) * width) // size)
            red = green = blue = count = 0
            for sy in range(top, bottom):
                row = sy * width * 4
                for sx in range(left, right):
                    offset = row + sx * 4
                    if pixels[offset + 3] == 0:
                        continue
                    red += pixels[offset]
                    green += pixels[offset + 1]
                    blue += pixels[offset + 2]
                    count += 1
            index = (y * size + x) * 3
            if count:
                table[index] = red // count
                table[index + 1] = green // count
                table[index + 2] = blue // count
            else:
                table[index] = table[index + 1] = table[index + 2] = 180
    return bytes(table)


def _make_sampler(image, size: int = SAMPLER_SIZE):
    """Fabrique ``(u, v) -> (r, g, b)`` depuis une image RGBA décodée.

    La table Rust reproduit la référence Python ; les entrées non prises en
    charge et les installations sans moteur natif utilisent le repli.
    """
    width, height, pixels = image
    if width < 1 or height < 1 or not pixels:
        raise ValueError("image vide")
    table = native.preview_sample_table(pixels, width, height, size) if native.available() else None
    if table is None:
        table = _sampler_table_python(width, height, pixels, size)

    def sample(u: float, v: float) -> tuple[float, float, float]:
        x = int((u if u > 0.0 else 0.0) * size)
        y = int((v if v > 0.0 else 0.0) * size)
        if x > size - 1:
            x = size - 1
        if y > size - 1:
            y = size - 1
        offset = (y * size + x) * 3
        return (table[offset] / 255.0, table[offset + 1] / 255.0,
                table[offset + 2] / 255.0)

    return sample


class ProjectError(Exception):
    """Erreur lisible par l'interface (aucune trace technique)."""


@dataclass
class Project:
    root: Path
    work: Path | None = None
    assets: list[Asset] = field(default_factory=list)
    _dicts: zs.ZstdDicts | None = None
    _cache: dict = field(default_factory=dict)
    scan_limit: int = field(default=MAX_SCAN_FILES, kw_only=True)  # 0 : illimité

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        if not self.root.is_dir():
            raise ProjectError(f"Dossier introuvable : {self.root}")
        if type(self.scan_limit) is not int or self.scan_limit < 0:
            raise ProjectError("La limite de scan doit être un entier positif ou 0 (illimité).")
        self.work = (Path(self.work) if self.work else self.root / "hookycreator_work")
        self.work = self.work.expanduser().resolve()
        if self.work == self.root:
            raise ProjectError("Le dossier de travail doit être distinct du dossier du projet.")
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / "cache").mkdir(exist_ok=True)
        (self.work / "mobs").mkdir(exist_ok=True)
        self.settings = Settings.load(self.settings_path)
        self.thumbs = cache_mod.ImageCache(self.work / "cache" / "thumbs",
                                           self.settings.cache_mb)
        self.scanned_at = ""
        self.scan_seconds = 0.0
        self.scan_truncated = False
        self.scan_errors = 0
        self.scan_error_examples: list[str] = []
        self._mesh_bytes = 0
        # Plusieurs requêtes pour le même asset partagent le décodage/rendu au
        # lieu de remplir le CPU et d'écrire simultanément le même cache.
        self._cache_lock = threading.RLock()
        self._settings_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._mesh_flights = cache_mod.KeyedLocks()
        self._image_flights = cache_mod.KeyedLocks()
        self._flatten_flights = cache_mod.KeyedLocks()
        self._sampler_flights = cache_mod.KeyedLocks()
        self._edit_flights = cache_mod.KeyedLocks()
        self._export_flights = _EXPORT_FLIGHTS
        self._export_cache: OrderedDict[tuple, tuple] = OrderedDict()
        self._export_cache_bytes = 0
        self._downloads_lock = threading.Lock()
        self._json_flights = cache_mod.KeyedLocks()
        # géométrie décodée : rouvrir un modèle ne doit pas repayer le décodage
        self.geo_folder = self.work / "cache" / "geo"
        self.geo_hits = 0
        self.geo_misses = 0
        # Les diagnostics appartiennent à une version précise du maillage.
        self._reports: dict[tuple, dict] = {}
        self._texture_index: dict[str, list] = {}
        self._map_index: dict[str, dict] = {}
        self._texture_reference_cache: OrderedDict[tuple, tuple] = OrderedDict()
        self._animation_cache: dict[str, list] = {}
        # index « nom → assets » construits à l'ouverture : sans eux, chaque
        # modèle relisait tout le projet (4 s pour 600 modèles, des minutes
        # sur un vrai dump)
        self._by_kind: dict[str, list[Asset]] = {}
        self._model_export_names: dict[str, str] = {}
        self._model_export_reserved: set[str] = set()
        self._model_export_folders: export_folders.ExportFolders | None = None
        self._texture_export_folders: export_folders.ExportFolders | None = None
        self._texture_names = _PrefixIndex()
        self._texture_full_names: dict[str, list[Asset]] = {}
        self._animation_names = _PrefixIndex()
        self._ensembles: list[dict] | None = None
        # état sérialisé : l'interface le demande souvent, et reconstruire
        # l'inventaire complet à chaque chargement ne sert à rien
        self._generation = 0
        # Les URL d'images doivent aussi changer entre deux sessions serveur.
        self._instance_id = uuid.uuid4().hex[:12]
        self._state_json: tuple[tuple, bytes] | None = None
        self._state_lock = threading.Lock()
        self._edited_cache: tuple[float, list[str]] | None = None
        self._json_cache: dict[str, tuple[tuple, bytes]] = {}
        self._stats_cache: tuple[float, dict] | None = None
        # géométrie aplatie pour l'affichage : reconstruite à chaque aperçu,
        # elle coûtait ~1 s sur un modèle de 170 000 faces
        self._render_data: dict[tuple, tuple] = {}
        # Un albédo peut servir à plusieurs modèles : partager sa petite table
        # et borner ce cache évite des décodages et des calculs redondants.
        self._samplers: OrderedDict[str, tuple] = OrderedDict()
        self._albedo_cache: OrderedDict[tuple, object] = OrderedDict()
        self.warm: dict = {"running": False, "done": 0, "total": 0, "error": None,
                           "failed": 0, "cancelled": False, "stopping": False}
        self._warm_thread: threading.Thread | None = None
        self._warm_lock = threading.Lock()
        self._warm_cancel: threading.Event | None = None
        self._dicts_lock = threading.Lock()

    # ------------------------------------------------------------------ réglages
    @property
    def settings_path(self) -> Path:
        return self.work / "settings.json"

    def update_settings(self, payload: dict) -> dict:
        """Applique les changements sans perdre les écritures concurrentes."""
        with self._settings_lock:
            previous = self.settings
            try:
                updated = Settings.from_json(previous.as_json() | payload)
            except SettingsError as error:
                raise ProjectError(str(error)) from None
            updated.save(self.settings_path)
            # Les lecteurs ne doivent pas associer les nouveaux réglages à
            # l'ancienne révision d'images, même si la réduction du cache prend
            # du temps après la publication.
            with self._cache_lock:
                self.settings = updated
                self.bump()
            # Changer une couleur d'interface ne doit ni rescanner le cache
            # disque, ni obliger à redécoder tous les modèles.
            if updated.cache_mb != previous.cache_mb:
                self.thumbs.set_limit_mb(updated.cache_mb)
                if not updated.cache_mb:
                    self.stop_warming()
            if updated.vram_gb < previous.vram_gb:
                with self._cache_lock:
                    self._render_data.clear()
                    budget = max(64 * 1024 * 1024, updated.budget_bytes // 2)
                    if self._mesh_bytes > budget:
                        for key in [key for key in self._cache if key[0] == "meshes"]:
                            self._cache.pop(key, None)
                        self._reports.clear()
                        self._mesh_bytes = 0
            # Les PNG dépendent des réglages via leur clé ; les maillages et les
            # échantillonneurs sont indépendants et restent donc réutilisables.
            return updated.as_json()

    # ------------------------------------------------------------------ fichiers
    def asset(self, rel: str) -> Path:
        """Chemin d'un asset, en refusant toute sortie du projet (``..``)."""
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ProjectError("Chemin hors du projet.") from None
        if not candidate.is_file():
            raise ProjectError(f"Fichier introuvable : {rel}")
        return candidate

    def _edited_path(self, source: Path) -> Path:
        """Destination de travail d'un fichier d'origine déjà validé.

        La version résolue ne doit pas sortir du dossier d'édition, même si le
        chemin fourni est absolu ou si un sous-dossier est un lien symbolique.
        """
        base = (self.work / "assets").resolve()
        candidate = (base / source.relative_to(self.root)).resolve()
        try:
            # Le dossier « assets » lui-même peut aussi être un lien : il ne
            # doit jamais rediriger les modifications hors du travail.
            base.relative_to(self.work)
            candidate.relative_to(base)
        except ValueError:
            raise ProjectError("Chemin hors du dossier de travail.") from None
        if base == self.work:
            raise ProjectError("Le dossier d'assets doit être distinct du dossier de travail.")
        if candidate == source:
            raise ProjectError("La copie de travail ne peut pas remplacer l'original.")
        return candidate

    def edited_asset_path(self, rel: str) -> Path:
        """Chemin de sortie dans le dossier de travail, jamais dans le dump."""
        return self._edited_path(self.asset(rel))

    def _current_asset(self, rel: str) -> tuple[Path, bool]:
        """Copie modifiée si elle existe, sinon fichier d'origine."""
        source = self.asset(rel)
        edited = self._edited_path(source)
        return (edited, True) if edited.is_file() else (source, False)

    def _current_version(self, rel: str) -> tuple[Path, tuple]:
        path, edited = self._current_asset(rel)
        try:
            stat = path.stat()
            # La provenance distingue l'original et la copie ; ctime et identité
            # détectent aussi les remplacements qui conservent taille et mtime
            # lorsque le système de fichiers fournit ces informations.
            stamp = (stat.st_size, stat.st_mtime_ns, edited,
                     stat.st_ctime_ns, stat.st_dev, stat.st_ino)
        except OSError:
            stamp = (0, 0, edited, 0, 0, 0)
        return path, stamp

    def _load_dicts(self) -> zs.ZstdDicts:
        """Dictionnaires zstd, chargés **une seule fois** et jamais à moitié.

        Deux vignettes qui se rendent en parallèle arrivaient ici en même temps :
        la seconde repartait avec un objet encore vide et échouait sur « aucun
        dictionnaire trouvé ». Le verrou publie donc l'objet seulement complet.
        """
        if self._dicts is not None:
            return self._dicts
        with self._dicts_lock:
            if self._dicts is None:
                # Une frame tronquée ne doit pas être interprétée comme un
                # BFRES ou une archive partiellement décompressés.
                dicts = zs.ZstdDicts(require_complete=True)
                if zs.zstd_available():
                    for candidate in zs.find_dictionaries_near(self.root):
                        try:
                            dicts.load_file(candidate)
                        except Exception:  # dictionnaire illisible : on continue
                            pass
                self._dicts = dicts
        return self._dicts

    # -- archives -----------------------------------------------------------
    def open_level(self, rel: str):
        """Ouvre un pack (``.pack.zs``, ``.sarc``) comme une archive navigable."""
        from hookymod.container import Level

        raw = self._current_asset(rel)[0].read_bytes()
        return Level(name=Path(rel).name, raw=raw, dicts=self._load_dicts(), rel=rel)

    def read_entry(self, rel: str, *, decompress: bool = False) -> bytes:
        """Entrée d'archive, par exemple ``pack.zs!/dossier/fichier.bfres.zs``.

        ``!/`` sépare les archives imbriquées ; un slash ordinaire peut faire
        partie du nom d'une entrée SARC. Les chemins imbriqués historiques
        sans second ``!/`` restent acceptés si le nom complet n'existe pas.
        Par défaut les octets finaux restent bruts, comme auparavant.
        """
        archive, separator, inner = rel.partition("!/")
        parts = inner.split("!/") if separator else []
        if not parts or any(not part for part in parts):
            raise ProjectError("Chemin d'entrée vide.")
        level = self.open_level(archive)
        current = archive
        for depth, fragment in enumerate(parts):
            part = fragment
            while True:
                if not level.archive:
                    raise ProjectError(f"{current} n'est pas une archive.")
                index = next((i for i in range(len(level))
                              if level.entry_name(i) == part), None)
                remainder = ""
                if index is None and "/" in part:
                    # Compatibilité avec ``pack!/archive/entrée`` : essayer
                    # d'abord le nom SARC complet, puis l'ancienne notation.
                    part, _, remainder = part.partition("/")
                    index = next((i for i in range(len(level))
                                  if level.entry_name(i) == part), None)
                    if not remainder:
                        raise ProjectError("Chemin d'entrée vide.")
                if index is None:
                    raise ProjectError(f"Entrée introuvable : {part} dans {current}")
                last = depth == len(parts) - 1 and not remainder
                try:
                    if last and not decompress:
                        return level.entry_blob(index)
                    child = level.open_entry(index)
                except HookyError as exc:
                    raise ProjectError(f"Impossible d'ouvrir {part} dans {current} : {exc}") from exc
                current = f"{current}!/{part}"
                if last:
                    return child.data
                level = child
                if not remainder:
                    break
                part = remainder
        raise ProjectError("Chemin d'entrée vide.")

    def read_asset(self, rel: str) -> bytes:
        """Contenu décompressé, en donnant priorité à la copie de travail."""
        if "!/" in rel:
            return self.read_entry(rel, decompress=True)
        return self._read_asset_path(self._current_asset(rel)[0])

    def _read_asset_path(self, path: Path) -> bytes:
        """Lit un chemin déjà validé, éventuellement compressé en zstd."""
        raw = path.read_bytes()
        if not zs.is_zstd(raw):
            return raw
        dict_id, _ = zs.frame_info(raw)
        if dict_id == 0:
            # Frame sans dictionnaire (textures du jeu) : lecture directe.
            import zstandard
            try:
                return zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 28)
            except zstandard.ZstdError as exc:
                raise ProjectError(f"Décompression zstd impossible : {exc}") from exc
        dicts = self._load_dicts()
        if not dicts:
            raise ProjectError(
                "Ce fichier est compressé (zstd, dictionnaire "
                f"id {dict_id}) et aucun dictionnaire n'a été trouvé : place "
                "ZsDic.pack.zs dans le dossier du projet."
            )
        try:
            return dicts.decompress(raw)
        except zs.DictMissing as exc:
            raise ProjectError(str(exc)) from exc

    # ------------------------------------------------------------------ scan
    def scan(self) -> dict:
        """Évite que deux scans simultanés ne publient des index mélangés."""
        with self._scan_lock:
            return self._scan()

    def _scan(self) -> dict:
        """Parcourt le projet **une fois** et indexe ce qui est affichable.

        ``rglob`` construisait un objet ``Path`` par fichier puis rappelait
        ``is_file()`` et ``stat()`` : trois appels système par entrée. Un dump
        complet en contient des centaines de milliers. ``scandir`` donne le nom,
        le type et la taille d'un coup, et le tri final (une seule fois) remplace
        le tri d'avance. Sur un dossier de 200 000 entrées, c'est l'ouverture de
        l'application qui passe de plusieurs secondes à un claquement de doigts.
        """
        started = cache_mod.now_iso()
        started_clock = time.perf_counter()
        assets: list[Asset] = []
        truncated = False
        scan_errors = 0
        error_examples: list[str] = []
        root = str(self.root)

        def note_error(path: str, error: OSError) -> None:
            nonlocal scan_errors
            scan_errors += 1
            if len(error_examples) < 5:
                try:
                    label = Path(path).relative_to(self.root).as_posix()
                except ValueError:
                    label = str(path)
                error_examples.append(f"{label} : {error}")

        # Un --work personnalisé peut être situé à n'importe quelle profondeur
        # sous la racine : ses copies et exports ne sont jamais des sources.
        work_dir = os.path.normcase(str(self.work)) if self.work.is_relative_to(self.root) else None
        stack = [(root, "")]
        while stack and not truncated:
            folder, prefix = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError as exc:
                note_error(folder, exc)
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        name = entry.name
                        # Dossier de travail et mods assemblés : pas des assets.
                        if ((not prefix and name in ("hookycreator_work", "mods"))
                                or (work_dir is not None
                                    and os.path.normcase(entry.path) == work_dir)):
                            continue
                        stack.append((entry.path, f"{prefix}{name}/"))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError as exc:
                    note_error(entry.path, exc)
                    continue
                name = entry.name
                low = name.lower()
                if low.endswith(MODEL_SUFFIXES):
                    # Le suffixe est une indication, pas une inspection du
                    # contenu. Séparer les animations des autres BFRES auxiliaires
                    # évite de compter les squelettes comme des animations liées.
                    if is_animation_bfres(name):
                        kind = "animation"
                    elif is_auxiliary_bfres(name):
                        kind = "auxiliary"
                    else:
                        kind = "model"
                elif low.endswith(TEXTURE_SUFFIXES):
                    kind = "texture"
                elif low.endswith(SOUND_SUFFIXES):
                    kind = "sound"
                elif low.endswith(VIDEO_SUFFIXES):
                    kind = "video"
                elif low.endswith((".byml", ".bgyml", ".bcett.byml")):
                    kind = "bytecode"
                elif low.endswith((".pack.zs", ".pack", ".sarc", ".szs", ".sarc.zs")):
                    kind = "archive"
                else:
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    note_error(entry.path, exc)
                    continue
                assets.append(Asset(rel=f"{prefix}{name}", kind=kind,
                                    size=stat.st_size,
                                    compressed=low.endswith(".zs"),
                                    # Conserver les secondes de l'API historique ;
                                    # les clés internes utilisent les nanosecondes.
                                    mtime=int(stat.st_mtime),
                                    # un `.bntx` est une carte précise (albédo,
                                    # normale, rugosité…) : le dire une fois pour
                                    # toutes évite de le redécouvrir à chaque écran
                                    role=texture_maps.classify(name)
                                    if kind == "texture" else ""))
                if self.scan_limit and len(assets) > self.scan_limit:
                    # Confirmer qu'il reste bien un fichier reconnu avant de
                    # déclarer une troncature (atteindre la limite ne suffit pas).
                    assets.pop()
                    truncated = True
                    break
        assets.sort(key=lambda asset: asset.rel)
        by_kind, textures, animations = self._build_indexes(assets)
        export_names = _model_export_names(by_kind.get("model", []))
        export_reserved = {name.casefold() for name in export_names.values()}
        full_texture_names: dict[str, list[Asset]] = {}
        for texture in by_kind.get("texture", ()):
            full_texture_names.setdefault(_png_stem(texture.stem).casefold(), []).append(texture)
        # Construire sans verrou, puis publier un instantané cohérent en une
        # fois : les autres vues peuvent continuer à lire l'ancien pendant le scan.
        with self._cache_lock:
            self.assets = assets
            self._by_kind = by_kind
            self._model_export_names = export_names
            self._model_export_reserved = export_reserved
            self._texture_names = textures
            self._texture_full_names = full_texture_names
            self._animation_names = animations
            self._texture_index.clear()
            self._map_index.clear()
            self._texture_reference_cache.clear()
            self._animation_cache.clear()
            self.scanned_at = started
            self.scan_seconds = round(time.perf_counter() - started_clock, 3)
            self.scan_truncated = truncated
            self.scan_errors = scan_errors
            self.scan_error_examples = error_examples
            self.bump()
        return {
            "root": str(self.root),
            "work": str(self.work),
            "count": len(assets),
            "truncated": truncated,
            "incomplete": truncated or bool(scan_errors),
            "scan_limit": self.scan_limit,
            "scan_errors": scan_errors,
            "scan_error_examples": error_examples,
            "models": sum(1 for a in assets if a.kind == "model"),
            "animations": sum(1 for a in assets if a.kind == "animation"),
            "auxiliary": sum(1 for a in assets if a.kind == "auxiliary"),
            "textures": sum(1 for a in assets if a.kind == "texture"),
            "sounds": sum(1 for a in assets if a.kind == "sound"),
            "videos": sum(1 for a in assets if a.kind == "video"),
            "bytecode": sum(1 for a in assets if a.kind == "bytecode"),
            "archives": sum(1 for a in assets if a.kind == "archive"),
            "scanned_at": self.scanned_at,
            "scan_seconds": self.scan_seconds,
        }

    # ------------------------------------------------------------------ index
    def _build_indexes(self, assets: list[Asset]) -> tuple[dict[str, list[Asset]], _PrefixIndex, _PrefixIndex]:
        """Une passe sur les assets, puis toutes les recherches sont indexées.

        C'est le correctif de lenteur principal : les vignettes et les fiches
        cherchaient les textures d'un modèle en reparcourant la liste complète
        des fichiers. Le coût était donc **modèles × fichiers** (4 s pour 600
        modèles et 3 600 fichiers, soit des minutes sur un vrai dump). Ici on
        paie une passe une fois, et chaque recherche devient un ``bisect``.
        """
        by_kind: dict[str, list[Asset]] = {}
        for asset in assets:
            by_kind.setdefault(asset.kind, []).append(asset)
        textures = _PrefixIndex()
        animations = _PrefixIndex()
        for asset in by_kind.get("texture", ()):
            textures.add(_texture_base(asset.stem, asset.role), asset)
        for asset in by_kind.get("animation", ()):
            animations.add(_animation_key(Path(asset.rel).name), asset)
        textures.build()
        animations.build()
        return by_kind, textures, animations

    def scan_snapshot(self) -> tuple[tuple[Asset, ...], dict]:
        """Inventaire et diagnostic du même scan, sans attendre son exploitation."""
        with self._cache_lock:
            return tuple(self.assets), {
                "scan_truncated": self.scan_truncated,
                "scan_incomplete": self.scan_truncated or bool(self.scan_errors),
                "scan_limit": self.scan_limit,
                "scan_errors": self.scan_errors,
                "scan_error_examples": list(self.scan_error_examples),
            }

    def by_kind(self, kind: str) -> list[Asset]:
        found = self._by_kind.get(kind)
        if found is None:
            # avant le premier scan (tests, import partiel) : liste vide plutôt
            # qu'une exception, l'interface appelle cette méthode très tôt
            return []
        return found

    def shown_path(self, path: Path) -> str:
        """Chemin lisible : relatif au projet si possible, absolu sinon.

        Le dossier de travail peut être hors du dump (c'est même recommandé) :
        ``Path.relative_to`` lèverait alors une erreur au lieu d'afficher un
        chemin, ce qui cassait l'export.
        """
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    # ------------------------------------------------------------------ cache
    def _version(self, rel: str) -> tuple:
        """Taille, dates, identité et provenance de la version courante de l'asset."""
        return self._current_version(rel)[1]

    def _render_digest(self) -> str:
        settings = self.settings
        # « couleurs v2 » : une couleur par matériau (les vignettes d'avant,
        # colorées par sous-maillage, ne doivent pas être réutilisées)
        return cache_mod.digest(("rendu", "couleurs-v4-pose", settings.supersample,
                                 settings.cull, settings.frustum,
                                 settings.max_faces, settings.thumb_faces,
                                 settings.preview_faces, settings.vram_gb,
                                 settings.adaptive, settings.lod))

    def _store_mesh(self, key, meshes, meta) -> list[MeshData]:
        cost = cache_mod.mesh_bytes(meshes)
        budget = max(64 * 1024 * 1024, self.settings.budget_bytes // 2)
        with self._cache_lock:
            if self._mesh_bytes + cost > budget and self._cache:
                # Budget atteint : jeter les maillages décodés, pas le disque.
                for old_key in [k for k in self._cache if k[0] == "meshes"]:
                    self._cache.pop(old_key, None)
                self._reports.clear()
                self._mesh_bytes = 0
            self._cache[key] = meshes
            self._reports[key] = meta
            self._mesh_bytes += cost
        return meshes

    def _albedo_bindings(self, rel: str):
        meshes, meta, _ = self._model_mesh_entry(rel)
        references = meta.get("material_textures") or {}
        bindings = []
        if references:
            resolved, _warnings = self._material_reference_sources(references)
            for material, roles in resolved.items():
                choices = {(asset.rel, index): (asset, index)
                           for asset, index, _name in roles.get("albedo", [])}
                if len(choices) == 1:
                    asset, index = next(iter(choices.values()))
                    bindings.append((material, asset, index, self._version(asset.rel)))
        else:
            # Sans référence FMAT, ne colorer que le seul matériau d'un modèle
            # qui n'a qu'un seul candidat albédo.
            materials = {mesh.material or "" for mesh in meshes}
            assets = self.texture_assets_for(rel)
            if len(materials) == 1 and len(assets) == 1:
                asset = assets[0]
                bindings.append((next(iter(materials)), asset, 0, self._version(asset.rel)))
        return bindings

    def _albedo_assets_and_stamps(self, rel: str):
        return [(asset, version) for _material, asset, _index, version in self._albedo_bindings(rel)]

    def albedo_samplers(self, rel: str) -> dict:
        """Échantillonne les albédos résolus pour chaque matériau, sans repli global ambigu."""
        bindings = self._albedo_bindings(rel)
        stamp = tuple((material, asset.rel, index, version) for material, asset, index, version in bindings)
        with self._cache_lock:
            cached = self._samplers.get(rel)
            if cached is not None and cached[0] == stamp:
                self._samplers.move_to_end(rel)
                return cached[1]
        samplers: dict = {}
        meshes, meta, _ = self._model_mesh_entry(rel)
        structured_references = bool(meta.get("material_textures"))
        for material, asset, index, version in bindings:
            key = (asset.rel, index, version)
            try:
                with self._sampler_flights.hold(key):
                    with self._cache_lock:
                        sampler = self._albedo_cache.get(key)
                        if sampler is not None:
                            self._albedo_cache.move_to_end(key)
                    if sampler is None:
                        file = self.texture_file(asset.rel)
                        texture = next((item for item in file.textures if item.index == index), None)
                        if texture is None:
                            continue
                        # BNTX fournit déjà du RGBA : pas de détour par un PNG.
                        sampler = _make_sampler(texture.decode(0))
                        if self._version(asset.rel) == version:
                            with self._cache_lock:
                                self._albedo_cache[key] = sampler
                                self._albedo_cache.move_to_end(key)
                                while len(self._albedo_cache) > 128:
                                    self._albedo_cache.popitem(last=False)
            except Exception:          # texture illisible : on garde la palette
                continue
            samplers[material] = sampler
            # Les alias historiques ne doivent pas écraser le nom d'un autre
            # matériau quand les références FMAT existent.
            if not structured_references:
                samplers[_base_name(asset.stem)] = sampler
                samplers[asset.stem] = sampler
        materials = {mesh.material or "" for mesh in meshes}
        if len(materials) == 1 and next(iter(materials)) in samplers:
            samplers[""] = samplers[next(iter(materials))]
        now = tuple((material, asset.rel, index, version)
                    for material, asset, index, version in self._albedo_bindings(rel))
        if now == stamp:
            with self._cache_lock:
                self._samplers[rel] = (stamp, samplers)
                self._samplers.move_to_end(rel)
                while len(self._samplers) > 128:
                    self._samplers.popitem(last=False)
        return samplers

    def thumbnail_key(self, rel: str, width: int, height: int,
                      yaw: float = 0.7, pitch: float = -0.35,
                      views: int = 1, face_limit: int | None = None,
                      textured: bool = False) -> str:
        """Clé de cache d'une vignette (identique pour l'aperçu et le préchauffage).

        ``textured`` fait partie de la clé : la vignette colorée par les textures
        n'écrase pas celle qui montre la géométrie nue.
        """
        textures = ""
        if textured:
            stamps = [(material, asset.rel, index, version)
                      for material, asset, index, version in self._albedo_bindings(rel)]
            textures = cache_mod.digest(("textures", stamps))
        return cache_mod.digest(("apercu", rel, self._version(rel), width, height,
                                 round(yaw, 2), round(pitch, 2), views,
                                 self.settings.preview_faces if face_limit is None
                                 else face_limit,
                                 self._render_digest(), textures))

    def thumbnail_cached(self, rel: str, width: int, height: int,
                         textured: bool = False) -> bool:
        try:
            key = self.thumbnail_key(rel, width, height, face_limit=0, textured=textured)
        except ProjectError:
            return False
        return self.thumbs.get(key) is not None

    def warm_status(self) -> dict:
        with self._warm_lock:
            return dict(self.warm)

    def stop_warming(self) -> dict:
        """Arrête les prochains modèles ; un rendu déjà en cours finit normalement."""
        with self._warm_lock:
            if self._warm_cancel is not None and self.warm.get("running"):
                self._warm_cancel.set()
                self.warm["stopping"] = True
            return dict(self.warm)

    def warm_thumbnails(self, width: int = 320, height: int = 240,
                        limit: int | None = None, workers: int = 1, *,
                        background_slot=None, render_slots=None, visible_activity=None) -> dict:
        """Préchauffage explicite et annulable, jamais lancé automatiquement.

        ``workers`` reste accepté pour compatibilité mais un seul fil daemon est
        utilisé. Les sémaphores du serveur réservent une place aux actions
        interactives. L'annulation s'applique entre deux rendus, pas au milieu.
        """
        with self._warm_lock:
            if self.warm.get("running"):
                return dict(self.warm)
            if not self.settings.cache_mb:
                raise ProjectError("Active le cache de vignettes avant de le préparer.")
            models = [asset.rel for asset in self.by_kind("model")]
            if limit is not None:
                models = models[:max(0, limit)]
            state = {"running": bool(models), "done": 0, "total": len(models),
                     "error": None, "failed": 0, "cancelled": False, "stopping": False}
            self.warm = state
            cancelled = threading.Event()
            self._warm_cancel = cancelled
            if not models:
                return dict(state)

            def acquire(slot) -> bool:
                if slot is None:
                    return not cancelled.is_set()
                while not cancelled.is_set():
                    if slot.acquire(timeout=0.1):
                        return True
                return False

            def run() -> None:
                try:
                    for rel in models:
                        if cancelled.is_set():
                            break
                        while not cancelled.is_set():
                            # Une vue active a priorité ; ne pas la faire attendre
                            # derrière toute la préparation du cache.
                            if visible_activity and not visible_activity.wait_idle(cancelled):
                                break
                            if not acquire(background_slot):
                                break
                            got_render = False
                            try:
                                if visible_activity and not visible_activity.idle():
                                    continue
                                got_render = acquire(render_slots)
                                if not got_render or cancelled.is_set():
                                    break
                                if visible_activity and not visible_activity.idle():
                                    continue
                                try:
                                    textured = bool(self.settings.textured_previews)
                                    if not self.thumbnail_cached(rel, width, height, textured=textured):
                                        self.model_preview(rel, width, height,
                                                           face_limit=0, textured=textured)
                                except Exception as exc:  # un échec ne casse pas la série
                                    with self._warm_lock:
                                        state["failed"] += 1
                                        state["error"] = str(exc)
                                finally:
                                    with self._warm_lock:
                                        state["done"] += 1
                                break
                            finally:
                                if got_render and render_slots is not None:
                                    render_slots.release()
                                if background_slot is not None:
                                    background_slot.release()
                finally:
                    with self._warm_lock:
                        state["running"] = False
                        state["cancelled"] = cancelled.is_set()
                        state["stopping"] = False

            self._warm_thread = threading.Thread(target=run, daemon=True,
                                                 name="hookycreator-thumbnails")
            self._warm_thread.start()
            return dict(state)

    def geo_stats(self) -> dict:
        """Ce que le cache de géométrie occupe (utile pour vider sans se tromper)."""
        files = 0
        size = 0
        if self.geo_folder.is_dir():
            for path in self.geo_folder.glob("*.geo"):
                files += 1
                try:
                    size += path.stat().st_size
                except OSError:
                    pass
        return {"entries": files, "bytes": size, "folder": str(self.geo_folder),
                "hits": self.geo_hits, "misses": self.geo_misses}

    def cache_stats(self, max_age: float = 0.0) -> dict:
        """Statistiques de cache ; ``max_age`` évite de relire le disque sans arrêt.

        L'interface les demande avec chaque état : sans mémo, chaque réponse
        relisait le dossier de géométrie (des milliers de fichiers).
        """
        with self._cache_lock:
            generation = self._generation
            cached = self._stats_cache
        if max_age and cached and time.monotonic() - cached[0] < max_age:
            return cached[1]
        stats = self._cache_stats()
        with self._cache_lock:
            if generation == self._generation:
                self._stats_cache = (time.monotonic(), stats)
        return stats

    def _cache_stats(self) -> dict:
        with self._cache_lock:
            meshes = sum(1 for key in self._cache if key[0] == "meshes")
            mesh_bytes = self._mesh_bytes
        return {
            "thumbs": self.thumbs.stats(),
            "geo": self.geo_stats(),
            "meshes": {"entries": meshes, "bytes": mesh_bytes,
                       "budget_bytes": max(64 * 1024 * 1024,
                                           self.settings.budget_bytes // 2)},
            "rss_bytes": cache_mod.rss_bytes(),
            "cache_mb": self.settings.cache_mb,
            "scanned_at": self.scanned_at,
            "scan_seconds": self.scan_seconds,
            "warm": self.warm_status(),
        }

    def clear_cache(self) -> dict:
        """Vide vignettes, géométrie et maillages en mémoire."""
        with self._cache_lock:
            self._cache.clear()
            self._mesh_bytes = 0
            self._reports.clear()
            self._render_data.clear()
            self._samplers.clear()
            self._albedo_cache.clear()
            self._export_cache.clear()
            self._export_cache_bytes = 0
        removed = 0
        if self.geo_folder.is_dir():
            for path in self.geo_folder.glob("*.geo"):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        result = self.thumbs.clear()
        result["geo_removed"] = removed
        return result

    # ------------------------------------------------------------------ validation progressive
    def check_asset(self, rel: str, kind: str) -> dict:
        """Vérifie le décodage réel d'un seul candidat, sans scanner tout le dump.

        Un nom ou un en-tête ne suffit pas : les modèles sont construits et la
        texture de niveau 0 est décodée. Une erreur ne rend jamais l'asset visible.
        Le contrôle reste une preuve de décodage, pas de fidélité visuelle en jeu.
        """
        if kind not in {"model", "texture", "sound"}:
            raise ProjectError("Type d'asset à vérifier inconnu.")
        name = Path(rel).name
        low = name.lower()
        if ((kind == "model" and (not low.endswith(MODEL_SUFFIXES)
                                  or is_auxiliary_bfres(name) or is_animation_bfres(name)))
                or (kind == "texture" and not low.endswith(TEXTURE_SUFFIXES))
                or (kind == "sound" and not low.endswith(SOUND_SUFFIXES))):
            return {"kind": kind, "rel": rel, "valid": False, "reason": "Type non pris en charge."}
        try:
            before = self._version(rel)
            if kind == "model":
                meshes, meta, stamp = self._model_mesh_entry(rel)
                if not any(mesh.positions and mesh.faces for mesh in meshes):
                    raise ProjectError("Aucun maillage avec des faces exploitables.")
                # Valider aussi le chemin de rendu, pas seulement l'en-tête BFRES.
                self.model_preview(rel, 160, 120, face_limit=2000, textured=False)
                injectable = meta.get("injectable")
                if injectable is None or injectable:
                    # La présence d'une forme ne suffit pas : la réécriture OBJ/glTF
                    # ne sait pas préserver toutes les structures BFRES.
                    from .model_target import why_not_model_target
                    injectable = not why_not_model_target(self.read_asset(rel))
            elif kind == "texture":
                # max_size=None force le niveau 0, également utilisé à l'export.
                self.texture_png(rel, 0)
                stamp, injectable = before, None
            else:
                # Vérifie le conteneur et les données audio décodables, pas la
                # compatibilité avec un jeu ou un lecteur spécifique.
                audio_codec_mod.decode(self._read_sound(rel))
                stamp, injectable = before, None
            if before != stamp or self._version(rel) != before:
                return {"kind": kind, "rel": rel, "valid": False,
                        "retry": True, "reason": "Fichier modifié pendant la vérification."}
            return {"kind": kind, "rel": rel, "valid": True, "injectable": injectable}
        except (ProjectError, HookyError, OSError, ValueError, IndexError) as exc:
            return {"kind": kind, "rel": rel, "valid": False, "reason": str(exc)}

    # ------------------------------------------------------------------ animations
    def _read_animation(self, rel: str) -> bytes:
        if not is_animation_bfres(rel):
            raise ProjectError("Seul un BFRES d'animation peut être ouvert ici.")
        path, _ = self._current_asset(rel)
        limit = animation_mod.MAX_BYTES
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ProjectError("Animation trop volumineuse (64 Mio maximum).")
        if not zs.is_zstd(raw):
            return raw
        dict_id, _ = zs.frame_info(raw)
        dictionaries = self._load_dicts() if dict_id else zs.ZstdDicts(require_complete=True)
        if dict_id and not dictionaries:
            raise ProjectError(f"Dictionnaire zstd id {dict_id} introuvable pour cette animation.")
        try:
            return dictionaries.decompress(raw, max_output_size=limit)
        except zs.DecompressionLimit as exc:
            raise ProjectError("Animation décompressée trop volumineuse (64 Mio maximum).") from exc

    def animation_list(self, rel: str) -> dict:
        before = self._version(rel)
        clips = animation_mod.list_clips(self._read_animation(rel))
        if before != self._version(rel):
            raise ProjectError("L'animation a changé pendant la lecture ; recommence.")
        return {"rel": rel, "clips": clips}

    def animation_clip(self, rel: str, index: int) -> dict:
        before = self._version(rel)
        clip = animation_mod.read_clip(self._read_animation(rel), index)
        if before != self._version(rel):
            raise ProjectError("L'animation a changé pendant la lecture ; recommence.")
        return {"rel": rel, **clip}

    # ------------------------------------------------------------------ audio
    def _read_sound(self, rel: str) -> bytes:
        if not rel.lower().endswith(SOUND_SUFFIXES):
            raise ProjectError("Seul un fichier BWAV peut être ouvert ici.")
        path, _edited = self._current_asset(rel)
        limit = audio_mod.MAX_BWAV_BYTES
        # Lire au maximum la limite, même si le fichier grandit entre stat et
        # read ; une petite frame zstd peut elle aussi cacher un son énorme.
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ProjectError("Son trop volumineux pour cette première conversion (64 Mio maximum).")
        if not zs.is_zstd(raw):
            return raw
        dict_id, _size = zs.frame_info(raw)
        dictionaries = self._load_dicts() if dict_id else zs.ZstdDicts(require_complete=True)
        if dict_id and not dictionaries:
            raise ProjectError(f"Dictionnaire zstd id {dict_id} introuvable pour ce son.")
        try:
            return dictionaries.decompress(raw, max_output_size=limit)
        except zs.DecompressionLimit as exc:
            raise ProjectError("Son décompressé trop volumineux (64 Mio maximum).") from exc

    def sound_info(self, rel: str) -> dict:
        return {"rel": rel, **audio_codec_mod.info(self._read_sound(rel))}

    def sound_ogg(self, rel: str) -> bytes:
        return audio_mod.to_ogg_opus(self._read_sound(rel))[0]

    def sound_convert(self, rel: str, fmt: str) -> tuple[bytes, str, str]:
        return audio_codec_mod.convert(self._read_sound(rel), fmt)

    def import_sound(self, rel: str, data: bytes) -> dict:
        """Replace an existing sound by an experimental PCM16 working copy.

        No BARS entry, event reference, RESTBL or original asset is rewritten.
        The source audio must match the target's channel count and sample rate;
        a shorter replacement that would invalidate an existing loop is refused.
        """
        source = self.asset(rel)
        with self._edit_flights.hold(source):
            before = self._version(rel)
            template = self.sound_info(rel)
            pcm = audio_codec_mod.read_import(data, template["sample_rate"], template["channels"])
            result = audio_codec_mod.encode_bwav_pcm(pcm, template["sample_rate"],
                                                      template["loop_start"], template["loop_end"])
            if self._version(rel) != before:
                raise ProjectError("Le son cible a changé pendant l'import ; recommence.")
            payload = result
            if rel.lower().endswith(".zs"):
                with source.open("rb") as stream:
                    original = stream.read(audio_mod.MAX_BWAV_BYTES + 1)
                if len(original) > audio_mod.MAX_BWAV_BYTES:
                    raise ProjectError("Son source trop volumineux (64 Mio maximum).")
                if not zs.is_zstd(original):
                    raise ProjectError("Le son cible .zs n'est pas une frame zstd valide.")
                original_id, _ = zs.frame_info(original)
                payload = self._load_dicts().compress(result, dict_id=original_id)
            target = self.edited_asset_path(rel)
            write_atomic(target, payload)
            self.bump()
            return {"rel": rel, "wrote": self.shown_path(target), "size": len(payload),
                    "format": "BWAV PCM16", "samples": len(pcm),
                    "warning": "Copie expérimentale : aucune validation en jeu, BARS ou RESTBL."}

    # ------------------------------------------------------------------ video
    def video_file(self, rel: str) -> Path:
        """Return a validated current WebM path for streaming, never NDVE data."""
        if not rel.lower().endswith(VIDEO_SUFFIXES):
            raise ProjectError("Seul un fichier WebM peut être ouvert ici.")
        path, _edited = self._current_asset(rel)
        with path.open("rb") as source:
            size = os.fstat(source.fileno()).st_size
            video_mod.check_header(source.read(video_mod.MAX_HEADER), size)
        return path

    def video_info(self, rel: str) -> dict:
        before = self._version(rel)
        path = self.video_file(rel)
        size = path.stat().st_size
        if before != self._version(rel):
            raise ProjectError("La vidéo a changé pendant la lecture ; recommence.")
        return {"rel": rel, "size": size, "format": "WebM", "edited": path != self.asset(rel),
                "warning": "Conteneur contrôlé uniquement ; codecs et compatibilité en jeu non vérifiés."}

    def import_video(self, rel: str, data: bytes) -> dict:
        """Copy a user-supplied WebM verbatim; never rewrite NDVE or the original."""
        if not data or len(data) > video_mod.MAX_IMPORT:
            raise ProjectError("Vidéo vide ou trop volumineuse (32 Mio maximum).")
        video_mod.check_header(data[:video_mod.MAX_HEADER], len(data))
        source = self.asset(rel)
        with self._edit_flights.hold(source):
            before = self._version(rel)
            self.video_file(rel)
            if self._version(rel) != before:
                raise ProjectError("La vidéo cible a changé pendant l'import ; recommence.")
            target = self.edited_asset_path(rel)
            write_atomic(target, data)
            self.bump()
            return {"rel": rel, "wrote": self.shown_path(target), "size": len(data),
                    "warning": "Copie expérimentale : codecs, NDVE et compatibilité en jeu non validés."}

    # ------------------------------------------------------------------ textures
    def texture_file(self, rel: str) -> bntx_mod.BntxFile:
        data = self.read_asset(rel)
        if not bntx_mod.is_bntx(data):
            raise ProjectError("Ce fichier n'est pas un BNTX.")
        return bntx_mod.BntxFile.parse(data, Path(rel).name)

    def texture_info(self, rel: str) -> dict:
        file = self.texture_file(rel)
        entries = []
        for texture in file.textures:
            entries.append({
                "index": texture.index,
                "name": texture.name or f"texture_{texture.index}",
                "format": texture.format_name,
                "format_code": texture.format_code,
                "width": texture.width,
                "height": texture.height,
                "mips": texture.mip_count,
                "editable": bntx_encode.can_encode_format(texture.format_code),
                "hint": bntx_encode.format_hint(texture.format_code),
            })
        return {"rel": rel, "revision": cache_mod.digest(self._version(rel)),
                "version": file.version_label, "orin": file.orin,
                "count": len(entries), "textures": entries}

    def texture_png(self, rel: str, index: int = 0, mip: int = 0,
                    max_size: int | None = None) -> bytes:
        """PNG d'une texture, éventuellement réduit pour une vignette.

        Une texture de 1024² pèse plusieurs centaines de kilo-octets : la
        grille en affiche 48 dans des tuiles de 160 px. Servir la taille pleine
        coûtait des dizaines de mégaoctets et autant de décodages dans le
        navigateur, pour des pixels invisibles.
        """
        limit = None if not max_size else max(16, min(2048, int(max_size)))
        stamp = self._version(rel)
        key = cache_mod.digest(("texture", rel, stamp, index, mip, limit))
        with self._image_flights.hold(("texture", key)):
            cached = self.thumbs.get(key)
            if cached:
                return cached
            file = self.texture_file(rel)
            texture = file.textures[index] if 0 <= index < len(file.textures) else None
            if texture is None:
                raise ProjectError(f"Texture {index} absente de {rel}.")
            if not limit:
                png = texture.to_png(mip)
            else:
                # le mip le plus proche d'abord (gratuit), puis réduction par pas
                width, height, rgba = texture.decode(mip, max_size=limit)
                if max(width, height) > limit:
                    width, height, rgba = _subsample(width, height, rgba, limit)
                from hookymod.util import png_bytes

                png = png_bytes(width, height, rgba)
            if self._version(rel) == stamp:
                self.thumbs.put(key, png)
            return png

    def export_texture(self, rel: str, index: int = 0) -> dict:
        """PNG séparé par conteneur et par index, sans écraser un autre export."""
        png = self.texture_png(rel, index)
        source = self.asset(rel).relative_to(self.root).as_posix()
        stem = safe_filename_stem(Asset(source, "texture", 0).stem, fallback="texture")
        base = self.work / "export_textures"
        resolved = base.resolve()
        if resolved == self.work or not resolved.is_relative_to(self.work):
            raise ProjectError("Le dossier d'export sort du dossier de travail.")
        with self._cache_lock:
            if self._texture_export_folders is None:
                self._texture_export_folders = export_folders.ExportFolders(base, self.root, "texture")
            registry = self._texture_export_folders
        try:
            folder = registry.claim(source, stem, f"{stem}--{cache_mod.digest(source)[:8]}")
            target = folder / f"{folder.name}--{index}.png"
            with self._export_flights.hold(os.path.normcase(str(folder.resolve()))):
                current_base = base.resolve()
                if (current_base == self.work or not current_base.is_relative_to(self.work)
                        or not folder.resolve().is_relative_to(current_base)
                        or not registry.owns(folder, source)):
                    raise ProjectError("Le dossier d'export a changé ou n'appartient plus à cette texture.")
                write_atomic(target, png)
        except OSError as exc:
            raise ProjectError(f"Impossible d'écrire le PNG : {exc}") from exc
        return {"file": self.shown_path(target), "absolute_file": str(target), "index": index}

    def import_texture(self, rel: str, index: int, image: bytes) -> dict:
        """Remplace les pixels d'une texture (même format, mêmes mips)."""
        # Deux imports simultanés d'un même BNTX ne doivent pas se perdre.
        with self._edit_flights.hold(self.asset(rel)):
            return self._import_texture(rel, index, image)

    def _import_texture(self, rel: str, index: int, image: bytes) -> dict:
        from .pngread import decode_image

        width, height, rgba = decode_image(image)
        template = self.read_asset(rel)
        name = self.texture_info(rel)["textures"][index]["name"]
        try:
            updated, report = bntx_encode.replace_texture(
                template, rgba, width, height, name=name, index=index)
        except Exception as exc:
            raise ProjectError(f"Remplacement impossible ({name}) : {exc}") from exc

        target = self.edited_asset_path(rel)
        payload = updated
        if Path(rel).name.lower().endswith(".zs"):
            # On recompresse *comme à l'origine* : les textures du jeu sont des
            # frames sans dictionnaire (dict_id 0), les modèles en utilisent un.
            original_id, _ = zs.frame_info(self.asset(rel).read_bytes())
            dicts = self._load_dicts()
            try:
                payload = dicts.compress(updated, dict_id=original_id)
            except zs.DictMissing as exc:
                raise ProjectError(str(exc)) from exc
        from hookymod.util import write_atomic

        write_atomic(target, payload)
        with self._cache_lock:
            self._cache.pop(("png", rel, index), None)
        self.bump()
        return {"rel": rel, "texture": name, "wrote": self.shown_path(target),
                "size": len(payload), "mips": getattr(report, "mip_count", None),
                "format": self.texture_info(rel)["textures"][index]["format"]}

    # ------------------------------------------------------------------ modèles
    def model_info(self, rel: str) -> dict:
        """Fiche d'un modèle.

        La géométrie vient du même chemin que l'aperçu (mémoire ou cache
        disque) : auparavant cette fiche **redécodait** tout le BFRES, ce qui
        doublait le temps d'un simple clic (4,97 s sur une armure).
        """
        meshes, meta, stamp = self._model_mesh_entry(rel)
        points = [point for mesh in meshes for point in mesh.positions]
        lowest = [min(point[i] for point in points) for i in range(3)]
        highest = [max(point[i] for point in points) for i in range(3)]
        references = meta.get("material_textures") or {}
        listing = []
        reference_warnings = []
        if references:
            resolved, reference_warnings = self._material_reference_sources(references)
            seen = set()
            for material, roles in resolved.items():
                for role, entries in roles.items():
                    for asset, index, reference in entries:
                        identity = (material, asset.rel, index)
                        if identity not in seen:
                            seen.add(identity)
                            listing.append({"rel": asset.rel, "name": reference, "index": index,
                                            "size": asset.size, "mtime": asset.mtime,
                                            "role": role, "label": ROLE_LABELS.get(role, "?"),
                                            "material": material, "match": "référence"})
        else:
            listing = [{"rel": asset.rel, "name": asset.stem, "index": 0,
                        "size": asset.size, "mtime": asset.mtime, "role": role,
                        "label": ROLE_LABELS.get(role, "?"), "match": "candidat"}
                       for role, assets in self.texture_maps_for(rel).items() for asset in assets[:4]]
        return {
            "rel": rel,
            "revision": cache_mod.digest(stamp, tuple(
                (asset.rel, version) for asset, version in self._albedo_assets_and_stamps(rel))),
            "version": meta.get("version") or self._version_label(rel),
            "models": [meta["model"]] if meta.get("model") else [Path(rel).stem],
            "meshes": len(meshes),
            "submeshes": meta.get("submeshes", len(meshes)),
            "dropped": meta.get("dropped", 0),
            "faces": sum(len(m.faces) for m in meshes),
            "vertices": sum(len(m.positions) for m in meshes),
            "materials": meta.get("materials") or sorted({m.material for m in meshes if m.material}),
            "bounds": [round(highest[i] - lowest[i], 4) for i in range(3)],
            "warnings": (meta.get("warnings", []) + reference_warnings)[:24],
            "textures": listing[:128],
            "texture_match": "références du matériau" if references else "candidats par nom",
            "animations": [{"rel": asset.rel, "name": asset.stem,
                            "size": asset.size}
                           for asset in self.animations_for(rel)][:32],
            "textured": bool(self._albedo_bindings(rel)
                             and self.settings.textured_previews),
        }

    def ensembles(self) -> list[dict]:
        """Modèles avec leurs textures et leurs animations, prêts à être vus entiers.

        C'est la vue « Ensembles » : un modèle du jeu n'est jamais seul — ses
        textures vivent dans un autre dossier et ses animations dans un
        ``*.anim.bfres``. On les réunit ici sans rien décoder (le nom suffit),
        pour que la liste s'affiche instantanément même sur un gros dump.
        """
        with self._cache_lock:
            cached = self._ensembles
            generation = self._generation
        if cached is not None:
            return cached
        rows = []
        for asset in self.by_kind("model"):
            textures = self.texture_assets_for(asset.rel)
            animations = self.animations_for(asset.rel)
            rows.append({
                "rel": asset.rel,
                "name": _display_name(asset.stem),
                "size": asset.size,
                "mtime": asset.mtime,
                "textures": len(textures),
                "animations": len(animations),
                "textured": bool(textures),
                "complete": bool(textures and animations),
            })
        with self._cache_lock:
            # Un scan en parallèle ne doit pas réinstaller l'ancienne liste.
            if self._generation == generation:
                self._ensembles = rows
        return rows

    def _version_label(self, rel: str) -> str:
        data = self.read_asset(rel)[:12]
        if data[:4] != b"FRES" or len(data) < 12:
            return ""
        value = int.from_bytes(data[8:12], "little")
        return f"{value >> 16}.{(value >> 8) & 0xFF}.{value & 0xFF}"

    def _geo_path(self, rel: str):
        return self.geo_folder / f"{cache_mod.digest(rel, *self._version(rel))}.geo"

    def _model_meshes(self, rel: str) -> list[MeshData]:
        """Mémoire, disque, puis décodage : un seul décodage par version active."""
        return self._model_mesh_entry(rel)[0]

    def _model_mesh_entry(self, rel: str) -> tuple[list[MeshData], dict, tuple]:
        source, stamp = self._current_version(rel)
        key = ("meshes", rel, stamp)
        with self._mesh_flights.hold(key):
            with self._cache_lock:
                cached = self._cache.get(key)
                meta = self._reports.get(key, {})
            if cached is not None:
                return cached, meta, stamp
            meshes, meta = self._load_model_meshes(rel, key, source)
            return meshes, meta, stamp

    def _load_model_meshes(self, rel: str, key: tuple, source: Path) -> tuple[list[MeshData], dict]:
        path = self.geo_folder / f"{cache_mod.digest(rel, *key[2])}.geo"
        cached = geo_mod.read(path) if path.is_file() else None
        if cached is not None:
            meshes, meta = cached
            # Les caches GEO antérieurs contenaient un avertissement devenu
            # faux lorsque l'export reconstruit explicitement un clip compatible.
            legacy = "Squelette et animation non exportés : géométrie en pose statique non garantie."
            current = "Décodage géométrique seul : pose statique non garantie ; squelette et animation à associer séparément."
            if legacy in meta.get("warnings", []):
                meta = {**meta, "warnings": [current if note == legacy else note for note in meta["warnings"]]}
            with self._cache_lock:
                self.geo_hits += 1
            self._store_mesh(key, meshes, meta)
            return meshes, meta
        with self._cache_lock:
            self.geo_misses += 1
        # Lire le chemin correspondant à la version choisie : une nouvelle
        # copie de travail peut apparaître pendant le décodage en arrière-plan.
        try:
            data = self._read_asset_path(source)
        except (ProjectError, HookyError, OSError) as exc:
            raise ProjectError(f"Impossible de lire le modèle « {rel} » : {exc}") from exc
        try:
            file = bfres_mod.BfresFile.parse(data)
            meshes = bfres_mod.meshes_from_file(file)
        except HookyError as exc:
            raise ProjectError(
                f"Impossible d'ouvrir le modèle « {rel} » : {exc}\n\n"
                "Ce BFRES peut contenir uniquement une animation ou un squelette, "
                "être incomplet, ou utiliser une disposition non reconnue. "
                "Essaie le fichier BFRES du modèle plutôt que .anim ou .Skeleton."
            ) from exc
        report = getattr(file, "v10_report", None)
        meta = {
            "warnings": list(file.warnings[:12]) + (
                [f"{len(file.warnings) - 12} remarque(s) de décodage supplémentaires omises."]
                if len(file.warnings) > 12 else []),
            "version": f"{file.version >> 16}.{(file.version >> 8) & 0xFF}.{file.version & 0xFF}",
            "model": (file.models[0].name if file.models else ""),
            "materials": [m.name for m in file.models[0].materials] if file.models else [],
            "material_textures": getattr(report, "material_textures", {}) if report else {},
            "structured": bool(getattr(report, "structured", False)) if report else False,
            "injectable": any(model.shapes for model in file.models),
            "dropped": getattr(report, "dropped", 0) if report else 0,
            "submeshes": getattr(report, "submeshes", len(meshes)) if report else len(meshes),
        }
        # Si le fichier lui-même a changé pendant la lecture, ne pas publier sa
        # géométrie sous une ancienne clé. Les modifications internes sont
        # écrites atomiquement ; cela couvre aussi les écritures externes usuelles.
        try:
            unchanged = self._current_version(rel) == (source, key[2])
        except (ProjectError, OSError):
            unchanged = False
        if unchanged:
            try:
                geo_mod.write(path, meshes, meta)
            except (OSError, AttributeError):
                pass
            self._store_mesh(key, meshes, meta)
        return meshes, meta

    def model_meshes(self, rel: str) -> list[MeshData]:
        """Maillages d'un modèle, utilisés notamment pour l'adaptation d'échelle."""
        return self._model_meshes(rel)

    def model_geometry(self, rel: str) -> bytes:
        """Géométrie complète pour la vue WebGL, avec albédos non ambigus."""
        from . import model_view

        meshes, _meta, stamp = self._model_mesh_entry(rel)
        bindings = {material: {"path": asset.rel, "index": index}
                    for material, asset, index, _version in self._albedo_bindings(rel)}
        try:
            result = model_view.encode(meshes, bindings)
        except HookyError as exc:
            raise ProjectError(str(exc)) from exc
        if self._version(rel) != stamp:
            raise ProjectError("Le modèle a changé pendant le chargement ; réessaie.")
        return result

    def model_skin(self, rel: str) -> bytes:
        """Skin expérimental associé exactement à la géométrie WebGL courante."""
        from . import model_skin

        meshes, meta, stamp = self._model_mesh_entry(rel)
        source, current = self._current_version(rel)
        if current != stamp:
            raise ProjectError("Le modèle a changé pendant le chargement ; réessaie.")
        data = self._read_asset_path(source)
        result = model_skin.encode(data, meshes, structured=meta.get("structured", False))
        if self._current_version(rel) != (source, stamp):
            raise ProjectError("Le modèle a changé pendant le chargement ; réessaie.")
        return result

    def _preview_plan(self, meshes, width: int, height: int, face_limit: int | None):
        # 0 signifie « toutes les faces », y compris si un ancien réglage
        # manuel ou la dégradation automatique impose autrement un plafond.
        requested = self.settings.preview_faces if face_limit is None else face_limit
        try:
            return vram.plan(self.settings, meshes, width, height,
                             None if requested == 0 else requested,
                             preserve_faces=requested == 0)
        except vram.BudgetError as error:
            raise ProjectError(str(error)) from None

    def preview_plan(self, rel: str, width: int = 640, height: int = 480,
                     face_limit: int | None = None) -> dict:
        """Chiffre exactement le même choix que le rendu PNG."""
        meshes = self._model_meshes(rel)
        return self._preview_plan(meshes, width, height, face_limit).as_json()

    def model_preview(self, rel: str, width: int = 640, height: int = 480,
                      yaw: float = 0.7, pitch: float = -0.35,
                      views: int = 1, face_limit: int | None = None,
                      textured: bool | None = None) -> bytes:
        """Rend un aperçu **dans le budget mémoire** réglé par l'utilisateur."""
        if textured is None:
            textured = bool(self.settings.textured_previews)
        key = self.thumbnail_key(rel, width, height, yaw, pitch, views,
                                 face_limit, textured)
        with self._image_flights.hold(("model", key)):
            cached = self.thumbs.get(key)
            if cached:
                return cached
            return self._render_model_preview(key, rel, width, height, yaw, pitch,
                                              views, face_limit, textured)

    def _render_model_preview(self, key: str, rel: str, width: int, height: int,
                              yaw: float, pitch: float, views: int,
                              face_limit: int | None, textured: bool) -> bytes:
        meshes, _meta, model_stamp = self._model_mesh_entry(rel)
        planned = self._preview_plan(meshes, width, height, face_limit)
        data = self._flatten(rel, meshes, textured=textured, model_stamp=model_stamp)
        options = dict(zoom=1.0, cull=self.settings.cull,
                       frustum=self.settings.frustum,
                       supersample=planned.supersample,
                       max_faces=planned.face_limit)
        if views and views > 1:
            png = render.turntable_png(meshes, views=views, width=width,
                                       height=height, data=data, **options)
        else:
            png = render.render_png(meshes, width, height, yaw, pitch,
                                    data=data, **options)
        # Un changement pendant le rendu ne doit pas contaminer une ancienne clé.
        if key == self.thumbnail_key(rel, width, height, yaw, pitch, views,
                                     face_limit, textured):
            self.thumbs.put(key, png)
        return png

    def _flatten(self, rel: str, meshes, textured: bool = False, model_stamp=None):
        """Géométrie d'affichage aplatie, gardée pour les aperçus suivants."""
        from hookymod.viewer import build_render_data

        model_stamp = model_stamp if model_stamp is not None else self._version(rel)
        texture_stamp = (tuple((asset.rel, version)
                               for asset, version in self._albedo_assets_and_stamps(rel))
                         if textured else ())
        key = (rel, bool(textured))
        with self._flatten_flights.hold(key):
            with self._cache_lock:
                entry = self._render_data.get(key)
            if entry and entry[0] == model_stamp and entry[1] == texture_stamp:
                return entry[2]
            samplers = self.albedo_samplers(rel) if textured else None
            data = build_render_data(meshes, textures=samplers)
            now_textures = (tuple((asset.rel, version)
                                  for asset, version in self._albedo_assets_and_stamps(rel))
                            if textured else ())
            if self._version(rel) == model_stamp and now_textures == texture_stamp:
                with self._cache_lock:
                    if len(self._render_data) > 24:
                        self._render_data.clear()      # borne simple : 24 modèles aplatis
                    self._render_data[key] = (model_stamp, texture_stamp, data)
            return data

    def _ranked_names(self, index: "_PrefixIndex", rel: str):
        """Assets qui portent le nom du modèle, du plus sûr au plus large.

        Trois rangs, comme avant : le nom complet (``Weapon_Sword_Example``),
        la famille (``Weapon_Sword``), puis le préfixe (``Weapon``). Le premier
        rang non vide gagne — jamais de mélange entre deux rangs.

        Générateur : un appelant qui trouve son compte au rang 0 ne paie pas les
        deux autres. Sur un projet de 3 600 fichiers, construire le rang « préfixe »
        (qui contient presque tout) coûtait à lui seul une seconde.
        """
        stem = Path(rel).name.split(".")[0].lower()
        parts = [part for part in _tokens(stem) if part]
        family = "_".join(parts[:2]) if len(parts) > 1 else stem
        for key in (stem, family, parts[0] if parts else ""):
            if not key:
                continue
            found: list = []
            seen: set[str] = set()

            def take(items) -> None:
                for item in items:
                    if item.rel not in seen:
                        seen.add(item.rel)
                        found.append(item)

            # la texture porte le nom du modèle…
            take(index.get(key))
            # …ou le prolonge (Modèle_Broken → Modèle)…
            take(index.starting_with(key))
            # …ou le modèle est une variante d'un nom plus court
            tokens = key.split("_")
            for depth in range(2, len(tokens)):
                take(index.get("_".join(tokens[:depth])))
            if found:
                yield found

    def texture_assets_for(self, rel: str) -> list:
        """Textures d'albédo les plus proches d'un modèle, de la plus sûre à la moins sûre.

        Classement : nom exact du modèle, puis famille (``Weapon_Sword``), puis
        première composante (``Weapon``). On ne garde que les albédos
        (``_Alb``, ``albedo``, ``basecolor``, ``_Dif``) : une normale ou une
        métallique ne doit pas colorer le modèle.
        """
        with self._cache_lock:
            entry = self._texture_index.get(rel)
            index = self._texture_names
        if entry is not None:
            return entry
        found: list = []
        for bucket in self._ranked_names(index, rel):
            albedos = [asset for asset in bucket if asset.role == "albedo"]
            if albedos:
                found = albedos
                break
        with self._cache_lock:
            if self._texture_names is index:
                self._texture_index[rel] = found
        return found

    def animations_for(self, rel: str) -> list:
        """Animations (``*.anim.bfres``) qui vont avec ce modèle.

        Certaines animations ont un nom différent de celui du modèle : à côté de
        ``Creature_Alpha.Creature_Alpha.bfres`` vit ``Creature_Alpha_Animation.anim.bfres``.
        On enlève donc le suffixe ``_Animation`` / ``_Player_Animation`` avant de
        comparer, puis on accepte les variantes préfixées (``Creature_Beta_Variant``
        pour ``Creature_Beta``) : c'est le seul appariement possible sans ouvrir
        chaque fichier, et il reste instantané.
        """
        with self._cache_lock:
            cached = self._animation_cache.get(rel)
            index = self._animation_names
        if cached is not None:
            return cached
        found = next(iter(self._ranked_names(index, rel)), [])
        found = sorted(found, key=lambda asset: asset.rel)
        with self._cache_lock:
            if self._animation_names is index:
                self._animation_cache[rel] = found
        return found

    def texture_maps_for(self, rel: str) -> dict[str, list]:
        """**Toutes** les cartes du modèle, groupées par rôle (``albedo``, ``normal``…).

        On classe les textures du projet par précision du nom (nom du modèle,
        puis famille ``Weapon_Sword``, puis préfixe), puis par rôle d'après le
        suffixe du fichier. Une texture au suffixe inconnu finit dans ``autre`` :
        elle est exportée quand même, jamais jetée par ignorance.
        """
        with self._cache_lock:
            cached = self._map_index.get(rel)
            index = self._texture_names
        if cached is not None:
            return cached
        chosen = next(iter(self._ranked_names(index, rel)), [])
        buckets: dict[str, list] = {}
        for asset in chosen:
            role = asset.role or texture_maps.classify(asset.rel)
            buckets.setdefault(role, []).append(asset)
        for role, items in buckets.items():
            # le nom le plus court est le plus proche du modèle (« X_Alb » plutôt
            # que « X_Alb_Copy_02 »)
            items.sort(key=lambda item: (len(item.rel), item.rel))
        with self._cache_lock:
            if self._texture_names is index:
                self._map_index[rel] = buckets
        return buckets

    def _material_reference_sources(self, references: dict[str, list[str]]) -> tuple[dict, list[str]]:
        """Résout uniquement les noms explicites FMAT ; ne choisit jamais entre doublons."""
        with self._cache_lock:
            index = self._texture_full_names
        resolved: dict[str, dict[str, list[tuple[Asset, int, str]]]] = {}
        warnings: list[str] = []
        lookup: dict[str, tuple[Asset, int] | None] = {}
        for material, names in references.items():
            roles: dict[str, list[tuple[Asset, int, str]]] = {}
            for reference in dict.fromkeys(names):
                key = _png_stem(reference).casefold()
                role = texture_maps.classify(reference)
                if key not in lookup:
                    candidates = index.get(key, [])
                    chosen = None
                    if len(candidates) == 1:
                        asset = candidates[0]
                        try:
                            cache_key = (asset.rel, self._version(asset.rel))
                            with self._cache_lock:
                                entries = self._texture_reference_cache.get(cache_key)
                            if entries is None:
                                container = self.texture_file(asset.rel)
                                entries = tuple((texture.index, texture.name) for texture in container.textures)
                                with self._cache_lock:
                                    self._texture_reference_cache[cache_key] = entries
                                    self._texture_reference_cache.move_to_end(cache_key)
                                    while len(self._texture_reference_cache) > 256:
                                        self._texture_reference_cache.popitem(last=False)
                            exact = [index for index, name in entries if _png_stem(name or "").casefold() == key]
                            # Un fichier portant exactement le nom demandé et
                            # contenant une seule image est également non ambigu.
                            if len(exact) == 1:
                                chosen = (asset, exact[0])
                            elif not exact and len(entries) == 1:
                                chosen = (asset, entries[0][0])
                        except (ProjectError, HookyError, OSError, ValueError, IndexError):
                            pass
                    lookup[key] = chosen
                    if chosen is None:
                        detail = "plusieurs fichiers" if len(candidates) > 1 else "absente ou illisible"
                        warnings.append(f"Texture référencée {reference} : {detail}, non reliée.")
                chosen = lookup[key]
                if chosen:
                    roles.setdefault(role, []).append((*chosen, reference))
            resolved[material] = roles
        return resolved, warnings

    def _export_referenced_maps(self, rel: str, folder: Path, references: dict, limit: int) -> dict:
        resolved, warnings = self._material_reference_sources(references)
        files: list[dict] = []
        exported: dict[tuple[str, int], str] = {}
        used: set[str] = set()
        material_maps: dict[str, dict[str, str]] = {}

        def unique(stem: str, identity: str) -> str:
            # Les noms viennent d'un fichier non fiable, jamais d'un chemin.
            stem = safe_filename_stem(stem, fallback="texture")
            name = f"{stem}.png"
            if name.casefold() in used:
                suffix = cache_mod.digest(identity)[:8]
                name = f"{stem}--{suffix}.png"
                number = 2
                while name.casefold() in used:
                    name = f"{stem}--{suffix}-{number}.png"
                    number += 1
            used.add(name.casefold())
            return name

        for material, roles in resolved.items():
            bound: dict[str, str] = {}
            for role, items in roles.items():
                available = []
                for asset, index, reference in items:
                    identity = (asset.rel, index)
                    if identity not in exported:
                        if len(exported) >= limit:
                            if not any("limite" in warning for warning in warnings):
                                warnings.append(f"Limite de {limit} textures atteinte ; export incomplet.")
                            continue
                        try:
                            png = self.texture_png(asset.rel, index)
                            name = unique(_png_stem(reference), f"{asset.rel}:{index}")
                            (folder / name).write_bytes(png)
                        except (ProjectError, HookyError, OSError, IndexError, ValueError) as error:
                            warnings.append(f"{reference} illisible : {error}")
                            continue
                        exported[identity] = name
                        files.append({"role": role, "label": ROLE_LABELS.get(role, "?"), "file": name,
                                      "source": asset.rel, "index": index, "materials": []})
                    name = exported[identity]
                    available.append(name)
                    next(item for item in files if item["file"] == name)["materials"].append(material)
                choices = list(dict.fromkeys(available))
                if len(choices) == 1:
                    bound[role] = choices[0]
                elif len(choices) > 1:
                    warnings.append(f"{material} : plusieurs cartes {role}, branchement automatique omis.")
            # Les canaux glTF G/B exigent une image combinée distincte par matériau.
            if "metalrough" not in bound and ("roughness" in bound or "metallic" in bound):
                try:
                    rough = (folder / bound["roughness"]).read_bytes() if "roughness" in bound else None
                    metal = (folder / bound["metallic"]).read_bytes() if "metallic" in bound else None
                    packed = texture_maps.compose_metal_roughness(rough, metal)
                    if packed:
                        name = unique(f"{material}_MetalRough", f"{rel}:{material}:metalrough")
                        (folder / name).write_bytes(packed)
                        bound["metalrough"] = name
                        files.append({"role": "metalrough", "label": "MR", "file": name,
                                      "source": "cartes combinées", "materials": [material]})
                except (HookyError, OSError, ValueError) as error:
                    warnings.append(f"{material} : combinaison métal/rugosité impossible : {error}")
            material_maps[material] = bound
        self._write_textures_note(folder, _display_name(Path(rel).name.split(".")[0]), rel, files, warnings)
        return {"maps": {}, "material_maps": material_maps, "files": files,
                "textures": len(files), "warnings": warnings, "cacheable": not warnings}

    def export_maps_for(self, rel: str, folder: Path,
                        limit: int = 128) -> dict:
        """Écrit **toutes** les cartes du modèle en PNG, plus ``TEXTURES.md``.

        :return: ``{maps: {rôle: fichier}, files: [...], textures: n,
                  warnings: [...]}``.
        """
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        meshes, meta, _stamp = self._model_mesh_entry(rel)
        references = meta.get("material_textures")
        if references:
            return self._export_referenced_maps(rel, folder, references, limit)
        buckets = self.texture_maps_for(rel)
        base = _display_name(Path(rel).name.split(".")[0])
        maps: dict[str, str] = {}
        files: list[dict] = []
        warnings: list[str] = []
        raw: dict[str, bytes] = {}
        used: set[str] = set()
        written = 0

        def unique_name(stem: str, identity: str) -> str:
            stem = safe_filename_stem(stem, fallback="texture")
            name = f"{stem}.png"
            if name.casefold() in used:
                name = f"{stem}--{cache_mod.digest(identity)[:8]}.png"
                number = 2
                while name.casefold() in used:
                    name = f"{stem}--{cache_mod.digest(identity)[:8]}-{number}.png"
                    number += 1
            used.add(name.casefold())
            return name

        for role in ROLE_ORDER:
            for asset in buckets.get(role, []):
                if written >= limit:
                    message = f"plus de {limit} cartes : les suivantes sont ignorées (exporte-les seules)"
                    if message not in warnings:
                        warnings.append(message)
                    break
                try:
                    png = self.texture_png(asset.rel, 0)
                except (ProjectError, HookyError, OSError, IndexError, ValueError) as error:
                    warnings.append(f"{Path(asset.rel).name} illisible : {error}")
                    continue
                name = unique_name(_png_stem(asset.stem), asset.rel)
                (folder / name).write_bytes(png)
                # Le raccord global reste le premier par rôle ; les autres PNG
                # sont également livrés et les albédos sont indexés par matériau.
                maps.setdefault(role, name)
                raw.setdefault(role, png)
                files.append({"role": role, "label": ROLE_LABELS.get(role, "?"),
                              "file": name, "source": asset.rel})
                written += 1

        # glTF n'a qu'une image pour rugosité + métal (G et B) : on la fabrique
        if "metalrough" not in maps and ("roughness" in raw or "metallic" in raw):
            composed = texture_maps.compose_metal_roughness(raw.get("roughness"),
                                                           raw.get("metallic"))
            if composed:
                name = unique_name(f"{base}_MetalRough", f"{rel}:metalrough")
                (folder / name).write_bytes(composed)
                maps["metalrough"] = name
                parts = [label for role, label in (("roughness", "Rgh"),
                                                   ("metallic", "Mtl"))
                         if role in raw]
                files.append({"role": "metalrough", "label": "MR", "file": name,
                              "source": " + ".join(parts)})

        cacheable = not warnings
        material_maps: dict[str, dict[str, str]] = {}
        materials = {mesh.material or mesh.name for mesh in meshes}
        if not maps:
            warnings.append("Aucune texture candidate trouvée : le modèle sort sans image.")
        else:
            warnings.append("Références de matériaux indisponibles : textures candidates par nom, association heuristique.")
            if len(materials) == 1:
                bound = {}
                for role in ROLE_ORDER:
                    choices = [item["file"] for item in files if item["role"] == role]
                    if len(choices) == 1:
                        bound[role] = choices[0]
                    elif len(choices) > 1:
                        warnings.append(f"Plusieurs cartes {role} candidates : liaison automatique omise.")
                if (len(buckets.get("roughness", [])) > 1 or len(buckets.get("metallic", [])) > 1):
                    bound.pop("metalrough", None)
                material_maps[next(iter(materials))] = bound
            else:
                warnings.append("Plusieurs matériaux sans référence explicite : aucune carte n'est branchée automatiquement.")
        self._write_textures_note(folder, base, rel, files, warnings)
        return {"maps": maps, "material_maps": material_maps, "files": files,
                "textures": len(files), "warnings": warnings, "cacheable": cacheable}

    def _write_textures_note(self, folder: Path, base: str, rel: str,
                             files: list[dict], warnings: list[str]) -> Path:
        """``TEXTURES.md`` : quelle image va sur quelle entrée de shader."""
        lines = [f"# Textures de {base}", "",
                 f"Modèle : `{rel}`", "",
                 "Les références non ambiguës peuvent être reliées par matériau dans le glTF.",
                 "Les rôles sont déduits des noms ; vérifie-les avant utilisation.",
                 "Les images non reliées restent disponibles pour un branchement manuel.", ""]
        lines += ["| Fichier | Rôle | Matériaux référents | Entrée possible |", "| --- | --- | --- | --- |"]
        for item in files:
            help_text = texture_maps.ROLE_HELP.get(item["role"], "")
            materials = ", ".join(item.get("materials", [])) or "—"
            lines.append(f"| `{item['file']}` | {item['label']} | {materials} | {help_text} |")
        if not files:
            lines.append("| — | — | — | aucune texture exportée |")
            if warnings:
                lines += ["", "## Remarques", ""] + [f"* {w}" for w in warnings]
            path = Path(folder) / "TEXTURES.md"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return path
        lines += ["", "## Espace colorimétrique (la cause n°1 d'un rendu « lavé »)", "",
                  "* **Albédo** : sRGB (c'est une couleur).",
                  "* **Tout le reste** (normale, rugosité, métal, AO) : **Non-Color**.",
                  "  Une normale lue en sRGB donne des facettes qui semblent fausses ;",
                  "  une rugosité en sRGB donne un métal trop brillant.", "",
                  "## Réglages qui vont avec", "",
                  "* normale : nœud **Normal Map**, force (`Strength`) 1.0 ;",
                  "* métal : 1.0 quand la carte `Mtl` est blanche à cet endroit, sinon 0 ;",
                  "* rugosité : `Roughness` reçoit la carte directement ;",
                  "* si seul l'albédo est présent, d'autres cartes peuvent être absentes,",
                  "  illisibles ou non résolues ; vérifie les remarques avant de conclure.",
                  "",
                  "## Rugosité + métal : deux façons", "",
                  "* **Deux images** (`Rgh` sur Roughness, `Mtl` sur Metallic) :",
                  "  branche-les manuellement si nécessaire ;",
                  "* **Une image** `*_MetalRough.png` : c'est l'emballage glTF/Unity —",
                  "  **G = rugosité, B = métal**, R et A ignorés. Pour l'utiliser dans",
                  "  Blender : nœud `Separate Color`, puis G → Roughness et B → Metallic.",
                  "",
                  "## Sortie glTF", "",
                  "Le `.gltf` peut utiliser les cartes disponibles : `baseColorTexture`,",
                  "`normalTexture`, `metallicRoughnessTexture` (rugosité en G, métal en B)",
                  "et `occlusionTexture`. Les cartes sans équivalent glTF (spéculaire,",
                  "masque) sont écrites quand même et listées ci-dessus."]
        if warnings:
            lines += ["", "## Remarques", ""] + [f"* {w}" for w in warnings]
        path = Path(folder) / "TEXTURES.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def export_textures_for(self, rel: str, folder: Path) -> dict[str, str]:
        """PNG d'albédo du modèle, indexés par nom de matériau **et** par nom de fichier.

        On ne devine pas : on classe les textures candidates par précision de
        nom (nom exact du modèle, puis famille ``Weapon_Sword``) et on ne garde
        que les albédos (``_Alb``…). Le nom d'un matériau (``Mt_Blade``) ne
        contient jamais celui de la texture : l'index contient donc aussi la
        forme sans suffixe, ce qui permet à Blender de retrouver la bonne image.
        """
        candidates = self.texture_assets_for(rel)
        found: dict[str, str] = {}
        folder.mkdir(parents=True, exist_ok=True)
        for asset in candidates[:24]:
            try:
                target = folder / f"{asset.stem}.png"
                target.write_bytes(self.texture_png(asset.rel, 0))
            except ProjectError:
                continue
            found[_base_name(asset.stem)] = str(target)
            found[asset.stem] = str(target)
        return found

    # ------------------------------------------------------------------ UV
    def _uv_texture(self, rel: str):
        """Atlas unique pour toutes les formes, ou damier si la liaison est ambiguë."""
        meshes, _meta, _ = self._model_mesh_entry(rel)
        materials = {mesh.material or "" for mesh in meshes if mesh.uvs}
        bindings = self._albedo_bindings(rel)
        by_material = {material: (asset, index) for material, asset, index, _version in bindings}
        if not materials or not materials.issubset(by_material):
            return None, None
        distinct = {(by_material[name][0].rel, by_material[name][1]) for name in materials}
        if len(distinct) != 1:
            return None, None
        asset, index = by_material[next(iter(materials))]
        try:
            return asset, self.texture_png(asset.rel, index)
        except (ProjectError, HookyError, OSError, ValueError, IndexError):
            return None, None

    def uv_info(self, rel: str) -> dict:
        """Diagnostic des UV : ce qui se voit (bornes, débordements, étirement)."""
        from . import uvmap

        meshes = self._model_meshes(rel)
        asset, _png = self._uv_texture(rel)
        report = uvmap.summarise(meshes, asset.stem if asset else "")
        report["rel"] = rel
        return report

    def uv_overlay(self, rel: str, size: int = 640, flip: bool = False,
                   mtime: int = 0) -> bytes:
        """Texture du modèle avec ses triangles UV tracés dessus (PNG)."""
        from . import uvmap

        asset, png = self._uv_texture(rel)
        stamp = (size, bool(flip), asset.rel if asset else "",
                 self._version(asset.rel) if asset else 0)
        key = cache_mod.digest(("uv", rel, self._version(rel), stamp))
        cached = self.thumbs.get(key)
        if cached:
            return cached
        result = uvmap.overlay(png, self._model_meshes(rel), size=size, flip=flip)
        self.thumbs.put(key, result)
        return result

    def _model_export_folder(self, rel: str) -> Path:
        """Noms portables, attribués à la source même après un nouveau scan."""
        source = self.asset(rel).relative_to(self.root).as_posix()
        base = self.work / "export"
        resolved = base.resolve()
        if resolved == self.work or not resolved.is_relative_to(self.work):
            raise ProjectError("Le dossier d'export sort du dossier de travail.")
        with self._cache_lock:
            name = self._model_export_names.get(source)
            stem = _model_export_stem(source)
            if self._model_export_folders is None:
                self._model_export_folders = export_folders.ExportFolders(base, self.root, "model")
            try:
                folder = self._model_export_folders.claim(
                    source, name or stem, f"{stem}--{cache_mod.digest(source)[:8]}",
                    reserved=self._model_export_reserved, preferred_is_reserved=name is not None)
            except (OSError, ValueError) as exc:
                raise ProjectError(f"Impossible de réserver un dossier d'export : {exc}") from exc
        if not folder.resolve().is_relative_to(resolved):
            raise ProjectError("Le dossier d'export sort du dossier de travail.")
        return folder

    def _export_signature(self, rel: str, textures: bool, stamp: tuple,
                          folder_name: str, archive: bool) -> tuple | None:
        """Clé liée aux sources réelles et à l'inventaire, jamais aux fichiers exportés."""
        with self._cache_lock:
            generation = self._generation
        try:
            if self._version(rel) != stamp:
                return None
            sources = []
            if textures:
                _meshes, meta, _model_stamp = self._model_mesh_entry(rel)
                references = meta.get("material_textures") or {}
                if references:
                    with self._cache_lock:
                        exact_index = self._texture_full_names
                    candidates = [(texture_maps.classify(name), asset)
                                  for names in references.values() for name in names
                                  for asset in exact_index.get(_png_stem(name).casefold(), [])]
                else:
                    buckets = self.texture_maps_for(rel)
                    candidates = [(role, asset) for role in ROLE_ORDER for asset in buckets.get(role, [])]
                for role, asset in candidates:
                    if len(sources) >= 512:
                        return None
                    sources.append((role, asset.rel, self._version(asset.rel)))
        except (ProjectError, OSError):
            return None
        with self._cache_lock:
            if generation != self._generation:
                return None
        return (rel, folder_name, generation, stamp, bool(textures), bool(archive), tuple(sources))

    def _cached_export(self, key: tuple | None):
        if key is None:
            return None
        with self._cache_lock:
            entry = self._export_cache.get(key)
            if entry is not None:
                self._export_cache.move_to_end(key)
        if entry is None:
            return None
        files, found, zipped, _cost = entry
        return files, copy.deepcopy(found), zipped

    def _remember_export(self, key: tuple | None, paths: list[Path], found: dict,
                         staged_zip: Path | None) -> None:
        # Une carte illisible peut redevenir lisible sans changer sa date : ne
        # jamais figer un export partiel, à l'exception d'une absence de cartes.
        if key is None or (not found.get("cacheable", not found["warnings"]) and key[-1]):
            return
        try:
            cost = sum(path.stat().st_size for path in paths)
            if staged_zip is not None:
                cost += staged_zip.stat().st_size
            if cost > EXPORT_CACHE_BYTES:
                return
            files = tuple((path.name, path.read_bytes()) for path in paths)
            zipped = staged_zip.read_bytes() if staged_zip is not None else None
            actual = sum(len(data) for _, data in files) + len(zipped or b"")
            if actual > EXPORT_CACHE_BYTES:
                return
        except OSError:
            return  # un cache facultatif ne doit pas empêcher un export
        rel, folder_name, generation, stamp, textures, archive, _ = key
        if self._export_signature(rel, textures, stamp, folder_name, archive) != key:
            return
        with self._cache_lock:
            if generation != self._generation:
                return
            old = self._export_cache.pop(key, None)
            if old:
                self._export_cache_bytes -= old[3]
            while self._export_cache and self._export_cache_bytes + actual > EXPORT_CACHE_BYTES:
                _old_key, old = self._export_cache.popitem(last=False)
                self._export_cache_bytes -= old[3]
            self._export_cache[key] = (files, copy.deepcopy(found), zipped, actual)
            self._export_cache_bytes += actual

    def export_model(self, rel: str, textures: bool = True, *, archive: bool = False,
                     animation_rel: str | None = None, animation_index: int | None = None) -> dict:
        """GLB/glTF et OBJ ; un clip explicitement choisi anime seulement GLB/glTF."""
        if ((animation_rel is None) != (animation_index is None)
                or (animation_rel is not None and (not isinstance(animation_rel, str) or not animation_rel
                                                   or type(animation_index) is not int or animation_index < 0))):
            raise ProjectError("Choisis un fichier d'animation et un index de clip valides, ou aucun des deux.")
        return self._export_model_bundle(rel, textures, archive=archive,
                                         animation_rel=animation_rel, animation_index=animation_index)

    def _export_model_bundle(self, rel: str, textures: bool, *, archive: bool,
                             animation_rel: str | None, animation_index: int | None) -> dict:
        import tempfile
        import zipfile

        meshes, meta, stamp = self._model_mesh_entry(rel)
        binding = None
        animation_stamp = None
        animation_info = None
        animation_notes = ["Aucun clip sélectionné : GLB, glTF et OBJ sont statiques."]
        if animation_rel is not None:
            from . import model_skin
            from hookymod import gltf_animation

            animation_stamp = self._version(animation_rel)
            clip = self.animation_clip(animation_rel, animation_index)
            skin = model_skin.decode(self.model_skin(rel), meshes)
            if self._version(rel) != stamp or self._version(animation_rel) != animation_stamp:
                raise ProjectError("Le modèle ou l'animation a changé pendant l'export ; recommence.")
            binding = gltf_animation.bind(skin, clip)
            animation_info = {"path": animation_rel, "index": animation_index, "name": clip["name"],
                              "frames": clip["frames"], "fps": clip["fps"],
                              "matched": binding["matched"], "missing": len(binding["missing"])}
            animation_notes = [
                f"Clip « {clip['name']} » : {binding['matched']} os associés par nom ; "
                "30 images/s par convention, courbes et rotations échantillonnées, résultat expérimental.",
                "OBJ/MTL restent statiques ; l'animation est dans GLB et glTF."]
            if binding["repeated_keys"]:
                animation_notes.append(
                    f"{binding['repeated_keys']} image(s)-clé(s) au même instant : les courbes sont évaluées "
                    "de chaque côté ; un éventuel saut est approché par une très courte transition dans GLB/glTF. "
                    "Certains logiciels peuvent fusionner des clés trop proches.")
            if binding["missing"]:
                animation_notes.append(f"{len(binding['missing'])} piste(s) absente(s) du squelette : non exportées.")
        source = self.asset(rel).relative_to(self.root).as_posix()
        folder = self._model_export_folder(rel)
        # Un autre export du même dossier ne peut pas mélanger ses fichiers avec
        # celui-ci. La préparation isolée évite d'utiliser d'anciennes textures.
        with self._export_flights.hold(os.path.normcase(str(folder.resolve()))):
            try:
                with tempfile.TemporaryDirectory(prefix="model-export-", dir=self.work / "cache") as tmp:
                    stage = Path(tmp) / folder.name
                    stage.mkdir()
                    # Un clip dépend d'une deuxième source : ne jamais réutiliser
                    # le cache statique pour une autre animation ou version.
                    key = (self._export_signature(rel, textures, stamp, folder.name, archive)
                           if binding is None else None)
                    cached = self._cached_export(key)
                    if cached is not None:
                        files, found, cached_zip = cached
                        for name, data in files:
                            (stage / name).write_bytes(data)
                    else:
                        cached_zip = None
                        if textures:
                            found = self.export_maps_for(rel, stage)
                        else:
                            found = {"maps": {}, "material_maps": {}, "files": [], "textures": 0,
                                     "warnings": ["Export des textures désactivé."]}
                            self._write_textures_note(stage, folder.name, rel, [], found["warnings"])
                        blender.export_bundle(meshes, stage, folder.name,
                                              formats=("gltf", "glb", "obj"),
                                              material_maps=found.get("material_maps", {}),
                                              warnings=animation_notes + meta.get("warnings", []) + found["warnings"],
                                              animation=binding)

                    # Lister les fichiers physiques, dont MTL et PNG, et non
                    # seulement ceux renvoyés par le générateur de formats.
                    produced = sorted(path for path in stage.iterdir() if path.is_file())
                    required = (".gltf", ".bin", ".glb", ".obj", ".mtl")
                    if any(not (stage / f"{folder.name}{suffix}").is_file() for suffix in required):
                        raise ProjectError("Export incomplet : GLB, glTF, BIN, OBJ ou MTL manquant.")
                    if animation_rel is not None and (self._version(rel) != stamp
                                                      or self._version(animation_rel) != animation_stamp):
                        raise ProjectError("Le modèle ou l'animation a changé pendant l'export ; recommence.")
                    archive_id = uuid.uuid4().hex if archive else ""
                    staged_zip = Path(tmp) / "result.zip"
                    if archive and cached_zip is not None:
                        staged_zip.write_bytes(cached_zip)
                    elif archive:
                        with zipfile.ZipFile(staged_zip, "w", compression=zipfile.ZIP_DEFLATED,
                                             compresslevel=1, allowZip64=True) as output:
                            for path in produced:
                                # Les PNG sont déjà comprimés.
                                compression = (zipfile.ZIP_STORED if path.suffix.lower()
                                               == ".png" else zipfile.ZIP_DEFLATED)
                                output.write(path, f"{folder.name}/{path.name}",
                                             compress_type=compression)
                    if cached is None:
                        self._remember_export(key, produced, found, staged_zip if archive else None)
                    base = (self.work / "export").resolve()
                    if (base == self.work or not base.is_relative_to(self.work)
                            or not folder.resolve().is_relative_to(base)
                            or self._model_export_folders is None
                            or not self._model_export_folders.owns(folder, source)):
                        raise ProjectError("Le dossier d'export a changé ou n'appartient plus à ce modèle.")
                    produced_names = {path.name for path in produced}
                    stale_files = sorted(path.name for path in folder.iterdir()
                                         if path.is_file() and path.name not in produced_names
                                         and path.name != export_folders.OWNER_FILE)
                    # Remplacer chaque fichier atomiquement ; les anciens fichiers
                    # sans homologue ne sont pas effacés et restent signalés.
                    for path in produced:
                        os.replace(path, folder / path.name)
                    if archive:
                        downloads = (self.work / "cache" / "downloads").resolve()
                        if not downloads.is_relative_to(self.work):
                            raise ProjectError("Dossier de téléchargement hors du travail.")
                        with self._downloads_lock:
                            downloads.mkdir(parents=True, exist_ok=True)
                            os.replace(staged_zip, downloads / f"{archive_id}.zip")
                            self._prune_export_downloads(downloads, keep=archive_id)
            except OSError as exc:
                raise ProjectError(f"Impossible d'écrire l'export dans {folder} : {exc}") from exc

        response = {"folder": self.shown_path(folder), "absolute_folder": str(folder),
                    "files": [path.name for path in produced],
                    "textures": found["textures"], "maps": found["files"],
                    "faces": sum(len(mesh.faces) for mesh in meshes),
                    "submeshes": len(meshes), "stale_files": stale_files,
                    "animation": animation_info,
                    "warnings": animation_notes + meta.get("warnings", []) + found["warnings"]
                    + ([blender.NO_UV_WARNING] if found["textures"] and any(not mesh.uvs for mesh in meshes) else [])
                    + (["Le dossier contient aussi des fichiers plus anciens ; "
                        "le ZIP ne contient que cet export."] if stale_files else [])}
        if archive_id:
            response["download_url"] = f"/api/export/download?id={archive_id}"
            response["download_name"] = f"{folder.name}-modeles.zip"
        return response

    @staticmethod
    def _prune_export_downloads(folder: Path, *, keep: str) -> None:
        """Limite les seuls ZIP temporaires à 24 fichiers / 2 Gio / 7 jours.

        Jamais de suppression dans le dossier d'export utilisateur. Le nouveau
        ZIP est conservé même s'il dépasse le budget ; un téléchargement déjà
        ouvert peut empêcher l'effacement sous Windows, ce qui n'est pas fatal.
        """
        candidates = []
        for path in folder.glob("*.zip"):
            token = path.stem
            if (len(token) != 32 or any(ch not in "0123456789abcdef" for ch in token)
                    or path.is_symlink()):
                continue
            try:
                stat = path.stat()
                if path.is_file():
                    candidates.append((stat.st_mtime, stat.st_size, token, path))
            except OSError:
                continue
        candidates.sort(reverse=True)
        count = total = 0
        cutoff = time.time() - 7 * 24 * 60 * 60
        for modified, size, token, path in candidates:
            count += 1
            total += size
            if token != keep and (modified < cutoff or count > 24 or total > 2 * 1024**3):
                try:
                    path.unlink()
                except OSError:
                    pass
                count -= 1
                total -= size

    def store_export_download(self, content: bytes) -> dict:
        """Publie une archive temporaire sous un jeton, sans exposer son chemin à l'API.

        La sélection utilisait un Blob navigateur ; certaines webviews ne savent
        pas le télécharger. Un lien HTTP authentifié permet aussi de réessayer.
        """
        import tempfile

        downloads = (self.work / "cache" / "downloads").resolve()
        if not downloads.is_relative_to(self.work):
            raise ProjectError("Dossier de téléchargement hors du travail.")
        archive_id = uuid.uuid4().hex
        try:
            with self._downloads_lock:
                downloads.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(prefix="selection-", suffix=".tmp",
                                                     dir=downloads, delete=False) as out:
                        temporary = Path(out.name)
                        out.write(content)
                    os.replace(temporary, downloads / f"{archive_id}.zip")
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                self._prune_export_downloads(downloads, keep=archive_id)
        except OSError as exc:
            raise ProjectError(f"Impossible de préparer le ZIP : {exc}") from exc
        return {"download_url": f"/api/export/download?id={archive_id}&kind=selection",
                "download_name": "hookycreator-selection.zip",
                "absolute_folder": str(downloads), "temporary_archive": True}

    def export_download(self, token: str) -> Path:
        """Résout uniquement une archive créée par cet atelier, pas un chemin fourni."""
        if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
            raise ProjectError("Téléchargement introuvable.")
        base = (self.work / "cache" / "downloads").resolve()
        path = (base / f"{token}.zip").resolve()
        if not base.is_relative_to(self.work) or path.parent != base or not path.is_file():
            raise ProjectError("Téléchargement introuvable ou expiré : relance l'export.")
        return path

    def _albedo_index(self, rel: str, folder: Path, files: list[dict]) -> dict[str, str]:
        """Indexe uniquement les PNG d'albédo produits par cet export."""
        buckets = self.texture_maps_for(rel)
        names = {item["source"]: item["file"] for item in files if item["role"] == "albedo"}
        found: dict[str, str] = {}
        for asset in buckets.get("albedo", []):
            name = names.get(asset.rel)
            if not name:
                continue
            target = folder / name
            if not target.is_file():
                continue
            base = _base_name(asset.stem)
            for key in (asset.stem, base,
                        base[3:] if base.startswith("mt_") else ""):
                if key:
                    found.setdefault(key, str(target))
        return found

    # ------------------------------------------------------------------ mobs
    def _mobs_file(self) -> Path:
        return self.work / "mobs" / "mobs.json"

    def list_mobs(self) -> list[dict]:
        path = self._mobs_file()
        if not path.is_file():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def create_mob(self, name: str, model: str, textures: list[str],
                   parent: str = "") -> dict:
        """Crée la fiche d'un mob et copie ses assets sous un nouveau nom.

        Le paramètre d'acteur du jeu (BYML) n'est **pas** encore écrit : la fiche
        liste donc précisément ce qui reste à faire, plutôt que de laisser croire
        que le mob est déjà jouable.
        """
        clean = "".join(ch for ch in name if ch.isalnum() or ch in "_- ") .strip()
        if not clean:
            raise ProjectError("Nom de mob invalide.")
        slug = clean.replace(" ", "_")
        mob_dir = self.work / "mobs" / slug
        mob_dir.mkdir(parents=True, exist_ok=True)

        copied = []
        model_stem = Path(model).name.split(".")[0]
        if model:
            self.read_asset(model)  # vérifie notamment que la compression est lisible
            source = self._current_asset(model)[0].read_bytes()
            extension = ".bfres.zs" if zs.is_zstd(source) else ".bfres"
            target = mob_dir / f"{slug}{extension}"
            # Recopier les octets courants sans recomprimer ni mentir sur le suffixe.
            target.write_bytes(source)
            copied.append({"from": model, "to": target.name, "kind": "model"})
        for rel in textures:
            info = self.texture_info(rel)
            name_t = info["textures"][0]["name"]
            suffix = name_t.split("_")[-1]
            source = self._current_asset(rel)[0].read_bytes()
            extension = ".bntx.zs" if zs.is_zstd(source) else ".bntx"
            target = mob_dir / f"{slug}_{suffix}{extension}"
            target.write_bytes(source)
            copied.append({"from": rel, "to": target.name, "kind": "texture",
                           "texture": name_t})

        entry = {
            "name": clean,
            "slug": slug,
            "parent": parent or model_stem,
            "model_source": model,
            "texture_sources": list(textures),
            "files": copied,
            "todo": [
                "écrire le paramètre d'acteur (BYML) : copie du $parent, nom du modèle",
                "déclarer les tailles dans la table RESTBL du mod",
                "vérifier sur une carte (Banc/*.bcett.byml)",
            ],
        }
        mobs = [m for m in self.list_mobs() if m.get("slug") != slug]
        mobs.append(entry)
        self._mobs_file().write_text(json.dumps(mobs, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
        self.bump()
        return entry

    # ------------------------------------------------------------------ export
    def export_selection(self, items: list[dict]) -> bytes:
        """ZIP de la sélection : GLB/glTF et OBJ statiques, PNG pour les textures."""
        import io
        import tempfile
        import zipfile

        if not isinstance(items, list) or not items:
            raise ProjectError("Aucun élément sélectionné.")
        if len(items) > 500:
            raise ProjectError(f"{len(items)} éléments d'un coup, c'est trop "
                               "(500 maximum) : découpe la sélection.")
        buffer = io.BytesIO()
        lines = ["Sélection exportée par HookyCreator", ""]
        models = textures = 0
        used: set[tuple[str, str]] = set()
        seen: set[tuple[str, str, int]] = set()
        with tempfile.TemporaryDirectory() as folder, \
                zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for item in items:
                rel = str(item.get("rel") or "")
                kind = str(item.get("kind") or "")
                if not rel or kind not in {"model", "texture"}:
                    continue
                index = int(item.get("index", 0)) if kind == "texture" else 0
                key = (kind, rel, index)
                if key in seen:
                    continue
                seen.add(key)
                stem = _model_export_stem(rel) if kind == "model" else safe_filename_stem(
                    rel.rsplit("/", 1)[-1].split(".", 1)[0])
                # Deux fichiers homonymes de répertoires différents ne doivent
                # ni fusionner leurs dossiers ni créer des entrées ZIP dupliquées,
                # même si un nom réel imite le suffixe de hachage.
                unique = stem
                if (kind, unique.casefold()) in used:
                    base = f"{stem}--{cache_mod.digest(rel, index)[:8]}"
                    unique, number = base, 2
                    while (kind, unique.casefold()) in used:
                        unique, number = f"{base}-{number}", number + 1
                used.add((kind, unique.casefold()))
                if kind == "texture":
                    png = self.texture_png(rel, index)
                    archive.writestr(f"textures/{unique}.png", png, compress_type=zipfile.ZIP_STORED)
                    lines.append(f"[texture] {rel}")
                    textures += 1
                    continue
                target = Path(folder) / unique
                meshes, meta, _ = self._model_mesh_entry(rel)
                maps = self.export_maps_for(rel, target)
                written = blender.export_bundle(
                    meshes, target, stem, formats=("gltf", "glb", "obj"),
                    material_maps=maps.get("material_maps", {}),
                    warnings=["Export de sélection statique : aucun clip n'a été sélectionné."]
                    + meta.get("warnings", []) + maps["warnings"])
                # tout ce que l'export a produit : le .bin du glTF et les PNG
                # compris (sans le .bin, Blender refuse le glTF)
                for produced in sorted(target.rglob("*")):
                    if produced.is_file():
                        relative = produced.relative_to(target)
                        compression = (zipfile.ZIP_STORED if produced.suffix.lower()
                                       == ".png" else zipfile.ZIP_DEFLATED)
                        archive.write(produced, f"modeles/{unique}/{relative.as_posix()}",
                                      compress_type=compression)
                lines.append(f"[modèle] {rel} ({len(written)} fichier(s) + "
                             f"{maps['textures']} carte(s) de texture)")
                models += 1
            lines.insert(1, f"{models} modèle(s), {textures} texture(s)")
            archive.writestr("LISEZMOI.txt", "\n".join(lines) + "\n")
        if not models and not textures:
            raise ProjectError("Rien à exporter dans cette sélection.")
        return buffer.getvalue()

    def build_mod(self, name: str = "HookyMod") -> dict:
        """Assemble un dossier de mod (layeredFS) avec tout ce qui est modifié."""
        clean = "".join(ch for ch in name if ch.isalnum() or ch in "_-").strip() or "HookyMod"
        target = self.root / "mods" / clean / "romfs"
        if target.parent.exists():
            shutil.rmtree(target.parent)
        target.mkdir(parents=True, exist_ok=True)

        written = []
        assets_dir = self.work / "assets"
        if assets_dir.is_dir():
            for path in assets_dir.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(assets_dir)
                destination = target / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
                written.append(rel.as_posix())

        mobs_dir = self.work / "mobs"
        for mob in self.list_mobs():
            folder = mobs_dir / mob["slug"]
            for file in mob.get("files", []):
                source = folder / file["to"]
                if not source.is_file():
                    continue
                folder_name = "Model" if file["kind"] == "model" else "Tex"
                destination = target / folder_name / file["to"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                written.append(f"{folder_name}/{file['to']}")

        manifest = {"name": clean, "files": written, "mobs": len(self.list_mobs()),
                    "source_root": str(self.root)}
        (target.parent / "hookycreator.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"mod": clean, "path": self.shown_path(target.parent),
                "files": len(written), "mobs": manifest["mobs"]}

    # ------------------------------------------------------------------ état
    def edited_files(self, max_age: float = 2.0) -> list[str]:
        """Assets déjà modifiés dans le dossier de travail (liste courte, mémorisée)."""
        with self._cache_lock:
            generation = self._generation
            cached = self._edited_cache
        if cached and time.monotonic() - cached[0] < max_age:
            return cached[1]
        folder = self.work / "assets"
        found = sorted(path.relative_to(folder).as_posix()
                       for path in folder.rglob("*") if path.is_file()) \
            if folder.is_dir() else []
        with self._cache_lock:
            # Un import peut finir pendant l'énumération du dossier : ne pas
            # remettre cette ancienne liste dans le cache après son invalidation.
            if generation == self._generation:
                self._edited_cache = (time.monotonic(), found)
        return found

    def bump(self) -> None:
        """Invalide ce qui dépend du contenu du projet (après une écriture)."""
        with self._cache_lock:
            self._generation += 1
            self._state_json = None
            self._edited_cache = None
            self._stats_cache = None
            self._ensembles = None
            self._json_cache.clear()
            self._export_cache.clear()
            self._export_cache_bytes = 0

    def cached_json(self, name: str, builder) -> bytes:
        """JSON d'une vue, sérialisé une fois par version du projet.

        La liste des ensembles ou des textures ne change qu'après un scan, un
        import ou un réglage : la reconstruire à chaque changement d'onglet
        revenait à payer le même travail en boucle.
        """
        with self._cache_lock:
            key = (self._generation, name)
            entry = self._json_cache.get(name)
        if entry and entry[0] == key:
            return entry[1]
        # Plusieurs onglets ou frappes rapides ne doivent pas construire en
        # parallèle la même liste potentiellement très volumineuse.
        with self._json_flights.hold(name):
            with self._cache_lock:
                key = (self._generation, name)
                entry = self._json_cache.get(name)
            if entry and entry[0] == key:
                return entry[1]
            body = json.dumps(builder(), ensure_ascii=False).encode("utf-8")
            with self._cache_lock:
                # Une écriture a pu invalider l'état pendant la sérialisation.
                if self._generation == key[0]:
                    self._json_cache[name] = (key, body)
                    if len(self._json_cache) > 8:
                        self._json_cache = {name: (key, body)}
            return body

    def image_revision(self) -> str:
        """Version de session pour éviter les images décodées périmées du navigateur."""
        with self._cache_lock:
            generation = self._generation
        return f"{self._instance_id}-{generation}"

    def settings_snapshot(self) -> dict:
        """Réglages et révision observés à la même génération."""
        with self._cache_lock:
            return {"settings": self.settings.as_json(),
                    "image_revision": f"{self._instance_id}-{self._generation}"}

    def state(self, *, quick: bool = False) -> dict:
        # Capturer ensemble la révision, les réglages et le dernier scan. La
        # conversion d'un gros inventaire reste hors verrou : un scan en cours
        # peut publier sa nouvelle liste sans mélanger les deux instantanés.
        with self._cache_lock:
            revision = f"{self._instance_id}-{self._generation}"
            assets = self.assets
            settings = self.settings
            scanned_at, scan_seconds = self.scanned_at, self.scan_seconds
            truncated, scan_errors = self.scan_truncated, self.scan_errors
            error_examples = self.scan_error_examples
            scan_limit = self.scan_limit
        return {
            "image_revision": revision,
            "root": str(self.root),
            "work": str(self.work.relative_to(self.root)) if self.work.is_relative_to(self.root) else str(self.work),
            "assets": [a.as_json() for a in assets],
            "mobs": self.list_mobs(),
            "dicts": len(self._load_dicts()),
            "zstd": zs.zstd_available(),
            "settings": settings.as_json(),
            # Le tout premier affichage n'a pas besoin d'énumérer des milliers
            # de vignettes et fichiers .geo. /api/system fournit ces chiffres
            # en arrière-plan ; /api/state historique reste complet par défaut.
            "cache": None if quick else self.cache_stats(max_age=3.0),
            "scanned_at": scanned_at,
            "scan_seconds": scan_seconds,
            "scan_truncated": truncated,
            "scan_incomplete": truncated or bool(scan_errors),
            "scan_limit": scan_limit,
            "scan_errors": scan_errors,
            "scan_error_examples": error_examples,
            "edited": self.edited_files(),
        }

    def state_payload(self, *, quick: bool = False) -> bytes:
        """``{"project": ...}`` en JSON, sérialisé une fois par version du projet."""
        def cache_key():
            return (self._generation, json.dumps(self.settings.as_json(), sort_keys=True), quick)

        with self._cache_lock:
            key = cache_key()
            cached = self._state_json
        if cached and cached[0] == key:
            return cached[1]
        with self._state_lock:
            # La génération peut avoir changé en attendant une autre requête.
            with self._cache_lock:
                key = cache_key()
                cached = self._state_json
            if cached and cached[0] == key:
                return cached[1]
            snapshot = self.state(quick=True) if quick else self.state()
            body = json.dumps({"project": snapshot}, ensure_ascii=False).encode("utf-8")
            with self._cache_lock:
                if cache_key() == key:
                    self._state_json = (key, body)
            return body


def open_project(root, work=None, *, scan_limit: int = MAX_SCAN_FILES) -> Project:
    project = Project(Path(root), Path(work) if work else None, scan_limit=scan_limit)
    project.scan()
    return project
