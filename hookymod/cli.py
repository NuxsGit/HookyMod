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
    python -m hookymod.cli export-model model.bfres -o model_export
    python -m hookymod.cli patch asset.zs --find Original --replace Example -o asset.patched.zs
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from . import bfres as bfres_mod
from . import bntx as bntx_mod
from . import blender as blender_mod
from . import identify
from . import modelio
from . import nup as nup_mod
from . import restbl as restbl_mod
from . import texturemaps
from . import translate as translate_mod
from .container import Level, detect_kind
from .mesh import export_obj
from .sarc import SarcArchive, is_sarc
from .util import FormatError, HookyError, human_size, safe_child, safe_filename_stem, write_atomic
from .zs import (DictMissing, ZstdDicts, find_dict_files, find_dictionaries_near,
                 frame_info, is_zstd, zstd_available)


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
    if not dicts and path is not None:
        for candidate in find_dictionaries_near(path):
            try:
                count = dicts.load_file(candidate)
                if verbose:
                    print(f"# dictionnaires chargés depuis {candidate} : {count}",
                          file=sys.stderr)
            except (HookyError, OSError) as exc:
                print(f"⚠ {candidate.name} : {exc}", file=sys.stderr)
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


def cmd_doctor(args) -> int:
    """Environnement d'exécution (et anatomie d'un fichier si ``--path``)."""
    from . import diagnose
    print(diagnose.environment_report(args.romfs))
    if getattr(args, "path", None):
        path = Path(args.path)
        dicts = load_dicts(args.romfs, path, args.verbose)
        level = open_level(path, dicts, _auto_romfs(path))
        data = _decompress_maybe(level.data, dicts)
        print()
        print(diagnose.file_report(data, path.name,
                                   orin=None if args.orin is None else args.orin))
    return 0


def cmd_textures(args) -> int:
    path = Path(args.path)
    dicts = load_dicts(args.romfs, path, args.verbose)
    level = open_level(path, dicts, _auto_romfs(path))
    data = _decompress_maybe(_pick_entry(level, args.entry), dicts)
    if data[:4] != b"BNTX":
        raise HookyError(f"Ce n'est pas un BNTX ({detect_kind(data)}).")
    file = bntx_mod.BntxFile.parse(data, path.name,
                                   orin=None if args.orin is None else args.orin)
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


def _bundle_stem(path: Path) -> str:
    """Nom lisible d'un asset : ``A.B.bfres.zs`` → ``A``, ``Tex.bntx.zs`` → ``Tex``."""
    name = path.name
    for suffix in (".zs", ".bfres", ".bntx", ".obj", ".gltf", ".glb"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    first = name.split(".")[0]
    return first or name or "modele"


def _neighbour_textures(model_path: Path, stem: str, dicts: ZstdDicts,
                        out: Path, verbose: bool = False,
                        warnings: list[str] | None = None) -> dict[str, str]:
    """Prépare des PNG candidats voisins ; leur association reste heuristique."""
    folder = model_path.parent
    found: dict[str, str] = {}
    # Un candidat doit contenir tous les mots spécifiques du nom du modèle.
    key = stem.split(".")[0]
    generic = {"animal", "mt", "body", "model", "obj", "tex"}
    tokens = {t.lower() for t in key.replace("-", "_").split("_") if t and t.lower() not in generic}
    if not tokens:
        return found
    candidates = []
    for pattern in ("*.bntx.zs", "*.bntx", "*.png"):
        candidates += sorted(folder.glob(pattern))
    planned = []
    for candidate in candidates:
        words = {t.lower() for t in candidate.name.split(".")[0].split("_")}
        if tokens <= words:
            name = safe_filename_stem(candidate.name.split(".")[0]) + ".png"
            planned.append((candidate, name))
    counts = Counter(name.casefold() for _, name in planned)
    ambiguous = set()

    def note(message: str) -> None:
        if warnings is not None:
            warnings.append(message)
        elif verbose:
            print(f"  ⚠ {message}", file=sys.stderr)

    for candidate, name in planned:
        if counts[name.casefold()] > 1:
            if name.casefold() not in ambiguous:
                note(f"Plusieurs textures voisines produiraient {name} : candidates ignorées.")
                ambiguous.add(name.casefold())
            continue
        target = out / name
        try:
            if candidate.suffix.lower() == ".png":
                # Une copie atomique remplace un éventuel lien, sans suivre sa
                # cible. Le plafond évite de charger un PNG arbitraire sans borne.
                if candidate.stat().st_size > 64 * 1024 * 1024:
                    raise HookyError("PNG supérieur à 64 Mio")
                if candidate.resolve() != target.resolve():
                    with candidate.open("rb") as stream:
                        png = stream.read(64 * 1024 * 1024 + 1)
                    if len(png) > 64 * 1024 * 1024:
                        raise HookyError("PNG supérieur à 64 Mio")
                    write_atomic(target, png)
            else:
                raw = candidate.read_bytes()
                data = dicts.decompress(raw) if is_zstd(raw) else raw
                file = bntx_mod.BntxFile.parse(data, candidate.name)
                if len(file.textures) != 1:
                    raise HookyError("conteneur sans image unique ; utiliser l'atelier pour choisir l'index")
                texture = file.textures[0]
                texture.save_png(target, 0)
                if verbose:
                    print(f"  texture {candidate.name} : {texture.format_name} "
                          f"{texture.width}×{texture.height}")
            found[candidate.name] = str(target)
        except (HookyError, IndexError, ValueError, OSError) as exc:
            note(f"Texture {candidate.name} ignorée : {exc}")
    return found


def cmd_blender(args) -> int:
    path = Path(args.path)
    stem = _bundle_stem(path)
    out = Path(args.output) if args.output else path.parent / f"{stem}_export"
    formats = tuple(part.strip().lower() for part in args.formats.split(",") if part.strip())
    if not formats or not set(formats) <= {"gltf", "glb", "obj"}:
        raise HookyError("Formats d'export attendus : gltf, glb et/ou obj.")
    if not math.isfinite(args.scale) or args.scale <= 0 or args.model < 0 or args.lod < 0:
        raise HookyError("L'échelle doit être positive et finie ; modèle et LOD doivent être positifs ou nuls.")
    if (path.suffix.lower() == ".obj" and "obj" in formats
            and (out / f"{stem}.obj").resolve() == path.resolve()):
        raise HookyError("Le dossier de sortie écraserait l'OBJ source ; choisis un autre dossier.")
    dicts = load_dicts(args.romfs, path, args.verbose)
    warnings: list[str] = []

    if path.suffix.lower() == ".obj":
        # L'ancien lecteur d'aperçu ignore les indices UV/normales par coin et
        # synthétise des normales. Utiliser le lecteur d'échange borné pour ne
        # pas attribuer aux sommets des données qui n'étaient pas les leurs.
        with path.open("rb") as stream:
            source = modelio.read_model(path.name, stream.read(modelio.MAX_BYTES + 1))
        meshes = source.meshes
        warnings.extend(source.warnings)
        warnings.append("OBJ : bibliothèques MTL et textures source non importées ; "
                        "seuls les noms des matériaux sont conservés.")
    else:
        level = open_level(path, dicts, _auto_romfs(path))
        data = _decompress_maybe(_pick_entry(level, args.entry), dicts)
        if data[:4] != b"FRES":
            raise HookyError(f"Ce n'est pas un BFRES ({detect_kind(data)}).")
        file = bfres_mod.BfresFile.parse(data)
        meshes = []
        for model in file.models[args.model:args.model + 1]:
            meshes += model.build_meshes(args.lod, file.warnings)
        if not meshes:
            detail = "\n".join(f"  ⚠ {w}" for w in file.warnings[:8])
            raise HookyError(
                "Aucun maillage exploitable dans ce BFRES.\n"
                + (detail or "  (aucun avertissement : structure non reconnue)")
                + "\nRien n'est exporté plutôt que d'envoyer une géométrie fausse "
                  "vers Blender."
            )
        warnings.extend(file.warnings)

    out.mkdir(parents=True, exist_ok=True)
    textures = {}
    if args.textures:
        textures = _neighbour_textures(path, stem, dicts, out, args.verbose, warnings)
        if not textures:
            warnings.append("Aucune texture reconnue à côté du modèle : export sans image.")
    else:
        warnings.append("Export des textures désactivé.")
    materials = {mesh.material or mesh.name for mesh in meshes}
    unique_images = {str(source) for source in textures.values()}
    albedos = {str(source) for name, source in textures.items()
               if texturemaps.classify(name) == "albedo"}
    material_maps = {}
    if len(materials) == 1 and len(albedos) == 1:
        material_maps[next(iter(materials))] = {"albedo": Path(next(iter(albedos))).name}
        warnings.append("Association d'albédo par nom : heuristique, à vérifier.")
    elif textures and not albedos:
        warnings.append("Aucun albédo identifié par son nom : PNG à relier manuellement.")
    elif textures:
        warnings.append("Plusieurs matériaux ou albédos candidats : PNG à relier manuellement.")
    if unique_images - albedos:
        warnings.append("Les autres cartes candidates ne sont pas reliées automatiquement par la CLI.")
    written = blender_mod.export_bundle(
        meshes, out, stem, textures=textures, material_maps=material_maps, scale=args.scale,
        formats=formats, warnings=warnings,
    )
    triangles = sum(len(m.faces) for m in meshes)
    print(f"Modèle : {stem} — {len(meshes)} maillage(s), {triangles:,} face(s)")
    for key in ("glb", "gltf", "obj", "mtl", "note", "report"):
        if key in written:
            print(f"  • {written[key]}")
    if textures:
        print(f"  textures : {len(unique_images)} PNG candidat(s)")
    for warning in warnings[:20]:
        print(f"  ⚠ {warning}")
    target = written.get("gltf") or written.get("glb") or written.get("obj")
    kind = "glTF 2.0" if "gltf" in written or "glb" in written else "Wavefront OBJ"
    print(f"\nBlender : Fichier ▸ Importer ▸ {kind} → {target}")
    return 0


def _parse_size(text: str) -> tuple[int, int]:
    """``"960x720"`` (ou ``"960X720"``, ``"960,720"``) → ``(960, 720)`` bornés."""
    raw = str(text).lower().replace("×", "x").replace(",", "x").replace(" ", "")
    parts = raw.split("x")
    try:
        if len(parts) != 2:
            raise ValueError("deux dimensions attendues")
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        raise HookyError(f"Taille invalide : « {text} » (attendu 960x720).") from None
    return max(64, min(4096, width)), max(64, min(4096, height))


def cmd_view(args) -> int:
    """Rend un modèle en PNG — affichage possible **sans** interface graphique."""
    from . import render as render_mod
    from .mesh import load_obj
    path = Path(args.path)
    dicts = load_dicts(args.romfs, path, args.verbose)
    level = open_level(path, dicts, _auto_romfs(path))
    data = _decompress_maybe(_pick_entry(level, args.entry), dicts)

    if data[:4] == b"FRES":
        file = bfres_mod.BfresFile.parse(data)
        meshes = bfres_mod.meshes_from_file(file, lod=args.lod, budget=args.faces)
        kind = f"BFRES v{file.version_major}"
    elif data[:5] == b"# OBJ" or data.lstrip()[:1] in (b"v", b"#"):
        meshes = [load_obj(data)]
        kind = "OBJ"
    else:
        raise HookyError(f"Ce n'est pas un modèle affichable ({detect_kind(data)}).")

    width, height = _parse_size(args.size)
    if args.views > 1:
        png = render_mod.turntable_png(meshes, views=args.views, width=width,
                                       height=height, pitch=args.pitch,
                                       zoom=args.zoom)
    else:
        png = render_mod.render_png(meshes, width, height, yaw=args.yaw,
                                    pitch=args.pitch, zoom=args.zoom,
                                    max_faces=args.faces,
                                    supersample=args.supersample)
    out = Path(args.output) if args.output else path.with_suffix(".png")
    write_atomic(out, png)
    faces = sum(len(mesh.faces) for mesh in meshes)
    print(f"{kind} — {len(meshes)} maillage(s), {faces:,} triangles")
    print(f"Écrit : {out} ({width}x{height})")
    if args.views == 1:
        print("Vues : --yaw / --pitch, planche de rotations : --views 4")
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


def _parse_sizes(values: list[str] | None) -> dict[str, int]:
    pairs: dict[str, int] = {}
    for item in values or []:
        path, _, size = item.rpartition("=")
        if not path or not size.strip().isdigit():
            raise HookyError(f"Format attendu CHEMIN=TAILLE, reçu « {item} ».")
        pairs[path.replace("\\", "/")] = int(size)
    return pairs


def cmd_translate(args) -> int:
    if args.action in ("unpack", "pack", "status") and not args.project:
        raise HookyError("précise --project (dossier du projet).")
    if args.action == "unpack":
        manifest = translate_mod.unpack(args.romfs, args.project, only=args.filter,
                                        limit=args.limit,
                                        progress=(lambda done, total: print(
                                            f"\r# {done}/{total}", end="",
                                            file=sys.stderr)) if args.verbose else None)
        if args.verbose:
            print(file=sys.stderr)
        kinds: dict[str, int] = {}
        for entry in manifest.entries:
            kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
        print(f"Projet créé : {args.project}")
        print(f"Entrées : {len(manifest.entries)} ("
              + ", ".join(f"{kind} ×{count}" for kind, count in sorted(kinds.items()))
              + ")")
        print(f"À éditer : {Path(args.project) / 'pc'}")
        if manifest.errors:
            print(f"⚠ {len(manifest.errors)} avertissement(s) :")
            for error in manifest.errors[:10]:
                print(f"  • {error}")
        return 0
    if args.action == "status":
        for line in translate_mod.status(args.project):
            print(line)
        return 0
    if args.action == "pack":
        result = translate_mod.pack(args.project, args.output,
                                    all_files=args.all, mod_name=args.mod_name or "",
                                    update_table=not args.no_table)
        for line in result.summary():
            print(line)
        root = Path(args.output) / (args.mod_name or "") / "romfs"
        if result.written:
            print(f"Fichiers écrits sous : {root}")
        return 0 if not result.errors else 1
    raise HookyError(f"action inconnue : {args.action}")


def cmd_restbl(args) -> int:
    target = Path(args.path)
    dicts = load_dicts(args.romfs, target, args.verbose)
    if target.is_dir():
        table, table_path, raw = restbl_mod.load_table(target, dicts)
        source = table_path
    else:
        table_path = target
        raw = target.read_bytes()
        if target.suffix.lower() == ".zs":
            from .zs import DictMissing
            try:
                data = dicts.decompress(raw)
            except DictMissing as exc:
                raise HookyError(f"{exc}\nOuvre le romfs (--romfs) pour charger "
                                 "ZsDic.pack.zs.") from exc
        else:
            data = raw
        table = restbl_mod.ResourceSizeTable.parse(data)
        source = table_path

    for line in table.summary():
        print(line)
    print(f"Source : {source}")

    changed = False
    for path, size in _parse_sizes(args.set_sizes).items():
        table.set(path, size)
        print(f"  fixé : {path} = {human_size(size)}")
        changed = True
    for path, size in _parse_sizes(args.ensure_sizes).items():
        if table.ensure(path, size):
            print(f"  agrandi : {path} → {human_size(table.get(path) or 0)}")
            changed = True
        else:
            print(f"  déjà suffisant : {path} ({human_size(table.get(path) or 0)})")

    if args.list:
        for key, size in sorted(table.sizes.items(), key=lambda kv: -kv[1])[:args.limit]:
            print(f"  0x{key:08X}  {human_size(size):>10}")
        if len(table) > args.limit:
            print(f"  … {len(table) - args.limit} entrée(s) de plus")

    for path in args.get or []:
        size = table.get(path)
        if size is None:
            print(f"Entrée absente de la table : {path}", file=sys.stderr)
            return 1
        print(f"{path} : {size} octets ({human_size(size)})")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".zs" and len(dicts):
            from .zs import frame_info
            wanted = args.dictionary_id
            if wanted is None:
                try:
                    wanted, _ = frame_info(raw) if raw[:4] == b"\x28\xb5\x2f\xfd" else (None, None)
                except FormatError:
                    wanted = None
            if wanted is None:
                wanted = next(iter(dicts.by_id))
            data = dicts.compress(table.build(), wanted, args.level)
        else:
            data = table.build()
        write_atomic(out, data)
        print(f"Table écrite : {out} ({human_size(len(data))})")
    elif changed:
        print("(utilise -o pour écrire la table modifiée)")
    return 0


def cmd_nup(args) -> int:
    package = nup_mod.NupPackage.open(Path(args.path))
    with package:
        if args.verify:
            problems = package.verify(args.title if args.title is not None else None,
                                      deep=args.deep)
            if problems:
                print(f"{len(problems)} anomalie(s) :")
                for problem in problems:
                    print(f"  • {problem}")
                return 1
            if args.deep:
                print("Aucune anomalie détectée (TOC, bornes, SHA-256 présents et "
                      "en-têtes .zs). Les empreintes absentes ne sont pas vérifiées.")
            else:
                print("Aucune anomalie détectée (TOC et bornes ; contenus non vérifiés).")
            if not args.extract and not args.list:
                return 0
        if args.list or not args.extract:
            for line in package.summary():
                print(line)
            for title in ([package.title(args.title)] if args.title is not None
                          else package.titles):
                print()
                print(f"Titre #{title.index} {title.label}")
                for rel, entry in package.members(title.index, args.only):
                    print(f"  {rel:<58} {human_size(entry.size):>10}  {entry.codec_name}")
            if not args.extract:
                return 0

        def progress(done: int, total: int) -> None:
            if args.verbose:
                print(f"\r# {done}/{total}", end="", file=sys.stderr)

        print()
        print(f"Extraction vers {args.extract}…")
        result = package.extract(args.extract, title=args.title, only=args.only,
                                 progress=progress if args.verbose else None)
        if args.verbose:
            print(file=sys.stderr)
        print(f"{result.count} fichier(s) extrait(s) — "
              f"{human_size(result.total_bytes)}")
        if result.heads:
            print(f"⚠ {len(result.heads)} .zs dont l'en-tête n'est pas zstd")
        for problem in result.problems[:20]:
            print(f"  • {problem}", file=sys.stderr)
        return 0 if not result.problems else 1


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
    parser.add_argument("--version", action="version", version=f"HookyMod {__version__}")

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
    swizzle = p.add_mutually_exclusive_group()
    swizzle.add_argument("--orin", dest="orin", action="store_true", default=None,
                         help="force la disposition Orin (Tegra T239)")
    swizzle.add_argument("--x1", dest="orin", action="store_false",
                         help="force la disposition Tegra X1")
    p.set_defaults(func=cmd_textures)

    p = sub.add_parser("view", parents=[common],
                       help="rend un modèle en PNG (aucune fenêtre requise)")
    p.add_argument("path")
    p.add_argument("--entry", "-e")
    p.add_argument("--output", "-o")
    p.add_argument("--size", default="960x720",
                   help="largeur x hauteur (défaut 960x720)")
    p.add_argument("--yaw", type=float, default=0.6)
    p.add_argument("--pitch", type=float, default=-0.3)
    p.add_argument("--zoom", type=float, default=1.0)
    p.add_argument("--lod", type=int, default=0)
    p.add_argument("--views", type=int, default=1,
                   help="planche de N rotations (0 à 360°)")
    p.add_argument("--faces", type=int, default=None, help="budget de faces")
    p.add_argument("--supersample", type=int, default=2,
                   help="anti-aliasing 1-4 (défaut 2)")
    p.set_defaults(func=cmd_view)

    p = sub.add_parser("obj", parents=[common], help="exporte un BFRES en OBJ")
    p.add_argument("path")
    p.add_argument("--entry", "-e")
    p.add_argument("--output", "-o")
    p.add_argument("--model", type=int, default=0)
    p.add_argument("--lod", type=int, default=0)
    p.add_argument("--scale", type=float, default=1.0)
    p.set_defaults(func=cmd_obj)

    p = sub.add_parser("export-model", aliases=["blender"], parents=[common],
                       help="exporte la géométrie en GLB / glTF / OBJ et les PNG candidats")
    p.add_argument("path")
    p.add_argument("--entry", "-e")
    p.add_argument("--output", "-o", help="dossier de sortie (défaut : <nom>_export)")
    p.add_argument("--model", type=int, default=0)
    p.add_argument("--lod", type=int, default=0)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--formats", default="gltf,obj",
                   help="formats statiques à écrire (glb,gltf,obj — défaut gltf,obj)")
    p.add_argument("--textures", action="store_true", default=True,
                   help="prépare les PNG voisins ; albédo unique relié de façon heuristique (défaut)")
    p.add_argument("--no-textures", dest="textures", action="store_false")
    p.set_defaults(func=cmd_blender)

    p = sub.add_parser("nup", parents=[common],
                       help="lit un paquet .nup déjà lisible : liste / extraction")
    p.add_argument("path")
    p.add_argument("--list", "-l", action="store_true", help="liste le contenu (défaut)")
    p.add_argument("--extract", "-x", help="dossier de destination")
    p.add_argument("--title", help="titre (index, identifiant ou version)")
    p.add_argument("--only", choices=["exefs", "romfs"], help="n'extraire qu'une partie")
    p.add_argument("--verify", action="store_true", help="contrôle d'intégrité")
    p.add_argument("--deep", action="store_true",
                   help="avec --verify : décompresse et compare les SHA-256")
    p.set_defaults(func=cmd_nup)

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

    p = sub.add_parser("translate", parents=[common],
                       help="traduit un romfs en projet PC éditable (PNG/OBJ) et retour")
    p.add_argument("action", choices=["unpack", "pack", "status"])
    p.add_argument("--project", "-p", help="dossier du projet (unpack/pack/status)")
    p.add_argument("--output", "-o", help="dossier de sortie (pack)")
    p.add_argument("--all", action="store_true",
                   help="pack : réécrire tous les fichiers, pas seulement les modifiés")
    p.add_argument("--mod-name", help="pack : écrire dans <sortie>/<nom>/romfs")
    p.add_argument("--no-table", action="store_true",
                   help="pack : ne pas mettre à jour la table des tailles")
    p.add_argument("--filter", "-f", help="unpack : filtre sur le chemin")
    p.add_argument("--limit", type=int, help="unpack : nombre maximum de fichiers")
    p.set_defaults(func=cmd_translate)

    p = sub.add_parser("restbl", parents=[common],
                       help="lit / agrandit la table des tailles de ressources (RESTBL)")
    p.add_argument("path", help="romfs, ou fichier .rsizetable[.zs]")
    p.add_argument("--list", "-l", action="store_true", help="liste les entrées")
    p.add_argument("--limit", type=int, default=40, help="entrées affichées (défaut 40)")
    p.add_argument("--get", action="append", metavar="CHEMIN",
                   help="affiche la taille réservée (répétable)")
    p.add_argument("--set", dest="set_sizes", action="append", metavar="CHEMIN=TAILLE",
                   help="fixe une entrée (répétable)")
    p.add_argument("--ensure", dest="ensure_sizes", action="append",
                   metavar="CHEMIN=TAILLE",
                   help="agrandit l'entrée si nécessaire (répétable)")
    p.add_argument("--dictionary-id", type=int,
                   help="identifiant du dictionnaire zstd pour une sortie .zs")
    p.add_argument("--output", "-o",
                   help="écrit la table (suffixe .zs pour recompresser)")
    p.set_defaults(func=cmd_restbl)

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

    p = sub.add_parser("doctor", parents=[common],
                       help="diagnostic : modules, Tk, formats, et un fichier si donné")
    p.add_argument("path", nargs="?")
    swizzle = p.add_mutually_exclusive_group()
    swizzle.add_argument("--orin", dest="orin", action="store_true", default=None)
    swizzle.add_argument("--x1", dest="orin", action="store_false")
    p.set_defaults(func=cmd_doctor)
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
