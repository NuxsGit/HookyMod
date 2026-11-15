"""Classement conservateur des BFRES auxiliaires d'après leur suffixe.

Le scan ne décompresse pas tous les fichiers. Ces noms connus sont donc une
heuristique, pas une preuve sur le contenu de tous les BFRES.
"""
from __future__ import annotations


_ANIMATION_SUFFIXES = (".anim.bfres", ".animation.bfres")
_AUXILIARY_SUFFIXES = _ANIMATION_SUFFIXES + (".skeleton.bfres", ".camera.bfres", ".light.bfres")


def _uncompressed_name(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base[:-3] if base.endswith(".zs") else base


def is_animation_bfres(name: str) -> bool:
    """BFRES d'animation nommé comme tel (pas un squelette ou une caméra)."""
    return _uncompressed_name(name).endswith(_ANIMATION_SUFFIXES)


def is_auxiliary_bfres(name: str) -> bool:
    """Animation, squelette, caméra ou lumière nommés comme tels par le jeu."""
    return _uncompressed_name(name).endswith(_AUXILIARY_SUFFIXES)
