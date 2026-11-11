#!/usr/bin/env python3
"""
Asset Explorer v3 - ouvrir / modifier / réempaqueter / convertir les assets de jeu Switch.

  - Explorateur romfs (à gauche) : choisis le dossier romfs, clique sur un fichier pour l'ouvrir
  - Dictionnaire zstd trouvé automatiquement (Pack/ZsDic.pack.zs)
  - .zs / SARC : décompression, navigation, extraction, remplacement, reconstruction
  - Patch : chercher/remplacer texte ou octets (même longueur) pour skins / références
  - Exporter en mod : <mod>/romfs/<même chemin>, recompressé (LayeredFS)
  - Convertir en .blend : lance Blender en arrière-plan (obj / glb / fbx, et BFRES via un add-on d'import)
  - Viewer 3D : affiche un .obj

Installation :  pip install zstandard
Lancement    :  python asset_explorer.py
"""
import glob
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import zstandard as zstd
except ImportError:
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror("Module manquant", "Installe-le avec :\n\npip install zstandard")
    sys.exit(1)

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
CONFIG = Path.home() / ".asset_explorer.json"
MAGICS = {
    b"SARC": "Archive SARC",
    b"FRES": "BFRES (modèle/anim)",
    b"BNTX": "BNTX (textures)",
    b"BNSH": "BNSH (shaders)",
    b"BY": "BYML",
    b"YB": "BYML",
}
BFRES_OPERATOR = "import_scene.nxbfres"  # opérateur d'import de l'add-on BFRES (modifiable dans la config)

BG, PANEL, PANEL2, FG, MUTED, ACC = "#1b1d23", "#23262e", "#2a2e37", "#e6e8ec", "#8b93a1", "#4c8dff"

# Script exécuté DANS Blender (blender -b --python to_blend.py -- src out addon_dir operator)
BLEND_SCRIPT = r'''
import bpy, sys, os, importlib, traceback
try:
    src, out, addon_dir, op = sys.argv[sys.argv.index("--") + 1:][:4]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(src)[1].lower()
    if addon_dir:
        sys.path.insert(0, os.path.dirname(addon_dir))
        mod = importlib.import_module(os.path.basename(addon_dir))
        mod.register()
    if ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=src)
        else:
            bpy.ops.import_scene.obj(filepath=src)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=src)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=src)
    else:
        cat, name = op.split(".")
        getattr(getattr(bpy.ops, cat), name)(filepath=src)
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print("CONVERSION_OK")
except Exception:
    traceback.print_exc()
    sys.exit(1)
'''


def kind(data: bytes) -> str:
    if data[:4] == ZSTD_MAGIC:
        return "Zstd (.zs)"
    for m, n in MAGICS.items():
        if data.startswith(m):
            return n
    return "?"


# --------------------------------------------------------------------------- SARC
def sarc_parse(data: bytes):
    if data[:4] != b"SARC":
        raise ValueError("Ce n'est pas une archive SARC")
    e = "<" if data[6:8] == b"\xff\xfe" else ">"
    hdr = struct.unpack_from(e + "H", data, 4)[0]
    data_off = struct.unpack_from(e + "I", data, 0x0C)[0]
    count = struct.unpack_from(e + "H", data, hdr + 6)[0]
    nodes_off = hdr + 0x0C
    names_off = nodes_off + 16 * count + 8
    entries = []
    for i in range(count):
        h, attr, start, end = struct.unpack_from(e + "IIII", data, nodes_off + 16 * i)
        if attr >> 24:
            p = names_off + (attr & 0xFFFFFF) * 4
            name = data[p:data.index(b"\0", p)].decode("utf-8", "replace")
        else:
            name = f"{h:08x}.bin"
        entries.append([name, data[data_off + start:data_off + end]])
    return entries, e


def _align(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def sarc_build(entries, e="<", mult=0x65) -> bytes:
    def h(name):
        x = 0
        for c in name.encode():
            x = (x * mult + c) & 0xFFFFFFFF
        return x

    items = sorted(entries, key=lambda it: h(it[0]))
    names = b""
    nodes, aligns, pos = [], [], 0
    for name, blob in items:
        al = 0x1000 if blob[:4] in (b"FRES", b"BNTX", b"BNSH") else 4
        noff = len(names) // 4
        names += name.encode() + b"\0"
        names += b"\0" * (-len(names) % 4)
        pos = _align(pos, al)
        nodes.append((h(name), 0x01000000 | noff, pos, pos + len(blob)))
        pos += len(blob)
        aligns.append(al)
    data_off = _align(0x14 + 0xC + 16 * len(nodes) + 8 + len(names), max(aligns, default=4))
    body = bytearray(pos)
    for (_, blob), (_, _, s, en) in zip(items, nodes):
        body[s:en] = blob
    out = b"SARC" + struct.pack(e + "H", 0x14)
    out += b"\xff\xfe" if e == "<" else b"\xfe\xff"
    out += struct.pack(e + "II", data_off + pos, data_off) + struct.pack(e + "HH", 0x100, 0)
    out += b"SFAT" + struct.pack(e + "HHI", 0xC, len(nodes), mult)
    for n in nodes:
        out += struct.pack(e + "IIII", *n)
    out += b"SFNT" + struct.pack(e + "HH", 8, 0) + names
    out += b"\0" * (data_off - len(out)) + bytes(body)
    return out


# --------------------------------------------------------------------------- Zstd + dictionnaires
class Dicts:
    def __init__(self):
        self.by_id = {}

    def add(self, name: str, raw: bytes):
        d = zstd.ZstdCompressionDict(raw)
        self.by_id[d.dict_id()] = (name, d)

    def load_file(self, path) -> int:
        raw = Path(path).read_bytes()
        if raw[:4] == ZSTD_MAGIC:  # ZsDic.pack.zs : compressé sans dictionnaire
            raw = self.decompress(raw)
        if raw[:4] == b"SARC":
            n = 0
            for name, blob in sarc_parse(raw)[0]:
                if name.endswith(".zsdic"):
                    self.add(name, blob)
                    n += 1
            return n
        self.add(Path(path).name, raw)
        return 1

    def _get(self, did: int):
        if not did:
            return None
        if did not in self.by_id:
            raise ValueError(
                f"Dictionnaire zstd ID {did} introuvable.\n"
                "Définis ton dossier romfs (bouton « Choisir romfs… ») : "
                "ZsDic.pack.zs sera trouvé automatiquement."
            )
        return self.by_id[did][1]

    def decompress(self, raw: bytes) -> bytes:
        d = self._get(zstd.get_frame_parameters(raw).dict_id)
        dec = zstd.ZstdDecompressor(dict_data=d) if d else zstd.ZstdDecompressor()
        return dec.decompressobj().decompress(raw)

    def compress(self, data: bytes, did: int, level: int = 16) -> bytes:
        d = self._get(did)
        comp = zstd.ZstdCompressor(level=level, dict_data=d) if d else zstd.ZstdCompressor(level=level)
        return comp.compress(data)


def find_dict_files(root: Path):
    """Cherche ZsDic.pack(.zs) / *.zsdic dans un dossier romfs (ou son parent). Retourne (fichiers, racine romfs)."""
    for base in (root, root / "romfs"):
        p = base / "Pack" / "ZsDic.pack.zs"
        if p.exists():
            return [p], base
    hits = []
    for dp, dn, fn in os.walk(root):
        depth = len(Path(dp).relative_to(root).parts)
        if depth >= 4:
            dn[:] = []
        for f in fn:
            if f.lower().startswith("zsdic.pack") or f.lower().endswith(".zsdic"):
                hits.append(Path(dp) / f)
        if hits:
            break
    return hits, (hits[0].parent.parent if hits else root)


class Level:
    """Un fichier ouvert : éventuellement compressé, éventuellement un SARC."""

    def __init__(self, name: str, raw: bytes, dicts: Dicts):
        self.name = name
        self.compressed = raw[:4] == ZSTD_MAGIC
        self.dict_id = zstd.get_frame_parameters(raw).dict_id if self.compressed else 0
        self.data = dicts.decompress(raw) if self.compressed else raw
        self.entries, self.endian = None, "<"
        self.modified = False
        self.parent_idx = None
        self.rel = name  # chemin relatif dans romfs (racine uniquement)
        if self.data[:4] == b"SARC":
            self.entries, self.endian = sarc_parse(self.data)

    def pack(self, dicts: Dicts) -> bytes:
        if self.entries is not None and self.modified:
            self.data = sarc_build(self.entries, self.endian)
        return dicts.compress(self.data, self.dict_id) if self.compressed else self.data


def describe(name: str, data: bytes) -> str:
    out = [f"{name}", f"Taille : {len(data):,} octets", f"Type   : {kind(data)}", "",
           "--- Hex (512 premiers octets) ---"]
    for i in range(0, min(len(data), 512), 16):
        ch = data[i:i + 16]
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in ch)
        out.append(f"{i:08x}  {ch.hex(' '):<47}  {asc}")
    strings = re.findall(rb"[\x20-\x7e]{6,}", data[:2_000_000])[:80]
    out += ["", "--- Chaînes ASCII ---"] + [s.decode() for s in strings]
    return "\n".join(out)


# --------------------------------------------------------------------------- Viewer 3D (OBJ)
class Mesh:
    def __init__(self, verts, faces):
        self.verts, self.faces = verts, faces


def load_obj(path) -> Mesh:
    verts, faces = [], []
    for line in Path(path).read_text(errors="ignore").splitlines():
        if line.startswith("v "):
            verts.append(tuple(float(x) for x in line.split()[1:4]))
        elif line.startswith("f "):
            idx = []
            for tok in line.split()[1:]:
                i = int(tok.split("/")[0])
                idx.append(i - 1 if i > 0 else len(verts) + i)
            if len(idx) >= 3:
                faces.append(tuple(idx))
    if not verts or not faces:
        raise ValueError("OBJ vide ou illisible")
    return Mesh(verts, faces)


class Viewer(tk.Toplevel):
    def __init__(self, master, mesh: Mesh, title="Viewer 3D"):
        super().__init__(master)
        self.title(title)
        self.geometry("820x640")
        self.configure(bg=BG)
        self.mesh = mesh
        xs, ys, zs = zip(*mesh.verts)
        self.c = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
        self.r = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 2 or 1.0
        self.yaw, self.pitch, self.zoom, self.wire = 0.6, -0.35, 1.0, False
        self.cv = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        tk.Label(self, bg=BG, fg=MUTED, anchor="w",
                 text=f"{len(mesh.verts):,} sommets · {len(mesh.faces):,} faces   |   glisser = tourner · molette = zoom · W = fil de fer"
                 ).pack(fill="x")
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonPress-1>", lambda e: setattr(self, "_p", (e.x, e.y)))
        self.cv.bind("<MouseWheel>", lambda e: self._zoom(1.1 if e.delta > 0 else 0.9))
        self.cv.bind("<Button-4>", lambda e: self._zoom(1.1))
        self.cv.bind("<Button-5>", lambda e: self._zoom(0.9))
        self.cv.bind("<Configure>", lambda e: self.draw())
        self.bind("w", lambda e: self._toggle_wire())
        self._p = (0, 0)
        self.draw()

    def _toggle_wire(self):
        self.wire = not self.wire
        self.draw()

    def _zoom(self, f):
        self.zoom *= f
        self.draw()

    def _drag(self, e):
        self.yaw += (e.x - self._p[0]) * 0.01
        self.pitch += (e.y - self._p[1]) * 0.01
        self._p = (e.x, e.y)
        self.draw()

    def draw(self):
        cv = self.cv
        cv.delete("all")
        w, h = max(cv.winfo_width(), 50), max(cv.winfo_height(), 50)
        s = min(w, h) / 2.3 / self.r * self.zoom
        cy, sy, cp, sp = math.cos(self.yaw), math.sin(self.yaw), math.cos(self.pitch), math.sin(self.pitch)
        pts = []
        for x, y, z in self.mesh.verts:
            x, y, z = x - self.c[0], y - self.c[1], z - self.c[2]
            x, z = x * cy + z * sy, -x * sy + z * cy
            y, z = y * cp - z * sp, y * sp + z * cp
            pts.append((w / 2 + x * s, h / 2 - y * s, z))
        faces = self.mesh.faces
        step = max(1, len(faces) // 20000)  # limite de dessin pour rester fluide
        order = sorted(faces[::step], key=lambda f: sum(pts[i][2] for i in f) / len(f), reverse=True)
        for f in order:
            poly = [c for i in f for c in pts[i][:2]]
            if self.wire:
                cv.create_polygon(poly, fill="", outline="#6aa1ff")
                continue
            (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = pts[f[0]], pts[f[1]], pts[f[2]]
            a, b = (x1 - x0, y1 - y0, z1 - z0), (x2 - x0, y2 - y0, z2 - z0)
            nx, ny, nz = a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            lit = abs((nx * 0.3 + ny * -0.5 + nz * -0.8) / ln)
            g = int(60 + 170 * min(1.0, lit))
            cv.create_polygon(poly, fill=f"#{g:02x}{g:02x}{min(255, g + 12):02x}", outline="")


# --------------------------------------------------------------------------- GUI
def apply_theme(root):
    s = ttk.Style(root)
    s.theme_use("clam")
    root.configure(bg=BG)
    s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL2,
                lightcolor=BG, darkcolor=BG, troughcolor=BG, font=("Segoe UI", 10))
    s.configure("TButton", background=PANEL2, foreground=FG, padding=(10, 6), borderwidth=0, focuscolor=PANEL2)
    s.map("TButton", background=[("active", "#353a46")])
    s.configure("Accent.TButton", background=ACC, foreground="white")
    s.map("Accent.TButton", background=[("active", "#6aa1ff")])
    s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=26, borderwidth=0)
    s.configure("Treeview.Heading", background=PANEL2, foreground=FG, relief="flat", font=("Segoe UI", 10, "bold"))
    s.map("Treeview", background=[("selected", ACC)], foreground=[("selected", "white")])
    s.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG, padding=5)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("Status.TLabel", background=PANEL, foreground=MUTED, padding=(10, 5))
    s.configure("TCheckbutton", background=BG, foreground=FG)
    s.configure("Vertical.TScrollbar", background=PANEL2, arrowcolor=FG)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Asset Explorer")
        self.geometry("1500x800")
        apply_theme(self)
        self.dicts = Dicts()
        self.dict_paths = []
        self.romfs = None
        self.blender = ""
        self.addon_dir = ""
        self.stack = []
        self.fs_paths = {}
        self.filter_var = tk.StringVar()
        self.fs_search_var = tk.StringVar()
        self._load_config()

        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        for text, cmd, style in [
            ("Choisir romfs…", self.set_romfs, "Accent.TButton"),
            ("Ouvrir…", self.open_file, "TButton"),
            ("← Retour", self.back, "TButton"),
            ("Extraire", self.extract_sel, "TButton"),
            ("Tout extraire", self.extract_all, "TButton"),
            ("Remplacer…", self.replace_sel, "TButton"),
            ("Patch…", self.patch_dialog, "TButton"),
            ("Viewer 3D", self.open_viewer, "TButton"),
            ("Convertir en .blend", self.convert_blend, "Accent.TButton"),
            ("Export BFRES", self.export_blender, "TButton"),
            ("Enregistrer…", self.save, "TButton"),
            ("Exporter en mod", self.export_mod, "Accent.TButton"),
        ]:
            ttk.Button(top, text=text, command=cmd, style=style).pack(side="left", padx=(0, 6))

        sub = ttk.Frame(self, padding=(10, 0, 10, 8))
        sub.pack(fill="x")
        self.romfs_lbl = ttk.Label(sub, style="Muted.TLabel")
        self.romfs_lbl.pack(side="left")
        ttk.Entry(sub, textvariable=self.filter_var, width=28).pack(side="right")
        ttk.Label(sub, text="Filtrer l'archive :", style="Muted.TLabel").pack(side="right", padx=6)
        self.filter_var.trace_add("write", lambda *a: self.refresh(keep_preview=True))

        pan = ttk.PanedWindow(self, orient="horizontal")
        pan.pack(fill="both", expand=True, padx=10)

        # --- explorateur romfs
        fsf = ttk.Frame(pan)
        sbar = ttk.Frame(fsf)
        sbar.pack(fill="x", pady=(0, 4))
        e = ttk.Entry(sbar, textvariable=self.fs_search_var)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.fs_search())
        ttk.Button(sbar, text="Chercher", command=self.fs_search).pack(side="left", padx=(4, 0))
        ttk.Button(sbar, text="✕", width=3, command=self.fs_reset).pack(side="left", padx=(4, 0))
        self.fs = ttk.Treeview(fsf, show="tree", selectmode="browse")
        fsb = ttk.Scrollbar(fsf, command=self.fs.yview)
        self.fs.configure(yscrollcommand=fsb.set)
        self.fs.pack(side="left", fill="both", expand=True)
        fsb.pack(side="right", fill="y")
        self.fs.bind("<<TreeviewOpen>>", self.fs_expand)
        self.fs.bind("<<TreeviewSelect>>", self.fs_click)
        pan.add(fsf, weight=2)

        # --- contenu de l'archive
        left = ttk.Frame(pan)
        self.tree = ttk.Treeview(left, columns=("name", "size", "type"), show="headings", selectmode="extended")
        for c, t, w in [("name", "Nom", 300), ("size", "Taille", 80), ("type", "Type", 130)]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self.on_open_entry)
        pan.add(left, weight=2)

        self.text = tk.Text(pan, font=("Consolas", 10), wrap="none", bg=PANEL, fg=FG, insertbackground=FG,
                            relief="flat", padx=10, pady=8, selectbackground=ACC)
        pan.add(self.text, weight=2)

        self.status = ttk.Label(self, anchor="w", style="Status.TLabel")
        self.status.pack(fill="x", pady=(8, 0))
        self.show("1. « Choisir romfs… » : dossier romfs de ton dump (dictionnaire trouvé tout seul).\n"
                  "2. Clique sur un fichier à gauche pour l'ouvrir.\n"
                  "3. Double-clic sur un .zs / .pack imbriqué pour entrer dedans.\n"
                  "4. Modifie (Remplacer / Patch), puis « Exporter en mod » ou « Convertir en .blend ».")
        if self.romfs and self.romfs.exists():
            self.fs_populate_root()
        self._update_status()

    # ---- config / dictionnaires
    def _load_config(self):
        try:
            cfg = json.loads(CONFIG.read_text())
        except Exception:
            cfg = {}
        for p in cfg.get("dicts", []):
            try:
                self.dicts.load_file(p)
                self.dict_paths.append(p)
            except Exception:
                pass
        self.romfs = Path(cfg["romfs"]) if cfg.get("romfs") else None
        self.blender = cfg.get("blender", "")
        self.addon_dir = cfg.get("addon_dir", "")

    def _save_config(self):
        try:
            CONFIG.write_text(json.dumps({"dicts": self.dict_paths, "romfs": str(self.romfs or ""),
                                          "blender": self.blender, "addon_dir": self.addon_dir}))
        except Exception:
            pass

    def guard(fn):
        def wrap(self, *a, **k):
            try:
                return fn(self, *a, **k)
            except Exception as ex:
                messagebox.showerror("Erreur", str(ex))
        return wrap

    @guard
    def set_romfs(self):
        d = filedialog.askdirectory(title="Dossier romfs (ou son parent)")
        if not d:
            return
        files, root = find_dict_files(Path(d))
        if not files:
            messagebox.showwarning("Dictionnaire", "Aucun ZsDic.pack.zs / .zsdic trouvé dans ce dossier.\n"
                                   "Le dossier est quand même ouvert.")
        n = 0
        for f in files:
            n += self.dicts.load_file(f)
            if str(f) not in self.dict_paths:
                self.dict_paths.append(str(f))
        self.romfs = root
        self._save_config()
        self.fs_populate_root()
        self._update_status()
        self.status.config(text=f"romfs : {root}  ·  {n} dictionnaire(s) chargé(s)")

    # ---- explorateur romfs
    def _fs_add(self, parent, path: Path):
        is_dir = path.is_dir()
        iid = self.fs.insert(parent, "end", text=("📁 " if is_dir else "   ") + path.name)
        self.fs_paths[iid] = path
        if is_dir:
            self.fs.insert(iid, "end", text="…")  # marqueur : contenu chargé à l'ouverture
        return iid

    def _fs_list(self, path: Path):
        try:
            return sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return []

    def fs_populate_root(self):
        self.fs.delete(*self.fs.get_children())
        self.fs_paths.clear()
        if self.romfs and self.romfs.exists():
            for p in self._fs_list(self.romfs):
                self._fs_add("", p)

    def fs_expand(self, _=None):
        iid = self.fs.focus()
        kids = self.fs.get_children(iid)
        if iid in self.fs_paths and len(kids) == 1 and kids[0] not in self.fs_paths:
            self.fs.delete(kids[0])
            for p in self._fs_list(self.fs_paths[iid]):
                self._fs_add(iid, p)

    def fs_click(self, _=None):
        sel = self.fs.selection()
        if not sel:
            return
        p = self.fs_paths.get(sel[0])
        if p and p.is_file():
            self.open_path(p, quiet=True)

    def fs_search(self):
        q = self.fs_search_var.get().strip().lower()
        if not q or not self.romfs:
            return
        self.fs.delete(*self.fs.get_children())
        self.fs_paths.clear()
        n = 0
        for dp, _, fn in os.walk(self.romfs):
            for f in fn:
                if q in f.lower():
                    p = Path(dp) / f
                    iid = self.fs.insert("", "end", text="   " + p.relative_to(self.romfs).as_posix())
                    self.fs_paths[iid] = p
                    n += 1
                    if n >= 1000:
                        break
            if n >= 1000:
                break
        self.status.config(text=f"{n} résultat(s) pour « {q} »" + (" (limité à 1000)" if n >= 1000 else ""))

    def fs_reset(self):
        self.fs_search_var.set("")
        self.fs_populate_root()

    # ---- helpers
    def show(self, s: str):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", s)

    def _update_status(self):
        self.romfs_lbl.config(text=f"romfs : {self.romfs or 'non défini'}   ·   dictionnaires : {len(self.dicts.by_id)}")
        if self.stack:
            lv = self.stack[-1]
            info = " › ".join(l.name for l in self.stack)
            if lv.compressed:
                info += f"   [zstd · dict ID {lv.dict_id}]"
            if lv.modified:
                info += "   [modifié]"
            self.status.config(text=info)

    def refresh(self, keep_preview=False):
        if not self.stack:
            return
        lv = self.stack[-1]
        self.tree.delete(*self.tree.get_children())
        if lv.entries is not None:
            q = self.filter_var.get().lower()
            for i, (n, b) in enumerate(lv.entries):
                if q in n.lower():
                    self.tree.insert("", "end", iid=str(i), values=(n, f"{len(b):,}", kind(b)))
            if not keep_preview:
                self.show(f"{lv.name} — {len(lv.entries)} fichiers\n\nSélectionne un fichier pour l'aperçu.\nDouble-clic : ouvrir un .zs / SARC imbriqué.")
        elif not keep_preview:
            self.show(describe(lv.name, lv.data))
        self._update_status()

    def _sel(self):
        lv = self.stack[-1] if self.stack else None
        if not lv or lv.entries is None:
            return []
        return [int(i) for i in self.tree.selection()]

    # ---- ouverture / navigation
    def open_path(self, p: Path, quiet=False):
        try:
            lv = Level(p.name, p.read_bytes(), self.dicts)
        except Exception as ex:
            if quiet:  # clic dans l'explorateur : message dans l'aperçu plutôt qu'une boîte de dialogue
                self.show(f"{p.name}\n\n{ex}")
                return
            raise
        lv.rel = p.name
        if self.romfs:
            try:
                lv.rel = p.resolve().relative_to(self.romfs.resolve()).as_posix()
            except ValueError:
                pass
        self.stack = [lv]
        self.filter_var.set("")
        self.refresh()

    @guard
    def open_file(self):
        p = filedialog.askopenfilename(title="Ouvrir un asset", initialdir=str(self.romfs) if self.romfs else None)
        if p:
            self.open_path(Path(p))

    def on_select(self, _=None):
        sel = self._sel()
        if len(sel) == 1:
            n, b = self.stack[-1].entries[sel[0]]
            self.show(describe(n, b))

    @guard
    def on_open_entry(self, _=None):
        sel = self._sel()
        if len(sel) != 1:
            return
        n, b = self.stack[-1].entries[sel[0]]
        if kind(b) not in ("Zstd (.zs)", "Archive SARC"):
            return
        child = Level(n, b, self.dicts)
        child.parent_idx = sel[0]
        self.stack.append(child)
        self.filter_var.set("")
        self.refresh()

    def _pop(self):
        child = self.stack.pop()
        if child.modified:
            parent = self.stack[-1]
            parent.entries[child.parent_idx][1] = child.pack(self.dicts)
            parent.modified = True

    @guard
    def back(self):
        if len(self.stack) > 1:
            self._pop()
            self.refresh()

    def _collapse(self):
        while len(self.stack) > 1:
            self._pop()

    # ---- extraction / remplacement / patch
    def _write_entries(self, base: Path, entries, decompress=False):
        n = 0
        for name, blob in entries:
            if decompress and blob[:4] == ZSTD_MAGIC:
                try:
                    blob = self.dicts.decompress(blob)
                except Exception:
                    continue
                name = name[:-3] if name.endswith(".zs") else name
            p = (base / name).resolve()
            if base.resolve() not in p.parents:
                continue  # protection ../
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(blob)
            n += 1
        return n

    @guard
    def extract_sel(self):
        sel = self._sel()
        d = filedialog.askdirectory(title="Dossier de destination") if sel else None
        if d:
            n = self._write_entries(Path(d), [self.stack[-1].entries[i] for i in sel])
            self.status.config(text=f"{n} fichier(s) extrait(s) dans {d}")

    @guard
    def extract_all(self):
        lv = self.stack[-1] if self.stack else None
        if not lv or lv.entries is None:
            return
        d = filedialog.askdirectory(title="Dossier de destination")
        if d:
            n = self._write_entries(Path(d), lv.entries)
            self.status.config(text=f"{n} fichier(s) extrait(s) dans {d}")

    @guard
    def replace_sel(self):
        sel = self._sel()
        if len(sel) != 1:
            messagebox.showinfo("Remplacer", "Sélectionne un seul fichier dans l'archive.")
            return
        p = filedialog.askopenfilename(title="Fichier de remplacement")
        if p:
            lv = self.stack[-1]
            lv.entries[sel[0]][1] = Path(p).read_bytes()
            lv.modified = True
            self.refresh()

    @guard
    def patch_dialog(self):
        if not self.stack:
            return
        lv = self.stack[-1]
        win = tk.Toplevel(self)
        win.title("Patch : chercher / remplacer")
        win.configure(bg=BG)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=14)
        f.pack()
        v_find, v_rep, v_hex = tk.StringVar(), tk.StringVar(), tk.BooleanVar()
        ttk.Label(f, text="Chercher").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=v_find, width=48).grid(row=0, column=1, pady=3)
        ttk.Label(f, text="Remplacer par").grid(row=1, column=0, sticky="w")
        ttk.Entry(f, textvariable=v_rep, width=48).grid(row=1, column=1, pady=3)
        ttk.Checkbutton(f, text="Octets hexadécimaux (ex. 46 52 45 53)", variable=v_hex).grid(row=2, column=1, sticky="w")
        ttk.Label(f, style="Muted.TLabel", wraplength=420,
                  text="Même longueur obligatoire (la structure du fichier ne bouge pas). Idéal pour changer un nom de texture/matériau. "
                       "S'applique à la sélection, sinon à tout le fichier ouvert. Les entrées encore en .zs sont ignorées : double-clique pour les ouvrir d'abord."
                  ).grid(row=3, column=0, columnspan=2, sticky="w", pady=8)

        def conv(s):
            return bytes.fromhex(s.replace(" ", "")) if v_hex.get() else s.encode()

        def apply():
            try:
                a, b = conv(v_find.get()), conv(v_rep.get())
                if not a or len(a) != len(b):
                    raise ValueError(f"Longueurs différentes ({len(a)} ≠ {len(b)} octets).")
                total = 0
                if lv.entries is None:
                    total = lv.data.count(a)
                    lv.data = lv.data.replace(a, b)
                else:
                    idx = self._sel() or range(len(lv.entries))
                    for i in idx:
                        blob = lv.entries[i][1]
                        if blob[:4] == ZSTD_MAGIC:
                            continue
                        c = blob.count(a)
                        if c:
                            lv.entries[i][1] = blob.replace(a, b)
                            total += c
                if total:
                    lv.modified = True
                self.refresh()
                self.status.config(text=f"Patch : {total} occurrence(s) remplacée(s)")
                win.destroy()
            except Exception as ex:
                messagebox.showerror("Patch", str(ex), parent=win)

        ttk.Button(f, text="Appliquer", style="Accent.TButton", command=apply).grid(row=4, column=1, sticky="e")

    # ---- viewer / Blender
    @guard
    def open_viewer(self):
        sel = self._sel()
        if len(sel) == 1 and self.stack[-1].entries[sel[0]][0].lower().endswith(".obj"):
            n, b = self.stack[-1].entries[sel[0]]
            tmp = Path(tempfile.gettempdir()) / "ae_view.obj"
            tmp.write_bytes(b)
            Viewer(self, load_obj(tmp), n)
            return
        p = filedialog.askopenfilename(title="Ouvrir un .obj", filetypes=[("OBJ", "*.obj")])
        if p:
            Viewer(self, load_obj(p), Path(p).name)

    def _find_blender(self) -> str:
        if self.blender and Path(self.blender).exists():
            return self.blender
        cands = [shutil.which("blender")]
        for pat in (r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
                    "/Applications/Blender.app/Contents/MacOS/Blender", "/usr/bin/blender"):
            cands += glob.glob(pat)
        for c in cands:
            if c and Path(c).exists():
                self.blender = c
                self._save_config()
                return c
        p = filedialog.askopenfilename(title="Chemin de blender.exe (Blender introuvable)")
        if p:
            self.blender = p
            self._save_config()
        return p

    @guard
    def convert_blend(self):
        if not self.stack:
            messagebox.showinfo("Convertir", "Ouvre d'abord un modèle.")
            return
        lv = self.stack[-1]
        tmp = Path(tempfile.mkdtemp(prefix="ae_blend_"))
        if lv.entries is None:
            name = lv.name[:-3] if lv.name.endswith(".zs") else lv.name
            (tmp / name).write_bytes(lv.data)
            src = tmp / name
        else:
            sel = self._sel()
            if len(sel) != 1:
                messagebox.showinfo("Convertir", "Sélectionne le modèle à convertir dans la liste.")
                return
            self._write_entries(tmp, lv.entries, decompress=True)  # tout l'archive : les .Tex voisins servent aux textures
            n, b = lv.entries[sel[0]]
            src = tmp / (n[:-3] if b[:4] == ZSTD_MAGIC and n.endswith(".zs") else n)
            if not src.exists():
                raise ValueError("Impossible de décompresser ce fichier (dictionnaire manquant ?).")
        ext = src.suffix.lower()
        if ext not in (".obj", ".glb", ".gltf", ".fbx", ".bfres", ".sbfres", ".fres"):
            raise ValueError(f"Format « {ext or src.name} » non convertible.\nFormats : .bfres, .obj, .glb/.gltf, .fbx.")
        if ext in (".bfres", ".sbfres", ".fres") and not self.addon_dir:
            if messagebox.askyesno("Add-on BFRES requis",
                                   "Blender ne lit pas le BFRES seul : il faut un add-on d'import BFRES "
                                   "compatible avec ta version de Blender.\n\nChoisir maintenant le dossier de l'add-on ?"):
                d = filedialog.askdirectory(title="Dossier de l'add-on BFRES (contient __init__.py)")
                if not d:
                    return
                self.addon_dir = d
                self._save_config()
            else:
                return
        exe = self._find_blender()
        if not exe:
            return
        out = filedialog.asksaveasfilename(title="Enregistrer le .blend", defaultextension=".blend",
                                           initialfile=src.stem + ".blend", filetypes=[("Blender", "*.blend")])
        if not out:
            return
        script = tmp / "to_blend.py"
        script.write_text(BLEND_SCRIPT)
        addon = self.addon_dir if ext in (".bfres", ".sbfres", ".fres") else ""
        self.status.config(text="Conversion Blender en cours…")
        self.config(cursor="watch")
        self.update()
        try:
            r = subprocess.run([exe, "-b", "--python", str(script), "--", str(src), out, addon, BFRES_OPERATOR],
                               capture_output=True, text=True, timeout=600,
                               creationflags=0x08000000 if os.name == "nt" else 0)
        finally:
            self.config(cursor="")
        log = (r.stdout + "\n" + r.stderr).strip()
        if "CONVERSION_OK" in r.stdout and Path(out).exists():
            self.status.config(text=f"Converti : {out}")
            messagebox.showinfo("Convertir en .blend", f"Fichier créé :\n{out}")
        else:
            self.status.config(text="Conversion échouée")
            messagebox.showerror("Conversion échouée",
                                 "Blender n'a pas produit de .blend.\n\n…" + log[-1500:] +
                                 "\n\nSi c'est un BFRES : l'add-on est peut-être incompatible avec cette version de Blender ou du jeu.")

    @guard
    def export_blender(self):
        if not self.stack:
            return
        lv = self.stack[-1]
        d = filedialog.askdirectory(title="Dossier de sortie")
        if not d:
            return
        base = Path(d)
        if lv.entries is None:
            name = lv.name[:-3] if lv.name.endswith(".zs") else lv.name
            (base / name).write_bytes(lv.data)
            n = 1
        else:
            keep = []
            for nme, blob in lv.entries:
                try:
                    raw = self.dicts.decompress(blob) if blob[:4] == ZSTD_MAGIC else blob
                except Exception:
                    continue
                if raw[:4] in (b"FRES", b"BNTX"):
                    keep.append((nme[:-3] if nme.endswith(".zs") else nme, raw))
            n = self._write_entries(base, keep)
        self.status.config(text=f"{n} fichier(s) BFRES/BNTX décompressé(s) dans {d}")

    # ---- sauvegarde / mod
    @guard
    def save(self):
        if not self.stack:
            return
        lv = self.stack[-1]
        p = filedialog.asksaveasfilename(initialfile=lv.name, title="Enregistrer (recompressé si besoin)")
        if p:
            Path(p).write_bytes(lv.pack(self.dicts))
            self.status.config(text=f"Enregistré : {p}")

    @guard
    def export_mod(self):
        if not self.stack:
            return
        self._collapse()
        root = self.stack[0]
        d = filedialog.askdirectory(title="Dossier du mod (le sous-dossier romfs/ sera créé dedans)")
        if not d:
            return
        out = Path(d) / "romfs" / root.rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(root.pack(self.dicts))
        self.refresh()
        warn = "" if self.romfs and root.rel != root.name else \
            "\n\n⚠ Le chemin relatif romfs est inconnu : vérifie que le fichier est au bon endroit dans romfs/."
        messagebox.showinfo("Mod exporté",
                            f"Écrit : {out}\n\nCopie le dossier romfs/ dans le dossier de mods de ton loader "
                            f"(LayeredFS / émulateur).{warn}")


if __name__ == "__main__":
    App().mainloop()
