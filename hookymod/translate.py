"""Traducteur de ressources romfs ↔ dossier PC éditable, et retour.

Le principe : un **projet** est un dossier qui garde à la fois

* ``pc/`` : l'arborescence éditable pour Windows/Linux — textures en PNG,
  modèles en OBJ (Blender), fichiers inconnus décompressés tels quels ;
* ``origin/`` : une copie des fichiers d'origine, utilisée comme gabarit
  (c'est ce qui permet de reconstruire un BNTX/BFRES valide au retour) ;
* ``manifest.json`` : la correspondance entre les deux, avec le format, la
  compression et les paramètres de chaque fichier.

``unpack`` remplit le projet depuis un romfs, ``pack`` reconstruit un romfs
(ou seulement les fichiers modifiés, pour un mod Hookshot) et met à jour la
table des tailles de ressources au passage.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import bfres as bfres_mod
from . import bfres_import, bfres_safe, bntx as bntx_mod, bntx_encode, modelio, restbl as restbl_mod
from .mesh import export_obj
from .sarc import is_sarc
from .util import FormatError, HookyError, human_size, safe_child, write_atomic
from .zs import ZstdDicts, find_dict_files, guess_romfs_root, is_zstd

MANIFEST = "manifest.json"
PC_DIR = "pc"
ORIGIN_DIR = "origin"
VERSION = 1


# --------------------------------------------------------------------------- modèle
@dataclass
class Entry:
    rel: str                    # chemin dans le romfs (avec .zs le cas échéant)
    kind: str                   # bntx | bfres | raw
    pc: str | None = None       # chemin relatif dans pc/
    compressed: bool = False    # le fichier du romfs est un .zs
    dict_id: int | None = None
    texture: str = ""           # BNTX : nom de la texture
    texture_index: int = 0
    model: int = 0              # BFRES : modèle / shape
    shape: int = 0
    size: int = 0
    note: str = ""
    digest: str = ""            # empreinte du fichier PC au moment de l'extraction

    def as_dict(self) -> dict:
        return {key: getattr(self, key) for key in (
            "rel", "kind", "pc", "compressed", "dict_id", "texture",
            "texture_index", "model", "shape", "size", "note", "digest")}

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        return cls(**{key: data.get(key, default)
                      for key, default in (("rel", ""), ("kind", "raw"), ("pc", None),
                                           ("compressed", False), ("dict_id", None),
                                           ("texture", ""), ("texture_index", 0),
                                           ("model", 0), ("shape", 0), ("size", 0),
                                           ("note", ""), ("digest", ""))})


@dataclass
class Manifest:
    romfs: str = ""
    entries: list[Entry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    version: int = VERSION

    def as_dict(self) -> dict:
        return {"version": self.version, "romfs": self.romfs,
                "generated_by": "HookyMod — traducteur romfs ↔ PC",
                "entries": [entry.as_dict() for entry in self.entries],
                "errors": self.errors}

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HookyError(f"manifest illisible : {exc}") from exc
        manifest = cls(romfs=data.get("romfs", ""), errors=data.get("errors", []),
                       version=data.get("version", VERSION))
        manifest.entries = [Entry.from_dict(item) for item in data.get("entries", [])]
        return manifest

    def save(self, path: Path) -> None:
        write_atomic(Path(path), json.dumps(self.as_dict(), indent=2,
                                            ensure_ascii=False).encode("utf-8"))


# --------------------------------------------------------------------------- utilitaires
class Project:
    """Un projet de traduction (dossier avec ``pc/``, ``origin/``, manifest)."""

    def __init__(self, root: str | Path, manifest: Manifest | None = None) -> None:
        self.root = Path(root)
        self.manifest = manifest or Manifest()

    # ------------------------------------------------------------------ chemins
    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST

    @property
    def pc_root(self) -> Path:
        return self.root / PC_DIR

    @property
    def origin_root(self) -> Path:
        return self.root / ORIGIN_DIR

    def open(self) -> "Project":
        if not self.manifest_path.exists():
            raise HookyError(f"Ce n'est pas un projet HookyMod : {self.manifest_path} "
                             "est absent.")
        self.manifest = Manifest.load(self.manifest_path)
        return self

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest.save(self.manifest_path)

    # ------------------------------------------------------------------ fichiers
    def origin_path(self, entry: Entry) -> Path:
        return safe_child(self.origin_root, entry.rel) or self.origin_root / entry.rel

    def pc_path(self, entry: Entry) -> Path | None:
        return safe_child(self.pc_root, entry.pc) if entry.pc else None

    def changed(self, entry: Entry) -> bool:
        """Le fichier PC a-t-il été modifié depuis l'extraction ?"""
        path = self.pc_path(entry)
        if path is None or not path.exists():
            return False
        current = _digest(path.read_bytes())
        if entry.digest:
            return current != entry.digest
        origin = self.origin_path(entry)
        if not origin.exists():
            return False
        return current != _digest(_reference_bytes(entry, origin))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reference_bytes(entry: Entry, origin: Path) -> bytes:
    """Octets de référence pour comparer : le PNG/OBJ exporté à l'unpack."""
    if entry.kind == "bntx":
        try:
            return bntx_encode.bntx_to_png(origin.read_bytes(), entry.texture,
                                           entry.texture_index)
        except (HookyError, FormatError):
            return b""
    if entry.kind == "bfres":
        try:
            return export_obj_bytes(origin.read_bytes(), entry.model, entry.shape)
        except (HookyError, FormatError):
            return b""
    raw = origin.read_bytes()
    return _decompress_origin(raw, entry)


def _decompress_origin(raw: bytes, entry: Entry) -> bytes:
    if not entry.compressed:
        return raw
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    except Exception:  # noqa: BLE001 - comparaison seulement
        return raw


def export_obj_bytes(data: bytes, model: int = 0, shape: int = 0) -> bytes:
    """OBJ (texte) d'une shape de BFRES, en mémoire."""
    import tempfile
    file = bfres_mod.BfresFile.parse(data)
    if not file.models or model >= len(file.models):
        raise HookyError("Modèle introuvable.")
    shapes = file.models[model].shapes
    if shape >= len(shapes):
        raise HookyError(f"Shape #{shape} absente.")
    meshes = []
    for item in shapes[shape:shape + 1]:
        built = item.build_mesh(0, file.warnings)
        if built is not None:
            meshes.append(built)
    if not meshes:
        raise HookyError("Shape vide.")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "shape.obj"
        export_obj(meshes, path, mtl=False)
        return path.read_bytes()


# --------------------------------------------------------------------------- unpack
def unpack(romfs: str | Path, project: str | Path, only: str | None = None,
           limit: int | None = None, progress=None) -> Manifest:
    """Convertit un romfs en projet PC éditable (PNG, OBJ, fichiers bruts)."""
    root = Path(romfs)
    if not root.is_dir():
        raise HookyError(f"Dossier romfs introuvable : {root}")
    romfs_root = root if (root / "Pack").is_dir() or (root / "System").is_dir() \
        else (guess_romfs_root(root) or root)
    dicts = ZstdDicts()
    files, _ = find_dict_files(romfs_root)
    for dictionary in files:
        try:
            dicts.load_file(dictionary)
        except HookyError:
            pass

    proj = Project(project)
    proj.root.mkdir(parents=True, exist_ok=True)
    proj.manifest = Manifest(romfs=str(romfs_root))
    candidates = sorted(path for path in romfs_root.rglob("*") if path.is_file())
    if only:
        needle = only.lower()
        candidates = [path for path in candidates if needle in str(path).lower()]
    if limit:
        candidates = candidates[:limit]

    total = len(candidates)
    for index, path in enumerate(candidates, 1):
        rel = path.relative_to(romfs_root).as_posix()
        raw = path.read_bytes()
        compressed = is_zstd(raw)
        dict_id = None
        data = raw
        if compressed:
            try:
                from .zs import frame_info
                dict_id, _ = frame_info(raw)
            except HookyError:
                dict_id = None
            try:
                data = dicts.decompress(raw)
            except HookyError as exc:
                proj.manifest.errors.append(f"{rel} : {exc}")
                data = raw
                compressed = False
        origin = safe_child(proj.origin_root, rel)
        origin.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(origin, raw)

        try:
            entries = _convert_to_pc(proj, rel, data, compressed, dict_id)
        except (HookyError, FormatError) as exc:
            proj.manifest.errors.append(f"{rel} : {exc}")
            entries = [_raw_entry(proj, rel, data, compressed, dict_id)]
        proj.manifest.entries.extend(entries)
        if progress is not None:
            progress(index, total)
    proj.save()
    return proj.manifest


def _raw_entry(proj: Project, rel: str, data: bytes, compressed: bool,
               dict_id: int | None) -> Entry:
    entry = Entry(rel=rel, kind="raw", compressed=compressed, dict_id=dict_id,
                  size=len(data))
    target = _pc_name(rel)
    entry.pc = target
    path = safe_child(proj.pc_root, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, data)
    entry.digest = _digest(data)
    return entry


def _pc_name(rel: str) -> str:
    name = rel
    while name.lower().endswith(".zs"):
        name = name[:-3]
    return name


def _convert_to_pc(proj: Project, rel: str, data: bytes, compressed: bool,
                   dict_id: int | None) -> list[Entry]:
    stem = _pc_name(rel)
    if bntx_mod.is_bntx(data):
        file = bntx_mod.BntxFile.parse(data)
        entries: list[Entry] = []
        for index, texture in enumerate(file.textures):
            if texture.format_name not in ("", None) and not texture.width:
                continue
            try:
                png = texture.to_png(0)
            except (HookyError, FormatError) as exc:
                proj.manifest.errors.append(f"{rel} : texture {texture.name} ({exc})")
                continue
            suffix = "" if len(file.textures) == 1 else f"__{_slug(texture.name or index)}"
            pc = f"{stem}{suffix}.png"
            path = safe_child(proj.pc_root, pc)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, png)
            entries.append(Entry(rel=rel, kind="bntx", pc=pc, compressed=compressed,
                                 dict_id=dict_id, texture=texture.name,
                                 texture_index=index, size=len(data), digest=_digest(png),
                                 note=f"{texture.width}×{texture.height} "
                                      f"{texture.format_name}"))
        if entries:
            return entries
        raise FormatError("aucune texture exploitable")
    if bfres_mod.is_bfres(data):
        file = bfres_mod.BfresFile.parse(data)
        entries = []
        for model_index, model in enumerate(file.models):
            for shape_index, shape in enumerate(model.shapes):
                try:
                    obj = export_obj_bytes(data, model_index, shape_index)
                except (HookyError, FormatError) as exc:
                    proj.manifest.errors.append(f"{rel} : {shape.name} ({exc})")
                    continue
                pc = f"{stem}_m{model_index}_s{shape_index}.obj"
                path = safe_child(proj.pc_root, pc)
                path.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(path, obj)
                entries.append(Entry(rel=rel, kind="bfres", pc=pc, compressed=compressed,
                                     dict_id=dict_id, model=model_index,
                                     shape=shape_index, size=len(data), digest=_digest(obj),
                                     note=f"{shape.name} — {len(shape.meshes)} maillage(s)"))
        if entries:
            return entries
        raise FormatError("aucune shape exploitable")
    entry = _raw_entry(proj, rel, data, compressed, dict_id)
    if is_sarc(data):
        entry.note = "archive SARC : à éditer via les commandes list/extract/replace"
    return [entry]


def _slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text)
    return cleaned.strip("_") or "tex"


# --------------------------------------------------------------------------- pack
@dataclass
class PackResult:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_bytes: int = 0
    table_note: str = ""
    sizes: dict[str, int] = field(default_factory=dict)

    def summary(self) -> list[str]:
        lines = [f"{len(self.written)} fichier(s) écrit(s), "
                 f"{human_size(self.total_bytes)}"]
        if self.skipped:
            lines.append(f"{len(self.skipped)} fichier(s) inchangé(s) non réécrit(s)")
        if self.table_note:
            lines.append(self.table_note)
        lines += [f"⚠ {error}" for error in self.errors[:20]]
        return lines


def pack(project: str | Path, output: str | Path, all_files: bool = False,
         make_mod: bool = True, mod_name: str = "", update_table: bool = True,
         progress=None) -> PackResult:
    """Reconstruit des fichiers du romfs depuis le projet (BNTX, BFRES, bruts).

    ``all_files=False`` (défaut) n'écrit que les fichiers modifiés — c'est ce
    qu'il faut pour un mod. ``all_files=True`` produit un romfs complet.
    """
    proj = Project(project).open()
    out_root = Path(output)
    target_root = (out_root / mod_name / "romfs") if mod_name else (out_root / "romfs")
    result = PackResult()
    dicts = ZstdDicts()
    romfs = Path(proj.manifest.romfs) if proj.manifest.romfs else None
    if romfs and romfs.is_dir():
        files, _ = find_dict_files(romfs)
        for dictionary in files:
            try:
                dicts.load_file(dictionary)
            except HookyError:
                pass

    total = len(proj.manifest.entries)
    for index, entry in enumerate(proj.manifest.entries, 1):
        if progress is not None:
            progress(index, total)
        changed = proj.changed(entry)
        if not all_files and not changed:
            result.skipped.append(entry.rel)
            continue
        try:
            # Un asset inchangé est recopié tel quel : ne pas le réinjecter,
            # notamment quand la géométrie d'origine est en lecture seule.
            if not changed:
                payload, already_encoded = proj.origin_path(entry).read_bytes(), True
            else:
                payload, already_encoded = _build_entry(proj, entry, dicts)
        except (HookyError, FormatError, OSError, ValueError) as exc:
            result.errors.append(f"{entry.rel} : {exc}")
            continue
        if entry.compressed and not already_encoded:
            level = 16
            try:
                payload = dicts.compress(payload, entry.dict_id, level)
            except HookyError as exc:
                result.errors.append(f"{entry.rel} : recompression impossible ({exc})")
                continue
        target = safe_child(target_root, entry.rel)
        if target is None:
            result.errors.append(f"{entry.rel} : chemin invalide")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(target, payload)
        result.written.append(entry.rel)
        result.total_bytes += len(payload)
        result.sizes[entry.rel] = len(payload)

    if update_table and romfs and result.sizes:
        result.table_note = _update_table(romfs, target_root, result.sizes, dicts)
    return result


def _build_entry(proj: Project, entry: Entry,
                 dicts: ZstdDicts) -> tuple[bytes, bool]:
    """Retourne ``(octets, déjà encodés pour le romfs)``."""
    origin = proj.origin_path(entry)
    if not origin.exists():
        raise HookyError("fichier d'origine absent de origin/")
    original = origin.read_bytes()
    pc = proj.pc_path(entry)
    if entry.kind == "raw":
        if pc is None or not pc.exists():
            return original, True          # rien n'a changé : on garde tel quel
        data = pc.read_bytes()
        reference = _decompress_origin(original, entry)
        if data == reference:
            return original, True
        return data, False
    if pc is None or not pc.exists():
        raise HookyError(f"fichier PC absent : {entry.pc}")
    if entry.kind == "bntx":
        template = dicts.decompress(original) if is_zstd(original) else original
        data, report = bntx_encode.png_to_bntx(pc.read_bytes(), template,
                                               texture=entry.texture,
                                               index=entry.texture_index)
        entry.note = str(report)
        return data, False
    if entry.kind == "bfres":
        template = dicts.decompress(original) if is_zstd(original) else original
        data, report = obj_to_bfres(template, pc.read_bytes(), entry.model, entry.shape)
        entry.note = " · ".join(report.lines())
        return data, False
    raise HookyError(f"type inconnu : {entry.kind}")


def _update_table(romfs: Path, target_root: Path, sizes: dict[str, int],
                  dicts: ZstdDicts) -> str:
    try:
        table, source, raw = restbl_mod.load_table(romfs, dicts)
    except HookyError:
        return ""
    problems = table.check(sizes)
    if not problems:
        return ""
    changes = table.grow_for(sizes)
    payload = table.build()
    if source.suffix.lower() == ".zs":
        from .zs import frame_info
        try:
            dictionary_id, _ = frame_info(raw)
        except HookyError:
            dictionary_id = None
        if not dictionary_id and len(dicts):
            dictionary_id = next(iter(dicts.by_id))
        if not dictionary_id:
            return "⚠ table de tailles non mise à jour : dictionnaire absent"
        payload = dicts.compress(payload, dictionary_id)
    target = target_root / "System" / "Resource" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, payload)
    return (f"Table de tailles mise à jour : {source.name} "
            f"({len(changes)} entrée(s) agrandie(s)) — {len(problems)} fichier(s) "
            "dépassaient la réservation du jeu.")


# --------------------------------------------------------------------------- retour OBJ borné
def obj_to_bfres(bfres_bytes: bytes, obj_bytes: bytes, model: int = 0,
                 shape: int = 0) -> tuple[bytes, "bfres_import.ImportReport"]:
    """Réimporte un OBJ triangulé, avec les mêmes limites que l'atelier."""
    if model != 0 or shape != 0:
        raise HookyError("L'injection exige un seul modèle et une seule forme cible.")
    source = modelio.read_model("modele.obj", obj_bytes)
    if any(note.startswith("Polygones OBJ") for note in source.warnings):
        raise HookyError("Triangule l'OBJ avant l'injection : les polygones ne sont pas acceptés.")
    if source.material_slots > 1:
        raise HookyError("Plusieurs matériaux source ne peuvent pas être conservés dans une seule forme cible.")
    return bfres_safe.rewrite(bfres_bytes, source.meshes)


# --------------------------------------------------------------------------- état
def status(project: str | Path) -> list[str]:
    proj = Project(project).open()
    changed = [entry for entry in proj.manifest.entries if proj.changed(entry)]
    lines = [f"Projet : {proj.root}",
             f"Entrées : {len(proj.manifest.entries)} — "
             f"{len(changed)} modifiée(s)"]
    for entry in changed:
        lines.append(f"  • {entry.pc} → romfs/{entry.rel} ({entry.kind})")
    if proj.manifest.errors:
        lines.append("")
        lines += [f"⚠ {error}" for error in proj.manifest.errors[:10]]
    return lines
