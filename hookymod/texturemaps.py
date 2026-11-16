"""Reconnaissance des cartes de texture du jeu et assemblage pour Blender.

Un modèle du jeu n'a pas « une » texture : il en a plusieurs, et chacune va sur
une entrée précise du shader. Les noms le disent, à condition de savoir lire le
suffixe :

===========================  =====================  =========================
suffixe du fichier           rôle                   entrée Blender (Principled)
===========================  =====================  =========================
``_Alb`` ``_Albedo`` ``_BC``  albédo (couleur)       Base Color  (sRGB)
``_Dif`` ``_BaseColor``
``_Nrm`` ``_Normal``          normale                Normal → Normal Map (Non-Color)
``_Rgh`` ``_Rough``           rugosité               Roughness  (Non-Color)
``_Mtl`` ``_Met`` ``_Metal``  métallique             Metallic   (Non-Color)
``_AO`` ``_Occ``              occlusion              Occlusion / facteur AO
``_Emi`` ``_Emit``            émission               Emission
``_Spc`` ``_Spec``            spéculaire             (pas d'équivalent glTF)
``_Msk`` ``_Mask`` ``_Op``    masque / opacité       Alpha (à brancher à la main)
===========================  =====================  =========================

Rien n'est deviné : un fichier dont le suffixe n'est pas reconnu est classé
« autre », **exporté quand même**, et listé comme tel. C'est la seule façon de ne
pas perdre une texture sous prétexte qu'on ne connaît pas son nom.

Les canaux suivent la convention des jeux Nintendo : les cartes de données
(rugosité, métallique, occlusion) sont en niveaux de gris, la valeur utile est
dans le canal R. glTF, lui, attend **une seule** image « metallicRoughness » avec
la rugosité en G et le métallique en B : ``compose_metal_roughness`` la fabrique
à partir des deux fichiers du jeu.
"""

from __future__ import annotations

from pathlib import Path

#: rôle → suffixes possibles (comparés en minuscules, sur le dernier jeton)
ROLE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "albedo": ("alb", "albedo", "bc", "basecolor", "dif", "diffuse", "col", "color"),
    "normal": ("nrm", "normal", "norm", "n"),
    "roughness": ("rgh", "rough", "roughness", "r"),
    "metallic": ("mtl", "met", "metal", "metallic", "mt"),
    "metalrough": ("mr", "mrao", "orm", "rmo", "mra"),
    "occlusion": ("ao", "occ", "occlusion", "ambientocclusion"),
    "emissive": ("emi", "emit", "emissive", "em"),
    "specular": ("spc", "spec", "specular"),
    "mask": ("msk", "mask", "op", "alpha", "trans"),
}

#: libellé court affiché dans l'atelier
ROLE_LABELS: dict[str, str] = {
    "albedo": "Alb",
    "normal": "Nrm",
    "roughness": "Rgh",
    "metallic": "Mtl",
    "metalrough": "MR",
    "occlusion": "AO",
    "emissive": "Emi",
    "specular": "Spc",
    "mask": "Msk",
    "autre": "?",
}

#: ce qu'on en fait dans Blender, en une phrase (affiché dans TEXTURES.md)
ROLE_HELP: dict[str, str] = {
    "albedo": "Base Color — couleur de base (espace colorimétrique sRGB)",
    "normal": "Normal ← nœud Normal Map, image en Non-Color",
    "roughness": "Roughness — rugosité (canal G de metallicRoughness en glTF)",
    "metallic": "Metallic — métal (canal B de metallicRoughness en glTF)",
    "metalrough": "metallicRoughness combinée (G = rugosité, B = métal)",
    "occlusion": "Occlusion — ombrage de contact (canal R)",
    "emissive": "Emission — parties lumineuses",
    "specular": "Specular — pas d'équivalent glTF : à régler à la main",
    "mask": "Alpha / masque — à brancher à la main selon l'effet voulu",
    "autre": "rôle inconnu — ouvre l'image pour décider où la mettre",
}

#: extensions qu'on enlève avant de lire le suffixe
_EXTENSIONS = (".zs", ".bntx", ".png", ".dds", ".jpg", ".jpeg", ".tga", ".bfres")


def _strip(name: str) -> str:
    """``Mt_Epee_Alb.0.bntx.zs`` → ``mt_epee_alb``."""
    text = Path(name).name.lower()
    changed = True
    while changed:                     # .zs, .bntx, puis « .0 », puis encore .zs…
        changed = False
        for extension in _EXTENSIONS:
            if text.endswith(extension):
                text = text[: -len(extension)]
                changed = True
        head, dot, tail = text.rpartition(".")
        if dot and tail.isdigit():
            text = head
            changed = True
    return text


def classify(name: str) -> str:
    """Rôle d'une texture d'après son nom (``albedo``, ``normal``, …).

    Le dernier jeton gagne : ``CeTexte_Mt_Hair_Npc_Nrm`` est une normale même si
    le nom contient « Mt » (le préfixe « Mt_ » veut dire *material*, pas
    *metallic*).
    """
    text = _strip(name)
    tokens = [token for token in text.replace("-", "_").replace(" ", "_").split("_")
              if token]
    if not tokens:
        return "autre"
    for token in (tokens[-1], tokens[-2] if len(tokens) > 1 else ""):
        for role, suffixes in ROLE_SUFFIXES.items():
            if token in suffixes:
                return role
    # forme collée : « biggoronalb »
    for role, suffixes in ROLE_SUFFIXES.items():
        for suffix in suffixes:
            if text.endswith(suffix):
                return role
    return "autre"


def base_name(name: str, role: str | None = None) -> str:
    """Nom sans extension ni suffixe de rôle (pour regrouper les cartes)."""
    text = _strip(name)
    role = role or classify(name)
    for suffix in ROLE_SUFFIXES.get(role, ()):
        if text.endswith("_" + suffix):
            return text[: -(len(suffix) + 1)]
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


#: ordre d'affichage (et d'écriture dans TEXTURES.md)
ROLE_ORDER = ("albedo", "normal", "roughness", "metallic", "metalrough",
              "occlusion", "emissive", "specular", "mask", "autre")


def group(names) -> dict[str, list[str]]:
    """Regroupe des noms de fichiers par rôle, dans l'ordre de ``ROLE_ORDER``."""
    buckets: dict[str, list[str]] = {}
    for name in names:
        buckets.setdefault(classify(name), []).append(name)
    return {role: sorted(buckets[role]) for role in ROLE_ORDER if role in buckets}


# --------------------------------------------------------------------------- images
def _any_reader(data: bytes) -> tuple[int, int, bytes]:
    from hookycreator.pngread import decode_image

    return decode_image(data)


def compose_metal_roughness(roughness_png: bytes | None,
                            metallic_png: bytes | None) -> bytes | None:
    """Fabrique l'image ``metallicRoughness`` attendue par glTF.

    glTF n'a qu'une texture pour les deux : rugosité en **G**, métal en **B**.
    On y met la valeur utile des cartes du jeu (canal R, niveaux de gris).
    Une carte absente devient une constante : rugosité 255 (mate) ou métal 0.
    """
    if not roughness_png and not metallic_png:
        return None
    rough = _any_reader(roughness_png) if roughness_png else None
    metal = _any_reader(metallic_png) if metallic_png else None
    # une taille différente n'est pas une erreur : la métallique est
    # rééchantillonnée au plus proche sur la grille de la rugosité
    width, height, _ = rough if rough else metal
    # Affecter les canaux par tranches : les cartes ont habituellement la même
    # taille et un million de pixels ne doivent pas passer par une boucle Python.
    output = bytearray(b"\xff\xff\x00\xff" * (width * height))
    if rough:
        output[1::4] = rough[2][0::4]
    if metal:
        mw, mh, mp = metal
        if (mw, mh) == (width, height):
            output[2::4] = mp[0::4]
        else:
            # Même voisin le plus proche que précédemment : floor(x*mw/width),
            # floor(y*mh/height). Ne garder qu'une ligne pour borner la mémoire.
            columns = [x * mw // width * 4 for x in range(width)]
            last_y, line = -1, b""
            for y in range(height):
                sy = y * mh // height
                if sy != last_y:
                    start = sy * mw * 4
                    line = bytes(mp[start + x] for x in columns)
                    last_y = sy
                first = y * width * 4 + 2
                output[first:first + width * 4:4] = line
    from hookymod import util

    return util.png_bytes(width, height, bytes(output))
