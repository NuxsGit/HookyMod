"""Point d'entrée : ``python -m hookycreator --root <dossier du jeu>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .server import HookyCreatorServer, _loopback_host
from .project import MAX_SCAN_FILES, ProjectError


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
                        help="types à exporter/signaler (texture, model, animation, auxiliary)")
    parser.add_argument("--limit", type=int, help="s'arrêter après N éléments (essai)")
    parser.add_argument("--work", help="dossier de travail (dictionnaires, cache)")
    parser.add_argument("--scan-limit", type=int, default=MAX_SCAN_FILES,
                        help="maximum de fichiers reconnus (0 = illimité)")
    args = parser.parse_args(argv)

    from .project import Project, ProjectError
    try:
        project = Project(Path(args.root), Path(args.work) if args.work else None,
                          scan_limit=args.scan_limit)
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
    return 0 if report["error"] == 0 and not report.get("scan_incomplete") else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("export-all", "export-all-assets"):
        return _export_all(argv[1:])
    parser = argparse.ArgumentParser(
        prog="hookycreator",
        description="HookyCreator — exploration et édition de ressources (textures, modèles, sons).",
    )
    parser.add_argument("--version", action="version", version=f"HookyCreator {__version__}")
    parser.add_argument("root", nargs="?", default=".", help="dossier du projet (romfs ou assets extraits)")
    parser.add_argument("--work", help="dossier de travail (défaut : <projet>/hookycreator_work)")
    parser.add_argument("--host", default="127.0.0.1", help="adresse d'écoute (défaut 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="port (défaut 8765)")
    parser.add_argument("--open", action="store_true", help="ouvre le navigateur")
    parser.add_argument("--scan-limit", type=int, default=MAX_SCAN_FILES,
                        help="maximum de fichiers reconnus (0 = illimité)")
    args = parser.parse_args(argv)

    try:
        server = HookyCreatorServer(args.root, args.work, args.host, args.port,
                                    scan_limit=args.scan_limit)
    except ProjectError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    server.start(open_browser=args.open)
    # Le constructeur a déjà scanné le projet avant d'ouvrir le navigateur.
    state = server.scan_summary
    print(f"HookyCreator {__version__} — {state['count']} asset(s) "
          f"({state['textures']} textures, {state['models']} modèles, "
          f"{state['bytecode']} fichiers de données)")
    print(f"  projet : {state['root']}")
    print(f"  écoute : {server.url}")
    if not _loopback_host(args.host):
        print("  Attention : accès réseau sans authentification. N'expose pas ce serveur à un réseau non fiable.")
    if state.get("truncated"):
        print("  Attention : scan limité. Relance avec --scan-limit 0 pour indexer "
              "tous les fichiers reconnus.")
    if state.get("scan_errors"):
        print(f"  Attention : {state['scan_errors']} entrée(s) illisible(s) pendant le scan.")
        for detail in state.get("scan_error_examples", []):
            print(f"    {detail}")
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
