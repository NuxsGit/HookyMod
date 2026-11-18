"""Archived mob sheets and assembly; not used or packaged by HookyCreator.

This historical builder deletes an existing mods/<name> folder before writing.
It remains source-only for future separation, not as a supported application.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from hookycreator.project import Project, ProjectError
from hookymod import zs


class LegacyModProject(Project):
    """Original experimental functions retained separately for reference."""

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
