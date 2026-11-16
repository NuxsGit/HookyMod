#!/usr/bin/env python3
"""Mesure l'ouverture d'un modèle du projet avec un cache HookyCreator vierge.

Ne copie et ne modifie aucun fichier du dump. Le dossier de travail et ses
caches sont temporaires. La mesure ne comprend pas l'interface du navigateur.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hookycreator.project import Project, ProjectError  # noqa: E402
from hookymod import native  # noqa: E402
from hookymod.util import HookyError  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="dossier du dump (par exemple romfs)")
    parser.add_argument("model", help="chemin du BFRES relatif au dossier du dump")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--no-textures", action="store_true", help="désactiver les textures")
    args = parser.parse_args(argv)
    if not 64 <= args.width <= 1600 or not 64 <= args.height <= 1600:
        parser.error("largeur et hauteur doivent être comprises entre 64 et 1600")
    root = Path(args.root).expanduser().resolve()
    requested = Path(args.model).expanduser()
    try:
        resolved = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
        rel = resolved.relative_to(root).as_posix()
    except ValueError:
        parser.error("le modèle doit se trouver dans le dossier du projet")
    try:
        with tempfile.TemporaryDirectory(prefix="hookymod-benchmark-") as work:
            start = time.perf_counter()
            project = Project(root, Path(work))
            initialization = time.perf_counter() - start
            start = time.perf_counter()
            scanned = project.scan()
            scan = time.perf_counter() - start

            def open_model():
                info = project.model_info(rel)
                png = project.model_preview(rel, args.width, args.height,
                                            textured=not args.no_textures)
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ProjectError("l'aperçu généré n'est pas un PNG")
                return info, png

            start = time.perf_counter()
            info, cold_png = open_model()
            cold = time.perf_counter() - start
            start = time.perf_counter()
            _, warm_png = open_model()
            warm = time.perf_counter() - start
            if cold_png != warm_png:
                raise ProjectError("l'aperçu en cache diffère de l'aperçu initial")
            print(f"Moteur Rust : {'actif' if native.available() else 'inactif'}")
            print(f"Initialisation : {initialization:.3f} s ; scan de {scanned['count']} "
                  f"fichier(s) : {scan:.3f} s")
            print(f"Modèle : {info['vertices']} sommets, {info['faces']} triangles, "
                  f"aperçu {args.width}×{args.height}")
            print(f"Ouverture côté serveur, cache HookyCreator vierge : {cold:.3f} s")
            print(f"Réouverture côté serveur, cache chaud : {warm:.3f} s")
            result = "atteint" if cold <= 3.0 else "non atteint"
            print(f"Objectif de 3 s côté serveur : {result}. "
                  "Le navigateur et son affichage ne sont pas mesurés.")
            print("Le cache du système d'exploitation n'a pas été vidé ; "
                  "le dossier de travail temporaire sera supprimé.")
        return 0
    except (ProjectError, HookyError, OSError, ValueError) as exc:
        print(f"Mesure impossible : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
