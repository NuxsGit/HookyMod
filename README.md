# 🗡️ OoT Remake Asset Explorer & Modding Tool

[![GitHub Stars](https://img.shields.io/github/stars/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=gold)](https://github.com/NuxsGit/HookyMod/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=blue)](https://github.com/NuxsGit/HookyMod/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/NuxsGit/HookyMod?style=for-the-badge&color=red)](https://github.com/NuxsGit/HookyMod/issues)
[![GitHub License](https://img.shields.io/github/license/NuxsGit/HookyMod?style=for-the-badge&color=green)](LICENSE)
[![Release Status](https://img.shields.io/github/v/release/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=purple)](https://github.com/NuxsGit/HookyMod/releases)
[![Solana Donate](https://img.shields.io/badge/Donate-Solana-00FFA3?style=for-the-badge&logo=solana&logoColor=black)](#-support-the-project)

## 📌 Overview

**OoT Remake Asset Explorer** (HookyMod) is a standalone, open-source modding and
reverse-engineering tool for the *Ocarina of Time Remake* romfs. It is designed to work
alongside PC decompilations and mod loaders like **Hookshot**, and lets modders inspect,
extract, edit, convert and repackage assets.

Everything is Python (Tkinter for the GUI): no proprietary library, no game asset bundled.

## ⚡ Features

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

### 🎨 Rendering & 3D pipeline

* **Real-time 3D viewer** for BFRES models (and `.obj`): orbit / pan / zoom, wireframe,
  back-face culling, flat shading, automatic normals when the model has none.
* **BNTX texture viewer**: list every texture of a file, preview mip levels, decode
  BC1/BC2/BC3/BC4/BC5, RGBA8/BGRA8, RGB565, BGR565, RGBA4, ABGR4, RGB5A1, R10G10B10A2, R8, RG8…
  (Tegra block-linear swizzle is implemented and validated against the reference algorithm).
  BC7 / BC6H / ASTC are supported when `texture2ddecoder` is installed.
* **Texture → PNG export** (single, filtered or all textures). PNG is written by the app
  itself: no Pillow dependency.
* **BFRES → OBJ export** with positions, UVs, normals and generated materials.
* **One-click Blender export (`.blend`)**: BFRES is converted to OBJ then imported by
  Blender in background mode — **no BFRES import add-on required anymore**.

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
python -m hookymod.cli obj pack.zs -e Model/Link.bfres -o Link.obj
python -m hookymod.cli dicts romfs
python -m hookymod.cli scan romfs --json -o rapport.json
python -m hookymod.cli carve conteneur_inconnu.bin     # structures à l'intérieur
```

## 🚀 Getting started

### Requirements

* Python **3.10+** with Tkinter (Windows/macOS installers include it; on Debian/Ubuntu:
  `sudo apt install python3-tk`).
* `zstandard` (required for `.zs` files).
* *Optional:* `texture2ddecoder` for BC7 / BC6H / ASTC textures.
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
  identify.py         identification des formats (magics) et scan d'un dump
  cli.py              command line interface
docs/                 guides (identification d'un dump inconnu)
tests/                unit tests + synthetic SARC/BFRES/BNTX generators
```

## ✅ Tests

No game asset is needed: the test suite generates synthetic SARC, zstd, BNTX and BFRES files.

```bash
python -m unittest discover -s tests -v
```

70 tests couvrent les conteneurs, les parseurs, l'identification de formats, la CLI et la
logique de l'interface.

The GUI modules are also covered by a headless smoke test (stubbed Tkinter).

## ⚠️ Known limitations

* Les formats pris en charge sont ceux d'un pipeline **Nintendo** (SARC, zstd, BFRES,
  BNTX). Si le dump s'avère être un conteneur Unreal (`.pak` / `.uasset` / IoStore), Unity
  (`.assets`) ou 3DS (`.zar` / `.cmb` / `.ctex`), utilise `scan` pour le confirmer : les
  parseurs correspondants ne sont pas encore écrits.
* Modern BFRES files (v10 / TotK-style) may use externally stored strings; those names
  will be empty, but geometry still loads.
* Skeletal animations and skinned deformation are **not** played back yet: models are shown
  in their bind pose.
* BNTX textures are decoded and exported, but **re-encoding a modified PNG back into a BNTX
  is not implemented** — replace the whole `.bntx` (or the archive entry) instead.
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
