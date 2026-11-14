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

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from hookymod import bfres as bfres_mod
from hookymod import bntx as bntx_mod
from hookymod import blendfile, bntx_encode, blender, render, zs
from hookymod import texturemaps as texture_maps
from hookymod.texturemaps import ROLE_LABELS, ROLE_ORDER
from hookymod.blender import _base_name
from hookymod.mesh import MeshData

from .settings import Settings, SettingsError
from . import cache as cache_mod
from . import geo as geo_mod
from . import vram

MODEL_SUFFIXES = (".bfres.zs", ".bfres")
TEXTURE_SUFFIXES = (".bntx.zs", ".bntx")
MAX_SCAN_FILES = 40_000


@dataclass
class Asset:
    """Un fichier du projet, tel qu'il apparaît dans le navigateur."""

    rel: str
    kind: str                       # model | animation | texture | bytecode | archive
    size: int
    compressed: bool = False
    mtime: int = 0                  # sert de version dans les URL de vignettes
    role: str = ""                  # texture seulement : albedo, normal, roughness…

    def as_json(self) -> dict:
        data = {"rel": self.rel, "kind": self.kind, "size": self.size,
                "compressed": self.compressed, "name": Path(self.rel).name,
                "mtime": self.mtime}
        if self.role:
            data["role"] = self.role
        return data

    @property
    def stem(self) -> str:
        name = Path(self.rel).name
        for suffix in (".zs",):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name.rsplit(".", 1)[0]


#: suffixes que le jeu ajoute au nom du modèle pour nommer ses animations
ANIMATION_SUFFIXES = ("_player_animation", "_animation", "_anim")


def _animation_key(name: str) -> str:
    """``Animal_Cat_Animation.anim.bfres.zs`` → ``animal_cat``."""
    base = name.split(".")[0].lower()
    for suffix in ANIMATION_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _png_stem(stem: str) -> str:
    """``Mt_Epee_Alb.0`` → ``Mt_Epee_Alb`` (le numéro de variante ne sert à rien)."""
    head, _, tail = stem.rpartition(".")
    return head if head and tail.isdigit() else stem


def _display_name(stem: str) -> str:
    """``Animal_Cat.Animal_Cat`` → ``Animal_Cat`` (le jeu double souvent le nom)."""
    head, _, tail = stem.partition(".")
    return head if head and head == tail else stem


#: côté de la table d'échantillonnage (assez pour une couleur par face)
SAMPLER_SIZE = 48


def _make_sampler(image, size: int = SAMPLER_SIZE):
    """Fabrique ``(u, v) -> (r, g, b)`` depuis une image décodée.

    L'image est réduite en une petite table de couleurs (moyenne par bloc, les
    pixels transparents ignorés) : un seul décodage, puis une lecture par face.
    """
    width, height, pixels = image
    if width < 1 or height < 1 or not pixels:
        raise ValueError("image vide")
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

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        if not self.root.is_dir():
            raise ProjectError(f"Dossier introuvable : {self.root}")
        self.work = Path(self.work) if self.work else self.root / "hookycreator_work"
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / "cache").mkdir(exist_ok=True)
        (self.work / "mobs").mkdir(exist_ok=True)
        self.settings = Settings.load(self.settings_path)
        self.thumbs = cache_mod.ImageCache(self.work / "cache" / "thumbs",
                                           self.settings.cache_mb)
        self.scanned_at = ""
        self.scan_seconds = 0.0
        self._mesh_bytes = 0
        # géométrie décodée : rouvrir un modèle ne doit pas repayer le décodage
        self.geo_folder = self.work / "cache" / "geo"
        self.geo_hits = 0
        self.geo_misses = 0
        self._reports: dict[str, dict] = {}
        self._texture_index: dict[str, list] = {}
        self._map_index: dict[str, dict] = {}
        self._animation_index: list | None = None
        # géométrie aplatie pour l'affichage : reconstruite à chaque aperçu,
        # elle coûtait ~1 s sur un modèle de 170 000 faces
        self._render_data: dict[str, tuple] = {}
        # échantillonneurs d'albédo (aperçus colorés par les vraies textures)
        self._samplers: dict[str, dict] = {}
        self.warm: dict = {"running": False, "done": 0, "total": 0, "error": None}
        self._warm_thread = None
        self._dicts_lock = __import__("threading").Lock()

    # ------------------------------------------------------------------ réglages
    @property
    def settings_path(self) -> Path:
        return self.work / "settings.json"

    def update_settings(self, payload: dict) -> dict:
        """Applique des réglages venus de l'interface, les valide et les écrit."""
        try:
            merged = self.settings.as_json() | payload
            self.settings = Settings.from_json(merged)
        except SettingsError as error:
            raise ProjectError(str(error)) from None
        self.settings.save(self.settings_path)
        self.thumbs.limit_bytes = self.settings.cache_mb * 1024 * 1024
        self.thumbs.trim()
        self._cache.clear()
        self._mesh_bytes = 0
        # une fiche ou un aperçu dépend des réglages : on ne réutilise pas l'ancien
        self._reports.clear()
        self._render_data.clear()
        self._samplers.clear()
        return self.settings.as_json()

    # ------------------------------------------------------------------ fichiers
    def asset(self, rel: str) -> Path:
        """Chemin d'un asset, en refusant toute sortie du projet (``..``)."""
        candidate = (self.root / rel).resolve()
        if not str(candidate).startswith(str(self.root)):
            raise ProjectError("Chemin hors du projet.")
        if not candidate.is_file():
            raise ProjectError(f"Fichier introuvable : {rel}")
        return candidate

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
                dicts = zs.ZstdDicts()
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

        raw = (self.root / rel).read_bytes()
        return Level(name=Path(rel).name, raw=raw, dicts=self._load_dicts(), rel=rel)

    def read_entry(self, rel: str) -> bytes:
        """Contenu d'une entrée d'archive, écrite ``pack.zs!/dossier/fichier``."""
        from hookymod.util import HookyError

        archive, _, inner = rel.partition("!/")
        level = self.open_level(archive)
        parts = [part for part in inner.split("/") if part]
        for depth, part in enumerate(parts):
            if not level.archive:
                raise ProjectError(f"{archive} n'est pas une archive.")
            index = next((i for i in range(len(level))
                          if level.entry_name(i) == part), None)
            if index is None:
                raise ProjectError(f"Entrée introuvable : {part} dans {archive}")
            if depth == len(parts) - 1:
                try:
                    return level.entry_blob(index)
                except HookyError as exc:
                    raise ProjectError(str(exc)) from exc
            level = level.open_entry(index)
        raise ProjectError("Chemin d'entrée vide.")

    def read_asset(self, rel: str) -> bytes:
        """Contenu décompressé d'un asset (``.zs`` géré s'il y a un dictionnaire)."""
        if "!/" in rel:
            raw = self.read_entry(rel)
            return raw
        path = self.asset(rel)
        raw = path.read_bytes()
        if not zs.is_zstd(raw):
            return raw
        dict_id, _ = zs.frame_info(raw)
        if dict_id == 0:
            # Frame sans dictionnaire (textures du jeu) : lecture directe.
            import zstandard
            return zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 28)
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
        started = cache_mod.now_iso()
        started_clock = __import__("time").perf_counter()
        assets: list[Asset] = []
        truncated = False
        for path in sorted(self.root.rglob("*")):
            if len(assets) >= MAX_SCAN_FILES:
                truncated = True
                break
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith(("hookycreator_work/", "mods/")):
                continue
            low = path.name.lower()
            if ".anim." in low:
                # Les animations sont des BFRES sans maillage : les afficher
                # comme « modèle » promet une géométrie qui n'existe pas.
                kind = "animation"
            elif low.endswith(MODEL_SUFFIXES):
                kind = "model"
            elif low.endswith(TEXTURE_SUFFIXES):
                kind = "texture"
            elif low.endswith((".byml", ".bgyml", ".bcett.byml")):
                kind = "bytecode"
            elif low.endswith((".pack.zs", ".pack", ".sarc", ".szs", ".sarc.zs")):
                kind = "archive"
            else:
                continue
            stat = path.stat()
            assets.append(Asset(rel=rel, kind=kind, size=stat.st_size,
                                compressed=low.endswith(".zs"),
                                mtime=int(stat.st_mtime),
                                # un `.bntx` est une carte précise (albédo,
                                # normale, rugosité…) : le dire une fois pour
                                # toutes évite de le redécouvrir à chaque écran
                                role=texture_maps.classify(path.name)
                                if kind == "texture" else ""))
        self.assets = assets
        self.scanned_at = started
        self.scan_seconds = round(__import__("time").perf_counter() - started_clock, 3)
        return {
            "root": str(self.root),
            "work": str(self.work),
            "count": len(assets),
            "truncated": truncated,
            "models": sum(1 for a in assets if a.kind == "model"),
            "animations": sum(1 for a in assets if a.kind == "animation"),
            "textures": sum(1 for a in assets if a.kind == "texture"),
            "bytecode": sum(1 for a in assets if a.kind == "bytecode"),
            "archives": sum(1 for a in assets if a.kind == "archive"),
            "scanned_at": self.scanned_at,
            "scan_seconds": self.scan_seconds,
        }

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

    def by_kind(self, kind: str) -> list[Asset]:
        return [a for a in self.assets if a.kind == kind]

    # ------------------------------------------------------------------ cache
    def _version(self, rel: str) -> tuple[int, int]:
        """(taille, date) : la « version » qui invalide vignettes et caches."""
        return cache_mod.file_stamp(self.asset(rel))

    def _render_digest(self) -> str:
        settings = self.settings
        # « couleurs v2 » : une couleur par matériau (les vignettes d'avant,
        # colorées par sous-maillage, ne doivent pas être réutilisées)
        return cache_mod.digest(("rendu", "couleurs-v2", settings.supersample,
                                 settings.cull, settings.frustum,
                                 settings.max_faces, settings.thumb_faces,
                                 settings.lod))

    def _store_mesh(self, key, meshes) -> list[MeshData]:
        cost = cache_mod.mesh_bytes(meshes)
        budget = max(64 * 1024 * 1024, self.settings.budget_bytes // 2)
        if self._mesh_bytes + cost > budget and self._cache:
            # budget mémoire atteint : on jette les maillages décodés, pas le disque
            for old_key in [k for k in self._cache if k[0] == "meshes"]:
                self._cache.pop(old_key, None)
            self._mesh_bytes = 0
        self._cache[key] = meshes
        self._mesh_bytes += cost
        return meshes

    def albedo_samplers(self, rel: str) -> dict:
        """Échantillonneurs ``(u, v) -> (r, g, b)`` des albédos d'un modèle.

        Une seule texture trouvée : elle colore tous les matériaux (c'est le cas
        d'une épée ou d'une arme). Plusieurs : on associe par nom quand c'est
        possible, sinon la première sert de repli — la géométrie n'est jamais
        concernée, seul l'habillage change.
        """
        cached = self._samplers.get(rel)
        if cached is not None:
            return cached
        from .pngread import decode_image

        samplers: dict = {}
        for asset in self.texture_assets_for(rel)[:8]:
            try:
                image = decode_image(self.texture_png(asset.rel, 0))
            except Exception:          # texture illisible : on garde la palette
                continue
            sampler = _make_sampler(image)
            samplers[_base_name(asset.stem)] = sampler
            samplers[asset.stem] = sampler
            samplers.setdefault("", sampler)
        self._samplers[rel] = samplers
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
            stamps = [(asset.rel, self._version(asset.rel))
                      for asset in self.texture_assets_for(rel)]
            textures = cache_mod.digest(("textures", stamps))
        return cache_mod.digest(("apercu", rel, self._version(rel), width, height,
                                 round(yaw, 2), round(pitch, 2), views,
                                 self.settings.thumb_faces if face_limit is None
                                 else face_limit,
                                 self._render_digest(), textures))

    def thumbnail_cached(self, rel: str, width: int, height: int,
                         textured: bool = False) -> bool:
        try:
            key = self.thumbnail_key(rel, width, height, textured=textured)
        except ProjectError:
            return False
        return self.thumbs.get(key) is not None

    def warm_thumbnails(self, width: int = 320, height: int = 240,
                        limit: int | None = None, workers: int = 2) -> dict:
        """Prépare les vignettes en tâche de fond, sans bloquer l'interface.

        Décoder un BFRES coûte près d'une seconde : plutôt que de le payer à
        l'ouverture de la vue, on remplit le cache disque dès le démarrage, deux
        modèles à la fois. Ce qui est déjà en cache est sauté.
        """
        if self.warm.get("running"):
            return self.warm
        models = [asset.rel for asset in self.by_kind("model")]
        if limit:
            models = models[:limit]
        self.warm = {"running": True, "done": 0, "total": len(models), "error": None}
        state = self.warm

        def run() -> None:
            from concurrent.futures import ThreadPoolExecutor

            def one(rel: str) -> None:
                try:
                    if not self.thumbnail_cached(rel, width, height):
                        self.model_preview(rel, width, height,
                                           face_limit=self.settings.thumb_faces)
                except Exception:            # une vignette ratée ne casse pas la série
                    pass
                finally:
                    state["done"] = state.get("done", 0) + 1

            try:
                with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                    list(pool.map(one, models))
            finally:
                state["running"] = False

        self._warm_thread = __import__("threading").Thread(target=run, daemon=True)
        self._warm_thread.start()
        return self.warm

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

    def cache_stats(self) -> dict:
        meshes = [k for k in self._cache if k[0] == "meshes"]
        return {
            "thumbs": self.thumbs.stats(),
            "geo": self.geo_stats(),
            "meshes": {"entries": len(meshes), "bytes": self._mesh_bytes,
                       "budget_bytes": max(64 * 1024 * 1024,
                                           self.settings.budget_bytes // 2)},
            "rss_bytes": cache_mod.rss_bytes(),
            "cache_mb": self.settings.cache_mb,
            "scanned_at": self.scanned_at,
            "scan_seconds": self.scan_seconds,
            "warm": dict(self.warm),
        }

    def clear_cache(self) -> dict:
        """Vide vignettes, géométrie et maillages en mémoire."""
        self._cache.clear()
        self._mesh_bytes = 0
        self._reports.clear()
        self._render_data.clear()
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
        return {"rel": rel, "version": file.version_label, "orin": file.orin,
                "count": len(entries), "textures": entries}

    def texture_png(self, rel: str, index: int = 0, mip: int = 0) -> bytes:
        key = cache_mod.digest(("texture", rel, self._version(rel), index, mip))
        cached = self.thumbs.get(key)
        if cached:
            return cached
        file = self.texture_file(rel)
        texture = file.textures[index] if 0 <= index < len(file.textures) else None
        if texture is None:
            raise ProjectError(f"Texture {index} absente de {rel}.")
        return self.thumbs.put(key, texture.to_png(mip))

    def import_texture(self, rel: str, index: int, image: bytes) -> dict:
        """Remplace les pixels d'une texture (même format, mêmes mips)."""
        from .pngread import decode_image

        width, height, rgba = decode_image(image)
        template = self.read_asset(rel)
        name = self.texture_info(rel)["textures"][index]["name"]
        try:
            updated, report = bntx_encode.replace_texture(
                template, rgba, width, height, name=name, index=index)
        except Exception as exc:
            raise ProjectError(f"Remplacement impossible ({name}) : {exc}") from exc

        target = self.work / "assets" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
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
        target.write_bytes(payload)
        self._cache.pop(("png", rel, index), None)
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
        meshes = self._model_meshes(rel)
        meta = self._reports.get(rel, {})
        points = [point for mesh in meshes for point in mesh.positions]
        lowest = [min(point[i] for point in points) for i in range(3)]
        highest = [max(point[i] for point in points) for i in range(3)]
        return {
            "rel": rel,
            "version": meta.get("version") or self._version_label(rel),
            "models": [meta["model"]] if meta.get("model") else [Path(rel).stem],
            "meshes": len(meshes),
            "submeshes": meta.get("submeshes", len(meshes)),
            "dropped": meta.get("dropped", 0),
            "faces": sum(len(m.faces) for m in meshes),
            "vertices": sum(len(m.positions) for m in meshes),
            "materials": meta.get("materials") or sorted({m.material for m in meshes if m.material}),
            "bounds": [round(highest[i] - lowest[i], 4) for i in range(3)],
            "warnings": meta.get("warnings", [])[:8],
            "textures": [{"rel": asset.rel, "name": asset.stem,
                          "size": asset.size, "mtime": asset.mtime,
                          "role": role,
                          "label": ROLE_LABELS.get(role, "?")}
                         for role, assets in self.texture_maps_for(rel).items()
                         for asset in assets[:4]][:24],
            "animations": [{"rel": asset.rel, "name": asset.stem,
                            "size": asset.size}
                           for asset in self.animations_for(rel)][:32],
            "textured": bool(self.texture_assets_for(rel)
                             and self.settings.textured_previews),
        }

    def ensembles(self) -> list[dict]:
        """Modèles avec leurs textures et leurs animations, prêts à être vus entiers.

        C'est la vue « Ensembles » : un modèle du jeu n'est jamais seul — ses
        textures vivent dans un autre dossier et ses animations dans un
        ``*.anim.bfres``. On les réunit ici sans rien décoder (le nom suffit),
        pour que la liste s'affiche instantanément même sur un gros dump.
        """
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
        return rows

    def _version_label(self, rel: str) -> str:
        data = self.read_asset(rel)[:12]
        if data[:4] != b"FRES" or len(data) < 12:
            return ""
        value = int.from_bytes(data[8:12], "little")
        return f"{value >> 16}.{(value >> 8) & 0xFF}.{value & 0xFF}"

    def _geo_path(self, rel: str):
        size, mtime = self._version(rel)
        return self.geo_folder / f"{cache_mod.digest(rel, size, mtime)}.geo"

    def _model_meshes(self, rel: str) -> list[MeshData]:
        """Maillages d'un modèle : mémoire, puis disque, puis décodage."""
        size, mtime = self._version(rel)
        key = ("meshes", rel, size, mtime)
        if key in self._cache:
            return self._cache[key]
        path = self._geo_path(rel)
        cached = geo_mod.read(path) if path.is_file() else None
        if cached is not None:
            meshes, meta = cached
            self.geo_hits += 1
            if meta:
                self._reports.setdefault(rel, meta)
            return self._store_mesh(key, meshes)
        self.geo_misses += 1
        data = self.read_asset(rel)
        file = bfres_mod.BfresFile.parse(data)
        meshes = bfres_mod.meshes_from_file(file)
        report = getattr(file, "v10_report", None)
        meta = {
            "warnings": list(file.warnings[:12]),
            "version": f"{file.version >> 16}.{(file.version >> 8) & 0xFF}.{file.version & 0xFF}",
            "model": (file.models[0].name if file.models else ""),
            "materials": [m.name for m in file.models[0].materials] if file.models else [],
            "dropped": getattr(report, "dropped", 0) if report else 0,
            "submeshes": getattr(report, "submeshes", len(meshes)) if report else len(meshes),
        }
        self._reports[rel] = meta
        try:
            geo_mod.write(path, meshes, meta)
        except (OSError, AttributeError):
            pass
        return self._store_mesh(key, meshes)

    def model_meshes(self, rel: str) -> list[MeshData]:
        """Maillages d'un modèle (public : l'import .blend s'en sert pour l'échelle)."""
        return self._model_meshes(rel)

    def preview_plan(self, rel: str, width: int = 640, height: int = 480,
                     face_limit: int | None = None) -> dict:
        """Chiffre ce que coûterait l'aperçu avant de le rendre."""
        meshes = self._model_meshes(rel)
        try:
            planned = vram.plan(self.settings, meshes, width, height, face_limit)
        except vram.BudgetError as error:
            raise ProjectError(str(error)) from None
        return planned.as_json()

    def model_preview(self, rel: str, width: int = 640, height: int = 480,
                      yaw: float = 0.7, pitch: float = -0.35,
                      views: int = 1, face_limit: int | None = None,
                      textured: bool | None = None) -> bytes:
        """Rend un aperçu **dans le budget mémoire** réglé par l'utilisateur."""
        if textured is None:
            textured = bool(self.settings.textured_previews)
        key = self.thumbnail_key(rel, width, height, yaw, pitch, views,
                                 face_limit, textured)
        cached = self.thumbs.get(key)
        if cached:
            return cached
        meshes = self._model_meshes(rel)
        limit = face_limit
        if limit is None:
            # Un plafond fixe troue les modèles denses (20 000 faces sur 165 000
            # laissent des trous) : on suit la densité du modèle, sans jamais
            # dépasser le plafond de l'aperçu détaillé.
            total = sum(len(mesh.faces) for mesh in meshes)
            thumb = self.settings.thumb_faces or 20_000
            limit = min(total, max(thumb, total // 2))
            if self.settings.preview_faces:
                limit = min(limit, self.settings.preview_faces)
            if width >= 480 and limit < total and self.settings.preview_faces:
                limit = min(total, max(limit, self.settings.preview_faces))
        try:
            planned = vram.plan(self.settings, meshes, width, height, limit)
        except vram.BudgetError as error:
            raise ProjectError(str(error)) from None
        data = self._flatten(rel, meshes, textured=textured)
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
        return self.thumbs.put(key, png)

    def _flatten(self, rel: str, meshes, textured: bool = False):
        """Géométrie d'affichage aplatie, gardée pour les aperçus suivants."""
        from hookymod.viewer import build_render_data

        size, mtime = self._version(rel)
        key = (rel, bool(textured))
        entry = self._render_data.get(key)
        if entry and entry[0] == size and entry[1] == mtime:
            return entry[2]
        samplers = self.albedo_samplers(rel) if textured else None
        data = build_render_data(meshes, textures=samplers)
        if len(self._render_data) > 24:
            self._render_data.clear()      # borne simple : 24 modèles aplatis
        self._render_data[key] = (size, mtime, data)
        return data

    def texture_assets_for(self, rel: str) -> list:
        """Textures d'albédo les plus proches d'un modèle, de la plus sûre à la moins sûre.

        Classement : nom exact du modèle, puis famille (``Weapon_Sword``), puis
        première composante (``Weapon``). On ne garde que les albédos
        (``_Alb``, ``albedo``, ``basecolor``, ``_Dif``) : une normale ou une
        métallique ne doit pas colorer le modèle.
        """
        entry = self._texture_index.get(rel)
        if entry is not None:
            return entry
        stem = Path(rel).name.split(".")[0].lower()
        parts = stem.split("_")
        family = "_".join(parts[:2]) if len(parts) > 1 else stem
        ranked: dict[int, list] = {0: [], 1: [], 2: []}
        for asset in self.by_kind("texture"):
            low = asset.rel.lower()
            name = Path(asset.rel).name.lower()
            if not any(tag in name for tag in ("_alb", "albedo", "basecolor", "_dif")):
                continue
            if stem and stem in low:
                ranked[0].append(asset)
            elif family and family in low:
                ranked[1].append(asset)
            elif parts and parts[0] and parts[0] in low:
                ranked[2].append(asset)
        found = next((ranked[rank] for rank in (0, 1, 2) if ranked[rank]), [])
        self._texture_index[rel] = found
        return found

    def animations_for(self, rel: str) -> list:
        """Animations (``*.anim.bfres``) qui vont avec ce modèle.

        Le jeu ne nomme pas ses animations comme ses modèles : à côté de
        ``Animal_Cat.Animal_Cat.bfres`` vit ``Animal_Cat_Animation.anim.bfres``.
        On enlève donc le suffixe ``_Animation`` / ``_Player_Animation`` avant de
        comparer, puis on accepte les variantes préfixées (``Animal_Dog_Wild``
        pour ``Animal_Dog``) : c'est le seul appariement possible sans ouvrir
        chaque fichier, et il reste instantané.
        """
        if self._animation_index is None:
            self._animation_index = [
                (_animation_key(Path(asset.rel).name), asset)
                for asset in self.by_kind("animation")]
        base = _animation_key(Path(rel).name)
        found = []
        for key, asset in self._animation_index:
            if key == base or base.startswith(key + "_") or key.startswith(base + "_"):
                found.append(asset)
        return found

    def texture_maps_for(self, rel: str) -> dict[str, list]:
        """**Toutes** les cartes du modèle, groupées par rôle (``albedo``, ``normal``…).

        On classe les textures du projet par précision du nom (nom du modèle,
        puis famille ``Weapon_Sword``, puis préfixe), puis par rôle d'après le
        suffixe du fichier. Une texture au suffixe inconnu finit dans ``autre`` :
        elle est exportée quand même, jamais jetée par ignorance.
        """
        cached = self._map_index.get(rel)
        if cached is not None:
            return cached
        stem = Path(rel).name.split(".")[0].lower()
        parts = stem.split("_")
        family = "_".join(parts[:2]) if len(parts) > 1 else stem
        ranks: dict[int, list] = {0: [], 1: [], 2: []}
        for asset in self.by_kind("texture"):
            low = Path(asset.rel).name.lower()
            if stem and stem in low:
                ranks[0].append(asset)
            elif family and family in low:
                ranks[1].append(asset)
            elif parts and parts[0] and parts[0] in low:
                ranks[2].append(asset)
        chosen = next((ranks[rank] for rank in (0, 1, 2) if ranks[rank]), [])
        buckets: dict[str, list] = {}
        for asset in chosen:
            role = asset.role or texture_maps.classify(asset.rel)
            buckets.setdefault(role, []).append(asset)
        for role, items in buckets.items():
            # le nom le plus court est le plus proche du modèle (« X_Alb » plutôt
            # que « X_Alb_Copy_02 »)
            items.sort(key=lambda item: (len(item.rel), item.rel))
        self._map_index[rel] = buckets
        return buckets

    def export_maps_for(self, rel: str, folder: Path,
                        limit: int = 24) -> dict:
        """Écrit **toutes** les cartes du modèle en PNG, plus ``TEXTURES.md``.

        :return: ``{maps: {rôle: fichier}, files: [...], textures: n,
                  warnings: [...]}``.
        """
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        buckets = self.texture_maps_for(rel)
        base = _display_name(Path(rel).name.split(".")[0])
        maps: dict[str, str] = {}
        files: list[dict] = []
        warnings: list[str] = []
        raw: dict[str, bytes] = {}
        written = 0

        for role in ROLE_ORDER:
            for asset in buckets.get(role, [])[:1]:
                if written >= limit:
                    warnings.append(f"plus de {limit} cartes : les suivantes sont "
                                    "ignorées (exporte-les seules)")
                    break
                try:
                    png = self.texture_png(asset.rel, 0)
                except ProjectError as error:
                    warnings.append(f"{Path(asset.rel).name} illisible : {error}")
                    continue
                name = f"{_png_stem(asset.stem)}.png"
                (folder / name).write_bytes(png)
                maps[role] = name
                raw[role] = png
                files.append({"role": role, "label": ROLE_LABELS.get(role, "?"),
                              "file": name, "source": asset.rel})
                written += 1

        # glTF n'a qu'une image pour rugosité + métal (G et B) : on la fabrique
        if "metalrough" not in maps and ("roughness" in raw or "metallic" in raw):
            composed = texture_maps.compose_metal_roughness(raw.get("roughness"),
                                                           raw.get("metallic"))
            if composed:
                name = f"{base}_MetalRough.png"
                (folder / name).write_bytes(composed)
                maps["metalrough"] = name
                parts = [label for role, label in (("roughness", "Rgh"),
                                                   ("metallic", "Mtl"))
                         if role in raw]
                files.append({"role": "metalrough", "label": "MR", "file": name,
                              "source": " + ".join(parts)})

        if not maps:
            warnings.append("aucune texture ne porte le nom de ce modèle : "
                            "rien à brancher (le modèle sort sans couleur)")
        else:
            blendfile.write_shader_script(folder, base)   # branchement dans Blender
        self._write_textures_note(folder, base, rel, files, warnings)
        return {"maps": maps, "files": files, "textures": len(files),
                "warnings": warnings}

    def _write_textures_note(self, folder: Path, base: str, rel: str,
                             files: list[dict], warnings: list[str]) -> Path:
        """``TEXTURES.md`` : quelle image va sur quelle entrée de shader."""
        lines = [f"# Textures de {base}", "",
                 f"Modèle : `{rel}`", "",
                 "Chaque fichier va sur **une** entrée du shader. Dans Blender, ouvre le",
                 "matériau → Principled BSDF, puis glisse l'image sur l'entrée indiquée.",
                 "",
                 "Pour ne pas le faire à la main : **`assembler_shaders.py`** (dans ce",
                 "dossier) branche tout d'un coup — onglet Scripting ▸ Open ▸ Run Script,",
                 "ou `blender --background --python assembler_shaders.py`.", "",
                 "| Fichier | Rôle | Entrée Blender |", "| --- | --- | --- |"]
        for item in files:
            help_text = texture_maps.ROLE_HELP.get(item["role"], "")
            lines.append(f"| `{item['file']}` | {item['label']} | {help_text} |")
        if not files:
            lines.append("| — | — | aucune texture trouvée pour ce modèle |")
        lines += ["", "## Espace colorimétrique (la cause n°1 d'un rendu « lavé »)", "",
                  "* **Albédo** : sRGB (c'est une couleur).",
                  "* **Tout le reste** (normale, rugosité, métal, AO) : **Non-Color**.",
                  "  Une normale lue en sRGB donne des facettes qui semblent fausses ;",
                  "  une rugosité en sRGB donne un métal trop brillant.", "",
                  "## Réglages qui vont avec", "",
                  "* normale : nœud **Normal Map**, force (`Strength`) 1.0 ;",
                  "* métal : 1.0 quand la carte `Mtl` est blanche à cet endroit, sinon 0 ;",
                  "* rugosité : `Roughness` reçoit la carte directement ;",
                  "* si tu n'as **que** l'albédo (pas de `Nrm`, `Rgh`…) : ce n'est pas une",
                  "  erreur, le modèle n'a pas plus de cartes — garde `Roughness` ≈ 0.5.",
                  "",
                  "## Rugosité + métal : deux façons", "",
                  "* **Deux images** (`Rgh` sur Roughness, `Mtl` sur Metallic) : le plus",
                  "  lisible dans Blender, c'est ce que fait `assembler_shaders.py` ;",
                  "* **Une image** `*_MetalRough.png` : c'est l'emballage glTF/Unity —",
                  "  **G = rugosité, B = métal**, R et A ignorés. Pour l'utiliser dans",
                  "  Blender : nœud `Separate Color`, puis G → Roughness et B → Metallic.",
                  "",
                  "## Sortie glTF", "",
                  "Le `.gltf` porte déjà : `baseColorTexture`, `normalTexture`,",
                  "`metallicRoughnessTexture` (rugosité en G, métal en B) et",
                  "`occlusionTexture`. Les cartes sans équivalent glTF (spéculaire,",
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
        """Albédo le plus probable du modèle, ou ``None`` (jamais une erreur)."""
        for asset in self.texture_assets_for(rel):
            try:
                return asset, self.texture_png(asset.rel, 0)
            except Exception:      # texture illisible : on juge les UV seuls
                continue
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

    def export_blend(self, rel: str, textures: bool = True) -> dict:
        """Exporte le modèle puis produit ``.blend`` (via Blender s'il est là).

        Sans Blender installé, l'export glTF/OBJ/PNG reste complet et le script
        ``creer_blend.py`` est déposé à côté : la fonction le dit explicitement
        au lieu de laisser croire qu'un ``.blend`` a été écrit.
        """
        meshes = self._model_meshes(rel)
        folder = self.work / "export" / Path(rel).name.split(".")[0]
        found = self.export_maps_for(rel, folder) if textures else {
            "maps": {}, "files": [], "textures": 0, "warnings": []}
        written = blender.export_bundle(meshes, folder, folder.name,
                                        textures=self._albedo_index(rel, folder),
                                        maps=found["maps"] or None)
        executable = blendfile.find_blender(self.settings.blender_path)
        result = blendfile.make_blend(folder, folder.name, blender=executable)
        files = {path.name for path in written.values()}
        files.add(Path(result["script"]).name)
        if result["blend"]:
            files.add(Path(result["blend"]).name)
        return {
            "folder": self.shown_path(folder),
            "files": sorted(files),
            "textures": found["textures"],
            "maps": found["files"],
            "faces": sum(len(mesh.faces) for mesh in meshes),
            "method": result["method"],
            "blend": self.shown_path(Path(result["blend"])) if result["blend"] else "",
            "script": Path(result["script"]).name,
            "command": result["command"],
            "message": result["message"],
            "output": (result.get("output") or "")[-1500:],
            "blender": str(executable) if executable else "",
            "platform_note": blendfile.platform_note(),
        }

    def export_model(self, rel: str, textures: bool = True) -> dict:
        """Exporte géométrie **et toutes les cartes** de texture du modèle."""
        meshes = self._model_meshes(rel)
        folder = self.work / "export" / Path(rel).name.split(".")[0]
        found = self.export_maps_for(rel, folder) if textures else {
            "maps": {}, "files": [], "textures": 0, "warnings": []}
        written = blender.export_bundle(meshes, folder, folder.name,
                                        textures=self._albedo_index(rel, folder),
                                        maps=found["maps"] or None)
        meta = self._reports.get(rel, {})
        files = sorted({path.name for path in written.values()}
                       | {item["file"] for item in found["files"]}
                       | {"TEXTURES.md"})
        warnings = meta.get("warnings", [])[:6] + found["warnings"]
        return {"folder": self.shown_path(folder),
                "files": files,
                "textures": found["textures"],
                "maps": found["files"],
                "faces": sum(len(m.faces) for m in meshes),
                "submeshes": len(meshes),
                "warnings": warnings}

    def _albedo_index(self, rel: str, folder: Path) -> dict[str, str]:
        """PNG d'albédo indexés par nom de matériau (Blender les y retrouve)."""
        buckets = self.texture_maps_for(rel)
        found: dict[str, str] = {}
        # un albédo par matériau : on ne tronque pas la liste, sinon un modèle à
        # six matériaux n'en garde que quatre et les autres sortent en gris
        for asset in buckets.get("albedo", [])[:32]:
            target = folder / f"{_png_stem(asset.stem)}.png"
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
            data = self.read_asset(model)
            target = mob_dir / f"{slug}.bfres.zs"
            # On recopie *tel quel* quand c'est déjà compressé : réécrire une
            # frame zstd sans nécessité, c'est un risque de la rendre illisible.
            source = self.asset(model).read_bytes()
            target.write_bytes(source if zs.is_zstd(source) else data)
            copied.append({"from": model, "to": target.name, "kind": "model"})
        for rel in textures:
            info = self.texture_info(rel)
            name_t = info["textures"][0]["name"]
            suffix = name_t.split("_")[-1]
            target = mob_dir / f"{slug}_{suffix}.bntx.zs"
            target.write_bytes(self.asset(rel).read_bytes())
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
        return entry

    # ------------------------------------------------------------------ export
    def export_selection(self, items: list[dict]) -> bytes:
        """ZIP de la sélection : OBJ + glTF pour les modèles, PNG pour les textures."""
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
        with tempfile.TemporaryDirectory() as folder, \
                zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in items:
                rel = str(item.get("rel") or "")
                kind = str(item.get("kind") or "")
                if not rel:
                    continue
                if kind == "texture":
                    png = self.texture_png(rel, int(item.get("index", 0)))
                    stem = Path(rel).name.split(".")[0]
                    archive.writestr(f"textures/{stem}.png", png)
                    lines.append(f"[texture] {rel}")
                    textures += 1
                    continue
                if kind != "model":
                    continue
                stem = Path(rel).name.split(".")[0]
                target = Path(folder) / stem
                maps = self.export_maps_for(rel, target)
                written = blender.export_bundle(
                    self._model_meshes(rel), target, stem,
                    textures=self._albedo_index(rel, target),
                    maps=maps["maps"] or None)
                # tout ce que l'export a produit : le .bin du glTF et les PNG
                # compris (sans le .bin, Blender refuse le glTF)
                for produced in sorted(target.rglob("*")):
                    if produced.is_file():
                        relative = produced.relative_to(target)
                        archive.write(produced, f"modeles/{stem}/{relative.as_posix()}")
                lines.append(f"[modèle] {rel} ({len(written)} fichier(s) + "
                             f"{maps['textures']} carte(s) de texture)")
                models += 1
            lines.insert(1, f"{models} modèle(s), {textures} texture(s)")
            archive.writestr("LISEZMOI.txt", "\n".join(lines) + "\n")
        if not models and not textures:
            raise ProjectError("Rien à exporter dans cette sélection.")
        return buffer.getvalue()

    def export_selection_blend(self, items: list[dict]) -> bytes:
        """Un seul ZIP pour toute la sélection : un ``.blend`` par modèle.

        Un modèle sans ``.blend`` (Blender absent) apporte quand même son script
        ``creer_blend.py`` et sa commande, consignée dans ``LIRE-MOI.txt`` : le
        ZIP est toujours utilisable, et jamais présenté comme complet s'il ne
        l'est pas.
        """
        import io
        import zipfile

        models = [str(item.get("rel") or "") for item in items
                  if str(item.get("kind") or "") == "model" and item.get("rel")]
        if not models:
            raise ProjectError("Sélectionne au moins un modèle pour un export .blend.")
        if len(models) > 50:
            raise ProjectError(f"{len(models)} modèles d'un coup, c'est trop "
                               "(50 maximum) : découpe la sélection.")
        buffer = io.BytesIO()
        notes = ["HookyCreator — export .blend", "",
                 "Chaque dossier contient le modèle prêt pour Blender :",
                 "  <modele>.gltf + <modele>.bin  →  à ouvrir, ou à passer au script",
                 "  creer_blend.py                →  fabrique <modele>.blend",
                 "  <modele>.blend                →  présent seulement si Blender a été",
                 "                                   trouvé au moment de l'export",
                 ""]
        blends = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for rel in models:
                name = Path(rel).name.split(".")[0]
                try:
                    result = self.export_blend(rel)
                except ProjectError as error:
                    notes.append(f"{name} : échec ({error})")
                    continue
                source = self.work / "export" / name
                for produced in sorted(source.iterdir()):
                    if produced.is_file():
                        archive.write(produced, f"{name}/{produced.name}")
                if result["method"] == "blender":
                    blends += 1
                    notes.append(f"{name} : {name}.blend écrit par Blender.")
                else:
                    notes.append(
                        f"{name} : pas de .blend (Blender introuvable) — depuis le "
                        f"dossier {name}/, lance :")
                    notes.append(f"    blender --background --factory-startup "
                                 f"--python creer_blend.py -- {name}.gltf {name}.blend")
            notes.append("")
            notes.append(f"{blends} fichier(s) .blend sur {len(models)} modèle(s).")
            notes.append("Le glTF, le .bin et les PNG sont joints : l'export "
                         "reste utilisable sans Blender.")
            archive.writestr("LIRE-MOI.txt", "\n".join(notes) + "\n")
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
    def state(self) -> dict:
        return {
            "root": str(self.root),
            "work": str(self.work.relative_to(self.root)) if self.work.is_relative_to(self.root) else str(self.work),
            "assets": [a.as_json() for a in self.assets],
            "mobs": self.list_mobs(),
            "dicts": len(self._load_dicts()),
            "zstd": zs.zstd_available(),
            "settings": self.settings.as_json(),
            "cache": self.cache_stats(),
            "scanned_at": self.scanned_at,
            "scan_seconds": self.scan_seconds,
            "edited": sorted(
                str(p.relative_to(self.work / "assets"))
                for p in (self.work / "assets").rglob("*") if p.is_file()
            ) if (self.work / "assets").is_dir() else [],
        }


def open_project(root, work=None) -> Project:
    project = Project(Path(root), Path(work) if work else None)
    project.scan()
    return project
