"""Réglages de l'atelier : budget mémoire, qualité de rendu, culling.

Le rendu se fait sur le processeur (aucune dépendance GPU), mais il consomme
quand même de la mémoire : chaque sommet et chaque face est copié dans une
structure de rendu, et l'image garde un tampon de couleurs plus un tampon de
profondeur. Sur un très gros modèle, cela suffit à faire tomber la machine.

D'où ce module : un **budget** exprimé en gigaoctets (6 Go par défaut, jusqu'à
25 Go dans les réglages) et une politique de dégradation qui reste visible :
on réduit d'abord l'anticrénelage, puis le nombre de faces dessinées, et si
rien ne rentre on refuse en expliquant combien il faudrait.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

#: bornes du budget mémoire proposé dans l'interface
VRAM_MIN_GB = 1.0
VRAM_DEFAULT_GB = 6.0
VRAM_MAX_GB = 25.0

#: bornes des autres réglages
SUPERSAMPLE_MIN, SUPERSAMPLE_MAX = 1, 4
FACES_MIN, FACES_MAX = 200, 4_000_000
LOD_MAX = 3

#: préférences d'interface (le « look » de l'atelier)
ACCENTS = ("menthe", "azur", "ambre", "framboise")
DENSITIES = ("confort", "compact")

#: plafond du cache de vignettes sur disque (Mo)
CACHE_MIN_MB, CACHE_MAX_MB, CACHE_DEFAULT_MB = 0, 4096, 256


class SettingsError(ValueError):
    """Réglage refusé (valeur hors bornes ou type inattendu)."""


def _number(value, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{name} : valeur numérique attendue") from None
    if number != number:                      # NaN
        raise SettingsError(f"{name} : valeur invalide")
    return max(minimum, min(maximum, number))


def _flag(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise SettingsError(f"{name} : booléen attendu")


def _choice(value, name: str, allowed: tuple[str, ...]) -> str:
    text = str(value).strip().lower()
    if text not in allowed:
        raise SettingsError(f"{name} : « {value} » inconnu "
                            f"(attendu : {', '.join(allowed)})")
    return text


@dataclass
class Settings:
    """Tout ce qui décide du coût d'un aperçu."""

    vram_gb: float = VRAM_DEFAULT_GB
    supersample: int = 2                      # anticrénelage (1 = rapide)
    cull: bool = True                         # faces vues de dos
    frustum: bool = True                      # faces hors de l'écran
    adaptive: bool = True                     # dégrader plutôt que de planter
    max_faces: int | None = None               # plafond manuel (None = auto)
    lod: int = 0                              # niveau de détail demandé
    # -- préférences d'interface
    accent: str = "menthe"                    # couleur d'accent
    density: str = "confort"                  # confort | compact
    export_textures: bool = True              # joindre les PNG à l'export Blender
    thumb_faces: int | None = 20_000          # plafond des vignettes (None = auto)
    preview_faces: int = 60_000               # plafond de l'aperçu détaillé (0 = tout)
    blender_path: str = ""                    # blender.exe (vide = détection auto)
    textured_previews: bool = True            # vignettes colorées par les textures
    cache_mb: int = CACHE_DEFAULT_MB          # cache de vignettes sur disque

    # ------------------------------------------------------------------ état
    def clamp(self) -> "Settings":
        """Ramène chaque valeur dans ses bornes (jamais d'exception ici)."""
        return Settings(
            vram_gb=round(_number(self.vram_gb, "vram_gb", VRAM_MIN_GB, VRAM_MAX_GB), 2),
            supersample=int(_number(self.supersample, "supersample",
                                    SUPERSAMPLE_MIN, SUPERSAMPLE_MAX)),
            cull=bool(self.cull),
            frustum=bool(self.frustum),
            adaptive=bool(self.adaptive),
            max_faces=(None if self.max_faces is None
                       else int(_number(self.max_faces, "max_faces", FACES_MIN, FACES_MAX))),
            lod=int(_number(self.lod, "lod", 0, LOD_MAX)),
            accent=_choice(self.accent, "accent", ACCENTS),
            density=_choice(self.density, "density", DENSITIES),
            export_textures=bool(self.export_textures),
            preview_faces=int(_number(self.preview_faces, "preview_faces", 0, 400_000)),
            blender_path=str(self.blender_path or "")[:400],
            textured_previews=bool(self.textured_previews),
            thumb_faces=(None if self.thumb_faces is None
                         else int(_number(self.thumb_faces, "thumb_faces",
                                          FACES_MIN, FACES_MAX))),
            cache_mb=int(_number(self.cache_mb, "cache_mb",
                                 CACHE_MIN_MB, CACHE_MAX_MB)),
        )

    @property
    def budget_bytes(self) -> int:
        return int(self.vram_gb * 1024 ** 3)

    def as_json(self) -> dict:
        return {
            "vram_gb": self.vram_gb,
            "vram_max_gb": VRAM_MAX_GB,
            "vram_min_gb": VRAM_MIN_GB,
            "budget_bytes": self.budget_bytes,
            "supersample": self.supersample,
            "cull": self.cull,
            "frustum": self.frustum,
            "adaptive": self.adaptive,
            "max_faces": self.max_faces,
            "lod": self.lod,
            "accent": self.accent,
            "density": self.density,
            "export_textures": self.export_textures,
            "thumb_faces": self.thumb_faces,
            "preview_faces": self.preview_faces,
            "blender_path": self.blender_path,
            "textured_previews": bool(self.textured_previews),
            "cache_mb": self.cache_mb,
            "cache_min_mb": CACHE_MIN_MB,
            "cache_max_mb": CACHE_MAX_MB,
            "accents": list(ACCENTS),
            "densities": list(DENSITIES),
        }

    # ------------------------------------------------------------------ entrée/sortie
    @classmethod
    def from_json(cls, payload: dict) -> "Settings":
        """Construit des réglages depuis un dictionnaire (API ou fichier)."""
        if not isinstance(payload, dict):
            raise SettingsError("réglages : objet JSON attendu")
        base = cls()
        values = {
            "vram_gb": payload.get("vram_gb", base.vram_gb),
            "supersample": payload.get("supersample", base.supersample),
            "cull": payload.get("cull", base.cull),
            "frustum": payload.get("frustum", base.frustum),
            "adaptive": payload.get("adaptive", base.adaptive),
            "max_faces": payload.get("max_faces", base.max_faces),
            "lod": payload.get("lod", base.lod),
            "accent": payload.get("accent", base.accent),
            "density": payload.get("density", base.density),
            "export_textures": payload.get("export_textures", base.export_textures),
            "thumb_faces": payload.get("thumb_faces", base.thumb_faces),
            "preview_faces": payload.get("preview_faces", base.preview_faces),
            "blender_path": str(payload.get("blender_path", base.blender_path) or "")[:400],
            "textured_previews": bool(payload.get("textured_previews",
                                                  base.textured_previews)),
            "cache_mb": payload.get("cache_mb", base.cache_mb),
        }
        for name in ("cull", "frustum", "adaptive", "export_textures"):
            values[name] = _flag(values[name], name)
        for name in ("max_faces", "thumb_faces"):
            if values[name] in ("", 0, "auto", None):
                values[name] = None
        return cls(**values).clamp()

    @classmethod
    def load(cls, path: Path) -> "Settings":
        """Charge les réglages ; un fichier illisible retombe sur les défauts."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        try:
            return cls.from_json(payload)
        except SettingsError:
            return cls()

    def save(self, path: Path) -> Path:
        from hookymod.util import write_atomic

        path = Path(path)
        data = json.dumps(self.as_json(), indent=2, ensure_ascii=False).encode("utf-8")
        write_atomic(path, data)
        return path

    # ------------------------------------------------------------------ dérivés
    def with_overrides(self, **changes) -> "Settings":
        """Copie avec quelques réglages changés, revalidés."""
        clean = {key: value for key, value in changes.items() if value is not None}
        return replace(self, **clean).clamp()
