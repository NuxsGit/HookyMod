#!/usr/bin/env python3
"""HookyMod · Asset Explorer — lanceur de l'application.

Utilisation :
    python AssetViewer.py              # interface graphique
    python -m hookymod.cli --help      # ligne de commande (sans GUI)

Dépendance principale : ``zstandard``  →  pip install -r requirements.txt
Optionnel : ``texture2ddecoder`` (décode BC7/ASTC des textures).
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from hookymod.app import main as gui_main
    except ModuleNotFoundError as exc:
        if exc.name == "tkinter":
            print(
                "Tkinter n'est pas installé pour ce Python.\n\n"
                "  • Windows / macOS : réinstalle Python depuis python.org (Tkinter inclus)\n"
                "  • Debian / Ubuntu : sudo apt install python3-tk\n\n"
                "En attendant, l'interface en ligne de commande fonctionne :\n"
                "    python -m hookymod.cli --help",
                file=sys.stderr,
            )
            return 1
        raise
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
