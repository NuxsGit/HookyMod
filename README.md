# 🗡️ OoT Remake Asset Explorer & Modding Tool

[![GitHub Stars](https://img.shields.io/github/stars/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=gold)](https://github.com/NuxsGit/HookyMod/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=blue)](https://github.com/NuxsGit/HookyMod/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=red)](https://github.com/NuxsGit/HookyMod/issues)
[![GitHub License](https://img.shields.io/github/license/NuxsGit/HookyMod?style=for-the-badge&color=green)](LICENSE)
[![Release Status](https://img.shields.io/github/v/release/NuxsGit/HookyMod?style=for-the-badge&logo=github&color=purple)](https://github.com/NuxsGit/HookyMod/releases)
[![Solana Donate](https://img.shields.io/badge/Donate-Solana-00FFA3?style=for-the-badge&logo=solana&logoColor=black)](#-support-the-project)

## 📌 Overview

**OoT Remake Asset Explorer** is a standalone, open-source modding and reverse-engineering tool built for the *Ocarina of Time Remake*. Designed to work alongside PC decompilations and mod loaders like **Hookshot**, this tool allows developers and modders to easily inspect, extract, edit, and package assets.

## ⚡ Features

### 🗂️ Asset Inspection & Navigation

* **RomFS Support:** Seamlessly browse and read raw game archives (`romfs`) in real time.
* **Built-in Hex Viewer:** Inspect file headers, magic bytes, and binary structures (`.zs`, `.bfres`, `.bntx`, `.pack`, etc.).
* **Metadata & ASCII String Dump:** Extract internal naming schemes and format structures instantly.
* **Instant Filtering & Search:** Real-time search engine to locate specific game assets (e.g., `Butterfly`, `Link`, `Tex/`, etc.).

### 🎨 Rendering & 3D Pipeline

* **Real-Time 3D Viewer:** Preview models (`.bfres`) and skeletal animations directly inside the app.
* **One-Click Blender Export (`.blend`):** Convert graphics assets directly into ready-to-edit `.blend` files.
* **BNTX & BFRES Editing:** Extract, view, and swap texture maps and 3D meshes without breaking file formatting.

### 🛠️ Modding Suite

* **Single & Batch Extraction/Replacement:** Extract assets for editing and import them back into the archive.
* **Patch Management:** Create and apply binary patches to data structures.
* **Mod Exporter:** Package all your modified assets into a clean, ready-to-distribute mod directory.

## 🚀 Getting Started

### System Requirements

* **OS:** Windows 10/11 (64-bit) or Linux (via Wine / Proton)
* **Game Dump:** A extracted `romfs` folder obtained legally from your copy of the game.

### Installation

1. Go to the [**Releases**](../../releases) page on this GitHub repository.
2. Download the latest release for your operating system.
3. Extract the `.zip` file anywhere and run `AssetExplorer.exe`.

## 🛠️ Modding Workflow

```
[ Original RomFS ] ➡️ [ Asset Explorer ] ➡️ [ Export .blend / BNTX ]
                                                     │
                                           (Edit in Blender / GIMP)
                                                     │
[ Playable Mod ]   ⬅️ [ Export as Mod ]  ⬅️ [ Import / Replace ]
```

1. **Load RomFS:** Click **`Choisir romfs...`** and select your extracted game folder.
2. **Locate Asset:** Filter assets (e.g., `Tex/Mt_Body_Animal_Butterfly_Alb.bntx.zs`).
3. **Modify:**
   * Click **`Convertir en .blend`** for 3D editing or **`Extraire`** to pull textures.
   * After editing, import your changes using **`Remplacer...`**.
4. **Export Mod:** Click **`Exporter en mod`** to compile a final mod structure.

## ⚖️ Legal Disclaimer

> [!IMPORTANT]
> **This project is developed independently for interoperability and modding research purposes only.**

* **No Copyrighted Assets:** This repository contains **source code only**. It does not provide, host, or distribute any proprietary assets owned by Nintendo (such as ROMs, textures, audio files, 3D models, or compiled code binaries).
* **User Responsibility:** Users must legally own the game and provide their own extracted `romfs` dump.
* **Trademarks:** *The Legend of Zelda*, *Ocarina of Time*, and Nintendo are registered trademarks of Nintendo Co., Ltd. This project is not affiliated with, endorsed by, or sponsored by Nintendo.

## 🤝 Contributing

Contributions are welcome! If you'd like to add support for new formats or fix bugs:

1. **Fork** the repository.
2. Create your Feature Branch (`git checkout -b feature/CoolFeature`).
3. Commit your changes (`git commit -m 'Add CoolFeature'`).
4. Push to the branch (`git push origin feature/CoolFeature`).
5. Open a **Pull Request**.

## ☕ Support the Project

If you find this tool useful and would like to support ongoing development and research, donations via Solana are greatly appreciated!

```text
SOL Address: VOTRE_ADRESSE_SOLANA_ICI
```

*(Thank you for your support!)*

## 📜 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information.
