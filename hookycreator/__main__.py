"""Point d'entrée : ``python -m hookycreator --root <dossier du jeu>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .server import HookyCreatorServer
from .project import ProjectError


def _export_all(argv: list[str]) -> int:
    """``python -m hookycreator export-all <racine> -o <dossier>`` (sans navigateur)."""
    from .bulk import export_all, summary

    parser = argparse.ArgumentParser(
        prog="hookycreator export-all",
        description="Exporte toutes les textures (PNG) et tous les modèles du projet.",
    )
    parser.add_argument("root", help="dossier du projet (romfs, packs compris)")
    parser.add_argument("--out", "-o", default="export_all", help="dossier de sortie")
    parser.add_argument("--kinds", default="texture,model",
                        help="types à exporter (texture, model, animation)")
    parser.add_argument("--limit", type=int, help="s'arrêter après N éléments (essai)")
    parser.add_argument("--work", help="dossier de travail (dictionnaires, cache)")
    args = parser.parse_args(argv)

    from .project import Project, ProjectError
    try:
        project = Project(Path(args.root), Path(args.work) if args.work else None)
    except ProjectError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    state = project.scan()
    print(f"{state['count']} fichier(s) repéré(s) — recherche dans les packs…")
    kinds = tuple(part.strip() for part in args.kinds.split(",") if part.strip())

    def progress(done: int, total: int, label: str) -> None:
        if label:
            print(f"  [{done + 1}/{total}] {label}", flush=True)

    report = export_all(project, Path(args.out), kinds=kinds, limit=args.limit,
                        progress=progress)
    print(summary(report))
    print(f"\nRapport : {Path(args.out) / 'rapport.json'}")
    return 0 if report["error"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("export-all", "export-all-assets"):
        return _export_all(argv[1:])
    parser = argparse.ArgumentParser(
        prog="hookycreator",
        description="HookyCreator — atelier de contenu (mobs, textures, modèles).",
    )
    parser.add_argument("root", nargs="?", default=".", help="dossier du projet (romfs ou assets extraits)")
    parser.add_argument("--work", help="dossier de travail (défaut : <projet>/hookycreator_work)")
    parser.add_argument("--host", default="0.0.0.0", help="adresse d'écoute (défaut 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="port (défaut 8765)")
    parser.add_argument("--open", action="store_true", help="ouvre le navigateur")
    args = parser.parse_args(argv)

    try:
        server = HookyCreatorServer(args.root, args.work, args.host, args.port)
    except ProjectError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    server.start(open_browser=args.open)
    state = server.project.scan()
    print(f"HookyCreator 1.0 — {state['count']} asset(s) "
          f"({state['textures']} textures, {state['models']} modèles, "
          f"{state['bytecode']} fichiers de données)")
    print(f"  projet : {state['root']}")
    print(f"  écoute : http://127.0.0.1:{server.port}/")
    try:
        import time
        while not server.stopped:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nArrêt.")
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
