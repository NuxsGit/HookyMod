#!/usr/bin/env python3
"""Compile et installe le moteur Rust facultatif dans le dossier HookyMod.

Les dépendances Rust sont incluses localement : aucun téléchargement de crate.
Une toolchain Cargo récente est requise. Fermer HookyCreator avant de remplacer
la DLL sous Windows. L'outil autonome hookymod-rs est aussi compilé.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "rust" / "Cargo.toml"
DESTINATION = ROOT / "hookymod" / "_native_lib"
ABI = 5
REQUIRED_SYMBOLS = (
    "hookymod_core_zstd_decompress", "hookymod_core_zstd_free", "hookymod_core_buffer_free",
    "hookymod_core_sarc_parse", "hookymod_core_sarc_build",
    "hookymod_core_restbl_parse", "hookymod_core_restbl_build",
    "hookymod_core_bfres_find_shapes",
    "hookymod_core_deswizzle", "hookymod_core_swizzle", "hookymod_core_decode_bc",
    "hookymod_core_encode_pixels", "hookymod_core_downsample_mip",
    "hookymod_core_segment_indices", "hookymod_core_decode_positions",
    "hookymod_core_filter_triangles", "hookymod_core_rasterize",
)


def library_name() -> str:
    if sys.platform == "win32":
        return "hookymod_core.dll"
    if sys.platform == "darwin":
        return "libhookymod_core.dylib"
    if sys.platform.startswith("linux"):
        return "libhookymod_core.so"
    raise RuntimeError(f"Plateforme non prise en charge : {sys.platform}")


def main() -> int:
    cargo = shutil.which("cargo")
    if not cargo:
        print("Cargo introuvable : installe Rust et rouvre le terminal.", file=sys.stderr)
        return 1
    try:
        expected = library_name()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    command = [cargo, "build", "--manifest-path", str(MANIFEST), "--release",
               "--locked", "--offline", "--message-format=json-render-diagnostics"]
    print("Compilation du moteur Rust (sans téléchargement de crates)…", flush=True)
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        assert process.stdout is not None
        artifact = None
        executable = None
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(line, end="", file=sys.stderr)
                continue
            if event.get("reason") == "compiler-message":
                rendered = event.get("message", {}).get("rendered")
                if rendered:
                    print(rendered, end="", file=sys.stderr)
            if (event.get("reason") == "compiler-artifact"
                    and event.get("target", {}).get("name") == "hookymod_core"):
                for filename in event.get("filenames", []):
                    path = Path(filename)
                    if path.name.lower() == expected.lower():
                        artifact = path
            if (event.get("reason") == "compiler-artifact"
                    and event.get("target", {}).get("name") == "hookymod-rs"
                    and event.get("executable")):
                executable = Path(event["executable"])
        code = process.wait()
        if (code or artifact is None or not artifact.is_file()
                or executable is None or not executable.is_file()):
            print("Compilation Rust échouée, bibliothèque ou outil autonome introuvable.",
                  file=sys.stderr)
            return code or 1

        DESTINATION.mkdir(parents=True, exist_ok=True)
        temporary = DESTINATION / f".{os.getpid()}-{expected}"
        try:
            shutil.copyfile(artifact, temporary)
            # Tester dans un sous-processus : une DLL chargée dans l'installateur
            # resterait verrouillée sous Windows. Cela détecte aussi une
            # architecture Rust/Python incompatible avant de remplacer l'ancien.
            check = ("import ctypes,sys; "
                     "lib=ctypes.CDLL(sys.argv[1]); "
                     "lib.hookymod_core_abi.restype=ctypes.c_uint32; "
                     f"sys.exit(0 if lib.hookymod_core_abi()=={ABI} and "
                     f"all(hasattr(lib, name) for name in {REQUIRED_SYMBOLS!r}) else 1)")
            verified = subprocess.run([sys.executable, "-c", check, str(temporary)])
            if verified.returncode:
                print("Bibliothèque incompatible avec ce Python (architecture ou ABI).",
                      file=sys.stderr)
                return 1
            os.replace(temporary, DESTINATION / expected)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Moteur Rust installé : {DESTINATION / expected}")
        print(f"Outil Rust autonome compilé : {executable}")
        print("Redémarre HookyCreator ; vérifie Réglages > Moteur & performances.")
        return 0
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Installation du moteur Rust impossible : {exc}", file=sys.stderr)
        print("Ferme HookyCreator avant de recompiler, surtout sous Windows.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
