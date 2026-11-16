"""Build a reviewable source ZIP without Git history or local ignored data.

This exports the current working-tree content of tracked files (excluding files
deleted from the working tree), plus only the untracked files explicitly named
with --include-untracked. It is a safety aid, not a legal, provenance or
secret-scanning audit. Review the ZIP before sharing.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "release-preparation" / "HookyMod-source.zip"
MAX_FILE_SIZE = 50 * 1024 * 1024

FORBIDDEN_DIRS = {".git", "node_modules", "target", "__pycache__", ".venv", "google_drive",
                  "hookycreator_work", "release-preparation", "romfs", "exefs", "mods"}
FORBIDDEN_SUFFIXES = {
    ".zs", ".zst", ".szs", ".sarc", ".pack", ".bfres", ".bntx", ".bftex", ".bnsh",
    ".byml", ".bgyml", ".aamp", ".zsdic", ".nup", ".nso", ".npdm",
    ".bwav", ".bars", ".bfstm", ".bfsar", ".opus", ".ogg", ".wav", ".mp3", ".flac",
    ".webm", ".nsp", ".xci", ".nca", ".bin", ".exe", ".dll", ".so", ".dylib",
    ".zip", ".7z", ".rar", ".pem", ".p12", ".pfx", ".key", ".keys",
}
FORBIDDEN_NAMES = {"id_rsa", "id_ed25519", "id_ecdsa", ".env", ".netrc"}
# Some resource, executable and archive signatures at the beginning of a file.
# This cannot detect all protected material, renamed files or embedded data.
FORBIDDEN_MAGIC = (
    b"\x28\xb5\x2f\xfd", b"BNTX", b"FRES", b"SARC", b"BWAV", b"BARS", b"FSTM", b"FSAR",
    b"NUP\x01", b"NUP0", b"NUPX", b"NSO0", b"BNSH", b"AAMP", b"BYML", b"Yaz0", b"Yaz1",
    b"\x7fELF", b"MZ", b"PK\x03\x04", b"\x1a\x45\xdf\xa3", b"OggS", b"fLaC",
)
# A small set of critical files, not an exhaustive application/dependency audit.
REQUIRED_FILES = {
    "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "docs/PROVENANCE.md",
    "docs/PUBLICATION.md", "docs/licenses/NUP_EXPLORER_MIT.txt",
    "hookycreator/web/shaders/LICENSE-FSR1.txt", "rust/vendor/README.md",
    "rust/vendor/ruzstd/LICENSE", "rust/vendor/twox-hash/LICENSE",
    "rust/vendor/hmac-sha256/LICENSE", "assets/HookyMod-logo.png",
    "hookycreator/web/HookyMod.png", "src-tauri/desktop/HookyMod.png",
    "src-tauri/icons/32x32.png", "src-tauri/icons/128x128.png",
    "src-tauri/icons/128x128@2x.png", "src-tauri/icons/icon.ico", "src-tauri/icons/icon.icns",
}


def validate_relative_path(name: str) -> PurePosixPath:
    """Reject paths that could escape the snapshot or contain excluded data."""
    path = PurePosixPath(name)
    if (not name or "\\" in name or "\0" in name or path.is_absolute()
            or any(part in (".", "..") or ":" in part for part in path.parts)):
        raise ValueError(f"Unsafe path: {name!r}")
    lowered_parts = [part.lower() for part in path.parts]
    if any(part in FORBIDDEN_DIRS for part in lowered_parts):
        raise ValueError(f"Excluded directory: {name}")
    lower_name = path.name.lower()
    if lower_name in FORBIDDEN_NAMES or lower_name.startswith(".env.") or path.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise ValueError(f"Excluded game or credential file: {name}")
    return path


def read_safe_source(root: Path, name: str) -> tuple[bytes, int]:
    relative = validate_relative_path(name)
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"Symbolic link is not allowed: {name}")
    metadata = current.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_SIZE:
        raise ValueError(f"Not a regular source file or too large: {name}")
    contents = current.read_bytes()
    if any(contents.startswith(magic) for magic in FORBIDDEN_MAGIC):
        raise ValueError(f"Suspicious binary signature: {name}")
    return contents, stat.S_IMODE(metadata.st_mode)


def git_paths(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", *args],
        check=True, stdout=subprocess.PIPE,
    )
    return {os.fsdecode(item) for item in result.stdout.split(b"\0") if item}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-untracked", action="append", default=[], metavar="PATH",
                        help="explicitly include a non-ignored untracked source file")
    args = parser.parse_args(argv)
    tracked = git_paths("--cached")
    deleted = tracked & git_paths("--deleted")
    untracked = git_paths("--others", "--exclude-standard")
    selected = tracked - deleted
    for raw in args.include_untracked:
        name = validate_relative_path(raw).as_posix()
        if name in deleted:
            raise ValueError(f"Tracked file is deleted from the working tree: {name}")
        if name not in tracked and name not in untracked:
            raise ValueError(f"Not a tracked or non-ignored untracked file: {name}")
        selected.add(name)
    missing = REQUIRED_FILES - selected
    if missing:
        raise ValueError(f"Required files missing from selection: {', '.join(sorted(missing))}")

    # Validate everything before writing the output. Never follow a symlink or
    # read ignored files, and do not implicitly export unrelated untracked data.
    sources = [(name, *read_safe_source(ROOT, name)) for name in sorted(selected)]
    if OUTPUT.parent.is_symlink():
        raise ValueError(f"Output directory cannot be a symbolic link: {OUTPUT.parent}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(prefix=".hookymod-snapshot-", suffix=".zip", dir=OUTPUT.parent)
    os.close(descriptor)
    temporary = Path(temp_path)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data, mode in sources:
                info = zipfile.ZipInfo(f"HookyMod-source/{name}", (2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temporary.replace(OUTPUT)
    finally:
        temporary.unlink(missing_ok=True)
    ignored_untracked = sorted(untracked - selected)
    print(f"Exported {len(sources)} files to {OUTPUT}")
    print(f"SHA-256: {hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}")
    if deleted:
        print(f"Not exported: {len(deleted)} tracked file(s) deleted from the working tree.", file=sys.stderr)
    if ignored_untracked:
        print(f"Not exported: {len(ignored_untracked)} other untracked file(s).", file=sys.stderr)
    print("No Git history is included. Review contents, rights, notices and secrets before publishing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
