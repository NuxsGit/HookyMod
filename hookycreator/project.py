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
from hookymod import bntx_encode, blender, render, util, zs
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

    def as_json(self) -> dict:
        return {"rel": self.rel, "kind": self.kind, "size": self.size,
                "compressed": self.compressed, "name": Path(self.rel).name,
                "mtime": self.mtime}

    @property
    def stem(self) -> str:
        name = Path(self.rel).name
        for suffix in (".zs",):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name.rsplit(".", 1)[0]


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
        # géométrie aplatie pour l'affichage : reconstruite à chaque aperçu,
        # elle coûtait ~1 s sur un modèle de 170 000 faces
        self._render_data: dict[str, tuple] = {}
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
                                mtime=int(stat.st_mtime)))
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
        return cache_mod.digest(("rendu", settings.supersample, settings.cull,
                                 settings.frustum, settings.max_faces,
                                 settings.thumb_faces, settings.lod))

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

    def thumbnail_key(self, rel: str, width: int, height: int,
                      yaw: float = 0.7, pitch: float = -0.35,
                      views: int = 1, face_limit: int | None = None) -> str:
        """Clé de cache d'une vignette (identique pour l'aperçu et le préchauffage)."""
        return cache_mod.digest(("apercu", rel, self._version(rel), width, height,
                                 round(yaw, 2), round(pitch, 2), views,
                                 self.settings.thumb_faces if face_limit is None
                                 else face_limit,
                                 self._render_digest()))

    def thumbnail_cached(self, rel: str, width: int, height: int) -> bool:
        try:
            key = self.thumbnail_key(rel, width, height)
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
        }

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
                      views: int = 1, face_limit: int | None = None) -> bytes:
        """Rend un aperçu **dans le budget mémoire** réglé par l'utilisateur."""
        key = self.thumbnail_key(rel, width, height, yaw, pitch, views, face_limit)
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
        data = self._flatten(rel, meshes)
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

    def _flatten(self, rel: str, meshes):
        """Géométrie d'affichage aplatie, gardée pour les aperçus suivants."""
        from hookymod.viewer import build_render_data

        size, mtime = self._version(rel)
        entry = self._render_data.get(rel)
        if entry and entry[0] == size and entry[1] == mtime:
            return entry[2]
        data = build_render_data(meshes)
        if len(self._render_data) > 24:
            self._render_data.clear()      # borne simple : 24 modèles aplatis
        self._render_data[rel] = (size, mtime, data)
        return data

    def export_textures_for(self, rel: str, folder: Path) -> dict[str, str]:
        """PNG d'albédo du modèle, indexés par nom de matériau **et** par nom de fichier.

        On ne devine pas : on classe les textures candidates par précision de
        nom (nom exact du modèle, puis famille ``Weapon_Sword``) et on ne garde
        que les albédos (``_Alb``…). Le nom d'un matériau (``Mt_Blade``) ne
        contient jamais celui de la texture : l'index contient donc aussi la
        forme sans suffixe, ce qui permet à Blender de retrouver la bonne image.
        """
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
        candidates = next((ranked[rank] for rank in (0, 1, 2) if ranked[rank]), [])
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

    def export_model(self, rel: str, textures: bool = True) -> dict:
        meshes = self._model_meshes(rel)
        folder = self.work / "export" / Path(rel).name.split(".")[0]
        found: dict[str, str] = self.export_textures_for(rel, folder) if textures else {}
        written = blender.export_bundle(meshes, folder, folder.name,
                                        textures=found or None)
        meta = self._reports.get(rel, {})
        return {"folder": self.shown_path(folder),
                "files": sorted({path.name for path in written.values()}),
                "textures": len({path for path in found.values()}),
                "faces": sum(len(m.faces) for m in meshes),
                "submeshes": len(meshes),
                "warnings": meta.get("warnings", [])[:6]}

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
                found = self.export_textures_for(rel, target)
                written = blender.export_bundle(self._model_meshes(rel), target,
                                                stem, textures=found or None)
                # tout ce que l'export a produit : le .bin du glTF et les PNG
                # compris (sans le .bin, Blender refuse le glTF)
                for produced in sorted(target.rglob("*")):
                    if produced.is_file():
                        relative = produced.relative_to(target)
                        archive.write(produced, f"modeles/{stem}/{relative.as_posix()}")
                lines.append(f"[modèle] {rel} ({len(written)} fichier(s) + "
                             f"{len(set(found.values()))} texture(s))")
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
