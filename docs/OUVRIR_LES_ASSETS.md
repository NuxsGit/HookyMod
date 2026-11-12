# Ouvrir les assets de Ocarina of Time (remake Switch 2, 2026)

Notes vérifiées le 11 novembre 2026 à partir des sources publiques listées en bas.
Elles expliquent **pourquoi un fichier ne s'affiche pas** et **quels outils
savent déjà le lire**, sans reprendre de code sous GPL.

## 1. Ce qu'est le jeu

* Remake de *Ocarina of Time* sorti le **5 novembre 2026** sur Switch 2 ;
  build de référence **E0F3CCFA**, update **v131072**, titre `0400486024DEC000`.
* Moteur : **ModuleSystem**, celui de *Tears of the Kingdom* (nom de code « Owl »).
  Conséquence directe : le romfs, les formats et la plupart des noms de classes
  sont ceux de TotK — un `.bfres.zs`, un `.bntx.zs`, un `.pack.zs` et un
  `.bgyml` se lisent avec la même boîte à outils.
* romfs : **186 587 fichiers, ~26 Go**.

| Dossier | Contenu | Extension |
|---|---|---|
| `Tex` | 20 667 textures (9,4 Go) | `<Nom>.bntx.zs` |
| `Model` | 7 896 modèles + animations | `<Nom>.<Nom>.bfres.zs`, `<Nom>_Animation.anim.bfres.zs` |
| `Pack` | 8 385 *acteur packs* (Link = `Player_Base`, `Player_Child`, `Player_Adult`) | `*.pack.zs` (SARC) |
| `TerrainArc`, `Bake`, `PreComputedCulling` | terrain, éclairage cuit, occlusion | `.extm`, `.bkprb`, `.bcul` |
| `AS` | 919 graphes d'animation | `*.root.asb.zs` |
| `Banc` | 247 cartes (une par zone) | `.bcett.byml.zs` |
| `Mals` | textes (16 langues, 540 MSBT chacune) | `*.sarc.zs` |
| `System` | table des tailles de ressources | `*.rsizetable.zs` |

## 2. Compression : `.zs` + dictionnaires

Presque tout est du **zstd**. Beaucoup de frames exigent un des quatre
dictionnaires du jeu, rangés dans `Pack/ZsDic.pack.zs` (zstd simple → SARC de
fichiers `.zsdic`) :

| `dict_id` | Fichier | Sert à |
|---:|---|---|
| 1 | `zs.zsdic` | tout le reste (textures, modèles…) |
| 2 | `bkprb.zsdic` | éclairage cuit |
| 3 | `bcett.byml.zsdic` | placement des cartes |
| 4 | `pack.zsdic` | acteur packs `.pack.zs` |

HookyMod charge ces dictionnaires automatiquement dès que le dossier `romfs`
est choisi (menu **Fichier → Choisir un dossier romfs…**) ; sans eux, un `.zs`
« ne s'ouvre pas » alors que le fichier est intact. Vérification en une ligne :

```bash
python -m hookymod.cli doctor romfs/Tex/<Nom>.bntx.zs
```

## 3. Pourquoi un asset ne s'affiche pas

1. **Tkinter absent** → aucune fenêtre ne s'ouvre (c'est le cas le plus fréquent
   sur Windows). `python -m hookymod.cli doctor` l'affiche en clair. Voir §4.
2. **Modèle sans ses textures** : le BFRES ne contient pas les textures, elles
   vivent à part dans `Tex/<Nom>.bntx.zs`. Ouvrir la texture correspondante
   (ou l'exporter en PNG) est donc une étape normale, pas un bug.
3. **`.zs` non décompressé** faute de dictionnaire (voir §2).
4. **Mod qui « ne fait rien » en jeu** : le fichier a grossi au-delà de sa
   réservation dans
   `System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs`
   (format RESTBL v1, entrée = CRC-32 du chemin sans `.zs` + taille réservée).
   Le jeu refuse alors le fichier. HookyMod sait lire/corriger cette table
   (`restbl`, `translate pack`) ; `tools/rstb_set.py` du dépôt communautaire fait
   la même chose.
5. **Format non géré** : `.ainb` (logique), `.asb` (animations), `.bcett.byml`
   (placement), `.bkprb` (éclairage cuit), `.bcul` (occlusion) ne sont pas encore
   analysés par HookyMod ; le diagnostic le dit explicitement au lieu d'échouer
   en silence.

## 3 bis. Ce que disent les fichiers réels du papillon (build 1.1.1)

Mesuré sur les quatre `Mt_Body_Animal_Butterfly_*.bntx.zs` et le
`Animal_Butterfly.Animal_Butterfly.bfres.zs` du dépôt :

**Textures** — BNTX **0.4.1.1** (`0x00040101`), conteneur **`Ounc`** (et non
`NX  `), disposition **Orin**, 9 mips, **aucun dictionnaire** (`dict_id = 0`) :

| Fichier | Format | Taille |
|---|---|---|
| `..._Alb` | `ASTC_8x5_SRGB` | 384 × 384 |
| `..._Nrm` | `ASTC_5x5_UNORM` | 384 × 384 |
| `..._AO`, `..._Rgh` | `BC4_UNORM` | 256 × 256 |

En 0.4.x, la structure BTI porte des adresses **32 bits** : nom à `+80`, table
des pointeurs de mips à `+96` (en 0.3.x/Switch 1, ce sont des 64 bits à
`+76`/`+92`). Les mips sont des pointeurs **absolus** chaînés
(`0x1000`, `0x9000`, `0xB000`…), et un en-tête `BRTD` précède les pixels.

**Modèles** — `FRES` version **10** (`0x000A0400`), compressé avec
`dict_id = 1` (`zs.zsdic`) : sans `Pack/ZsDic.pack.zs`, rien ne se décompresse.
Structure observée (toutes les adresses sont des `u64` qui suivent la signature
et un `u32` compteur) :

| Bloc | Offset | Champs |
|---|---|---|
| `_STR` | `0x3590` | `u32` nombre de chaînes à `+0x10`, entrées (`u16` longueur + texte + NUL) à partir de `+0x18` ; **pas de `_DIC`** |
| `FMDL` | `0xF0` | `+0x08` nom (offset de chaîne), `+0x18` `FSKL`, `+0x20` `FVTX`, `+0x28` `FSHP`, `+0x30` dictionnaire des formes, `+0x38` `FMAT`, `+0x40` dictionnaire des matériaux, `+0x48` user data |
| `FVTX` | `0x1B0` | `+0x08` liste des attributs, `+0x10` `0xE40` (?), `+0x18` tampons |
| `FMAT` | `0x208` | compteur 1, `+0x08` nom, puis `0x1920` (textures), `0x1090`, `0x1900`… |
| `FSHP` | `0x310` | compteur 2 (`Wing_L`, `Wing_R`), `+0x08` nom, puis `0x640`, `0x838`, `0x840`, `0x9F0`… |
| `FSKL` | `0x3F0` | squelette (`Root`, `Skl_Root`, `Wing_L`, `Wing_R`) |

Les noms de textures référencés par le matériau apparaissent dans `_STR`
(`Mt_Body_Animal_Butterfly_Alb`, `…_Rgh`, `_ao0`, `_rgh0`, `_p0`, `_t0`) : c'est
la clé pour retrouver le fichier `Tex/*.bntx.zs` correspondant.

### Où en est la géométrie v10 (état exact, sans bluff)

Le conteneur est lu (sections, tables de pointeurs, chaînes), mais les **sommets
ne sont pas encore décodés**. Ce qui est mesuré :

* la zone de données (index et vertex buffers) commence à **`0x4000`**
  (champ `0x170` de l'en-tête, juste après un compteur `0x24`), et court
  jusqu'à ~`0x1F800` ;
* à `0x4000` on lit des **indices u16** propres (suites croissantes, valeurs
  bornées à quelques milliers) — c'est bien de la topologie ;
* les descripteurs de maillage (`0x640`, `0x840`, `0x9F0`…) contiennent des
  compteurs plausibles (`2046`, `1830`, `653`) et des décalages internes
  (`0x1000`, `0x1F00`), donc relatifs à la base `0x4000` ;
* en revanche **aucune suite de positions en `f32` ni en `f16`** n'existe dans
  cette zone (balayage exhaustif par strides 8 à 32) : les positions ne sont pas
  stockées en virgule flottante simple, et il n'y a pas de « stride 24 avec
  position en tête » naïf ;
* on trouve des enregistrements de **24 octets** (`0x9700`…) dont le deuxième
  mot est un `f32` de l'ordre de `0,008` — taille plausible d'une aile en
  mètres, mais la suite ne correspond à aucun schéma simple : l'hypothèse la
  plus probable est un **codage quantifié / entrelacé** (attributs séparés,
  ou blocs compressés) plutôt qu'un tableau de sommets classique.

Conséquence pratique : `cli blender` **refuse** d'exporter ce modèle et dit
pourquoi, au lieu d'écrire un OBJ vide ou décalé. La prochaine étape est
d'identifier le codage des sommets (comparer deux modèles de tailles
différentes, ou isoler le buffer d'une aile) — et de là, la géométrie tombe.

## 4. Voir quelque chose **sans** fenêtre

```bash
python -m hookymod.cli doctor                              # modules, Tk, dictionnaires
python -m hookymod.cli ls romfs/Tex                        # inventaire (via le romfs)
python -m hookymod.cli textures romfs/Tex/<Nom>.bntx.zs -o png/   # textures en PNG
python -m hookymod.cli view romfs/Model/<Nom>.<Nom>.bfres.zs -o modele.png
python -m hookymod.cli view ... --views 4 --size 480x480    # planche de rotations
python -m hookymod.cli obj  romfs/Model/<Nom>.<Nom>.bfres.zs -o modele.obj
```

Sur Windows, pour retrouver l'interface : réinstaller Python depuis python.org
en cochant **« tcl/tk and IDLE »** (ou lancer `py -3.12 -m hookymod.app`).
Sous Debian/Ubuntu : `sudo apt install python3-tk`.

## 5. Outils existants qui traduisent déjà ces formats

| Outil | Ce qu'il fait | Remarque |
|---|---|---|
| [OctoSquiddy/Switch-2-Toolbox](https://github.com/OctoSquiddy/Switch-2-Toolbox) | fork de Switch-Toolbox avec **BFRES v10** (Switch 2), **BNTX 0.4.1.x** (« Ounc »), chargement automatique des textures externes du dossier `Tex` | nécessite `Pack/ZsDic.pack.zs` décompressé dans `Lib/ZstdDictionaries` ([issue #757](https://github.com/KillzXGaming/Switch-Toolbox/issues/757)). GPL v3. |
| [Kabosune/oot-remake-modding](https://github.com/Kabosune/oot-remake-modding) | `zstool.py` (ls/cat/extract `.zs` + SARC + BYML), `zsmod.py` (édition de nombres), `asbtool.py` (ASB), `rstb_set.py` (table des tailles), `nxpatch.py` | orienté code/paramètres, pas les modèles ; c'est la référence la plus à jour sur la structure du romfs. |
| [CosmicDreamsOfCode/BfresToCast](https://github.com/CosmicDreamsOfCode/BfresToCast) | conversion BFRES → Cast (glTF/FBX) | PR #8 : textures externes, ASTC, tableaux, blend shapes. |
| [NotRexed/Hookshot](https://github.com/NotRexed/Hookshot) | recompilation statique du jeu pour PC (Vulkan) ; `nx2/prompt_assets.py` documente `.nup`, dictionnaires, RESTBL | ne sert pas à éditer des assets. |
| [zortos293/Lichii](https://github.com/zortos293/Lichii) | émulateur (lignée yuzu/Eden) | décode ASTC/BC7 à l'exécution. |
| TotK tools (TotkToolkit, etc.) | mêmes formats côté TotK | beaucoup de connaissances sont transférables. |

Conclusion pratique : **la traduction est possible** — Switch-2-Toolbox (fork
Switch 2) pour ouvrir/modifier modèles et textures, `zstool.py` pour inspecter
`.zs`/SARC/BYML, `zsmod.py` pour les valeurs, `rstb_set.py` pour la table des
tailles. HookyMod reste l'outil « tout-en-un » côté ligne de commande et
diagnostic, avec un périmètre volontairement limité à SARC / zstd / BFRES /
BNTX / OBJ / PNG.

## 6. Sources

* `docs/romfs.md` et `docs/engine.md` de `Kabosune/oot-remake-modding` (build
  E0F3CCFA, update v131072) — structure du romfs, dictionnaires zstd, RESTBL v1.
* `tools/zstool.py` du même dépôt — implémentation de référence SARC/zstd/BYML.
* `OctoSquiddy/Switch-2-Toolbox` — BFRES v10, BNTX 0.4.1.x, textures externes.
* `NotRexed/Hookshot` (`nx2/prompt_assets.py`) — `.nup`, dictionnaires, RESTBL.
* `KillzXGaming/tegra_swizzle` — formules de swizzle X1 et Orin.
