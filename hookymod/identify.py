"""Identification de formats et scan d'un dossier de jeu.

Objectif : savoir *ce qu'il y a réellement* dans un dump avant d'écrire un
parseur. ``scan()`` parcourt un dossier, reconnaît les magics connus, regroupe
les résultats par extension et propose un moteur probable.

Le module est volontairement sans dépendance (ni Tkinter, ni zstandard
obligatoire) et produit un rapport JSON sérialisable, facile à partager.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .util import HookyError, human_size, hexdump

# --------------------------------------------------------------------------- magics
# signature, position, famille, libellé. Les signatures sont cherchées à
# ``offset`` (par défaut 0) ; ``at_end`` teste la fin de fichier.
@dataclass(frozen=True)
class Magic:
    kind: str            # identifiant court et stable (utilisé dans le JSON)
    label: str           # description lisible
    family: str          # grande famille technique
    signature: bytes
    offset: int = 0
    at_end: bool = False
    note: str = ""


MAGICS: tuple[Magic, ...] = (
    # ---------------- archives et formats de ressources
    Magic("nup", "paquet NUP", "Formats NUP / NSO2", b"NUP\x01"),
    Magic("nso2", "exécutable NSO2", "Formats NUP / NSO2", b"NSO0"),
    Magic("sarc", "archive SARC", "Formats de ressources", b"SARC"),
    Magic("rarc", "archive RARC", "Formats de ressources", b"RARC"),
    Magic("u8", "archive U8", "Formats de ressources", b"U\xaa8-"),
    Magic("zar", "archive ZAR", "Formats de ressources", b"ZAR\x01",
          note="archive ZAR pour ressources portables"),
    Magic("yaz0", "compression Yaz0", "Formats de ressources", b"Yaz0"),
    Magic("yaz1", "compression Yaz1", "Formats de ressources", b"Yaz1"),
    Magic("narc", "archive NARC", "Formats de ressources", b"NARC"),
    # ---------------- formats complémentaires
    Magic("zstd", "compression zstd (.zs / .zs.zst)", "zstd", b"\x28\xb5\x2f\xfd"),
    Magic("bntx", "textures BNTX", "Formats de ressources", b"BNTX"),
    Magic("bfres", "modèle BFRES", "Formats de ressources", b"FRES"),
    Magic("bnsh", "shader BNSH", "Formats de ressources", b"BNSH"),
    Magic("bnshd", "shader BNSH (précompilé)", "Formats de ressources", b"BNSHD"),
    Magic("aamp", "paramètres AAMP", "Formats de ressources", b"AAMP"),
    Magic("byml", "données BYML (binaire YAML)", "Formats de ressources", b"BYML"),
    Magic("byml_text", "BYML au format texte", "Formats de ressources", b"YAML"),
    Magic("beln", "table de messages BELN", "Formats de ressources", b"BELN"),
    Magic("bwav", "audio BWAV", "Formats de ressources", b"BWAV"),
    Magic("bars", "banque audio BARS", "Formats de ressources", b"BARS"),
    Magic("bfstm", "flux audio BFSTM", "Formats de ressources", b"FSTM"),
    Magic("bfsar", "banque audio BFSAR", "Formats de ressources", b"FSAR"),
    # ---------------- formats portables
    Magic("cmb", "modèle CMB", "Formats portables", b"CMB "),
    Magic("ctex", "texture CTEX", "Formats portables", b"CTEX"),
    Magic("bch", "modèle BCH", "Formats portables", b"BCH\x00"),
    Magic("cseq", "séquence CSEQ", "Formats portables", b"CSEQ"),
    Magic("cbnk", "banque CWAV/CBNK", "Formats portables", b"CBNK"),
    # ---------------- Unreal Engine
    Magic("uasset", "asset Unreal Engine (.uasset/.umap)", "Unreal",
          b"\x9e\x2a\x83\xc1", note="version du package dans l'en-tête"),
    Magic("ue_pak", "archive Unreal (.pak)", "Unreal", b"\xe1\x12\x6f\x5a", at_end=True),
    Magic("iostore", "conteneur IoStore Unreal (.utoc)", "Unreal", b"IoTs"),
    # ---------------- Unity
    Magic("unityfs", "bundle Unity (AssetBundle)", "Unity", b"UnityFS"),
    Magic("unityweb", "bundle Unity Web", "Unity", b"UnityWeb"),
    Magic("unityraw", "bundle Unity brut", "Unity", b"UnityRaw"),
    Magic("unity_asset", "fichier série Unity", "Unity", b"UnityArch"),
    # ---------------- autres moteurs / conteneurs
    Magic("godot", "pack Godot (.pck)", "Godot", b"GDPC"),
    Magic("gcf", "archive GCF (Steam)", "Valve", b"GCF\x00"),
    Magic("vpk", "archive VPK (Source)", "Valve", b"\x34\x12\xaa\x55"),
    Magic("cri_acb", "banque audio CRI ACB (.acb)", "CRI", b"@UTF"),
    Magic("cri_awb", "banque audio CRI AWB (.awb)", "CRI", b"AFS2"),
    Magic("mz", "exécutable Windows (PE)", "Divers", b"MZ"),
    Magic("elf", "exécutable ELF", "Divers", b"\x7fELF"),
    Magic("zip", "archive ZIP", "Divers", b"PK\x03\x04"),
    Magic("png", "image PNG", "Média", b"\x89PNG\r\n\x1a\n"),
    Magic("dds", "texture DDS", "Média", b"DDS "),
    Magic("ktx", "texture KTX", "Média", b"\xabKTX 20\xbb\r\n\x1a\n"),
    Magic("ogg", "audio Ogg", "Média", b"OggS"),
    Magic("riff", "conteneur RIFF (wav / webp)", "Média", b"RIFF"),
    Magic("flac", "audio FLAC", "Média", b"fLaC"),
    Magic("mp3_id3", "audio MP3 (tag ID3)", "Média", b"ID3"),
    Magic("bink", "vidéo Bink", "Média", b"BIK"),
    Magic("usm", "vidéo USM (CRI)", "Média", b"CRID"),
    Magic("sqlite", "base SQLite", "Divers", b"SQLite format 3\x00"),
)

# Hints donnés par l'extension quand aucun magic ne correspond.
EXTENSIONS: dict[str, tuple[str, str]] = {
    ".nup": ("nup", "paquet NUP (romfs + exefs)"),
    ".nso": ("nso2", "exécutable NSO"),
    ".zs": ("zstd", "compression zstd"),
    ".zst": ("zstd", "compression zstd"),
    ".szs": ("yaz0", "compression Yaz0 (SZS est une Yaz0)"),
    ".pack": ("sarc", "archive (souvent SARC)"),
    ".sarc": ("sarc", "archive SARC"),
    ".bfres": ("bfres", "modèle BFRES"),
    ".bntx": ("bntx", "textures BNTX"),
    ".bntx.zs": ("bntx", "textures BNTX compressées"),
    ".bnsh": ("bnsh", "shader BNSH"),
    ".byml": ("byml", "données BYML"),
    ".bgyml": ("byml", "données BYML"),
    ".aamp": ("aamp", "paramètres AAMP"),
    ".zar": ("zar", "archive ZAR"),
    ".cmb": ("cmb", "modèle CMB"),
    ".ctex": ("ctex", "texture CTEX"),
    ".bch": ("bch", "modèle BCH"),
    ".bcres": ("bch", "modèle BCH"),
    ".uasset": ("uasset", "asset Unreal Engine"),
    ".umap": ("uasset", "niveau Unreal Engine"),
    ".pak": ("ue_pak", "archive Unreal"),
    ".utoc": ("iostore", "conteneur IoStore (Unreal)"),
    ".ucas": ("iostore", "conteneur IoStore (Unreal)"),
    ".bundle": ("unityfs", "bundle Unity"),
    ".assets": ("unity_asset", "assets Unity"),
    ".pck": ("godot", "pack Godot"),
    ".obj": ("obj", "maillage OBJ (texte)"),
    ".dds": ("dds", "texture DDS"),
    ".png": ("png", "image PNG"),
    ".bfstm": ("bfstm", "audio BFSTM"),
    ".bfsar": ("bfsar", "banque audio BFSAR"),
    ".bwav": ("bwav", "audio BWAV"),
    ".bwav.zs": ("bwav", "audio BWAV compressé"),
    ".bars": ("bars", "banque audio BARS"),
    ".bars.zs": ("bars", "banque audio BARS compressée"),
    ".bar": ("bar", "archive BAR"),
    ".nca": ("nca", "contenu NCA (Switch)"),
    ".nsp": ("nsp", "paquet NSP (Switch)"),
    ".xci": ("xci", "cartouche XCI (Switch)"),
}



@dataclass
class Found:
    kind: str
    label: str
    family: str
    confidence: str = "magic"   # magic | extension | texte | inconnu
    offset: int = 0
    detail: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "family": self.family,
                "confidence": self.confidence, "offset": self.offset,
                "detail": self.detail}


# --------------------------------------------------------------------------- helpers
def printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    good = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return good / len(data)


def extension_of(name: str) -> str:
    """Extension « double » quand elle existe (``.bntx.zs``), sinon simple."""
    lower = name.lower()
    stem, dot, tail = lower.rpartition(".")
    if dot and stem.rpartition(".")[2] in {"bntx", "bfres", "bwav", "bars", "bntx.zs"}:
        return "." + stem.rpartition(".")[2] + "." + tail
    return "." + tail if dot else ""


def _zstd_detail(data: bytes) -> str:
    try:
        from .zs import frame_info, zstd_available
        if not zstd_available():
            return ""
        dict_id, size = frame_info(data)
    except Exception:  # noqa: BLE001 - l'identification ne doit jamais échouer
        return ""
    parts = []
    if dict_id:
        parts.append(f"dictionnaire id {dict_id}")
    if size is not None:
        parts.append(f"contenu décompressé {human_size(size)}")
    return ", ".join(parts)


def sniff(data: bytes, name: str = "", tail: bytes = b"") -> list[Found]:
    """Identifie ``data`` (et éventuellement ses derniers octets ``tail``)."""
    found: list[Found] = []
    head = data[:64]
    for magic in MAGICS:
        if magic.at_end:
            if not tail:
                continue
            position = tail.rfind(magic.signature)
        else:
            position = head.find(magic.signature, magic.offset)
        if position < 0:
            continue
        detail = magic.note
        if magic.kind == "zstd":
            detail = ", ".join(p for p in (detail, _zstd_detail(data)) if p)
        found.append(Found(magic.kind, magic.label, magic.family, "magic",
                           position, detail))
        if len(found) >= 4:
            break
    # le magic situé à l'offset 0 est le plus parlant : il passe en premier
    found.sort(key=lambda item: (item.offset, -len(item.label)))

    ext = extension_of(name)
    hint = EXTENSIONS.get(ext)
    if hint and not any(item.kind == hint[0] for item in found):
        found.append(Found(hint[0], hint[1], "Extension", "extension", 0,
                           f"extension {ext}"))
    if not found and data and printable_ratio(data[:512]) > 0.95:
        found.append(Found("text", "fichier texte", "Texte", "texte", 0,
                           f"{printable_ratio(data[:512]) * 100:.0f} % imprimable"))
    if not found:
        found.append(Found("unknown", "format inconnu", "Inconnu", "inconnu", 0,
                           f"{len(data)} octets lus"))
    return found


def carve(data: bytes, limit: int = 12) -> list[Found]:
    """Cherche des structures connues **à n'importe quel offset** (conteneurs).

    Utile pour un fichier au magic inconnu qui embarque des blocs SARC, BNTX,
    BFRES, zstd… après un en-tête maison.
    """
    found: list[Found] = []
    for magic in MAGICS:
        if magic.at_end or not magic.signature:
            continue
        start = 0
        while len(found) < limit:
            position = data.find(magic.signature, start)
            if position < 0:
                break
            start = position + 1
            if position == 0:
                continue
            found.append(Found(magic.kind, magic.label, magic.family, "magic",
                               position, f"trouvé à l'offset 0x{position:X}"))
    found.sort(key=lambda item: item.offset)
    return found[:limit]


def sniff_file(path: Path, head: int = 4096) -> list[Found]:
    """Comme :func:`sniff` mais lit directement le fichier (sans tout charger)."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        data = handle.read(head)
        tail = b""
        if size > 8:
            handle.seek(max(0, size - 16))
            tail = handle.read(16)
    return sniff(data, path.name, tail)


# Poids des formats dans la détection du moteur (nombre de fichiers vu).
ENGINE_WEIGHTS: dict[str, dict[str, int]] = {
    "NUP / NSO2": {"nup": 5, "nso2": 2, "sarc": 2, "bfres": 2, "bntx": 2,
                            "zstd": 1, "byml": 1, "aamp": 1, "bnsh": 1},
    "Unreal Engine": {"uasset": 4, "iostore": 4, "ue_pak": 2},
    "Unity": {"unityfs": 4, "unity_asset": 3, "unityweb": 3, "unityraw": 2},
    "Godot": {"godot": 5},
    "ZAR / CMB / CTEX": {
        "zar": 3, "cmb": 3, "ctex": 2, "bch": 2},
    "SARC / BFRES / BNTX": {"sarc": 3, "bfres": 3, "bntx": 3, "bnsh": 2,
                               "byml": 2, "aamp": 1, "zstd": 1},
    "RARC / U8 / Yaz0": {"rarc": 3, "u8": 3, "yaz0": 2},
}

ENGINE_REASONS = {
    "NUP / NSO2": "paquet .nup / NSO2 : romfs + exefs",
    "Unreal Engine": "conteneurs Unreal (.uasset / .pak / IoStore) : les assets sont "
                     "dans des paquets binaires, pas dans un romfs",
    "Unity": "bundles UnityFS / SerializedFiles : les assets sont dans des .assets",
    "Godot": "pack .pck détecté",
    "ZAR / CMB / CTEX": "archives .zar, modèles CMB et textures CTEX",
    "SARC / BFRES / BNTX": "SARC + zstd + BFRES/BNTX : famille de formats de ressources",
    "RARC / U8 / Yaz0": "RARC / U8 / Yaz0",
}


def guess_engine(kinds: dict[str, int] | set[str],
                 exts: set[str]) -> tuple[str, str]:
    """Retourne ``(moteur probable, justification)`` à partir des formats vus."""
    counts: dict[str, int] = dict(kinds) if not isinstance(kinds, set) else \
        {kind: 1 for kind in kinds}
    ranked = []
    for engine, weights in ENGINE_WEIGHTS.items():
        score = sum(weights.get(kind, 0) * count for kind, count in counts.items())
        # les extensions seules comptent pour un point
        score += sum(1 for ext in exts
                     if EXTENSIONS.get(ext, ("", ""))[0] in weights)
        ranked.append((score, engine))
    ranked.sort(reverse=True)
    best_score, best = ranked[0] if ranked else (0, "Inconnu")
    if best_score <= 0:
        return ("Inconnu", "aucun magic ni extension caractéristique")
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
    detail = ", ".join(f"{kind} ×{count}" for kind, count in top)
    return best, f"{ENGINE_REASONS.get(best, '')} — formats vus : {detail}"


# --------------------------------------------------------------------------- scan
def _iter_files(root: Path, max_depth: int | None, limit: int):
    """Parcourt ``root`` sans suivre les liens symboliques, trié et borné."""
    count = 0
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if max_depth is None or depth < max_depth:
                        stack.append((Path(entry.path), depth + 1))
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            yield Path(entry.path)
            count += 1
            if count >= limit:
                return
        stack.reverse()


def scan(root: Path, *, samples: int = 3, read: int = 4096,
         hexdump_bytes: int = 0, strings: int = 0,
         max_depth: int | None = None, limit: int = 200_000) -> dict:
    """Scanne ``root`` et retourne un rapport sérialisable en JSON."""
    root = Path(root)
    if not root.exists():
        raise HookyError(f"Chemin introuvable : {root}")
    files = [root] if root.is_file() else None
    if files is None:
        files = list(_iter_files(root, max_depth, limit))

    total_size = 0
    per_ext: dict[str, dict] = {}
    kinds: dict[str, int] = {}
    families: dict[str, int] = {}
    readable = 0
    errors: list[str] = []
    truncated = len(files) >= limit

    for path in files:
        try:
            size = path.stat().st_size
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        total_size += size
        ext = extension_of(path.name) or "(aucune)"
        bucket = per_ext.setdefault(ext, {"count": 0, "size": 0, "samples": []})
        bucket["count"] += 1
        bucket["size"] += size

        if len(bucket["samples"]) >= samples:
            continue
        try:
            found = sniff_file(path, read)
            readable += 1
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        entry = {
            "path": path.name if files and root.is_file() else
                    path.relative_to(root).as_posix(),
            "size": size,
            "kind": found[0].kind,
            "label": found[0].label,
            "detail": found[0].detail,
            "findings": [f.as_dict() for f in found],
        }
        with path.open("rb") as handle:
            carved = carve(handle.read(read))
        if carved:
            entry["carved"] = [item.as_dict() for item in carved]
        if hexdump_bytes:
            with path.open("rb") as handle:
                entry["hexdump"] = hexdump(handle.read(hexdump_bytes), hexdump_bytes)
        if strings:
            from .util import ascii_strings
            with path.open("rb") as handle:
                entry["strings"] = ascii_strings(handle.read(read), strings, min_len=4)
        bucket["samples"].append(entry)
        for found_item in found:
            kinds[found_item.kind] = kinds.get(found_item.kind, 0) + 1
            families[found_item.family] = families.get(found_item.family, 0) + 1

    engine, reason = guess_engine(set(kinds), set(per_ext))
    ordered = dict(sorted(per_ext.items(), key=lambda kv: (-kv[1]["count"], kv[0])))
    return {
        "root": str(root),
        "is_file": root.is_file(),
        "file_count": len(files),
        "total_size": total_size,
        "total_size_human": human_size(total_size),
        "readable": readable,
        "truncated": truncated,
        "extension_count": len([e for e in per_ext if e != "(aucune)"]),
        "extensions": ordered,
        "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "families": dict(sorted(families.items(), key=lambda kv: -kv[1])),
        "engine": engine,
        "engine_reason": reason,
        "errors": errors[:20],
    }


# --------------------------------------------------------------------------- rapport
def format_report(report: dict) -> str:
    lines: list[str] = []
    target = report["root"]
    if report.get("is_file"):
        lines.append(f"Fichier : {target}")
    else:
        lines.append(f"Dossier : {target}")
    lines.append(f"{report['file_count']:,} fichier(s) — {report['total_size_human']}"
                 f" — {report['extension_count']} extension(s)")
    if report.get("truncated"):
        lines.append("⚠ scan tronqué (limite atteinte) : utilise --limit pour aller plus loin")
    lines.append("")

    lines.append("Moteur probable : " + report["engine"])
    lines.append(f"  → {report['engine_reason']}")
    lines.append("")

    if report["families"]:
        lines.append("Familles détectées")
        for family, count in report["families"].items():
            lines.append(f"  {family:<18} {count:>6} fichier(s)")
        lines.append("")

    lines.append("Extensions (par nombre)")
    for ext, info in report["extensions"].items():
        label = ""
        if info["samples"]:
            label = " — " + info["samples"][0]["label"]
        lines.append(f"  {ext:<12} {info['count']:>6}  {human_size(info['size']):>10}{label}")
    lines.append("")

    if report["errors"]:
        lines.append("Erreurs de lecture")
        lines.extend("  " + error for error in report["errors"])
        lines.append("")

    with_samples = [(ext, info) for ext, info in report["extensions"].items()
                    if info["samples"]]
    if with_samples:
        lines.append("Échantillons")
        for ext, info in with_samples:
            for sample in info["samples"]:
                lines.append(f"  • {sample['path']}  ({human_size(sample['size'])})")
                lines.append(f"      {sample['label']}")
                if sample["detail"]:
                    lines.append(f"      {sample['detail']}")
                for found in sample["findings"][1:]:
                    lines.append(f"      aussi : {found['label']} ({found['confidence']})")
                for carved in sample.get("carved", [])[:6]:
                    lines.append(f"      contient : {carved['label']} "
                                 f"@ 0x{carved['offset']:X}")
                for line in sample.get("hexdump", []):
                    lines.append("      " + line)
                if sample.get("strings"):
                    shown = [item[:60] for item in sample["strings"][:12]]
                    lines.append("      chaînes : " + ", ".join(shown))
        lines.append("")

    lines.append("Ce rapport suffit pour identifier les formats du jeu.")
    return "\n".join(lines)


def report_to_json(report: dict, indent: int = 2) -> str:
    return json.dumps(report, indent=indent, ensure_ascii=False)
