# 🗡️ OoT Remake Asset Explorer & Modding Tool

[![GitHub Stars](https://img.shields.io/github/stars/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=gold)](https://github.com/NuxsGit/HookyMod/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=blue)](https://github.com/NuxsGit/HookyMod/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/NuxsGit/HookyMod?style=for-the-badge&color=red)](https://github.com/NuxsGit/HookyMod/issues)
[![GitHub License](https://img.shields.io/github/license/NuxsGit/HookyMod?style=for-the-badge&color=green)](LICENSE)
[![Release Status](https://img.shields.io/github/v/release/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=purple)](https://github.com/NuxsGit/HookyMod/releases)
[![Solana Donate](https://img.shields.io/badge/Donate-Solana-00FFA3?style=for-the-badge&logo=solana&logoColor=black)](#-support-the-project)

## 📌 Overview

> **Code original uniquement.** HookyMod ne contient aucun code, SDK ou outil de
> Nintendo, et aucun code décompilé : chaque lecteur (BNTX, BFRES, SARC, NUP,
> BYML, RESTBL, zstd) a été écrit ici à partir de l'observation des fichiers.
> Les dumps du jeu ne sont là que pour les tests, et sont ignorés par Git
> (voir `docs/PROVENANCE.md`).


**OoT Remake Asset Explorer** (HookyMod) is a standalone, open-source modding and
reverse-engineering tool for the *Ocarina of Time Remake* romfs. It is designed to work
alongside PC decompilations and mod loaders like **Hookshot**, and lets modders inspect,
extract, edit, convert and repackage assets.

Everything is Python (Tkinter for the GUI): no proprietary library, no game asset bundled.

## ⚡ Features

### 🎮 Remake Switch 2 (Hookshot) — lecture des `.nup`

Le remake *Ocarina of Time* Switch 2 n'expose pas ses fichiers : ils arrivent dans un paquet
`.nup`, que le portage PC **Hookshot** sait lire. HookyMod lit ce paquet directement :

```bash
python -m hookymod.cli nup game.nup --verify --deep --extract dump --only romfs
```

* liste des titres, `exefs/` et `romfs/`, codec par fichier (brut ou zstd) ;
* extraction fidèle (décompression des membres compressés par le paquet, sans toucher aux
  `.zs` du jeu qui sont déjà des frames zstd), contrôle SHA-256 et en-têtes `.zs` ;
* le romfs extrait s'ouvre ensuite dans l'explorateur, comme n'importe quel dump.

Pour le modding, l'export écrit `romfs/<chemin>` (donc `mods/<nom>/romfs/...` pour
Hookshot) et **agrandit automatiquement la table des tailles de ressources** quand un
fichier modifié est plus gros que la réservation du jeu — sans quoi le jeu ignore le
fichier. Détails : [`docs/HOOKSHOT.md`](docs/HOOKSHOT.md).

### 🗂️ Asset inspection & navigation

* **RomFS browser** with lazy folder tree, instant filtering and recursive search.
* **zstd (`.zs`) support** with automatic dictionary handling: `Pack/ZsDic.pack.zs` is
  found and loaded from your dump, and a file compressed with an unknown dictionary ID is
  retried against every loaded dictionary. Clear error messages when a dictionary is missing.
* **SARC archives**: browse, extract, replace, rebuild (hash table, name table, per-entry
  alignment and endianness preserved). Nested `.zs` / SARC can be entered recursively
  (double-click) and edited in place.
* **Built-in hex viewer + metadata + ASCII string dump** for any file or archive entry
  (compressed entries are shown decompressed).
* **Type detection** for SARC, zstd, BFRES, BNTX, BNSH, BYML, AAMP, Yaz0, RIFF/Ogg…

### 🔁 Traducteur romfs ↔ PC (PNG, OBJ, Blender)

Le jeu ne lit ni PNG ni OBJ : le traducteur fait l'aller-retour.

```bash
python -m hookymod.cli translate unpack --romfs dump/romfs -p mon_projet
python -m hookymod.cli translate status -p mon_projet
python -m hookymod.cli translate pack -p mon_projet -o mods --mod-name MonMod
```

* `pc/` contient l'arborescence éditable : **textures en PNG**, **modèles en OBJ**
  (Blender), fichiers bruts décompressés ; `origin/` garde les fichiers du jeu qui
  servent de gabarits, et `manifest.json` suit les conversions.
* **PNG → BNTX** : remplacement dans le BNTX d'origine (format, swizzle, mips et
  taille de fichier conservés), ou création complète ; BC1/BC3/BC4/BC5 et formats
  non compressés sont encodés, mips générées.
* **OBJ / .blend → BFRES** : `translate blend` convertit un `.blend` en OBJ via
  Blender, puis la géométrie est réimportée dans le BFRES du jeu (positions,
  normales, UV0), en gardant matériaux, os et disposition des sommets.
* `pack` n'écrit que les fichiers modifiés, recompresse les `.zs` avec le bon
  dictionnaire et **met à jour la table des tailles** : le résultat est directement
  un mod `mods/<nom>/romfs/...` pour Hookshot.

### 🧩 Modèle → Blender (aller simple, sans add-on)

```bash
python -m hookymod.cli blender romfs/Model/Papillon.bfres.zs -o Papillon_blender
blender --background --python tools/blender_import.py -- Papillon_blender --save Papillon.blend
```

* écrit un **glTF 2.0** (`.gltf` + `.bin`) et un **OBJ + MTL**, plus une note
  d'import ; un objet Blender par maillage, matériaux nommés comme dans le jeu ;
* les **textures voisines** (`<préfixe>_Alb.bntx.zs`, PNG déjà décodés) sont
  trouvées, décodées et branchées en couleur de base — rien n'est inventé ;
* si la géométrie n'est pas décodable, **rien n'est écrit** : mieux vaut un refus
  explicite qu'un modèle faux dans Blender.

### 🎨 Rendering & 3D pipeline

* **Real-time 3D viewer** for BFRES models (and `.obj`): orbit / pan / zoom, wireframe,
  back-face culling, flat shading, automatic normals when the model has none.
* **Rien n'est chargé sans être regardé** : un BFRES n'est analysé qu'à l'ouverture de
  l'onglet « Modèle 3D », les maillages ne sont construits qu'au clic sur « Voir en 3D »,
  puis mis en cache. Le dessin écarte les faces invisibles, borne le nombre de polygones
  à la surface de la fenêtre (moins pendant une rotation) et ne projette que les sommets
  utiles : un modèle de 250 000 triangles reste manipulable.
* **BNTX texture viewer**: list every texture of a file, preview mip levels (image ajustée
  à la fenêtre, cache disque des aperçus, Pillow en secours si Tk ne lit pas le PNG), decode
  BC1/BC2/BC3/BC4/BC5, RGBA8/BGRA8, RGB565, BGR565, RGBA4, ABGR4, RGB5A1, R10G10B10A2, R8, RG8…
  (Tegra block-linear swizzle is implemented for **both** generations: X1/Switch 1 and
  **Orin/Switch 2**, selected automatically from the BNTX version — `0.4.1.x` = remake
  Switch 2 — and overridable from the *Pixels* selector or `--orin` / `--x1`).
  BC7 / BC6H / ASTC are supported when `texture2ddecoder` is installed.
* **Texture → PNG export** (single, filtered or all textures). PNG is written by the app
  itself: no Pillow dependency.
* **Rendu PNG hors interface** (`cli view`) : maillage rasterisé avec tampon de
  profondeur (donc occlusion correcte), anti-aliasing, planche de rotations. C'est
  la voie de secours quand Tkinter n'est pas installé — et un moyen rapide de
  jeter un œil à un modèle depuis un terminal.
* **BFRES → OBJ export** with positions, UVs, normals and generated materials.
* **One-click Blender export (`.blend`)**: BFRES is converted to OBJ then imported by
  Blender in background mode — **no BFRES import add-on required anymore**.

### 🧭 Interface : une action, un seul endroit

* Barre d'outils réduite aux actions globales (romfs, ouvrir, `.nup`, retour, enregistrer,
  exporter en mod) ; extraction/remplacement/patch dans le menu **Édition** ; textures et
  modèle 3D dans leurs onglets. Plus aucun bouton en double.
* Les onglets suivent le contenu : un `.bntx` ouvre l'onglet Textures, un `.bfres` l'onglet
  Modèle 3D (`Ctrl+1/2/3` pour passer de l'un à l'autre).
* **Fenêtre de chargement** avec barre de progression pour les opérations longues, en
  particulier l'ouverture d'un `.nup` et l'extraction de son `romfs`, et les traductions.
* **Thèmes sombre et clair** (menu **Affichage**, ou *Paramètres*), appliqués à chaud sans
  reconstruire l'interface et sans perdre le fichier ouvert : le choix est mémorisé. Le
  viewer 3D et les fenêtres de diagnostic reprennent les couleurs du thème courant.
* **Diagnostics** : *Affichage → Diagnostic du fichier courant…* (magic, version, disposition
  des pixels, sections, mipmaps, avertissements du parseur) et *Aide → Diagnostic de
  l'installation…* (Python, Tk et support PNG, `zstandard`, `texture2ddecoder`, dictionnaires,
  chemins de configuration). En ligne de commande :
  `python -m hookymod.cli doctor [fichier]`.

### 🛠️ Modding suite

* **Single & batch extraction**: raw bytes or automatically decompressed `.zs`.
* **Single & batch replacement**: pick one file, or a whole folder whose layout mirrors the
  archive; compressed entries are transparently recompressed with the original dictionary.
* **Patch management**: search/replace text or hex bytes, on the current file, the whole
  archive, the selection, and recursively inside `.zs` and nested SARC. Archive entry names
  and headers can be patched too. Same-length patches keep file structures intact.
* **Mod exporter**: writes `<mod>/romfs/<exact romfs path>` for LayeredFS / emulators,
  handles several modified files at once, and lists pending modifications before exporting.
* **Settings**: Blender path, zstd compression level, texture preview resolution, dictionary
  status.

### 🔍 Identification d'un dump inconnu

Avant de pouvoir lire les assets, il faut savoir **dans quels formats** ils sont stockés
(remake PC sur Unreal/Unity, portage Switch, version 3DS, moteur maison…). Le scanner
intégré répond à cette question sans parseur :

```bash
python -m hookymod.cli scan "/chemin/vers/le/jeu" --hexdump 32 --strings 6
python -m hookymod.cli scan "/chemin/vers/le/jeu" --json -o rapport.json
```

Il parcourt le dossier, reconnaît les magics (SARC, RARC, U8, Yaz0, zstd, BNTX, BFRES,
BNSH, BYML, AAMP, CMB, CTEX, BCH, `.uasset`, `.pak`, IoStore, UnityFS, `.pck`, ZIP, DDS,
Ogg…), détaille les échantillons par extension (en-tête + chaînes internes) et propose le
**moteur probable** avec sa justification. Menu **Outils → Scanner un dossier (formats)…**
pour la même chose dans l'interface.

Procédure détaillée et liste de ce qu'il faut transmettre pour ajouter un format :
[`docs/IDENTIFIER_UN_DUMP.md`](docs/IDENTIFIER_UN_DUMP.md).

### 🖥️ Command line interface (headless, CI-friendly)

The same engine is available without Tkinter:

```bash
python -m hookymod.cli info romfs
python -m hookymod.cli list romfs/Pack/Some.pack.zs --recursive
python -m hookymod.cli extract romfs/Pack/Some.pack.zs -o out --decompress
python -m hookymod.cli replace pack.zs -e Model/Link.bfres -i new.bfres -o new_pack.zs
python -m hookymod.cli patch pack.zs --find Link --replace Hero -o patched.zs
python -m hookymod.cli textures pack.zs -e Tex/Butterfly.bntx.zs -o png/
python -m hookymod.cli view romfs/Model/Link.bfres.zs -o link.png   # rendu sans fenêtre
python -m hookymod.cli view modele.bfres --views 4 --size 480x480  # planche de rotations
python -m hookymod.cli textures tex.bntx --orin            # force la disposition Switch 2
python -m hookymod.cli doctor romfs/Pack/Tex.pack.zs       # modules + anatomie d'un fichier
python -m hookymod.cli obj pack.zs -e Model/Link.bfres -o Link.obj
python -m hookymod.cli blender modele.bfres -o modele_blender   # glTF + OBJ + PNG
python -m hookymod.cli dicts romfs
python -m hookymod.cli scan romfs --json -o rapport.json
python -m hookymod.cli carve conteneur_inconnu.bin     # structures à l'intérieur
python -m hookymod.cli nup game.nup --list             # paquet Switch 2
python -m hookymod.cli restbl romfs --get Model/Link.bfres
python -m hookymod.cli translate unpack --romfs romfs -p projet   # PNG + OBJ
python -m hookymod.cli translate pack -p projet -o mods --mod-name MonMod
```

## 🚀 Getting started

### Si la fenêtre ne s'ouvre pas (Tkinter absent)

`python -m hookymod.cli doctor` dit immédiatement si Tkinter est disponible.
S'il manque, tout le reste fonctionne sans fenêtre : `textures` exporte les PNG,
`view` rend un modèle en image, `obj` exporte le maillage.

En attendant, la lecture d'un asset passe par les mêmes fichiers : un modèle
`.bfres.zs` **ne contient pas ses textures**, elles sont dans `Tex/*.bntx.zs`.

Guide complet (structure du romfs du remake, dictionnaires zstd, table des
tailles, outils existants) : [`docs/OUVRIR_LES_ASSETS.md`](docs/OUVRIR_LES_ASSETS.md).

### Requirements

* Python **3.10+** with Tkinter (Windows/macOS installers include it; on Debian/Ubuntu:
  `sudo apt install python3-tk`).
* `zstandard` (required for `.zs` files).
* `texture2ddecoder` for BC7 / BC6H /**ASTC** textures — indispensable pour les assets
  Switch 2 du remake (le décodeur intégré ne couvre que BC1-BC5 et les formats non
  compressés). `python -m hookymod.cli doctor` indique ce qui manque.
* A legally obtained, extracted `romfs` folder of the game.

### Installation

```bash
git clone https://github.com/NuxsGit/HookyMod.git
cd HookyMod
python -m pip install -r requirements.txt
python AssetViewer.py
```

Every source file is plain Python; the tool also runs from a checkout without installation.

## 🛠️ Modding workflow

```
[ Original RomFS ] ➡️ [ Asset Explorer ] ➡️ [ Export .blend / PNG / OBJ ]
                                                     │
                                           (Edit in Blender / GIMP)
                                                     │
[ Playable Mod ]   ⬅️ [ Export as Mod ]  ⬅️ [ Import / Replace ]
```

1. **Load RomFS:** click **`Choisir romfs…`** and pick your extracted game folder (the
   parent folder of `romfs/` works too). Dictionaries are detected automatically.
2. **Locate an asset:** browse the tree, use the search box, or filter the archive content.
3. **Inspect:** the right panel shows type, size, hex dump, ASCII strings, then dedicated
   tabs for BNTX textures (preview/PNG) and BFRES models (3D view / OBJ / `.blend`).
4. **Modify:**
   * **`Remplacer…`** for one file (`.zs` are recompressed automatically),
   * **`Patch…`** for targeted byte/text changes, including inside compressed entries,
   * batch replacement is available from the *Édition* menu.
5. **Export:** **`Exporter en mod`** writes `romfs/<same path>` for your loader. Several
   modified files can be exported in one go; **`Enregistrer…`** also writes a standalone file.

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+O` / `Ctrl+S` | Open / Save as |
| `Ctrl+R` | Choose romfs folder |
| `Ctrl+E` | Export as mod |
| `Ctrl+P` | Patch dialog |
| `Ctrl+B` | Convert to `.blend` |
| `Ctrl+1` / `Ctrl+2` / `Ctrl+3` | Onglets Aperçu / Textures / Modèle 3D |
| `Alt+←` | Go up one archive level |
| `F5` | Refresh preview |

In the 3D viewer: drag = rotate, right-drag = pan, wheel = zoom, `W` = wireframe,
`B` = back-face culling, `F` = frame the model.

## 🧱 Project layout

```
AssetViewer.py        GUI launcher (python AssetViewer.py)
hookymod/
  sarc.py             SARC read/rebuild (hash + name tables, alignment)
  zs.py               zstd + dictionaries, ZsDic.pack discovery
  container.py        opened file model (nested .zs / SARC navigation)
  bntx.py             BNTX parser, Tegra swizzle, BCn/pixel decoding, PNG export
  bfres.py            BFRES parser (models, shapes, meshes, vertex attributes)
  mesh.py             mesh data, OBJ import/export
  viewer.py           Tkinter 3D viewer (software painter)
  app.py              Tkinter application
  nup.py              paquets .nup (dumps Switch 2) : lecture, vérification, extraction
  restbl.py           table des tailles de ressources (RESTBL) : lecture et agrandissement
  translate.py        traducteur romfs ↔ PC (projet pc/ + origin/ + manifest)
  bntx_encode.py      PNG → BNTX (BC1/BC3/BC4/BC5, formats simples, mips, swizzle)
  bfres_import.py     OBJ / Blender → BFRES (géométrie réécrite dans le fichier d'origine)
  identify.py         identification des formats (magics) et scan d'un dump
  cli.py              command line interface
docs/                 guides (dump inconnu, modding Switch 2 / Hookshot)
tests/                unit tests + synthetic SARC/BFRES/BNTX generators
```

## ✅ Tests

No game asset is needed: the test suite generates synthetic SARC, zstd, BNTX and BFRES files.

```bash
python -m unittest discover -s tests -v
```

236 tests couvrent les conteneurs, les parseurs, l'identification de formats, la CLI,
les dispositions de pixels Switch 1/Orin, les thèmes et la logique de l'interface.

The GUI modules are also covered by a headless smoke test (stubbed Tkinter).

## ⚠️ Known limitations

* Les formats pris en charge sont ceux d'un pipeline **Nintendo** (SARC, zstd, BFRES,
  BNTX), y compris ceux du remake Switch 2 une fois le `.nup` extrait. Si le dump s'avère
  être un conteneur Unreal (`.pak` / `.uasset` / IoStore), Unity (`.assets`) ou 3DS
  (`.zar` / `.cmb` / `.ctex`), utilise `scan` pour le confirmer : les parseurs
  correspondants ne sont pas encore écrits.
* Modern BFRES files (v10 / Switch 2) may use externally stored strings; those names
  will be empty, but geometry still loads. External textures referenced from
  `Tex/<name>.bntx.zs` are not resolved automatically yet: open the BNTX alongside.
* Sur un asset Switch 2 qui ne s'affiche pas, lancer **Affichage → Diagnostic du fichier
  courant…** : la version du conteneur et la disposition des pixels (X1/Orin) y figurent,
  et le sélecteur *Pixels* de l'onglet Textures permet de forcer l'autre disposition.
* Skeletal animations and skinned deformation are **not** played back yet: models are shown
  in their bind pose.
* BNTX : PNG → BNTX fonctionne (remplacement dans le fichier d'origine ou création),
  sauf BC7/ASTC qui n'ont pas d'encodeur. Un BNTX reconstruit de zéro doit être
  validé en jeu.
* BFRES : la géométrie est réimportée dans le fichier d'origine, mais **la taille des
  tampons ne peut pas augmenter** (pas de BFRES reconstruit depuis zéro) et les
  boîtes englobantes ne sont pas recalculées.
* BFRES internal textures (FTEX) are not decoded; use the separate `.bntx` files.
* Wii U BFRES (big endian) is explicitly rejected.
* BC6 (HDR) textures are decoded approximately / require `texture2ddecoder`.
* Patches keep the original byte length (structure-preserving edits).

## ⚖️ Legal disclaimer

> [!IMPORTANT]
> **This project is developed independently for interoperability and modding research purposes only.**

* **No Copyrighted Assets:** This repository contains **source code only**. It does not provide, host, or distribute any proprietary assets owned by Nintendo (such as ROMs, textures, audio files, 3D models, or compiled code binaries).
* **User Responsibility:** Users must legally own the game and provide their own extracted `romfs` dump.
* **Trademarks:** *The Legend of Zelda*, *Ocarina of Time*, and Nintendo are registered trademarks of Nintendo Co., Ltd. This project is not affiliated with, endorsed by, or sponsored by Nintendo.

## 🤝 Contributing

Contributions are welcome! If you'd like to add support for new formats or fix bugs:

1. Fork the repository.
2. Create your Feature Branch (`git checkout -b feature/CoolFeature`).
3. Commit your changes (`git commit -m 'Add CoolFeature'`).
4. Push to the branch (`git push origin feature/CoolFeature`).
5. Open a **Pull Request**.

When touching binary formats, please add a synthetic test in `tests/` so regressions are
caught without shipping game data.

## ☕ Support the project

If you find this tool useful and would like to support ongoing development and research,
donations via Solana are greatly appreciated!

SOL Address:
```text
y8eeEqNTo7pANwRVvwsrNDu9gpPTFYW5FrP2oMUtfhZ
```

*(Thank you for your support!)*

## 📜 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information.
