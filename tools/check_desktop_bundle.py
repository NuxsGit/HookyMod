"""Vérifie les modules et ressources présents dans un sidecar PyInstaller.

Usage : python tools/check_desktop_bundle.py chemin/vers/hookycreator-backend
PyInstaller doit être installé dans le Python qui exécute cette vérification.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REQUIRED = frozenset({
    "hookycreator.server", "hookycreator.project", "hookycreator.session",
    "hookycreator.model_skin", "hookymod.animation", "hookymod.gltf_animation",
    "hookymod.modelio", "hookycreator/web/index.html", "hookycreator/web/app.js",
    "hookycreator/web/style.css", "hookycreator/web/i18n.js",
    "hookycreator/web/model_viewer.js", "hookycreator/web/model_animation.js",
    "hookycreator/web/player.js",
})
# Include both PYZ module names and possible accidentally bundled source paths.
FORBIDDEN = frozenset({
    "modmaker", "hookycreator.modpackage", "hookycreator/modpackage",
    "hookycreator.studio", "hookycreator/studio", "hookycreator/web/studio",
    "hookymod.app", "hookymod/app", "hookymod.cli", "hookymod/cli",
    "hookymod.__main__", "hookymod/__main__", "hookymod.translate", "hookymod/translate",
})


def verify_entries(entries: set[str]) -> None:
    """Reject absent app components and maker/standalone CLI inclusions."""
    normalized = {entry.strip().replace("\\", "/") for entry in entries}
    missing = sorted(REQUIRED - normalized)
    unwanted = sorted(name for name in normalized if any(
        name == prefix or name.startswith((prefix + ".", prefix + "/")) for prefix in FORBIDDEN))
    if missing or unwanted:
        raise ValueError(f"Contenu du backend inattendu : absents={missing}, interdits={unwanted}")


def archive_entries(binary: Path) -> set[str]:
    if not binary.is_file():
        raise FileNotFoundError(f"Backend introuvable : {binary}")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller.utils.cliutils.archive_viewer", "--recursive", "--brief", str(binary)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Inspection PyInstaller impossible : {result.stderr.strip()}")
    # Brief listings indent entries; headings and section labels are not names.
    return {line.strip() for line in result.stdout.splitlines() if line.startswith(" ")}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    try:
        verify_entries(archive_entries(Path(sys.argv[1])))
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print("Backend PyInstaller : ressources assets/Player présentes, mod maker et CLI séparées absents.")


if __name__ == "__main__":
    main()
