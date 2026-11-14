"""Couverture d'une famille de modèles : ce qui est traduit, ce qui est refusé.

Une famille de modèles arrive par lot (les ennemis, puis les boss, les PNJ…).
Avant de dire « le traducteur tient », il faut une preuve par fichier :

* combien de sous-maillages et de faces sortent du fichier ;
* ce que le traducteur a **refusé** et pourquoi ;
* un rendu PNG de chaque modèle, pour regarder au lieu de croire un compteur.

Ce module produit exactement cela : un rapport Markdown + JSON et un PNG par
modèle. Rien n'est inventé : un fichier qui ne donne pas de géométrie est
marqué ``refuse`` avec le message du traducteur, jamais « ok ».

Utilisation ::

    python -m tools.coverage /chemin/vers/Model -o previews/ennemis
    python -m tools.coverage fichier.bfres.zs --sizes 320x240
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:               # exécution directe : python tools/coverage.py
    sys.path.insert(0, str(ROOT))

from hookymod import bfres as bfres_mod            # noqa: E402
from hookymod import render                        # noqa: E402
from hookymod.util import HookyError               # noqa: E402
from hookymod.zs import ZstdDicts                  # noqa: E402
from hookycreator.bulk import _classify            # noqa: E402

MODEL_SUFFIXES = (".bfres", ".bfres.zs")


# --------------------------------------------------------------------------- entrées
def find_models(paths: list[Path]) -> list[Path]:
    """Liste les fichiers de modèle (BFRES, éventuellement compressés zstd)."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file() and str(candidate).lower().endswith(MODEL_SUFFIXES):
                    found.append(candidate)
        elif path.is_file():
            found.append(path)
    # dédoublonne en gardant l'ordre (un dossier + un fichier peuvent se recouper)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _dictionaries(folder: Path | None) -> ZstdDicts | None:
    """Dictionnaires zstd du jeu, si un ``ZsDic.pack.zs`` traîne à côté."""
    if folder is None:
        return None
    for name in ("ZsDic.pack.zs", "ZsDic.zs"):
        candidate = folder / name
        if candidate.is_file():
            dictionaries = ZstdDicts()
            try:
                dictionaries.load_file(candidate)
            except HookyError:
                return None
            return dictionaries if dictionaries else None   # fichier vide ou illisible
    return None


def read_bytes(path: Path, dictionaries: ZstdDicts | None) -> bytes:
    """Contenu décompressé d'un fichier, dictionnaires du jeu si disponibles."""
    raw = path.read_bytes()
    if not raw[:4] == b"\x28\xb5\x2f\xfd":       # pas une frame zstd
        return raw
    if dictionaries is not None:
        try:
            return dictionaries.decompress(raw)
        except HookyError:
            pass
    import zstandard as zstd

    return zstd.ZstdDecompressor().decompressobj().decompress(raw)


# --------------------------------------------------------------------------- mesure
def inspect(path: Path, dictionaries: ZstdDicts | None) -> dict:
    """Traduit un fichier et résume honnêtement ce qui en sort."""
    entry: dict = {"file": path.name, "size": path.stat().st_size, "status": "error",
                   "meshes": 0, "faces": 0, "vertices": 0, "version": "",
                   "bounds": None, "warnings": [], "reason": "", "seconds": 0.0}
    started = time.time()
    try:
        data = read_bytes(path, dictionaries)
    except Exception as error:                   # décompression : pire cas, on le dit
        entry["reason"] = f"lecture impossible : {error}"
        return entry
    entry["kind"] = _classify(data, path.name)
    if data[:4] != b"FRES":
        entry["reason"] = "ce n'est pas un BFRES"
        return entry
    if entry["kind"] == "animation":
        entry["status"] = "animation"
        entry["reason"] = "BFRES d'animation : pas de maillage à traduire"
        return entry
    try:
        file = bfres_mod.BfresFile.parse(data)
        entry["version"] = f"{file.version >> 16}.{(file.version >> 8) & 0xFF}.{file.version & 0xFF}"
        meshes = bfres_mod.meshes_from_file(file)
    except HookyError as error:
        entry["status"] = "refuse"
        entry["reason"] = str(error)
        entry["seconds"] = round(time.time() - started, 2)
        return entry
    except Exception as error:                   # bug du traducteur : on ne masque pas
        entry["reason"] = f"{type(error).__name__} : {error}"
        entry["seconds"] = round(time.time() - started, 2)
        return entry
    facing = sum(len(mesh.faces) for mesh in meshes)
    entry.update(meshes=len(meshes), faces=facing,
                 vertices=sum(len(mesh.positions) for mesh in meshes))
    entry["warnings"] = list(file.warnings[:8])
    if meshes:
        lo, hi = meshes[0].bounds()
        entry["bounds"] = [round(hi[i] - lo[i], 3) for i in range(3)]
    entry["status"] = "ok" if facing else "refuse"
    if not facing:
        entry["reason"] = "aucune face exploitable"
    entry["seconds"] = round(time.time() - started, 2)
    return entry


def preview(path: Path, dictionaries: ZstdDicts | None, size: tuple[int, int]) -> bytes | None:
    """Rendu PNG du modèle, ou ``None`` si la géométrie est refusée."""
    try:
        data = read_bytes(path, dictionaries)
        meshes = bfres_mod.meshes_from_file(bfres_mod.BfresFile.parse(data))
        return render.render_png(meshes, size[0], size[1], 0.7, -0.35, supersample=1)
    except Exception:            # aucune géométrie : pas d'aperçu, le rapport dit pourquoi
        return None


# --------------------------------------------------------------------------- rapport
def sweep(paths: list[Path], outdir: Path | None = None,
          size: tuple[int, int] = (480, 360)) -> list[dict]:
    """Mesure chaque modèle, écrit les aperçus et les rapports ; renvoie les lignes."""
    dictionaries = _dictionaries(next((p for p in paths if p.is_dir()), None))
    if outdir is not None and outdir.is_dir():
        for stale in outdir.glob("[0-9][0-9]_*.png"):   # aperçus d'un sweep précédent
            stale.unlink()
    rows: list[dict] = []
    for index, path in enumerate(find_models(paths)):
        row = inspect(path, dictionaries)
        if outdir is not None and row["status"] == "ok":
            png = preview(path, dictionaries, size)
            if png is not None:
                outdir.mkdir(parents=True, exist_ok=True)
                target = outdir / f"{index:02d}_{Path(path).name.split('.')[0]}.png"
                target.write_bytes(png)
                row["preview"] = target.name
        rows.append(row)
    if outdir is not None:
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "coverage.json").write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        (outdir / "coverage.md").write_text(markdown(rows), encoding="utf-8")
    return rows


def markdown(rows: list[dict]) -> str:
    """Tableau de couverture en Markdown (lisible dans une PR)."""
    ok = sum(1 for row in rows if row["status"] == "ok")
    refused = sum(1 for row in rows if row["status"] == "refuse")
    failed = sum(1 for row in rows if row["status"] == "error")
    animations = sum(1 for row in rows if row["status"] == "animation")
    lines = ["# Couverture", "",
             f"{len(rows)} fichier(s) : **{ok} traduit(s)**, {refused} refusé(s), "
             f"{failed} en erreur, {animations} animation(s).", "",
             "| fichier | état | sous-maillages | faces | sommets | taille | avertissements |",
             "| --- | --- | ---: | ---: | ---: | --- | --- |"]
    for row in rows:
        notes = list(row["warnings"])
        if row["reason"]:
            notes.insert(0, row["reason"])
        note = " ; ".join(notes)[:160].replace("|", "/")
        lines.append(f"| `{row['file']}` | {row['status']} | {row['meshes']} | "
                     f"{row['faces']} | {row['vertices']} | "
                     f"{row['bounds'] or '—'} | {note or '—'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Couverture d'une famille de modèles BFRES.")
    parser.add_argument("paths", nargs="+", type=Path, help="dossier(s) ou fichier(s)")
    parser.add_argument("-o", "--out", type=Path, default=None,
                        help="dossier des aperçus PNG et des rapports")
    parser.add_argument("--sizes", default="480x360", help="taille des aperçus (LxH)")
    args = parser.parse_args(argv)
    try:
        width, height = (int(part) for part in args.sizes.lower().split("x"))
    except ValueError:
        parser.error("--sizes attend la forme 480x360")
    rows = sweep(args.paths, args.out, (width, height))
    if not rows:
        print("Aucun BFRES trouvé dans ces chemins.", file=sys.stderr)
        return 2
    print(markdown(rows))
    ok = sum(1 for row in rows if row["status"] == "ok")
    print(f"{ok}/{len(rows)} fichier(s) traduit(s).")
    return 0 if ok else 1


if __name__ == "__main__":                   # pragma: no cover - point d'entrée CLI
    raise SystemExit(main())
