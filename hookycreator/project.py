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
from hookymod.mesh import MeshData

MODEL_SUFFIXES = (".bfres.zs", ".bfres")
TEXTURE_SUFFIXES = (".bntx.zs", ".bntx")
MAX_SCAN_FILES = 40_000


@dataclass
class Asset:
    """Un fichier du projet, tel qu'il apparaît dans le navigateur."""

    rel: str
    kind: str                       # model | texture | bytecode | other
    size: int
    compressed: bool = False

    def as_json(self) -> dict:
        return {"rel": self.rel, "kind": self.kind, "size": self.size,
                "compressed": self.compressed, "name": Path(self.rel).name}

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
        if self._dicts is None:
            self._dicts = zs.ZstdDicts()
            if zs.zstd_available():
                for candidate in zs.find_dictionaries_near(self.root):
                    try:
                        self._dicts.load_file(candidate)
                    except Exception:      # dictionnaire illisible : on continue
                        pass
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
            if low.endswith(MODEL_SUFFIXES):
                kind = "model"
            elif low.endswith(TEXTURE_SUFFIXES):
                kind = "texture"
            elif low.endswith((".byml", ".bgyml", ".bcett.byml")):
                kind = "bytecode"
            elif low.endswith((".pack.zs", ".pack", ".sarc", ".szs", ".sarc.zs")):
                kind = "archive"
            else:
                continue
            assets.append(Asset(rel=rel, kind=kind, size=path.stat().st_size,
                                compressed=low.endswith(".zs")))
        self.assets = assets
        return {
            "root": str(self.root),
            "work": str(self.work),
            "count": len(assets),
            "truncated": truncated,
            "models": sum(1 for a in assets if a.kind == "model"),
            "textures": sum(1 for a in assets if a.kind == "texture"),
            "bytecode": sum(1 for a in assets if a.kind == "bytecode"),
            "archives": sum(1 for a in assets if a.kind == "archive"),
        }

    def by_kind(self, kind: str) -> list[Asset]:
        return [a for a in self.assets if a.kind == kind]

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
        file = self.texture_file(rel)
        texture = file.textures[index] if 0 <= index < len(file.textures) else None
        if texture is None:
            raise ProjectError(f"Texture {index} absente de {rel}.")
        return texture.to_png(mip)

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
        return {"rel": rel, "texture": name, "wrote": str(target.relative_to(self.root)),
                "size": len(payload), "mips": getattr(report, "mip_count", None),
                "format": self.texture_info(rel)["textures"][index]["format"]}

    # ------------------------------------------------------------------ modèles
    def model_info(self, rel: str) -> dict:
        data = self.read_asset(rel)
        if data[:4] != b"FRES":
            raise ProjectError("Ce fichier n'est pas un BFRES.")
        file = bfres_mod.BfresFile.parse(data)
        meshes = bfres_mod.meshes_from_file(file)
        lo, hi = meshes[0].bounds()
        total = sum(len(m.faces) for m in meshes)
        return {
            "rel": rel,
            "version": f"{file.version >> 16}.{(file.version >> 8) & 0xFF}.{file.version & 0xFF}",
            "models": [m.name for m in file.models],
            "meshes": len(meshes),
            "faces": total,
            "vertices": sum(len(m.positions) for m in meshes),
            "materials": [mat.name for m in file.models for mat in m.materials],
            "bounds": [round(hi[i] - lo[i], 4) for i in range(3)],
            "warnings": file.warnings[:6],
        }

    def _model_meshes(self, rel: str) -> list[MeshData]:
        key = ("meshes", rel)
        if key not in self._cache:
            data = self.read_asset(rel)
            file = bfres_mod.BfresFile.parse(data)
            self._cache[key] = bfres_mod.meshes_from_file(file)
        return self._cache[key]

    def model_preview(self, rel: str, width: int = 640, height: int = 480,
                      yaw: float = 0.7, pitch: float = -0.35,
                      views: int = 1) -> bytes:
        meshes = self._model_meshes(rel)
        if views and views > 1:
            return render.turntable_png(meshes, views=views, width=width, height=height)
        return render.render_png(meshes, width, height, yaw, pitch, supersample=2)

    def export_model(self, rel: str, textures: bool = True) -> dict:
        meshes = self._model_meshes(rel)
        folder = self.work / "export" / Path(rel).name.split(".")[0]
        found: dict[str, str] = {}
        if textures:
            stem = Path(rel).name.split(".")[0].split("_")[0]
            for asset in self.by_kind("texture"):
                if stem.lower() in asset.rel.lower():
                    try:
                        target = folder / f"{asset.stem}.png"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(self.texture_png(asset.rel, 0))
                        found[asset.stem] = str(target)
                    except ProjectError:
                        continue
        written = blender.export_bundle(meshes, folder, folder.name,
                                        textures=found or None)
        return {"folder": str(folder.relative_to(self.root)),
                "files": sorted(p.name for p in written.values()),
                "textures": len(found),
                "faces": sum(len(m.faces) for m in meshes)}

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
        return {"mod": clean, "path": str(target.parent.relative_to(self.root)),
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
            "edited": sorted(
                str(p.relative_to(self.work / "assets"))
                for p in (self.work / "assets").rglob("*") if p.is_file()
            ) if (self.work / "assets").is_dir() else [],
        }


def open_project(root, work=None) -> Project:
    project = Project(Path(root), Path(work) if work else None)
    project.scan()
    return project
