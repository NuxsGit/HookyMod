"""Assemble four tested installer artifacts and SHA-256 sums for a beta draft.

This is run after the platform jobs have passed. It refuses missing, duplicate
or empty installers and never packages local projects, user data or Git history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# (CI artifact name, suffix, platform-specific output suffix)
INSTALLERS = (
    ("HookyCreator-ubuntu-24.04", ".deb", "linux-x86_64.deb"),
    ("HookyCreator-windows-2022", ".exe", "windows-x86_64-setup.exe"),
    ("HookyCreator-macos-15", ".dmg", "macos-arm64.dmg"),
    ("HookyCreator-macos-15-intel", ".dmg", "macos-x86_64.dmg"),
)
VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assemble(artifacts: Path, output: Path, version: str) -> list[Path]:
    if not VERSION.fullmatch(version):
        raise ValueError(f"Version numérique attendue : {version!r}")
    sources: list[tuple[Path, str]] = []
    for artifact, suffix, destination in INSTALLERS:
        directory = artifacts / artifact
        matches = sorted(path for path in directory.rglob(f"*{suffix}") if path.is_file())
        if len(matches) != 1 or matches[0].is_symlink() or matches[0].stat().st_size == 0:
            raise ValueError(f"Un seul installateur non vide attendu pour {artifact} : {matches}")
        sources.append((matches[0], f"HookyCreator-{version}-{destination}"))

    # Validate all inputs before creating any output; do not overwrite a prior draft.
    output.mkdir(parents=True, exist_ok=False)
    files = []
    for source, name in sources:
        target = output / name
        shutil.copyfile(source, target)
        files.append(target)
    sums = output / "SHA256SUMS.txt"
    sums.write_text("".join(f"{sha256_file(path)}  {path.name}\n" for path in files),
                    encoding="utf-8")
    return [*files, sums]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    version = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    for path in assemble(args.artifacts, args.output, version):
        print(f"{sha256_file(path)}  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
