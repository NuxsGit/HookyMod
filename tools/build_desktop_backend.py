"""Construit le moteur Python autonome à embarquer dans Tauri v2.

Ce script est exécuté sur la plateforme cible ; les binaires PyInstaller ne
se cross-compilent pas. Le noyau Rust existant est compilé puis inclus, avec
les ressources web et les décodeurs Python. Aucun dump ni son du jeu n'est
intégré. Il faut installer requirements-desktop-build.txt avant de lancer.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "src-tauri" / "binaries"


def host_triple() -> str:
    result = subprocess.run(["rustc", "-vV"], check=True, text=True, capture_output=True)
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.partition(": ")[2]
    raise RuntimeError("Impossible de déterminer la plateforme de rustc.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-rust", action="store_true",
                        help="ne pas reconstruire le noyau Rust (il doit déjà exister)")
    args = parser.parse_args(argv)
    triple = host_triple()
    if not args.skip_rust:
        subprocess.run([sys.executable, str(ROOT / "tools" / "build_rust.py")], cwd=ROOT, check=True)
    lib_name = ("hookymod_core.dll" if sys.platform == "win32" else
                "libhookymod_core.dylib" if sys.platform == "darwin" else
                "libhookymod_core.so")
    native = ROOT / "hookymod" / "_native_lib" / lib_name
    if not native.is_file():
        raise RuntimeError(f"Noyau Rust absent : {native}")
    try:
        import PyInstaller  # noqa: F401
        import texture2ddecoder  # noqa: F401
        import zstandard  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Installer requirements-desktop-build.txt avec ce Python.") from exc

    # Le répertoire temporaire ne peut pas absorber de fichiers personnels :
    # seuls les modules et ressources explicitement nommés sont fournis.
    with tempfile.TemporaryDirectory(prefix="hooky-desktop-build-") as temp:
        temp = Path(temp)
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--name", "hookycreator-backend", "--paths", str(ROOT),
            "--distpath", str(temp / "dist"), "--workpath", str(temp / "build"),
            "--specpath", str(temp),
            "--collect-submodules", "hookycreator", "--collect-submodules", "hookymod",
            "--hidden-import", "texture2ddecoder", "--hidden-import", "zstandard",
            "--hidden-import", "soundfile", "--collect-all", "_soundfile_data",
            "--add-data", f"{ROOT / 'hookycreator' / 'web'}{os.pathsep}hookycreator/web",
            "--add-binary", f"{native}{os.pathsep}hookymod/_native_lib",
            str(ROOT / "tools" / "desktop_server.py"),
        ]
        subprocess.run(command, cwd=ROOT, check=True)
        suffix = ".exe" if sys.platform == "win32" else ""
        binary = temp / "dist" / f"hookycreator-backend{suffix}"
        DEST.mkdir(parents=True, exist_ok=True)
        output = DEST / f"hookycreator-backend-{triple}{suffix}"
        shutil.copy2(binary, output)
        print(f"Backend Tauri construit : {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
