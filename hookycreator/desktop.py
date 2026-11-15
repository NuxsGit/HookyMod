"""Backend de l'application Tauri, lancé et surveillé par son processus parent.

Ce point d'entrée ne remplace pas ``python -m hookycreator`` : il écoute
uniquement sur la boucle locale et un port choisi par le système. Un jeton
aléatoire est communiqué à la fenêtre native sur stdout, jamais dans les
arguments ou les URL HTTP ; il est échangé sur la boucle locale. La fermeture
du tube stdin arrête le serveur, même si la fenêtre parente a disparu
brutalement, y compris pendant le scan initial.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time

from .server import HookyCreatorServer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hookycreator-desktop-backend")
    parser.add_argument("root", help="dossier du projet choisi dans la fenêtre native")
    parser.add_argument("--work", help="dossier de travail facultatif")
    args = parser.parse_args(argv)
    token = secrets.token_urlsafe(32)
    lifecycle_lock = threading.Lock()
    started: list[HookyCreatorServer] = []

    def parent_closed() -> None:
        try:
            # Le parent conserve l'extrémité d'écriture ouverte pendant toute
            # sa vie ; EOF signifie qu'il a quitté, même sans arrêt propre.
            sys.stdin.buffer.read(1)
        except (AttributeError, OSError, ValueError):
            pass
        with lifecycle_lock:
            if not started:
                # Le scan initial peut durer longtemps : ne pas laisser un
                # processus orphelin simplement parce qu'il n'écoute pas encore.
                os._exit(0)
            server = started[0]
        server.stop()

    threading.Thread(target=parent_closed, daemon=True).start()
    try:
        server = HookyCreatorServer(args.root, args.work, host="127.0.0.1", port=0,
                                    desktop_token=token).start()
        with lifecycle_lock:
            started.append(server)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
        return 1

    try:
        print(json.dumps({"port": server.port, "token": token}), flush=True)
    except (BrokenPipeError, OSError):
        server.stop()
        return 1

    try:
        while not server.stopped:
            time.sleep(0.2)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
