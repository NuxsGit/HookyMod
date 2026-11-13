"""Point d'entrée : ``python -m hookycreator --root <dossier du jeu>``."""

from __future__ import annotations

import argparse
import sys

from .server import HookyCreatorServer
from .project import ProjectError


def main(argv: list[str] | None = None) -> int:
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
        while True:
            import time
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nArrêt.")
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
