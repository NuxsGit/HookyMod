"""Entrée PyInstaller du backend Tauri (pas de lancement navigateur)."""
from hookycreator.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
