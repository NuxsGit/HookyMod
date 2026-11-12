# Provenance et licence du code

## Règle du projet

**Aucun code Nintendo dans HookyMod.** Ni SDK, ni bibliothèque officielle, ni
code décompilé, ni extrait d'un outil tiers copié dans le dépôt. Chaque lecteur
et chaque écrivain de format a été écrit ici, en observant les fichiers un octet
après l'autre.

Ce que cela implique concrètement :

| Élément | État |
|---|---|
| `hookymod/bntx.py`, `bfres.py`, `sarc.py`, `nup.py`, `restbl.py`, `zs.py`, `nup.py` | écrits pour ce projet, à partir des fichiers réels |
| `hookymod/bntx_encode.py`, `bfres_import.py`, `blender.py` | idem, pour l'écriture (côté mod) |
| Code de Nintendo (SDK, moteur, exécutables) | **absent** — et le restera |
| Code décompilé (jeu, firmwares, homebrew dérivé) | **absent** |
| Bibliothèques tierces | seulement des dépendances génériques, installées par `pip` : `zstandard`, `texture2ddecoder`, `Pillow` ; aucun code d'outil de modding |
| Dumps du jeu (`*.zs`, `*.bntx`, `*.bfres`, `*.nup`) | fournis par l'utilisateur, **hors Git**, uniquement pour valider les lecteurs |

Les commentaires du code citent des formats publics (« documentation BFRES »)
pour expliquer une structure ; aucune ligne n'est recopiée d'un projet tiers.
Quand un comportement vient d'un autre outil, c'est dit en clair — jamais dupliqué.

## Pourquoi c'est important

Un outil de modding qui embarque du code Nintendo est impossible à publier, et
impossible à maintenir : on ne sait plus ce qui est observé, ce qui est copié, ni
ce qu'on a le droit de corriger. Ici, tout est relisible : si un champ se décale
dans une nouvelle version du jeu, on corrige **notre** lecture.

La contrepartie assumée : le travail est plus lent, parce qu'il faut mesurer
chaque structure. C'est le prix d'un outil propre.

## Les assets de test

Les fichiers du papillon à la racine (`Animal_Butterfly*.bfres.zs`,
`Mt_Body_Animal_Butterfly_*.bntx.zs`, `ZsDic.pack.zs`) servent à vérifier que
les lecteurs fonctionnent sur de vrais fichiers. Ils sont conservés pour le
moment, comme demandé, mais :

* ils ne sont **pas** du code, et ne serviront jamais de dépendance ;
* `previews/` (images dérivées) est ignoré par Git ;
* à terme, la suite de tests tournera sur des fichiers **synthétiques** générés
  par `tests/synth.py`, et les dumps resteront hors du dépôt (variable
  `HOOKYMOD_ASSETS` pour indiquer où ils sont).

## Ce que l'outil devient : un modmaker

Ordre de travail, du plus sûr au plus risqué :

1. **Lire** (fait, en cours) : BNTX (textures), BFRES (géométrie v9, v10 en
   cours), SARC, NUP, RESTBL, BYML, dictionnaires zstd.
2. **Modifier sans casser** :
   * textures : PNG → BNTX en gardant format, swizzle, mips et taille
     (`bntx_encode.py`) ;
   * géométrie : OBJ édité dans Blender → réinjecté dans le BFRES d'origine, en
     conservant la disposition des sommets (`bfres_import.py`) ;
   * traductions : MSBT / BYML (`translate.py`).
3. **Ajouter** : nouveaux acteurs, mobs, objets — c'est-à-dire toucher aux
   `ActorParam` (BYML), aux tables RESTBL (tailles des fichiers) et aux liens
   modèle/texture. Cela suppose un BFRES **écrivable**, pas seulement lisible :
   c'est la prochaine grande étape après la lecture v10.

Rien de tout cela ne demande une ligne de code Nintendo : seulement des fichiers
du jeu, lus et écrits par nos propres structures.
