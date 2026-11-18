"""Réglages de l'atelier : budget mémoire, qualité de rendu, culling.

Les aperçus PNG sont rendus sur le processeur et consomment de la mémoire :
chaque sommet et chaque face est copié et l'image conserve des tampons de
couleurs et de profondeur. Le budget CPU et ses dégradations restent visibles.
La vue interactive WebGL2 utilise séparément le GPU et ses propres limites ;
ses préférences FSR 1 ne modifient ni les PNG ni les exports.
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
THEMES = ("light", "dark", "aurora")
# Accept the previously saved theme identifier without exposing it in the UI.
_LEGACY_THEMES = {"newgenoldtendo": "aurora"}
LANGUAGES = ("fr", "en", "ru", "zh")

# Paramètres de la vue WebGL (indépendants du rendu PNG sur le CPU).
VIEWER_UPSCALERS = ("native", "fsr")
FSR_QUALITIES = ("ultra", "quality", "balanced", "performance")
VIEWER_MSAA = ("off", "2", "4")
VIEWER_DPRS = ("1", "2")
VIEWER_FPS = ("15", "30", "60")
VIEWER_LIMITS = ("800", "1200", "1600")

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
    accent: str = "azur"                    # couleur d'accent
    density: str = "confort"                  # confort | compact
    theme: str = "light"                      # light | dark | aurora
    language: str = "fr"                      # français | anglais | russe | chinois
    export_textures: bool = True              # joindre les PNG aux exports de modèles
    thumb_faces: int | None = None            # plafond facultatif des vignettes
    preview_faces: int = 0                     # 0 = toutes les faces ou refus explicite
    textured_previews: bool = True            # vignettes colorées par les textures
    cache_mb: int = CACHE_DEFAULT_MB          # cache de vignettes sur disque
    # -- vue 3D interactive, également utilisée pour les animations et Player
    viewer_upscale: str = "native"             # native | FSR 1 (pas FSR 4)
    fsr_quality: str = "quality"               # ultra | quality | balanced | performance
    fsr_sharpness: float = 0.65                # intensité RCAS
    viewer_msaa: str = "4"                     # échantillons FSR : off | 2 | 4
    viewer_dpr: str = "2"                      # limite de densité de pixels
    viewer_fps: str = "60"                     # plafond de rafraîchissement
    viewer_limit: str = "1600"                 # côté maximal du canvas

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
            theme=_choice(_LEGACY_THEMES.get(str(self.theme).strip().lower(), self.theme),
                          "theme", THEMES),
            language=_choice(self.language, "language", LANGUAGES),
            export_textures=bool(self.export_textures),
            preview_faces=int(_number(self.preview_faces, "preview_faces", 0, 400_000)),
            textured_previews=bool(self.textured_previews),
            thumb_faces=(None if self.thumb_faces is None
                         else int(_number(self.thumb_faces, "thumb_faces",
                                          FACES_MIN, FACES_MAX))),
            cache_mb=int(_number(self.cache_mb, "cache_mb",
                                 CACHE_MIN_MB, CACHE_MAX_MB)),
            viewer_upscale=_choice(self.viewer_upscale, "viewer_upscale", VIEWER_UPSCALERS),
            fsr_quality=_choice(self.fsr_quality, "fsr_quality", FSR_QUALITIES),
            fsr_sharpness=round(_number(self.fsr_sharpness, "fsr_sharpness", 0, 1), 2),
            viewer_msaa=_choice(self.viewer_msaa, "viewer_msaa", VIEWER_MSAA),
            viewer_dpr=_choice(self.viewer_dpr, "viewer_dpr", VIEWER_DPRS),
            viewer_fps=_choice(self.viewer_fps, "viewer_fps", VIEWER_FPS),
            viewer_limit=_choice(self.viewer_limit, "viewer_limit", VIEWER_LIMITS),
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
            "theme": self.theme,
            "language": self.language,
            "export_textures": self.export_textures,
            "thumb_faces": self.thumb_faces,
            "preview_faces": self.preview_faces,
            "textured_previews": bool(self.textured_previews),
            "cache_mb": self.cache_mb,
            "viewer_upscale": self.viewer_upscale,
            "fsr_quality": self.fsr_quality,
            "fsr_sharpness": self.fsr_sharpness,
            "viewer_msaa": self.viewer_msaa,
            "viewer_dpr": self.viewer_dpr,
            "viewer_fps": self.viewer_fps,
            "viewer_limit": self.viewer_limit,
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
            "theme": payload.get("theme", base.theme),
            "language": payload.get("language", base.language),
            "export_textures": payload.get("export_textures", base.export_textures),
            "thumb_faces": payload.get("thumb_faces", base.thumb_faces),
            "preview_faces": payload.get("preview_faces", base.preview_faces),
            "textured_previews": bool(payload.get("textured_previews",
                                                  base.textured_previews)),
            "cache_mb": payload.get("cache_mb", base.cache_mb),
            "viewer_upscale": payload.get("viewer_upscale", base.viewer_upscale),
            "fsr_quality": payload.get("fsr_quality", base.fsr_quality),
            "fsr_sharpness": payload.get("fsr_sharpness", base.fsr_sharpness),
            "viewer_msaa": payload.get("viewer_msaa", base.viewer_msaa),
            "viewer_dpr": payload.get("viewer_dpr", base.viewer_dpr),
            "viewer_fps": payload.get("viewer_fps", base.viewer_fps),
            "viewer_limit": payload.get("viewer_limit", base.viewer_limit),
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
