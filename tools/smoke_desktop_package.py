"""Vérifie le contenu et le moteur réellement fournis par un paquet Tauri.

Sous Linux, extrait le .deb sans l'installer. Sous Windows, installe le NSIS
silencieusement dans un dossier temporaire (à utiliser sur une machine de test).
Sous macOS, monte le DMG en lecture seule et teste le backend dans l'application.
Le backend ainsi livré est testé avec des ressources entièrement synthétiques.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from smoke_desktop_backend import run as smoke_backend

ROOT = Path(__file__).resolve().parent.parent

# These source notices accompany each installer. They are not a complete
# inventory of the platform-specific dependency licenses.
NOTICE_PATHS = (
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "docs/PROVENANCE.md",
    "docs/licenses/NUP_EXPLORER_MIT.txt",
    "hookycreator/web/shaders/LICENSE-FSR1.txt",
    "rust/vendor/README.md",
    "rust/vendor/ruzstd/LICENSE",
    "rust/vendor/twox-hash/LICENSE",
    "rust/vendor/hmac-sha256/LICENSE",
)


def check_notices(license_dir: Path) -> None:
    for relative in NOTICE_PATHS:
        bundled = license_dir / relative
        if not bundled.is_file() or bundled.read_bytes() != (ROOT / relative).read_bytes():
            raise RuntimeError(f"Licence ou notice absente ou altérée dans le paquet : {bundled}")


def macos_bundle_paths(volume: Path) -> tuple[Path, Path, Path]:
    """Find the actual executable from Info.plist and its adjacent sidecar."""
    applications = sorted(path for path in volume.glob("*.app") if path.is_dir())
    if len(applications) != 1:
        raise RuntimeError(f"Une seule application attendue dans le DMG : {applications}")
    contents = applications[0] / "Contents"
    with (contents / "Info.plist").open("rb") as file:
        executable = plistlib.load(file).get("CFBundleExecutable")
    if not isinstance(executable, str) or not executable or Path(executable).name != executable:
        raise RuntimeError("CFBundleExecutable invalide dans Info.plist.")
    binary_dir = contents / "MacOS"
    return (binary_dir / executable, binary_dir / "hookycreator-backend",
            contents / "Resources" / "licenses")


@contextmanager
def mounted_dmg(package: Path, mount: Path):
    """Mount read-only and always detach, including after a failed smoke test."""
    mount.mkdir()
    subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint",
                    str(mount), str(package)], check=True, timeout=180)
    try:
        yield mount
    finally:
        subprocess.run(["hdiutil", "detach", str(mount)], check=True, timeout=60)


def verify_contents(app: Path, backend: Path, license_dir: Path, *, executable: bool) -> None:
    if not app.is_file() or not backend.is_file():
        raise RuntimeError(f"Exécutables absents du paquet : {app}, {backend}")
    if executable and (not os.access(app, os.X_OK) or not os.access(backend, os.X_OK)):
        raise RuntimeError("Les exécutables du paquet ne sont pas exécutables.")
    check_notices(license_dir)
    smoke_backend(backend)
    print("Paquet : exécutables, licences connues et sidecar livré vérifiés.")


def run() -> None:
    windows = sys.platform == "win32"
    macos = sys.platform == "darwin"
    linux = sys.platform.startswith("linux")
    if not (windows or macos or linux):
        raise SystemExit("Ce test est prévu pour les paquets Linux, macOS et Windows.")
    kind, suffix = (("nsis", ".exe") if windows else
                    ("dmg", ".dmg") if macos else ("deb", ".deb"))
    bundle_dir = ROOT / "src-tauri" / "target" / "release" / "bundle" / kind
    packages = sorted(bundle_dir.glob(f"*{suffix}"))
    if len(packages) != 1:
        raise RuntimeError(f"Un seul paquet {kind} attendu dans {bundle_dir}, reçu : {packages}")
    package = packages[0].resolve()
    print(f"Vérification du paquet : {package.name}", flush=True)
    with tempfile.TemporaryDirectory(prefix="hooky-package-test-") as temporary:
        directory = Path(temporary)
        if macos:
            with mounted_dmg(package, directory / "volume") as volume:
                app, backend, license_dir = macos_bundle_paths(volume)
                verify_contents(app, backend, license_dir, executable=True)
            return
        if windows:
            installed = directory / "installed"
            # NSIS exige /D= en dernier et sans guillemets autour du chemin.
            command = f'"{package}" /S /D={installed}'
            subprocess.run(command, check=True, timeout=180)
            app = installed / "hookycreator-desktop.exe"
            backend = installed / "hookycreator-backend.exe"
            license_dir = installed / "licenses"
        else:
            subprocess.run(["dpkg-deb", "--extract", str(package), str(directory)],
                           check=True, timeout=180)
            app = directory / "usr" / "bin" / "hookycreator-desktop"
            backend = app.with_name("hookycreator-backend")
            license_dir = directory / "usr" / "lib" / "HookyCreator" / "licenses"
        verify_contents(app, backend, license_dir, executable=not windows)


if __name__ == "__main__":
    run()
