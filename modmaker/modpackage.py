"""Reference implementation of HookyMod's v1 ROMFS overlay package.

A mod package and a design workspace both use the .hooky extension, but have
separate format identifiers. This module packages explicitly supplied files;
it does not translate visual blocks, generate game assets, install a mod, or
run a launcher. The launcher must treat every payload as untrusted game data.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import struct
import sys
import unicodedata
import zipfile
import zlib
from pathlib import Path

FORMAT = "org.hookymod.mod"
VERSION = 1
KIND = "romfs-overlay"
MAX_FILES = 256
MAX_PATH_BYTES = 512
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 36 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = 512 * 1024
MAX_SOURCE_ENTRIES = 8192
ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?\Z")
SHA_RE = re.compile(r"[a-f0-9]{64}\Z")
META_KEYS = {"id", "name", "mod_version", "author", "description", "runtime_version", "game_version"}
MANIFEST_KEYS = {"format", "version", "kind", "id", "name", "mod_version", "author", "description", "target", "files"}
WINDOWS_DEVICES = {"con", "prn", "aux", "nul", "conin$", "conout$", "clock$",
                   *(f"com{i}" for i in "123456789¹²³"), *(f"lpt{i}" for i in "123456789¹²³")}


class PackageError(ValueError):
    """Invalid or unsupported .hooky mod package."""


def _keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise PackageError(f"{label} invalide ou version non prise en charge.")
    return value


def _text(value: object, label: str, limit: int, *, required: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > limit
            or any((ord(c) < 32 and c not in "\n\r\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise PackageError(f"{label} invalide.")
    if required and not value.strip():
        raise PackageError(f"{label} ne peut pas être vide.")
    return value


def _metadata(value: object) -> dict:
    meta = _keys(value, META_KEYS, "Métadonnées du mod")
    identity = meta["id"]
    if not isinstance(identity, str) or not ID_RE.fullmatch(identity):
        raise PackageError("Identifiant de mod invalide (minuscules, chiffres, points, tirets et underscores).")
    return {
        "id": identity,
        "name": _text(meta["name"], "Nom du mod", 96, required=True).strip(),
        "mod_version": _text(meta["mod_version"], "Version du mod", 64, required=True).strip(),
        "author": _text(meta["author"], "Auteur", 96),
        "description": _text(meta["description"], "Description", 1200),
        "runtime_version": _text(meta["runtime_version"], "Version de Longshot", 96).strip(),
        "game_version": _text(meta["game_version"], "Version du jeu", 96).strip(),
    }


def _path(value: object) -> str:
    if not isinstance(value, str) or unicodedata.normalize("NFC", value) != value:
        raise PackageError("Chemin de ressource invalide (UTF-8 NFC requis).")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise PackageError("Chemin de ressource invalide.") from exc
    if (len(encoded) > MAX_PATH_BYTES or any(c in '<>:"\\|?*' or unicodedata.category(c) in {"Cc", "Cs"}
                                               for c in value)):
        raise PackageError("Chemin de ressource interdit ou trop long.")
    parts = value.split("/")
    if len(parts) < 2 or len(parts) > 24 or parts[0] != "romfs":
        raise PackageError("Chaque fichier doit être sous romfs/.")
    for part in parts:
        if (not part or part in {".", ".."} or part.startswith(".") or part.endswith((" ", "."))
                or len(part.encode("utf-8")) > 255
                or part.split(".", 1)[0].rstrip(" ").casefold() in WINDOWS_DEVICES):
            raise PackageError("Composant de chemin interdit ou ambigu.")
    return value


def _paths(values: list[str]) -> None:
    """Reject duplicate files, case collisions and file/directory collisions."""
    prefixes: dict[str, str] = {}
    files: set[str] = set()
    directories: set[str] = set()
    for path in values:
        parts = _path(path).split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            folded = unicodedata.normalize("NFC", prefix.casefold())
            previous = prefixes.setdefault(folded, prefix)
            if previous != prefix:
                raise PackageError("Chemins en conflit par casse ou normalisation.")
            if index < len(parts):
                if folded in files:
                    raise PackageError("Un fichier ne peut pas être aussi un dossier.")
                directories.add(folded)
            else:
                if folded in files or folded in directories:
                    raise PackageError("Fichiers en double ou chemins contradictoires.")
                files.add(folded)


def _json(data: bytes) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PackageError("Clé JSON en double.")
            result[key] = value
        return result

    def reject_constant(_value):
        raise PackageError("Valeur JSON invalide.")

    return json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject_constant)


def _zip_info(name: str) -> zipfile.ZipInfo:
    item = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
    item.compress_type = zipfile.ZIP_DEFLATED
    item.create_system = 3
    item.external_attr = (stat.S_IFREG | 0o600) << 16
    return item


def make_package(metadata: object, files: object) -> bytes:
    """Create a deterministic package from a list of (romfs/path, bytes)."""
    meta = _metadata(metadata)
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise PackageError("Le paquet doit contenir de 1 à 256 fichiers.")
    entries: list[tuple[str, bytes]] = []
    total = 0
    for item in files:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise PackageError("Entrée de fichier invalide.")
        path, data = item
        path = _path(path)
        if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
            raise PackageError("Fichier invalide ou supérieur à 16 Mio.")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise PackageError("Les fichiers du paquet dépassent 32 Mio au total.")
        entries.append((path, data))
    _paths([path for path, _ in entries])
    entries.sort(key=lambda entry: entry[0])
    manifest = {
        "format": FORMAT, "version": VERSION, "kind": KIND,
        "id": meta["id"], "name": meta["name"], "mod_version": meta["mod_version"],
        "author": meta["author"], "description": meta["description"],
        "target": {"runtime": "longshot", "platform": "pc", "runtime_version": meta["runtime_version"],
                   "game_version": meta["game_version"]},
        "files": [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for path, data in entries],
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise PackageError("Manifeste trop volumineux.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(_zip_info("manifest.json"), manifest_bytes)
        for path, data in entries:
            archive.writestr(_zip_info(path), data)
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise PackageError("Paquet trop volumineux.")
    return result


def _preflight_zip(data: bytes) -> tuple[int, int]:
    """Bound central-directory parsing before zipfile allocates ZipInfo objects.

    Only classic, single-volume ZIPs without a prefix or archive comment are
    accepted. Scanning the directory ourselves also catches a forged entry
    count before handing untrusted bytes to ZipFile.
    """
    if len(data) < 22 or data[:4] != b"PK\x03\x04":
        raise PackageError("Enveloppe ZIP invalide.")
    end = len(data) - 22
    signature, disk, directory_disk, disk_count, count, size, offset, comment = struct.unpack_from(
        "<4s4H2LH", data, end)
    if (signature != b"PK\x05\x06" or disk or directory_disk or disk_count != count or comment
            or not 2 <= count <= MAX_FILES + 1 or size > MAX_CENTRAL_DIRECTORY_BYTES
            or offset + size != end):
        raise PackageError("Répertoire ZIP invalide, trop volumineux ou non pris en charge.")
    cursor = offset
    seen = 0
    while cursor < end:
        if cursor + 46 > end or data[cursor:cursor + 4] != b"PK\x01\x02":
            raise PackageError("Répertoire ZIP tronqué.")
        flags = struct.unpack_from("<H", data, cursor + 8)[0]
        compressed, uncompressed = struct.unpack_from("<II", data, cursor + 20)
        name_len, extra_len, comment_len, disk_start = struct.unpack_from("<HHHH", data, cursor + 28)
        local_offset = struct.unpack_from("<I", data, cursor + 42)[0]
        stop = cursor + 46 + name_len + extra_len + comment_len
        if (not name_len or name_len > MAX_PATH_BYTES or stop > end or disk_start
                or flags & (1 | 0x40 | 0x2000) or local_offset >= offset
                or compressed == 0xFFFFFFFF or uncompressed == 0xFFFFFFFF or local_offset == 0xFFFFFFFF):
            raise PackageError("Entrée ZIP invalide ou non prise en charge.")
        raw_name = data[cursor + 46:cursor + 46 + name_len]
        try:
            decoded = raw_name.decode("utf-8" if flags & 0x800 else "ascii")
        except UnicodeError as exc:
            raise PackageError("Nom d'entrée ZIP invalide : UTF-8 requis.") from exc
        if "\x00" in decoded:
            raise PackageError("Nom d'entrée ZIP contenant un caractère nul.")
        seen += 1
        if seen > MAX_FILES + 1:
            raise PackageError("Nombre d'entrées ZIP trop élevé.")
        cursor = stop
    if cursor != end or seen != count:
        raise PackageError("Nombre d'entrées ZIP incohérent.")
    return seen, offset


def _entry_bytes(archive: zipfile.ZipFile, data: bytes, entry: zipfile.ZipInfo, boundary: int) -> bytes:
    """Read one complete ZIP stream, rejecting size truncation and ambiguity.

    ZipExtFile stops at the advertised uncompressed size. A forged ZIP can
    therefore have a valid CRC for a short prefix while its DEFLATE stream
    actually expands further. Check the entire stream with a bounded decoder,
    as well as both local and central headers, before trusting its bytes.
    """
    offset = entry.header_offset
    if offset + 30 > boundary:
        raise PackageError("En-tête ZIP tronqué.")
    (signature, _version, flags, method, _time, _date, local_crc, local_compressed,
     local_size, name_size, extra_size) = struct.unpack_from("<4s5H3I2H", data, offset)
    if signature != b"PK\x03\x04" or flags != entry.flag_bits or method != entry.compress_type:
        raise PackageError("En-têtes ZIP incohérents.")
    start = offset + 30 + name_size + extra_size
    stop = start + entry.compress_size
    if start > boundary or stop > boundary:
        raise PackageError("Fichiers ZIP chevauchants ou tronqués.")
    expected = (entry.CRC, entry.compress_size, entry.file_size)
    local = (local_crc, local_compressed, local_size)
    if flags & 8:  # A classic data descriptor may follow the compressed stream.
        if any(value not in (0, actual) for value, actual in zip(local, expected)):
            raise PackageError("En-tête ZIP incohérent.")
        unsigned = (stop + 12 == boundary and struct.unpack_from("<III", data, stop) == expected)
        signed = (stop + 16 == boundary and data[stop:stop + 4] == b"PK\x07\x08"
                  and struct.unpack_from("<III", data, stop + 4) == expected)
        if not (unsigned or signed):
            raise PackageError("Descripteur ZIP incohérent ou tronqué.")
    elif local != expected or stop != boundary:
        raise PackageError("En-tête ZIP incohérent ou données ZIP non déclarées.")
    # Let zipfile check local filename decoding, supported flags and overlap.
    with archive.open(entry):
        pass
    compressed = memoryview(data)[start:stop]
    if method == zipfile.ZIP_STORED:
        if entry.compress_size != entry.file_size:
            raise PackageError("Taille ZIP stockée incohérente.")
        result = bytes(compressed)
    elif method == zipfile.ZIP_DEFLATED:
        decoder = zlib.decompressobj(-15)
        result = decoder.decompress(compressed, entry.file_size + 1)
        if (len(result) != entry.file_size or not decoder.eof
                or decoder.unconsumed_tail or decoder.unused_data):
            raise PackageError("Flux ZIP tronqué, excédentaire ou invalide.")
    else:
        raise PackageError("Méthode ZIP non prise en charge.")
    if len(result) != entry.file_size or (zlib.crc32(result) & 0xFFFFFFFF) != entry.CRC:
        raise PackageError("Taille ou CRC ZIP incorrect.")
    return result


def read_package(data: bytes) -> tuple[dict, dict[str, bytes]]:
    """Validate the entire package without extracting or executing anything."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ARCHIVE_BYTES:
        raise PackageError("Paquet .hooky vide ou trop volumineux.")
    try:
        expected_entries, directory_offset = _preflight_zip(data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) != expected_entries:
                raise PackageError("Nombre d'entrées du paquet invalide.")
            offsets = sorted(entry.header_offset for entry in entries)
            if offsets[0] != 0 or len(set(offsets)) != len(offsets):
                raise PackageError("Emplacements d'entrées ZIP invalides.")
            boundaries = dict(zip(offsets, offsets[1:] + [directory_offset]))
            names = [entry.filename for entry in entries]
            if len(set(names)) != len(names) or names.count("manifest.json") != 1:
                raise PackageError("Manifeste manquant ou entrées ZIP en double.")
            for entry in entries:
                mode = entry.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                limit = MAX_MANIFEST_BYTES if entry.filename == "manifest.json" else MAX_FILE_BYTES
                if (entry.is_dir() or entry.orig_filename != entry.filename or entry.external_attr & 0x10
                        or entry.flag_bits & (1 | 0x40 | 0x2000) or entry.file_size > limit
                        or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or (entry.create_system == 3 and file_type not in {0, stat.S_IFREG})):
                    raise PackageError("Entrée ZIP non prise en charge ou trop volumineuse.")
            manifest_entry = archive.getinfo("manifest.json")
            manifest = _json(_entry_bytes(archive, data, manifest_entry, boundaries[manifest_entry.header_offset]))
            if isinstance(manifest, dict) and manifest.get("format") == "org.hookymod.workspace":
                raise PackageError("Ceci est un projet de conception .hooky, pas un paquet de mod.")
            manifest = _keys(manifest, MANIFEST_KEYS, "Manifeste")
            if (manifest["format"] != FORMAT or type(manifest["version"]) is not int
                    or manifest["version"] != VERSION or manifest["kind"] != KIND):
                raise PackageError("Ce fichier n'est pas un paquet de mod .hooky v1 pris en charge.")
            target = _keys(manifest["target"], {"runtime", "platform", "runtime_version", "game_version"}, "Cible")
            if target["runtime"] != "longshot" or target["platform"] != "pc":
                raise PackageError("Cible du paquet non prise en charge.")
            _metadata({key: (target[key] if key in {"runtime_version", "game_version"} else manifest[key])
                       for key in META_KEYS})
            rows = manifest["files"]
            if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
                raise PackageError("Liste de fichiers invalide.")
            paths = []
            total = 0
            for row in rows:
                row = _keys(row, {"path", "size", "sha256"}, "Fichier")
                path = _path(row["path"])
                size, digest = row["size"], row["sha256"]
                if (type(size) is not int or not 0 <= size <= MAX_FILE_BYTES
                        or not isinstance(digest, str) or not SHA_RE.fullmatch(digest)):
                    raise PackageError("Taille ou empreinte de fichier invalide.")
                total += size
                if total > MAX_TOTAL_BYTES:
                    raise PackageError("Les fichiers du paquet dépassent 32 Mio au total.")
                paths.append(path)
            _paths(paths)
            if set(names) != {"manifest.json", *paths}:
                raise PackageError("Les fichiers ZIP ne correspondent pas au manifeste.")
            content = {}
            for row in rows:
                path = row["path"]
                entry = archive.getinfo(path)
                if entry.file_size != row["size"]:
                    raise PackageError("Taille de fichier incohérente.")
                raw = _entry_bytes(archive, data, entry, boundaries[entry.header_offset])
                if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                    raise PackageError("Empreinte de fichier incorrecte.")
                content[path] = raw
            return manifest, content
    except PackageError:
        raise
    except (zipfile.BadZipFile, ValueError, UnicodeError, OSError, RuntimeError, KeyError,
            NotImplementedError, RecursionError, EOFError, struct.error, zlib.error) as exc:
        raise PackageError("Impossible de lire le paquet de mod .hooky.") from exc


def _is_link_or_reparse(info: os.stat_result) -> bool:
    # Windows junctions and other reparse points must not act as directories.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def collect_romfs(root: Path) -> list[tuple[str, bytes]]:
    """Read only a user-selected mod overlay; do not follow its tree's links."""
    root = Path(root)
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise PackageError("Choisis un dossier romfs existant.") from exc
    if _is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise PackageError("Choisis un dossier romfs réel, sans lien symbolique.")
    result = []
    total = 0
    for seen, path in enumerate(root.rglob("*"), 1):
        if seen > MAX_SOURCE_ENTRIES:
            raise PackageError("Trop de fichiers ou dossiers à parcourir dans la source.")
        info = path.lstat()
        if _is_link_or_reparse(info):
            raise PackageError("Les liens symboliques et points de réanalyse ne sont pas acceptés.")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise PackageError("Seuls les fichiers ordinaires sont acceptés.")
        name = "romfs/" + path.relative_to(root).as_posix()
        _path(name)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _is_link_or_reparse(opened) or not stat.S_ISREG(opened.st_mode):
                raise PackageError("Seuls les fichiers ordinaires sont acceptés.")
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise PackageError("Un fichier du paquet dépasse 16 Mio.")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise PackageError("Les fichiers du paquet dépassent 32 Mio au total.")
        result.append((name, data))
        if len(result) > MAX_FILES:
            raise PackageError("Le paquet contient plus de 256 fichiers.")
    return result


def _read_archive_file(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise PackageError("Paquet .hooky trop volumineux.")
    return data


def unpack_new(data: bytes, destination: Path) -> dict:
    """Validate then extract to a newly created folder, never overwriting one."""
    manifest, content = read_package(data)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise PackageError("Le dossier de destination existe déjà : choisis un nouveau nom.")
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise PackageError("Le dossier de destination existe déjà.") from exc
    try:
        for name, raw in content.items():
            path = destination.joinpath(*name.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                output.write(raw)
    except OSError as exc:
        # Never delete a partially written folder: another local process might
        # have put files there. The user can inspect and remove it explicitly.
        raise PackageError(f"Extraction interrompue ; le dossier partiel est conservé : {destination}") from exc
    return manifest


def _write_new(path: Path, data: bytes) -> None:
    """Create a package without replacing an existing file or symbolic link."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PackageError("Le fichier de destination existe déjà : choisis un nouveau nom.") from exc
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
    except OSError as exc:
        # Preserve a partial file instead of risking removal of a path another
        # local process has changed. Its destination was created exclusively.
        raise PackageError(f"Écriture interrompue ; le fichier partiel est conservé : {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paquets .hooky de fichiers de mod ROMFS, sans installation automatique.")
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack", help="emballer un dossier romfs de mod existant")
    pack.add_argument("--romfs", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--id", required=True)
    pack.add_argument("--name", required=True)
    pack.add_argument("--mod-version", default="0.1.0")
    pack.add_argument("--author", default="")
    pack.add_argument("--description", default="")
    pack.add_argument("--runtime-version", default="")
    pack.add_argument("--game-version", default="")
    inspect = sub.add_parser("inspect", help="valider et afficher le manifeste sans extraire")
    inspect.add_argument("archive", type=Path)
    unpack = sub.add_parser("unpack", help="extraire dans un nouveau dossier, sans installer")
    unpack.add_argument("archive", type=Path)
    unpack.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            if args.output.suffix.lower() != ".hooky":
                raise PackageError("La destination doit porter l'extension .hooky.")
            if args.output.resolve().is_relative_to(args.romfs.resolve()):
                raise PackageError("Place le paquet en dehors du dossier romfs source.")
            metadata = {key: getattr(args, key) for key in META_KEYS}
            content = make_package(metadata, collect_romfs(args.romfs))
            _write_new(args.output, content)
            print(f"Paquet créé : {args.output} (compatibilité en jeu non vérifiée)")
        elif args.command == "inspect":
            manifest, _files = read_package(_read_archive_file(args.archive))
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        else:
            unpack_new(_read_archive_file(args.archive), args.destination)
            print(f"Fichiers extraits dans un nouveau dossier : {args.destination} (non installé)")
        return 0
    except (PackageError, OSError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
