"""Interface en ligne de commande (sans Tkinter).

Exemples ::

    python -m hookymod.cli scan /chemin/dump_du_jeu
    python -m hookymod.cli scan /chemin/dump_du_jeu --json -o rapport.json
    python -m hookymod.cli dicts /chemin/romfs
    python -m hookymod.cli list romfs/Pack/Some.pack.zs --romfs romfs
    python -m hookymod.cli extract romfs/Pack/Some.pack.zs -o sortie --decompress
    python -m hookymod.cli textures model.bntx --list
    python -m hookymod.cli textures model.bntx -o textures_png
    python -m hookymod.cli obj model.bfres -o model.obj
    python -m hookymod.cli patch asset.zs --find Link --replace Hero -o asset.patched.zs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import bfres as bfres_mod
from . import bntx as bntx_mod
from . import identify
from .container import Level, detect_kind
from .mesh import export_obj
from .sarc import SarcArchive, is_sarc
from .util import FormatError, HookyError, human_size, safe_child, write_atomic
from .zs import DictMissing, ZstdDicts, find_dict_files, frame_info, is_zstd, zstd_available


# --------------------------------------------------------------------------- utilitaires
def _auto_romfs(path: Path) -> Path | None:
    """Cherche un dossier romfs au-dessus du fichier (dumps organisés)."""
    current = path if path.is_dir() else path.parent
    for candidate in [current, *current.parents]:
        if (candidate / "Pack").is_dir() or (candidate / "System").is_dir():
            return candidate
        if candidate.name.lower() == "romfs":
            return candidate
    return None


def load_dicts(romfs_arg: str | None, path: Path | None, verbose: bool = False) -> ZstdDicts:
    dicts = ZstdDicts()
    if not zstd_available():
        print("⚠ module « zstandard » absent : impossible de lire les .zs "
              "(pip install zstandard)", file=sys.stderr)
        return dicts
    root = Path(romfs_arg) if romfs_arg else (_auto_romfs(path) if path else None)
    if root and root.exists():
        try:
            count, _ = dicts.load_romfs(root)
            if verbose:
                print(f"# dictionnaires chargés depuis {root} : {count}", file=sys.stderr)
        except HookyError as exc:
            print(f"⚠ {exc}", file=sys.stderr)
    if not dicts and verbose:
        print("# aucun dictionnaire trouvé (assets non compressés ?)", file=sys.stderr)
    return dicts


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise HookyError(f"Lecture impossible : {path} ({exc})") from exc


def open_level(path: Path, dicts: ZstdDicts, romfs: Path | None = None, depth: int = 0) -> Level:
    raw = _read(path)
    rel = path.name
    if romfs:
        try:
            rel = path.resolve().relative_to(romfs.resolve()).as_posix()
        except (ValueError, OSError):
            rel = path.name
    level = Level(name=path.name, raw=raw, dicts=dicts, rel=rel)
    return level


def _pick_entry(level: Level, name: str | None) -> bytes:
    if level.archive is not None:
        if not name:
            raise HookyError("Cette archive contient plusieurs fichiers : précise --entry.")
        entry = level.archive.get(name)
        if entry is None:
            matches = level.archive.find(name)
            if not matches:
                raise HookyError(f"Entrée « {name} » absente de l'archive.")
            if len(matches) > 1:
                raise HookyError("Plusieurs entrées correspondent : "
                                 + ", ".join(m.name for m in matches[:10]))
            entry = matches[0]
        return entry.data
    return level.data


def _decompress_maybe(data: bytes, dicts: ZstdDicts) -> bytes:
    return dicts.decompress(data) if is_zstd(data) else data


def _walk(level: Level, prefix: str = "", depth: int = 0, max_depth: int = 3):
    """Parcourt récursivement les archives/.zs et renvoie (nom, blob, profondeur)."""
    if depth > max_depth:
        return
    if level.archive is not None:
        for entry in level.archive.entries:
            yield prefix + entry.name, entry.data, depth
            try:
                if is_zstd(entry.data) or is_sarc(entry.data):
                    child = Level(entry.name, entry.data, level.dicts)
                    yield from _walk(child, prefix + entry.name + "/", depth + 1, max_depth)
            except HookyError:
                continue


# --------------------------------------------------------------------------- commandes
def cmd_info(args) -> int:
    path = Path(args.path)
    dicts = load_dicts(args.romfs, path, args.verbose)
    if path.is_dir():
        files, root = find_dict_files(path)
        print(f"Dossier : {path}")
        print(f"romfs détecté : {root}")
        print(f"Dictionnaires : {len(files)}")
        for file in files:
            try:
                n = ZstdDicts()
                count = n.load_file(file)
                print(f"  • {file} — {count} dictionnaire(s)")
            except HookyError as exc:
                print(f"  • {file} — illisible : {exc}")
        if root:
            try:
                entries = sorted(p.name for p in root.iterdir())
                print(f"Contenu racine : {', '.join(entries[:15])}"
                      + (" …" if len(entries) > 15 else ""))
            except OSError:
                pass
        return 0

    level = open_level(path, dicts, _auto_romfs(path))
    data = level.data
    print(f"Fichier : {path}")
    print(f"Taille  : {human_size(len(data))} ({len(data):,} octets)")
    print(f"Type    : {detect_kind(data)}")
    if level.compressed:
        print(f"zstd    : dictionnaire id {level.dict_id or '(aucun)'}")
    if level.archive is not None:
        print(f"Archive : {len(level.archive)} entrée(s)")
        for entry in level.archive.entries[:20]:
            print(f"   - {entry.name} — {human_size(len(entry.data))} — "
                  f"{detect_kind(entry.data)}")
        if len(level.archive) > 20:
            print(f"   … {len(level.archive) - 20} autres")
    if data[:4] == b"BNTX":
        try:
            print()
            print(bntx_mod.BntxFile.parse(data, path.name).info())
        except HookyError as exc:
            print(f"⚠ BNTX illisible : {exc}")
    if data[:4] == b"FRES":
        try:
            print()
            print(bfres_mod.BfresFile.parse(data).info())
        except HookyError as exc:
            print(f"⚠ BFRES illisible : {exc}")
    return 0


def cmd_list(args) -> int:
    path = Path(args.path)
    dicts = load_dicts(args.romfs, path, args.verbose)
    level = open_level(path, dicts, _auto_romfs(path))
    if level.archive is None:
        print(f"{path.name} n'est pas une archive ({detect_kind(level.data)}).")
        return 0
    pattern = (args.filter or "").lower()
    for name, blob, depth in _walk(level, max_depth=args.depth if args.recursive else 0):
        if pattern and pattern not in name.lower():
            continue
        print(f"[{depth}] {name:<60} {human_size(len(blob)):>10}  {detect_kind(blob)}")
    return 0


def cmd_extract(args) -> int:
    path = Path(args.path)
    dicts = load_dicts(args.romfs, path, args.verbose)
    level = open_level(path, dicts, _auto_romfs(path))
    out = Path(args.output)
    if level.archive is None:
        target = safe_child(out, path.name)
        if target is None:
            raise HookyError("Nom de fichier invalide.")
        write_atomic(target, level.data)
        print(f"Écrit : {target}")
        return 0
    pattern = (args.filter or "").lower()
    written = skipped = 0
    errors: list[str] = []
    for entry in level.archive.entries:
        if pattern and pattern not in entry.name.lower():
            continue
        name = entry.name
        data = entry.data
        if args.decompress and is_zstd(data):
            try:
                data = dicts.decompress(data)
                name = name[:-3] if name.lower().endswith(".zs") else name
            except HookyError as exc:
                errors.append(f"{name} : {exc}")
                skipped += 1
                continue
        target = safe_child(out, name)
        if target is None:
            errors.append(f"{name} : nom dangereux, ignoré")
            skipped += 1
            continue
        write_atomic(target, data)
        written += 1
    print(f"{written} fichier(s) extrait(s) vers {out}"
          + (f" · {skipped} ignoré(s)" if skipped else ""))
    for error in errors[:20]:
        print(f"  ⚠ {error}", file=sys.stderr)
    return 0 if written or not errors else 1


def cmd_replace(args) -> int:
    path = Path(args.path)
    dicts = load_dicts(args.romfs, path, args.verbose)
    level = open_level(path, dicts, _auto_romfs(path))
    replacement = _read(Path(args.file))
    if level.archive is None:
        level.data = replacement
        level.modified = True
    else:
        entry = level.archive.get(args.entry) if args.entry else None
        if entry is None:
            raise HookyError(f"Entrée « {args.entry} » introuvable.")
        original = entry.data
        if is_zstd(original) and not is_zstd(replacement):
            if args.no_compress:
                raise HookyError("L'entrée est compressée : utilise un .zs ou retire --no-compress.")
            dict_id = frame_info(original)[0]
            replacement = dicts.compress(replacement, dict_id, args.level)
            print(f"# recompressé (dict {dict_id}, niveau {args.level})")
        level.replace_entry(level.archive.index_of(entry), replacement)
    out = Path(args.output) if args.output else path.with_suffix(path.suffix + ".patched")
    write_atomic(out, level.pack())
    print(f"Écrit : {out}")
    return 0


def cmd_patch(args) -> int:
    path = Path(args.path)
    dicts = load_dicts(args.romfs, path, args.verbose)
    level = open_level(path, dicts, _auto_romfs(path))
    if args.hex:
        needle = bytes.fromhex(args.find.replace(" ", ""))
        replacement = bytes.fromhex(args.replace.replace(" ", ""))
    else:
        needle = args.find.encode()
        replacement = args.replace.encode()
    if not needle:
        raise HookyError("Motif vide.")
    if len(needle) != len(replacement):
        raise HookyError(f"Longueurs différentes ({len(needle)} ≠ {len(replacement)}). "
                         "Un patch sur place conserve la taille.")

    def patch_blob(blob: bytes, depth: int = 0) -> tuple[bytes, int]:
        total = 0
        if is_zstd(blob):
            try:
                plain = dicts.decompress(blob)
            except HookyError:
                return blob, 0
            patched, count = patch_blob(plain, depth)
            if not count:
                return blob, 0
            return dicts.compress(patched, frame_info(blob)[0], args.level), count
        if is_sarc(blob) and depth < 3:
            archive = SarcArchive.parse(blob)
            changed = False
            for entry in archive.entries:
                new_blob, count = patch_blob(entry.data, depth + 1)
                if count:
                    entry.data = new_blob
                    total += count
                    changed = True
            if changed:
                blob = archive.build()
        count = blob.count(needle)
        if count:
            blob = blob.replace(needle, replacement)
            total += count
        return blob, total

    total = 0
    if level.archive is not None:
        if args.entry:
            entry = level.archive.get(args.entry)
            if entry is None:
                raise HookyError(f"Entrée « {args.entry} » introuvable.")
            targets = [entry]
        else:
            targets = level.archive.entries
        for entry in targets:
            new_blob, count = patch_blob(entry.data)
            if count:
                entry.data = new_blob
                total += count
        if not total:
            # Les noms de fichiers de la SARC vivent dans l'en-tête : on patche
            # aussi les octets bruts de l'archive puis on la relit.
            count = level.data.count(needle)
            if count:
                patched = level.data.replace(needle, replacement)
                try:
                    archive = SarcArchive.parse(patched)
                except FormatError:
                    archive = None
                level.data = patched
                if archive is not None:
                    level.archive = archive
                total += count
        if total:
            level.modified = True
    else:
        new_data, count = patch_blob(level.data)
        if count:
            level.data = new_data
            level.modified = True
            total += count
    if not total:
        print("Aucune occurrence trouvée.")
        return 1
    out = Path(args.output) if args.output else path.with_suffix(path.suffix + ".patched")
    write_atomic(out, level.pack())
    print(f"{total} occurrence(s) remplacée(s) — écrit : {out}")
    return 0


def cmd_textures(args) -> int:
    path = Path(args.path)
    dicts = load_dicts(args.romfs, path, args.verbose)
    level = open_level(path, dicts, _auto_romfs(path))
    data = _decompress_maybe(_pick_entry(level, args.entry), dicts)
    if data[:4] != b"BNTX":
        raise HookyError(f"Ce n'est pas un BNTX ({detect_kind(data)}).")
    file = bntx_mod.BntxFile.parse(data, path.name)
    if args.list or not args.output:
        print(file.info())
        if not args.list:
            print("\nAstuce : -o DOSSIER pour exporter les PNG.")
        return 0
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    pattern = (args.filter or "").lower()
    written = 0
    for texture in file.textures:
        if pattern and pattern not in (texture.name or "").lower():
            continue
        name = (texture.name or f"tex{texture.index}").replace("/", "_")
        target = out / f"{name}.png"
        try:
            texture.save_png(target, args.mip)
            written += 1
            print(f"  • {target}")
        except HookyError as exc:
            print(f"  ⚠ {texture.name or texture.index} : {exc}", file=sys.stderr)
    print(f"{written} texture(s) exportée(s) vers {out}")
    return 0 if written else 1


def cmd_obj(args) -> int:
    path = Path(args.path)
    dicts = load_dicts(args.romfs, path, args.verbose)
    level = open_level(path, dicts, _auto_romfs(path))
    data = _decompress_maybe(_pick_entry(level, args.entry), dicts)
    if data[:4] != b"FRES":
        raise HookyError(f"Ce n'est pas un BFRES ({detect_kind(data)}).")
    file = bfres_mod.BfresFile.parse(data)
    meshes = []
    for model in file.models[args.model:args.model + 1]:
        meshes += model.build_meshes(args.lod, file.warnings)
    if not meshes:
        raise HookyError("Aucun maillage exploitable.\n" + "\n".join(file.warnings[:10]))
    out = Path(args.output) if args.output else path.with_suffix(".obj")
    export_obj(meshes, out, scale=args.scale)
    print(f"Écrit : {out} ({sum(len(m.faces) for m in meshes):,} triangles)")
    return 0


def cmd_dicts(args) -> int:
    root = Path(args.path)
    if not root.is_dir():
        raise HookyError(f"{root} n'est pas un dossier.")
    files, romfs = find_dict_files(root)
    print(f"romfs : {romfs}")
    if not files:
        print("Aucun dictionnaire trouvé (ZsDic.pack.zs).")
        return 1
    dicts = ZstdDicts()
    for file in files:
        try:
            count = dicts.load_file(file)
            print(f"  • {file.name} — {count} dictionnaire(s)")
        except HookyError as exc:
            print(f"  • {file.name} — illisible : {exc}")
    print(f"Total : {len(dicts)} dictionnaire(s)")
    for description in dicts.describe():
        print(f"   - {description}")
    return 0 if dicts else 1


def cmd_carve(args) -> int:
    path = Path(args.path)
    data = _read(path)
    data = data[:args.bytes] if args.bytes else data
    found = identify.carve(data, limit=args.limit)
    if not found:
        print(f"Aucune structure connue dans les {human_size(len(data))} analysés.")
        print("→ lance « scan » sur le dossier pour identifier le format global.")
        return 1
    print(f"{path.name} — {human_size(len(data))} analysés, "
          f"{len(found)} structure(s) :")
    for item in found:
        print(f"  0x{item.offset:08X}  {item.label}")
    return 0


def cmd_scan(args) -> int:
    root = Path(args.path)
    report = identify.scan(root, samples=max(1, args.samples), hexdump_bytes=args.hexdump,
                           strings=args.strings, max_depth=args.depth or None,
                           limit=args.limit)
    text = (identify.report_to_json(report) if args.json
            else identify.format_report(report))
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Rapport écrit : {out} ({human_size(len(text.encode('utf-8')))})")
    else:
        print(text)
    return 0


# --------------------------------------------------------------------------- parsing
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hookymod",
        description="HookyMod — explorer / modifier les assets romfs (SARC, zstd, BFRES, BNTX).")
    parser.add_argument("--version", action="version", version="HookyMod 4.0.0")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", "-v", action="store_true", help="messages détaillés")
    common.add_argument("--romfs", help="dossier romfs (pour charger les dictionnaires zstd)")
    common.add_argument("--level", type=int, default=16,
                        help="niveau de compression zstd (défaut 16)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", parents=[common],
                       help="informations sur un fichier ou un dossier romfs")
    p.add_argument("path")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("list", parents=[common], help="liste le contenu d'une archive")
    p.add_argument("path")
    p.add_argument("--filter", "-f", help="filtre sur le nom")
    p.add_argument("--recursive", "-r", action="store_true",
                   help="descend dans les .zs / archives imbriquées")
    p.add_argument("--depth", type=int, default=3)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("extract", parents=[common], help="extrait une archive (ou un fichier)")
    p.add_argument("path")
    p.add_argument("--output", "-o", default="extract", help="dossier de destination")
    p.add_argument("--filter", "-f")
    p.add_argument("--decompress", "-d", action="store_true", help="décompresse les .zs extraits")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("replace", parents=[common], help="remplace un fichier d'une archive")
    p.add_argument("path")
    p.add_argument("--entry", "-e", required=True, help="nom de l'entrée à remplacer")
    p.add_argument("--file", "-i", required=True, help="fichier de remplacement")
    p.add_argument("--output", "-o", help="fichier de sortie")
    p.add_argument("--no-compress", action="store_true",
                   help="ne pas recompresser si l'entrée d'origine est un .zs")
    p.set_defaults(func=cmd_replace)

    p = sub.add_parser("patch", parents=[common], help="chercher/remplacer des octets ou du texte")
    p.add_argument("path")
    p.add_argument("--find", required=True)
    p.add_argument("--replace", required=True)
    p.add_argument("--hex", action="store_true", help="motifs hexadécimaux")
    p.add_argument("--entry", "-e", help="limiter à une entrée")
    p.add_argument("--output", "-o")
    p.set_defaults(func=cmd_patch)

    p = sub.add_parser("textures", parents=[common], help="liste / exporte les textures d'un BNTX")
    p.add_argument("path")
    p.add_argument("--entry", "-e", help="entrée d'une archive")
    p.add_argument("--output", "-o", help="dossier d'export PNG")
    p.add_argument("--list", "-l", action="store_true", help="liste seulement")
    p.add_argument("--filter", "-f")
    p.add_argument("--mip", type=int, default=0)
    p.set_defaults(func=cmd_textures)

    p = sub.add_parser("obj", parents=[common], help="exporte un BFRES en OBJ")
    p.add_argument("path")
    p.add_argument("--entry", "-e")
    p.add_argument("--output", "-o")
    p.add_argument("--model", type=int, default=0)
    p.add_argument("--lod", type=int, default=0)
    p.add_argument("--scale", type=float, default=1.0)
    p.set_defaults(func=cmd_obj)

    p = sub.add_parser("scan", parents=[common],
                       help="identifie les formats d'un dossier (ou d'un fichier) et le moteur probable")
    p.add_argument("path")
    p.add_argument("--samples", type=int, default=3,
                   help="fichiers analysés par extension (défaut 3)")
    p.add_argument("--hexdump", type=int, default=0,
                   help="affiche les N premiers octets des échantillons (défaut 0)")
    p.add_argument("--strings", type=int, default=0,
                   help="affiche N chaînes ASCII des échantillons (noms internes, chemins)")
    p.add_argument("--depth", type=int, default=0, help="profondeur max (0 = illimitée)")
    p.add_argument("--limit", type=int, default=200000, help="nombre max de fichiers lus")
    p.add_argument("--json", action="store_true", help="sortie JSON (pour partage)")
    p.add_argument("--output", "-o", help="écrit le rapport dans un fichier")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("carve", parents=[common],
                       help="cherche des structures connues à l'intérieur d'un fichier")
    p.add_argument("path")
    p.add_argument("--bytes", type=int, default=4 * 1024 * 1024,
                   help="octets analysés (défaut 4 Mio, 0 = tout)")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_carve)

    p = sub.add_parser("dicts", parents=[common], help="analyse les dictionnaires zstd d'un romfs")
    p.add_argument("path")
    p.set_defaults(func=cmd_dicts)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except (HookyError, FormatError, DictMissing) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrompu.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        if getattr(args, "verbose", False):
            raise
        print(f"Erreur inattendue : {exc}\n(relance avec --verbose pour la trace complète)",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
