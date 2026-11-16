"""Vérifie le contenu et le moteur réellement fournis par un paquet Tauri.

Sous Linux, extrait le .deb sans l'installer. Sous Windows, installe le NSIS
silencieusement dans un dossier temporaire (à utiliser sur une machine de test).
Le backend ainsi livré est testé avec des ressources entièrement synthétiques.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from smoke_desktop_backend import run as smoke_backend

ROOT = Path(__file__).resolve().parent.parent


def run() -> None:
    windows = sys.platform == "win32"
    if not windows and not sys.platform.startswith("linux"):
        raise SystemExit("Ce test est prévu pour les paquets Linux et Windows.")
    kind, suffix = ("nsis", ".exe") if windows else ("deb", ".deb")
    bundle_dir = ROOT / "src-tauri" / "target" / "release" / "bundle" / kind
    packages = sorted(bundle_dir.glob(f"*{suffix}"))
    if len(packages) != 1:
        raise RuntimeError(f"Un seul paquet {kind} attendu dans {bundle_dir}, reçu : {packages}")
    package = packages[0].resolve()
    print(f"Vérification du paquet : {package.name}", flush=True)
    with tempfile.TemporaryDirectory(prefix="hooky-package-test-") as temporary:
        directory = Path(temporary)
        if windows:
            installed = directory / "installed"
            # NSIS exige /D= en dernier et sans guillemets autour du chemin.
            command = f'"{package}" /S /D={installed}'
            subprocess.run(command, check=True, timeout=180)
            app = installed / "hookycreator-desktop.exe"
            backend = installed / "hookycreator-backend.exe"
        else:
            subprocess.run(["dpkg-deb", "--extract", str(package), str(directory)],
                           check=True, timeout=180)
            app = directory / "usr" / "bin" / "hookycreator-desktop"
            backend = app.with_name("hookycreator-backend")
        if not app.is_file() or not backend.is_file():
            raise RuntimeError(f"Exécutables absents du paquet : {app}, {backend}")
        if not windows and (not os.access(app, os.X_OK) or not os.access(backend, os.X_OK)):
            raise RuntimeError("Les exécutables du paquet ne sont pas exécutables.")
        smoke_backend(backend)
        print("Paquet : application et sidecar présents côte à côte ; sidecar livré vérifié.")


if __name__ == "__main__":
    run()
