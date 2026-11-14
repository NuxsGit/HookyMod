#!/usr/bin/env python3
"""Compare Rust et Python sur un BFRES réel, sans rien écrire dans le dump.

Deux dossiers de travail indépendants sont créés puis supprimés. Les maillages,
la fiche, l'aperçu PNG et les exports géométriques OBJ/glTF sont comparés. Pour
les gros modèles, les deux copies de géométrie peuvent demander de la mémoire.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hookycreator.project import Project, ProjectError  # noqa: E402
from hookymod import blender, native  # noqa: E402
from hookymod.util import HookyError  # noqa: E402


def geometry_files(meshes, folder: Path) -> dict[str, tuple[int, str]]:
    """Empreintes des exports géométriques ; aucune texture n'est exportée."""
    blender.export_bundle(meshes, folder, name="comparaison")
    files = {}
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in (".obj", ".mtl", ".gltf", ".bin"):
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        files[path.name] = (path.stat().st_size, digest.hexdigest())
    return files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="dossier du dump romfs")
    parser.add_argument("model", help="chemin du BFRES dans le dump")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    parser.add_argument("--no-textures", action="store_true", help="aperçu sans textures")
    args = parser.parse_args(argv)
    if not 64 <= args.width <= 1600 or not 64 <= args.height <= 1600:
        parser.error("largeur et hauteur doivent être comprises entre 64 et 1600")
    if not native.available():
        print(f"Comparaison impossible : {native.status()['reason']}", file=sys.stderr)
        return 2
    root = Path(args.root).expanduser().resolve()
    requested = Path(args.model).expanduser()
    try:
        resolved = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
        rel = resolved.relative_to(root).as_posix()
    except ValueError:
        parser.error("le modèle doit se trouver dans le dossier du projet")
    try:
        with tempfile.TemporaryDirectory(prefix="hookymod-compare-") as directory:
            work = Path(directory)
            project = Project(root, work / "rust")
            project.scan()
            rust_info = project.model_info(rel)
            rust_meshes = project.model_meshes(rel)
            rust_png = project.model_preview(rel, args.width, args.height,
                                             textured=not args.no_textures)
            rust_files = geometry_files(rust_meshes, work / "rust-export")

            # Chaque projet a son propre cache : un résultat Rust ne peut donc
            # pas être relu par erreur pendant la mesure de référence Python.
            with patch.object(native, "available", return_value=False):
                reference = Project(root, work / "python")
                reference.scan()
                python_info = reference.model_info(rel)
                python_meshes = reference.model_meshes(rel)
                python_png = reference.model_preview(rel, args.width, args.height,
                                                      textured=not args.no_textures)
                python_files = geometry_files(python_meshes, work / "python-export")

            checks = {
                "fiche et diagnostics": rust_info == python_info,
                "positions, indices, UV et normales": rust_meshes == python_meshes,
                "aperçu PNG": rust_png == python_png,
                "exports géométriques OBJ/glTF": rust_files == python_files and bool(rust_files),
            }
            for label, identical in checks.items():
                print(f"{label} : {'identique' if identical else 'DIFFÉRENT'}")
            print(f"{rust_info['vertices']} sommets, {rust_info['faces']} triangles, "
                  f"{len(rust_info['textures'])} texture(s) associée(s).")
            print("Les fichiers temporaires sont supprimés ; le dump n'est pas modifié.")
            return 0 if all(checks.values()) else 1
    except (ProjectError, HookyError, OSError, ValueError) as exc:
        print(f"Comparaison impossible : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
