"""Interface graphique Tkinter de HookyMod / Asset Explorer."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from . import bfres as bfres_mod
from . import bntx as bntx_mod
from . import identify
from . import nup as nup_mod
from . import restbl as restbl_mod
from .container import Document, Level, detect_kind
from .mesh import MeshData, export_obj, load_obj
from .sarc import SarcArchive
from .util import (FormatError, HookyError, ascii_strings, hexdump, human_size,
                   safe_child, write_atomic)
from .viewer import open_viewer
from .zs import DEFAULT_LEVEL, ZstdDicts, find_dict_files, frame_info, is_zstd, zstd_available

APP_NAME = "HookyMod · Asset Explorer"
BG, PANEL, PANEL2, FG, MUTED, ACC = ("#1b1d23", "#23262e", "#2a2e37",
                                    "#e6e8ec", "#8b93a1", "#4c8dff")
CONFIG_PATH = Path(os.environ.get("HOOKYMOD_CONFIG",
                                  Path.home() / ".config" / "hookymod" / "config.json"))
OLD_CONFIG_PATH = Path.home() / ".asset_explorer.json"

BLEND_SCRIPT = r'''
import bpy, sys, os, traceback
try:
    args = sys.argv[sys.argv.index("--") + 1:]
    src, out = args[0], args[1]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(src)[1].lower()
    if ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=src)
        else:
            bpy.ops.import_scene.obj(filepath=src)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=src)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=src)
    elif ext in (".bfres", ".sbfres"):
        # Nécessite un add-on BFRES installé dans Blender.
        bpy.ops.import_scene.nxbfres(filepath=src)
    else:
        raise RuntimeError("Format non géré par le script Blender : " + ext)
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print("CONVERSION_OK")
except Exception:
    traceback.print_exc()
    sys.exit(1)
'''

TEXT_EXT = {".txt", ".xml", ".json", ".yml", ".yaml", ".csv", ".md", ".ini",
            ".html", ".css", ".js", ".py", ".byml", ".aamp"}


def find_blender(configured: str = "") -> str:
    """Trouve un exécutable Blender (config, PATH, chemins standards)."""
    if configured and Path(configured).exists():
        return configured
    candidates = [shutil.which("blender")]
    patterns = [
        r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
        "/Applications/Blender.app/Contents/MacOS/Blender",
        "/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender",
    ]
    import glob
    for pattern in patterns:
        candidates += sorted(glob.glob(pattern), reverse=True)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


class App(tk.Tk):
    # ------------------------------------------------------------------ init
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1500x860")
        self.minsize(1000, 600)
        self.configure(bg=BG)
        self._apply_theme()

        # --- état
        self.config_data = self._load_config()
        self.dicts = ZstdDicts()
        self.romfs: Path | None = None
        self.documents: dict[str, Document] = {}
        self.current_key: str | None = None
        self.texture_file: bntx_mod.BntxFile | None = None
        self.bfres_file: bfres_mod.BfresFile | None = None
        self._photo = None
        self._preview_png: Path | None = None
        self._jobs: list[threading.Thread] = []
        self._busy = 0
        self.fs_paths: dict[str, Path] = {}

        self.filter_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Prêt.")
        self.romfs_var = tk.StringVar(value="romfs : non défini")

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status()
        self._bind_shortcuts()
        self._load_dicts_from_config()

        if self.config_data.get("romfs"):
            candidate = Path(self.config_data["romfs"])
            if candidate.is_dir():
                self.romfs = candidate
        self._refresh_romfs_label()
        self.fs_populate_root()
        self._show_welcome()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    @property
    def current(self) -> Document | None:
        if not self.current_key:
            return None
        return self.documents.get(self.current_key)

    # ------------------------------------------------------------------ thème
    def _apply_theme(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                        bordercolor=PANEL2, lightcolor=BG, darkcolor=BG,
                        troughcolor=BG, font=("Segoe UI", 10))
        style.configure("TButton", background=PANEL2, foreground=FG, padding=(10, 6),
                        borderwidth=0, focuscolor=PANEL2)
        style.map("TButton", background=[("active", "#353a46")])
        style.configure("Accent.TButton", background=ACC, foreground="white")
        style.map("Accent.TButton", background=[("active", "#6aa1ff")])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=25, borderwidth=0)
        style.configure("Treeview.Heading", background=PANEL2, foreground=FG,
                        relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", ACC)],
                  foreground=[("selected", "white")])
        style.configure("TEntry", fieldbackground=PANEL, foreground=FG,
                        insertcolor=FG, padding=5)
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL2,
                        foreground=FG, arrowcolor=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Panel.TLabel", background=PANEL, foreground=FG)
        style.configure("Status.TLabel", background=PANEL, foreground=MUTED,
                        padding=(10, 6))
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.configure("TLabelframe", background=BG, foreground=FG)
        style.configure("TLabelframe.Label", background=BG, foreground=MUTED)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL2, foreground=FG,
                        padding=(14, 7))
        style.map("TNotebook.Tab", background=[("selected", PANEL)],
                  foreground=[("selected", "white")])
        style.configure("Vertical.TScrollbar", background=PANEL2, arrowcolor=FG)

    # ------------------------------------------------------------------ menus
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        fichier = tk.Menu(menubar, tearoff=0)
        fichier.add_command(label="Choisir un dossier romfs…", command=self.set_romfs,
                            accelerator="Ctrl+R")
        fichier.add_command(label="Ouvrir un fichier…", command=self.open_file,
                            accelerator="Ctrl+O")
        fichier.add_command(label="Ouvrir un paquet .nup (Switch 2)…", command=self.open_nup)
        fichier.add_separator()
        fichier.add_command(label="Enregistrer sous…", command=self.save, accelerator="Ctrl+S")
        fichier.add_command(label="Exporter en mod…", command=self.export_mod,
                            accelerator="Ctrl+E")
        fichier.add_command(label="Voir les modifications…", command=self.show_modifications)
        fichier.add_separator()
        fichier.add_command(label="Quitter", command=self._on_close)
        menubar.add_cascade(label="Fichier", menu=fichier)

        edition = tk.Menu(menubar, tearoff=0)
        edition.add_command(label="Extraire la sélection…", command=self.extract_selection)
        edition.add_command(label="Tout extraire…", command=self.extract_all)
        edition.add_command(label="Remplacer la sélection…", command=self.replace_selection)
        edition.add_command(label="Remplacement par lot…", command=self.batch_replace)
        edition.add_separator()
        edition.add_command(label="Patch chercher/remplacer…", command=self.patch_dialog,
                            accelerator="Ctrl+P")
        menubar.add_cascade(label="Édition", menu=edition)

        outils = tk.Menu(menubar, tearoff=0)
        outils.add_command(label="Voir le modèle en 3D", command=self.view_model)
        outils.add_command(label="Exporter en OBJ…", command=self.export_model_obj)
        outils.add_command(label="Convertir en .blend…", command=self.convert_blend,
                           accelerator="Ctrl+B")
        outils.add_separator()
        outils.add_command(label="Exporter les textures en PNG…", command=self.export_textures)
        outils.add_command(label="Recharger les dictionnaires", command=self.reload_dicts)
        outils.add_command(label="Scanner un dossier (formats)…", command=self.scan_folder)
        outils.add_command(label="Paramètres…", command=self.settings_dialog)
        menubar.add_cascade(label="Outils", menu=outils)

        aide = tk.Menu(menubar, tearoff=0)
        aide.add_command(label="Raccourcis clavier", command=self.show_shortcuts)
        aide.add_command(label="À propos", command=self.show_about)
        menubar.add_cascade(label="Aide", menu=aide)
        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 10, 10, 4))
        bar.pack(fill="x")
        buttons = [
            ("Choisir romfs…", self.set_romfs, "Accent.TButton", "Ouvrir un dump romfs"),
            ("Ouvrir…", self.open_file, "TButton", "Ouvrir un asset (Ctrl+O)"),
            ("← Retour", self.back, "TButton", "Remonter d'un niveau"),
            ("Extraire", self.extract_selection, "TButton", "Extraire les fichiers sélectionnés"),
            ("Remplacer…", self.replace_selection, "TButton", "Remplacer un fichier de l'archive"),
            ("Patch…", self.patch_dialog, "TButton", "Chercher/remplacer des octets ou du texte"),
            ("Viewer 3D", self.view_model, "TButton", "Prévisualiser le modèle"),
            (".blend", self.convert_blend, "Accent.TButton", "Convertir en fichier Blender"),
            ("Enregistrer…", self.save, "TButton", "Enregistrer le fichier courant (Ctrl+S)"),
            ("Exporter en mod", self.export_mod, "Accent.TButton",
             "Créer romfs/… pour LayeredFS (Ctrl+E)"),
        ]
        for text, command, style, tooltip in buttons:
            button = ttk.Button(bar, text=text, command=command, style=style)
            button.pack(side="left", padx=(0, 6))
            self._tooltip(button, tooltip)

        sub = ttk.Frame(self, padding=(10, 0, 10, 6))
        sub.pack(fill="x")
        ttk.Label(sub, textvariable=self.romfs_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(sub, text="⚙", width=3, command=self.settings_dialog).pack(side="right",
                                                                              padx=(6, 0))
        ttk.Entry(sub, textvariable=self.filter_var, width=26).pack(side="right")
        ttk.Label(sub, text="Filtrer les fichiers :", style="Muted.TLabel").pack(side="right",
                                                                                padx=6)
        self.filter_var.trace_add("write", lambda *a: self._refresh_entries())

    def _build_body(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        # --- 1. explorateur romfs
        left = ttk.Frame(paned)
        search_bar = ttk.Frame(left)
        search_bar.pack(fill="x", pady=(0, 4))
        self.search_entry = ttk.Entry(search_bar, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<Return>", lambda e: self.fs_search())
        ttk.Button(search_bar, text="Chercher", command=self.fs_search).pack(side="left", padx=(4, 0))
        ttk.Button(search_bar, text="✕", width=3, command=self.fs_reset).pack(side="left", padx=(4, 0))
        self.fs = ttk.Treeview(left, show="tree", selectmode="browse")
        fs_scroll = ttk.Scrollbar(left, command=self.fs.yview)
        self.fs.configure(yscrollcommand=fs_scroll.set)
        self.fs.pack(side="left", fill="both", expand=True)
        fs_scroll.pack(side="right", fill="y")
        self.fs.bind("<<TreeviewOpen>>", self.fs_expand)
        self.fs.bind("<<TreeviewSelect>>", self.fs_select)
        paned.add(left, weight=2)

        # --- 2. contenu du fichier courant
        middle = ttk.Frame(paned)
        self.header_var = tk.StringVar(value="Aucun fichier ouvert")
        ttk.Label(middle, textvariable=self.header_var, style="Muted.TLabel").pack(
            fill="x", pady=(0, 4))
        self.entry_tree = ttk.Treeview(middle, columns=("name", "size", "type"),
                                       show="headings", selectmode="extended")
        for column, title, width in (("name", "Nom", 300), ("size", "Taille", 90),
                                     ("type", "Type", 150)):
            self.entry_tree.heading(column, text=title)
            self.entry_tree.column(column, width=width, anchor="w")
        scroll = ttk.Scrollbar(middle, command=self.entry_tree.yview)
        self.entry_tree.configure(yscrollcommand=scroll.set)
        self.entry_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.entry_tree.bind("<<TreeviewSelect>>", self.on_entry_select)
        self.entry_tree.bind("<Double-1>", self.on_entry_open)
        self.entry_tree.bind("<Return>", self.on_entry_open)
        paned.add(middle, weight=3)

        # --- 3. détails / textures / modèle
        self.notebook = ttk.Notebook(paned)
        self.preview = tk.Text(self.notebook, font=("Consolas", 10), wrap="none",
                               bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                               padx=10, pady=8, selectbackground=ACC)
        preview_scroll = ttk.Scrollbar(self.preview, command=self.preview.yview)
        self.preview.configure(yscrollcommand=preview_scroll.set)
        self.preview.pack(side="left", fill="both", expand=True)
        preview_scroll.pack(side="right", fill="y")
        self.notebook.add(self.preview, text="Aperçu")
        self._build_texture_tab()
        self._build_model_tab()
        paned.add(self.notebook, weight=4)

    def _build_texture_tab(self) -> None:
        self.texture_tab = ttk.Frame(self.notebook, padding=6)
        top = ttk.Frame(self.texture_tab)
        top.pack(fill="x")
        self.texture_info_var = tk.StringVar(value="Ouvre un fichier BNTX pour voir ses textures.")
        ttk.Label(top, textvariable=self.texture_info_var, style="Panel.TLabel").pack(
            side="left", fill="x", expand=True)
        ttk.Label(top, text="Mip", style="Panel.TLabel").pack(side="left", padx=(6, 2))
        self.mip_var = tk.StringVar(value="0")
        self.mip_box = ttk.Combobox(top, textvariable=self.mip_var, width=4, values=["0"],
                                    state="readonly")
        self.mip_box.pack(side="left")
        self.mip_box.bind("<<ComboboxSelected>>", lambda e: self._redraw_texture())
        ttk.Button(top, text="PNG", width=6, command=self.export_selected_texture).pack(
            side="left", padx=(6, 0))

        body = ttk.Frame(self.texture_tab)
        body.pack(fill="both", expand=True, pady=(6, 0))
        self.texture_tree = ttk.Treeview(body, columns=("name", "size", "format"),
                                         show="headings", selectmode="browse")
        for column, title, width in (("name", "Texture", 180), ("size", "Taille", 90),
                                     ("format", "Format", 130)):
            self.texture_tree.heading(column, text=title)
            self.texture_tree.column(column, width=width, anchor="w")
        tscroll = ttk.Scrollbar(body, command=self.texture_tree.yview)
        self.texture_tree.configure(yscrollcommand=tscroll.set)
        self.texture_tree.pack(side="left", fill="y")
        tscroll.pack(side="left", fill="y")
        self.texture_tree.bind("<<TreeviewSelect>>", lambda e: self._redraw_texture())

        canvas_frame = ttk.Frame(body)
        canvas_frame.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.texture_canvas = tk.Canvas(canvas_frame, bg="#111318", highlightthickness=0)
        hscroll = ttk.Scrollbar(canvas_frame, orient="horizontal",
                                command=self.texture_canvas.xview)
        vscroll = ttk.Scrollbar(canvas_frame, orient="vertical",
                                command=self.texture_canvas.yview)
        self.texture_canvas.configure(xscrollcommand=hscroll.set, yscrollcommand=vscroll.set)
        self.texture_canvas.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self.notebook.add(self.texture_tab, text="Textures")

    def _build_model_tab(self) -> None:
        self.model_tab = ttk.Frame(self.notebook, padding=8)
        controls = ttk.Frame(self.model_tab)
        controls.pack(fill="x")
        ttk.Button(controls, text="Voir en 3D", style="Accent.TButton",
                   command=self.view_model).pack(side="left")
        ttk.Button(controls, text="Exporter OBJ…", command=self.export_model_obj).pack(
            side="left", padx=6)
        ttk.Button(controls, text="Convertir en .blend…", command=self.convert_blend).pack(
            side="left")
        ttk.Label(controls, text="LOD", style="Panel.TLabel").pack(side="left", padx=(16, 4))
        self.lod_var = tk.StringVar(value="0")
        self.lod_box = ttk.Combobox(controls, textvariable=self.lod_var, width=4,
                                    values=["0"], state="readonly")
        self.lod_box.pack(side="left")
        self.model_text = tk.Text(self.model_tab, bg=PANEL, fg=FG, relief="flat",
                                  font=("Consolas", 10), wrap="word", padx=8, pady=6)
        self.model_text.pack(fill="both", expand=True, pady=(8, 0))
        self.notebook.add(self.model_tab, text="Modèle 3D")

    def _build_status(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel",
                  anchor="w").pack(side="left", fill="x", expand=True)
        self.mod_label = ttk.Label(bar, text="", style="Status.TLabel")
        self.mod_label.pack(side="right")

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda e: self.open_file())
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-r>", lambda e: self.set_romfs())
        self.bind("<Control-e>", lambda e: self.export_mod())
        self.bind("<Control-p>", lambda e: self.patch_dialog())
        self.bind("<Control-b>", lambda e: self.convert_blend())
        self.bind("<Alt-Left>", lambda e: self.back())
        self.bind("<F5>", lambda e: self._refresh_all())
        self.bind("<Control-f>", lambda e: self._focus_search())

    def _focus_search(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    # ------------------------------------------------------------------ tooltip
    @staticmethod
    def _tooltip(widget, text: str) -> None:
        state = {"id": None}

        def enter(_event):
            state["id"] = widget.after(600, lambda: _show(widget, text))

        def leave(_event=None):
            if state["id"]:
                widget.after_cancel(state["id"])
                state["id"] = None
            try:
                tip.withdraw()
            except tk.TclError:
                pass

        tip = tk.Toplevel(widget)
        tip.withdraw()
        tip.overrideredirect(True)
        tk.Label(tip, text=text, bg=PANEL2, fg=FG, padx=6, pady=3,
                 font=("Segoe UI", 9)).pack()

        def _show(widget, text):
            tip.geometry(f"+{widget.winfo_rootx() + 12}+{widget.winfo_rooty() + widget.winfo_height() + 4}")
            tip.deiconify()

        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    # ------------------------------------------------------------------ config
    def _load_config(self) -> dict:
        for path in (CONFIG_PATH, OLD_CONFIG_PATH):
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict):
                    data.setdefault("dicts", [])
                    return data
            except (OSError, ValueError):
                continue
        return {"dicts": [], "romfs": "", "blender": "", "compress_level": DEFAULT_LEVEL,
                "preview_max": 1024}

    def _save_config(self) -> None:
        self.config_data["romfs"] = str(self.romfs or "")
        self.config_data["dicts"] = [str(p) for p in self.dicts.sources]
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(self.config_data, indent=2, ensure_ascii=False))
        except OSError:
            pass

    def _load_dicts_from_config(self) -> None:
        loaded = 0
        for path in list(self.config_data.get("dicts", [])):
            try:
                loaded += self.dicts.load_file(path)
            except HookyError:
                pass
        if loaded:
            self.status_var.set(f"{loaded} dictionnaire(s) zstd chargé(s) depuis la configuration.")
        self._refresh_romfs_label()

    # ------------------------------------------------------------------ utilitaires UI
    def status(self, text: str) -> None:
        self.status_var.set(text)
        self.update_idletasks()

    def _show_welcome(self) -> None:
        self._set_preview(
            "HookyMod · Asset Explorer\n"
            "========================\n\n"
            "1. Clique sur « Choisir romfs… » et sélectionne le dossier romfs de ton dump\n"
            "   (les dictionnaires zstd de Pack/ZsDic.pack.zs sont chargés automatiquement).\n"
            "2. Sélectionne un fichier à gauche : il s'ouvre dans la colonne du milieu.\n"
            "3. Double-clique sur un fichier .zs ou une archive SARC pour entrer dedans.\n"
            "4. Modifie (« Remplacer… », « Patch… ») puis « Exporter en mod » ou « .blend ».\n\n"
            "Fichiers reconnus en détail : archives SARC, .zs (zstd), .bfres (modèles 3D),\n"
            ".bntx (textures), .obj (maillages).\n"
        )
        self._set_model_text("")
        self._reset_textures()

    def _set_preview(self, text: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def _set_model_text(self, text: str) -> None:
        self.model_text.configure(state="normal")
        self.model_text.delete("1.0", "end")
        self.model_text.insert("1.0", text)
        self.model_text.configure(state="disabled")

    def _refresh_romfs_label(self) -> None:
        romfs = f"romfs : {self.romfs}" if self.romfs else "romfs : non défini"
        self.romfs_var.set(f"{romfs}   ·   dictionnaires : {len(self.dicts)}")
        zstd_note = "" if zstd_available() else "   ·   ⚠ module zstandard absent"
        bc_note = "" if bntx_mod._t2d is not None else "   ·   BC7/ASTC limités"
        self.romfs_var.set(self.romfs_var.get() + zstd_note + bc_note)

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = max(0, self._busy + (1 if busy else -1))
        if busy and label:
            self.status(label)
        self.configure(cursor="watch" if self._busy else "")
        self.update_idletasks()

    def _run_bg(self, label: str, work, done, failed=None) -> None:
        """Exécute ``work`` dans un fil, puis ``done(result)`` dans le fil Tk."""
        self._set_busy(True, label)

        def worker():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - on remonte tout à l'UI
                self.after(0, lambda err=exc: self._bg_failed(err, failed))
                return
            self.after(0, lambda: self._bg_done(result, done))

        thread = threading.Thread(target=worker, daemon=True)
        self._jobs.append(thread)
        thread.start()

    def _bg_done(self, result, done) -> None:
        self._set_busy(False)
        try:
            done(result)
        except Exception as exc:  # noqa: BLE001
            self.error(exc)

    def _bg_failed(self, exc, failed) -> None:
        self._set_busy(False)
        if failed:
            failed(exc)
        else:
            self.error(exc)

    def error(self, exc: Exception, context: str = "") -> None:
        if isinstance(exc, HookyError):
            message = str(exc)
        else:
            message = f"{exc}\n\n{traceback.format_exc(limit=3)}"
        self.status_var.set(f"Erreur : {exc}")
        messagebox.showerror("Erreur" + (f" — {context}" if context else ""), message, parent=self)

    def _action(func):
        """Décorateur : toute exception devient une boîte de dialogue lisible."""
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                self.error(exc, func.__name__)
                return None
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    # ------------------------------------------------------------------ romfs
    @_action
    def set_romfs(self) -> None:
        folder = filedialog.askdirectory(title="Dossier romfs (ou son parent)")
        if not folder:
            return
        self._use_romfs(Path(folder))

    def _use_romfs(self, folder: Path) -> None:
        self._set_busy(True, "Chargement du dossier romfs…")
        try:
            count = 0
            files, romfs_root = find_dict_files(folder)
            errors = []
            for path in files:
                try:
                    count += self.dicts.load_file(path)
                except HookyError as exc:
                    errors.append(f"{path.name} : {exc}")
            from .zs import guess_romfs_root
            self.romfs = romfs_root or guess_romfs_root(folder) or folder
            self._save_config()
            self.fs_populate_root()
            self._refresh_romfs_label()
            if not files:
                self.status(f"Aucun ZsDic.pack.zs trouvé — dossier {self.romfs} ouvert quand même.")
            else:
                note = f"{count} dictionnaire(s) chargé(s)" if count else "dictionnaires déjà chargés"
                self.status(f"romfs : {self.romfs}   ·   {note}")
            if errors:
                messagebox.showwarning("Dictionnaires",
                                       "Certains dictionnaires n'ont pas pu être lus :\n\n"
                                       + "\n".join(errors), parent=self)
        finally:
            self._set_busy(False)

    @_action
    def reload_dicts(self) -> None:
        if not self.romfs:
            messagebox.showinfo("Dictionnaires", "Choisis d'abord un dossier romfs.", parent=self)
            return
        self.dicts = ZstdDicts()
        self._use_romfs(self.romfs)

    def fs_populate_root(self) -> None:
        self.fs.delete(*self.fs.get_children())
        self.fs_paths: dict[str, Path] = {}
        if not self.romfs or not self.romfs.exists():
            return
        try:
            children = sorted(self.romfs.iterdir(),
                              key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            self.error(exc, "lecture du romfs")
            return
        for child in children:
            self._fs_add("", child)

    def _fs_add(self, parent: str, path: Path) -> str:
        is_dir = path.is_dir()
        try:
            label = path.name
        except OSError:
            label = "?"
        iid = self.fs.insert(parent, "end", text=("📁 " if is_dir else "   ") + label)
        self.fs_paths[iid] = path
        if is_dir:
            self.fs.insert(iid, "end", text="…")
        return iid

    def _fs_list(self, path: Path) -> list[Path]:
        try:
            return sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return []

    def fs_expand(self, _event=None) -> None:
        iid = self.fs.focus()
        children = self.fs.get_children(iid)
        if iid in getattr(self, "fs_paths", {}) and len(children) == 1 \
                and children[0] not in self.fs_paths:
            self.fs.delete(children[0])
            for child in self._fs_list(self.fs_paths[iid]):
                self._fs_add(iid, child)

    def fs_select(self, _event=None) -> None:
        selection = self.fs.selection()
        if not selection:
            return
        path = self.fs_paths.get(selection[0])
        if path and path.is_file():
            self.open_path(path, quiet=True)

    @_action
    def fs_search(self) -> None:
        query = self.search_var.get().strip().lower()
        if not query:
            self.fs_reset()
            return
        if not self.romfs:
            messagebox.showinfo("Recherche", "Choisis d'abord un dossier romfs.", parent=self)
            return
        self.fs.delete(*self.fs.get_children())
        self.fs_paths = {}
        limit = 800
        found = 0
        self.status(f"Recherche de « {query} »…")
        for root, dirs, files in os.walk(self.romfs):
            dirs.sort()
            for name in sorted(files):
                if query in name.lower():
                    path = Path(root) / name
                    try:
                        rel = path.relative_to(self.romfs).as_posix()
                    except ValueError:
                        rel = path.name
                    iid = self.fs.insert("", "end", text="   " + rel)
                    self.fs_paths[iid] = path
                    found += 1
                    if found >= limit:
                        break
            if found >= limit:
                break
        suffix = f" (limité à {limit})" if found >= limit else ""
        self.status(f"{found} résultat(s) pour « {query} »{suffix}")

    def fs_reset(self) -> None:
        self.search_var.set("")
        self.fs_populate_root()
        self.status("Explorateur romfs réinitialisé.")

    # ------------------------------------------------------------------ ouverture
    @_action
    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Ouvrir un asset",
            initialdir=str(self.romfs) if self.romfs else None)
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path, quiet: bool = False) -> None:
        path = Path(path)
        try:
            stat = path.stat()
        except OSError as exc:
            if quiet:
                self._set_preview(f"{path.name}\n\n{exc}")
                return
            raise
        if stat.st_size > 512 * 1024 * 1024:
            if not messagebox.askyesno("Fichier volumineux",
                                       f"{path.name} fait {human_size(stat.st_size)}.\n"
                                       "L'ouvrir quand même ?", parent=self):
                return
        self._set_busy(True, f"Ouverture de {path.name}…")
        try:
            raw = path.read_bytes()
            rel = path.name
            if self.romfs:
                try:
                    rel = path.resolve().relative_to(self.romfs.resolve()).as_posix()
                except (ValueError, OSError):
                    rel = path.name
            key = str(path)
            existing = self.documents.get(key)
            if existing and existing.modified():
                if not messagebox.askyesno(
                        "Fichier déjà ouvert",
                        f"{path.name} a des modifications non exportées.\n"
                        "Les abandonner et recharger depuis le disque ?", parent=self):
                    self.current_key = key
                    self._refresh_all()
                    return
            document = Document(Level(name=path.name, raw=raw, dicts=self.dicts, rel=rel))
            self.documents[key] = document
            self.current_key = key
            self._refresh_all()
            if document.top.archive is not None:
                self.status(f"{path.name} : archive de {len(document.top)} entrée(s).")
            else:
                self.status(f"{path.name} : {detect_kind(document.top.data)}.")
        finally:
            self._set_busy(False)

    def _refresh_all(self) -> None:
        self._refresh_entries()
        self._refresh_preview()
        self._refresh_mod_label()

    def _refresh_mod_label(self) -> None:
        dirty = sum(1 for doc in self.documents.values() if doc.modified())
        self.mod_label.config(text=f"{dirty} fichier(s) modifié(s)" if dirty else "")
        doc = self.current
        if doc and doc.top.compressed:
            self.header_var.set(f"{doc.top.path_label()}   ·   zstd (dict {doc.top.dict_id or '-'})")
        elif doc:
            self.header_var.set(doc.top.path_label())
        else:
            self.header_var.set("Aucun fichier ouvert")

    def _refresh_entries(self) -> None:
        self.entry_tree.delete(*self.entry_tree.get_children())
        doc = self.current
        if not doc:
            return
        level = doc.top
        if level.archive is None:
            return
        query = self.filter_var.get().lower()
        for index, entry in enumerate(level.archive.entries):
            if query and query not in entry.name.lower():
                continue
            self.entry_tree.insert("", "end", iid=str(index),
                                   values=(entry.name, human_size(len(entry.data)),
                                           detect_kind(entry.data)))

    def _refresh_preview(self) -> None:
        doc = self.current
        self._reset_textures()
        self._set_model_text("")
        self.lod_box.configure(values=["0"])
        self.lod_var.set("0")
        if not doc:
            self._show_welcome()
            return
        level = doc.top
        if level.archive is not None:
            self._set_preview(
                f"{level.name}\n"
                f"Archive SARC · {len(level.archive)} entrées · "
                f"{human_size(len(level.data))}\n\n"
                "Sélectionne un fichier pour l'aperçu, double-clique pour entrer dans un\n"
                ".zs / une archive imbriquée.\n\n"
                "Astuces : « Extraire » garde les octets d'origine, « Remplacer… »\n"
                "recompresse automatiquement les .zs si besoin.")
            return

        data = level.data
        if data[:4] == b"BNTX":
            self._load_textures(level)
        if data[:4] == b"FRES":
            self._load_bfres(level)
        self._set_preview(self._describe_level(level))

    def _describe_level(self, level: Level) -> str:
        lines = [f"{level.name}", f"Chemin : {level.rel}",
                 f"Taille : {human_size(len(level.data))} ({len(level.data):,} octets)",
                 f"Type   : {detect_kind(level.data)}"]
        if level.compressed:
            lines.append(f"zstd   : dictionnaire id {level.dict_id or '(aucun)'}")
        if level.modified:
            lines.append("État   : MODIFIÉ")
        lines.append("")
        lines.append("--- Aperçu hexadécimal ---")
        lines += hexdump(level.data, 2048)
        strings = ascii_strings(level.data, limit=80)
        if strings:
            lines += ["", f"--- Chaînes ASCII ({len(strings)}) ---"]
            lines += strings
        return "\n".join(lines)

    # ------------------------------------------------------------------ entrées
    def _selected_indices(self) -> list[int]:
        doc = self.current
        if not doc or doc.top.archive is None:
            return []
        out = []
        for iid in self.entry_tree.selection():
            try:
                out.append(int(iid))
            except ValueError:
                continue
        return out

    @_action
    def on_entry_select(self, _event=None) -> None:
        indices = self._selected_indices()
        if len(indices) != 1:
            return
        doc = self.current
        entry = doc.top.archive.entries[indices[0]]
        self._set_preview(self._describe_entry(entry.name, entry.data))
        # Si l'entrée est elle-même un BNTX/BFRES, on enrichit les onglets.
        data = entry.data
        if is_zstd(data):
            try:
                data = self.dicts.decompress(data)
            except HookyError:
                data = b""
        if data[:4] == b"BNTX":
            self._load_textures_from_data(data)
        elif data[:4] == b"FRES":
            self._load_bfres_from_data(data)

    def _describe_entry(self, name: str, data: bytes) -> str:
        lines = [f"{name}"]
        if is_zstd(data):
            try:
                decompressed = self.dicts.decompress(data)
                lines.append(f"Compressé : zstd → {human_size(len(decompressed))} "
                             f"({len(decompressed):,} octets)")
                data = decompressed
            except HookyError as exc:
                lines.append(f"⚠ {exc}")
                lines.append("")
                lines.append("--- Aperçu hexadécimal (compressé) ---")
                lines += hexdump(data, 1024)
                return "\n".join(lines)
        lines.insert(1, f"Type : {detect_kind(data)}")
        lines.append(f"Taille : {human_size(len(data))} ({len(data):,} octets)")
        lines.append("")
        if data[:4] == b"BNTX":
            try:
                info = bntx_mod.BntxFile.parse(data).info()
                lines += ["--- BNTX ---", info, ""]
            except HookyError as exc:
                lines += [f"⚠ BNTX illisible : {exc}", ""]
        elif data[:4] == b"FRES":
            try:
                info = bfres_mod.BfresFile.parse(data).info()
                lines += ["--- BFRES ---", info, ""]
            except HookyError as exc:
                lines += [f"⚠ BFRES illisible : {exc}", ""]
        lines.append("--- Aperçu hexadécimal ---")
        lines += hexdump(data, 1024)
        strings = ascii_strings(data, limit=40)
        if strings:
            lines += ["", "--- Chaînes ASCII ---"] + strings
        return "\n".join(lines)

    @_action
    def on_entry_open(self, _event=None) -> None:
        doc = self.current
        if not doc or doc.top.archive is None:
            return
        indices = self._selected_indices()
        if len(indices) != 1:
            return
        index = indices[0]
        if not doc.top.can_open_entry(index):
            messagebox.showinfo("Ouvrir",
                                "Cette entrée n'est ni une archive SARC ni un fichier .zs.",
                                parent=self)
            return
        self._set_busy(True, "Ouverture de l'entrée…")
        try:
            doc.push(index)
        finally:
            self._set_busy(False)
        self.filter_var.set("")
        self._refresh_all()

    @_action
    def back(self) -> None:
        doc = self.current
        if not doc or len(doc.stack) <= 1:
            return
        doc.pop()
        self.filter_var.set("")
        self._refresh_all()

    # ------------------------------------------------------------------ extraction
    def _ask_extract_dir(self, title: str = "Dossier de destination") -> Path | None:
        folder = filedialog.askdirectory(title=title)
        return Path(folder) if folder else None

    def _extract_entries(self, entries, base: Path, decompress: bool) -> tuple[int, int, list[str]]:
        written = 0
        skipped = 0
        errors: list[str] = []
        for name, blob in entries:
            target_name = name
            data = blob
            if decompress and is_zstd(blob):
                try:
                    data = self.dicts.decompress(blob)
                    target_name = name[:-3] if name.lower().endswith(".zs") else name
                except HookyError as exc:
                    errors.append(f"{name} : {exc}")
                    skipped += 1
                    continue
            target = safe_child(base, target_name)
            if target is None:
                errors.append(f"{name} : nom de fichier dangereux, ignoré")
                skipped += 1
                continue
            try:
                write_atomic(target, data)
                written += 1
            except OSError as exc:
                errors.append(f"{name} : {exc}")
                skipped += 1
        return written, skipped, errors

    @_action
    def extract_selection(self) -> None:
        doc = self.current
        if not doc:
            return
        if doc.top.archive is None:
            self._extract_current_file()
            return
        indices = self._selected_indices()
        if not indices:
            messagebox.showinfo("Extraire",
                                "Sélectionne au moins un fichier dans la liste.",
                                parent=self)
            return
        base = self._ask_extract_dir()
        if not base:
            return
        entries = [(doc.top.archive.entries[i].name, doc.top.archive.entries[i].data)
                   for i in indices]
        decompress = self._ask_decompress(entries)
        written, skipped, errors = self._extract_entries(entries, base, decompress)
        self.status(f"{written} fichier(s) extrait(s) vers {base}"
                    + (f" · {skipped} ignoré(s)" if skipped else ""))
        if errors:
            messagebox.showwarning("Extraction", "\n".join(errors[:20]), parent=self)

    def _extract_current_file(self) -> None:
        doc = self.current
        if not doc:
            return
        base = self._ask_extract_dir()
        if not base:
            return
        name = doc.top.name
        target = safe_child(base, name)
        if target is None:
            raise HookyError("Nom de fichier invalide.")
        write_atomic(target, doc.top.data)
        self.status(f"Écrit : {target}")

    def _ask_decompress(self, entries) -> bool:
        if not any(is_zstd(blob) for _, blob in entries):
            return False
        return messagebox.askyesno(
            "Décompresser",
            "Certains fichiers sont compressés en .zs.\n\n"
            "Les extraire décompressés (recommandé pour éditer) ?\n"
            "Répondre « Non » garde les octets d'origine.", parent=self)

    @_action
    def extract_all(self) -> None:
        doc = self.current
        if not doc or doc.top.archive is None:
            messagebox.showinfo("Tout extraire", "Ouvre d'abord une archive.", parent=self)
            return
        base = self._ask_extract_dir()
        if not base:
            return
        entries = [(e.name, e.data) for e in doc.top.archive.entries]
        decompress = self._ask_decompress(entries)
        self._set_busy(True, "Extraction…")
        try:
            written, skipped, errors = self._extract_entries(entries, base, decompress)
        finally:
            self._set_busy(False)
        self.status(f"{written} fichier(s) extrait(s) vers {base}")
        if errors:
            messagebox.showwarning("Extraction", "\n".join(errors[:20]), parent=self)

    # ------------------------------------------------------------------ remplacement
    @_action
    def replace_selection(self) -> None:
        doc = self.current
        if not doc:
            return
        if doc.top.archive is None:
            self._replace_current_file()
            return
        indices = self._selected_indices()
        if len(indices) != 1:
            messagebox.showinfo("Remplacer",
                                "Sélectionne un seul fichier dans l'archive.", parent=self)
            return
        level = doc.top
        index = indices[0]
        name = level.archive.entries[index].name
        path = filedialog.askopenfilename(title=f"Remplacer « {name} » par…")
        if not path:
            return
        data = Path(path).read_bytes()
        data = self._maybe_compress(level.archive.entries[index].data, data, name)
        level.replace_entry(index, data)
        self._refresh_all()
        self.status(f"« {name} » remplacé ({human_size(len(data))}).")

    def _maybe_compress(self, original: bytes, data: bytes, name: str) -> bytes:
        """Recompresse si le fichier d'origine l'était (et que le nouveau ne l'est pas)."""
        if not is_zstd(original) or is_zstd(data):
            return data
        if not messagebox.askyesno(
                "Recompresser",
                f"« {name} » est compressé en .zs.\n\n"
                "Recompresser automatiquement le fichier fourni avec le dictionnaire "
                "d'origine ?", parent=self):
            return data
        try:
            dict_id, _ = frame_info(original)
            level = max(1, min(22, int(self.config_data.get("compress_level", DEFAULT_LEVEL))))
            return self.dicts.compress(data, dict_id, level)
        except HookyError as exc:
            raise HookyError(
                f"Recompression impossible : {exc}\n\n"
                "Le fichier a été inséré tel quel (il ne sera probablement pas lisible "
                "par le jeu).") from exc

    def _replace_current_file(self) -> None:
        doc = self.current
        path = filedialog.askopenfilename(title="Remplacer le contenu par…")
        if not path:
            return
        doc.top.data = Path(path).read_bytes()
        doc.top.archive = SarcArchive.parse(doc.top.data) if doc.top.data[:4] == b"SARC" else None
        doc.top.modified = True
        self._refresh_all()
        self.status("Contenu remplacé.")

    @_action
    def batch_replace(self) -> None:
        doc = self.current
        if not doc or doc.top.archive is None:
            messagebox.showinfo("Remplacement par lot",
                                "Ouvre d'abord une archive.", parent=self)
            return
        folder = filedialog.askdirectory(
            title="Dossier contenant les fichiers de remplacement")
        if not folder:
            return
        base = Path(folder)
        level = doc.top
        by_name: dict[str, int] = {}
        for index, entry in enumerate(level.archive.entries):
            by_name.setdefault(entry.name.replace("\\", "/"), index)
        replaced = 0
        compressed = 0
        missing: list[str] = []
        errors: list[str] = []
        self._set_busy(True, "Remplacement par lot…")
        try:
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(base).as_posix()
                candidates = [rel]
                if rel.endswith(".zs"):
                    candidates.append(rel[:-3])
                index = None
                for candidate in candidates:
                    if candidate in by_name:
                        index = by_name[candidate]
                        break
                if index is None:
                    missing.append(rel)
                    continue
                try:
                    data = path.read_bytes()
                    original = level.archive.entries[index].data
                    if is_zstd(original) and not is_zstd(data):
                        dict_id = frame_info(original)[0]
                        data = self.dicts.compress(
                            data, dict_id,
                            int(self.config_data.get("compress_level", DEFAULT_LEVEL)))
                        compressed += 1
                    level.replace_entry(index, data)
                    replaced += 1
                except (OSError, HookyError) as exc:
                    errors.append(f"{rel} : {exc}")
        finally:
            self._set_busy(False)
        self._refresh_all()
        message = f"{replaced} fichier(s) remplacé(s)"
        if compressed:
            message += f" · {compressed} recompressé(s)"
        if missing:
            message += f" · {len(missing)} sans correspondance"
        self.status(message)
        details = []
        if missing:
            details.append("Sans correspondance :\n" + "\n".join(missing[:20]))
        if errors:
            details.append("Erreurs :\n" + "\n".join(errors[:20]))
        if details:
            messagebox.showwarning("Remplacement par lot", "\n\n".join(details), parent=self)

    # ------------------------------------------------------------------ scan
    @_action
    def scan_folder(self) -> None:
        """Identifie les formats d'un dossier (dump inconnu) et affiche un rapport."""
        folder = filedialog.askdirectory(title="Dossier à scanner (dump du jeu)")
        if not folder:
            return
        root = Path(folder)

        def work():
            return identify.scan(root, samples=2, hexdump_bytes=32, strings=6)

        self._run_bg("Analyse du dossier…", work, self._show_scan_report)

    def _show_scan_report(self, report: dict) -> None:
        win = tk.Toplevel(self)
        win.title(f"Scan — {Path(report['root']).name or report['root']}")
        win.configure(bg=BG)
        win.geometry("940x620")
        win.transient(self)
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="none", bg=PANEL, fg=FG, insertbackground=FG,
                       relief="flat", padx=8, pady=8)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        body = identify.format_report(report)
        text.insert("1.0", body)
        text.configure(state="disabled")

        bar = ttk.Frame(win, padding=(10, 0, 10, 10))
        bar.pack(fill="x")

        def copy():
            self.clipboard_clear()
            self.clipboard_append(body)
            self.status("Rapport copié dans le presse-papiers.")

        def save():
            target = filedialog.asksaveasfilename(
                title="Enregistrer le rapport", defaultextension=".json",
                filetypes=[("Rapport JSON", "*.json"), ("Texte", "*.txt")])
            if not target:
                return
            path = Path(target)
            payload = (identify.report_to_json(report) if path.suffix.lower() == ".json"
                       else body)
            write_atomic(path, payload.encode("utf-8"))
            self.status(f"Rapport écrit : {path}")

        ttk.Button(bar, text="Copier le rapport", command=copy).pack(side="left")
        ttk.Button(bar, text="Enregistrer…", command=save).pack(side="left", padx=8)
        ttk.Button(bar, text="Fermer", command=win.destroy).pack(side="right")

    # ------------------------------------------------------------------ switch 2
    @_action
    def open_nup(self) -> None:
        """Ouvre un paquet .nup (dump Switch 2) et extrait son romfs."""
        choice = filedialog.askopenfilename(
            title="Ouvrir un paquet .nup (dump Nintendo Switch 2)",
            filetypes=[("Paquet Switch 2", "*.nup"), ("Tous les fichiers", "*.*")])
        if not choice:
            return
        package_path = Path(choice)
        destination = filedialog.askdirectory(
            title="Dossier d'extraction (romfs et exefs seront écrits dedans)")
        if not destination:
            return
        target = Path(destination)

        def work():
            package = nup_mod.NupPackage.open(package_path)
            with package:
                summary = package.summary()
                result = package.extract(target)
            return summary, result, target

        self._run_bg("Extraction du .nup…", work, self._nup_extracted)

    def _nup_extracted(self, payload) -> None:
        summary, result, target = payload
        romfs = target / "romfs"
        self._use_romfs(romfs if romfs.is_dir() else target)
        message = (f"{result.count} fichier(s) extrait(s) — "
                   f"{human_size(result.total_bytes)}\n{target}\n\n"
                   + "\n".join(summary[:6]))
        if result.heads:
            message += (f"\n\n⚠ {len(result.heads)} fichier(s) .zs dont l'en-tête n'est "
                        "pas zstd (fichiers abîmés ou non compressés).")
        if result.problems:
            message += ("\n\nProblèmes :\n" + "\n".join(result.problems[:10]))
        messagebox.showinfo("Paquet .nup", message, parent=self)

    def _update_size_table(self, base: Path, sizes: dict[str, int]) -> list[str]:
        """Agrandit la table de tailles du mod si un fichier dépasse sa réservation."""
        if not self.romfs or not sizes:
            return []
        try:
            table, source, raw = restbl_mod.load_table(self.romfs, self.dicts)
        except HookyError:
            return []
        problems = table.check(sizes)
        if not problems:
            return []
        detail = "\n".join(problems[:10])
        if not messagebox.askyesno(
                "Table de tailles des ressources",
                "Ces fichiers dépassent la taille réservée par le jeu :\n\n"
                f"{detail}\n\nLe jeu ne chargerait pas les fichiers trop gros. "
                "Agrandir la table et l'inclure dans le mod ?", parent=self):
            return ["Table non modifiée : un fichier plus gros peut ne pas être chargé."]
        changes = table.grow_for(sizes)
        payload = table.build()
        if source.suffix.lower() == ".zs":
            dictionary = None
            try:
                dictionary, _ = frame_info(raw)
            except HookyError:
                dictionary = None
            if not dictionary and len(self.dicts):
                dictionary = next(iter(self.dicts.by_id))
            if not dictionary:
                return ["Table non écrite : dictionnaire zstd introuvable "
                        "(ouvre le romfs pour charger ZsDic.pack.zs)."]
            try:
                payload = self.dicts.compress(payload, dictionary, DEFAULT_LEVEL)
            except HookyError as exc:
                return [f"Table non écrite : {exc}"]
        destination = base / "romfs" / "System" / "Resource" / source.name
        try:
            write_atomic(destination, payload)
        except (HookyError, OSError) as exc:
            return [f"Table non écrite : {exc}"]
        return [f"{source.name} agrandie ({len(changes)} entrée(s)) et incluse dans le mod."]

    # ------------------------------------------------------------------ patch
    @_action
    def patch_dialog(self) -> None:
        doc = self.current
        if not doc:
            messagebox.showinfo("Patch", "Ouvre d'abord un fichier.", parent=self)
            return
        win = tk.Toplevel(self)
        win.title("Patch — chercher / remplacer")
        win.configure(bg=BG)
        win.transient(self)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        find_var = tk.StringVar()
        replace_var = tk.StringVar()
        hex_var = tk.BooleanVar(value=False)
        scope_var = tk.StringVar(value="all" if doc.top.archive else "file")
        nested_var = tk.BooleanVar(value=True)

        ttk.Label(frame, text="Chercher").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=find_var, width=52).grid(row=0, column=1, pady=3)
        ttk.Label(frame, text="Remplacer par").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=replace_var, width=52).grid(row=1, column=1, pady=3)
        ttk.Checkbutton(frame, text="Octets hexadécimaux (ex. « 46 52 45 53 »)",
                        variable=hex_var).grid(row=2, column=1, sticky="w", pady=3)

        scope = ttk.Labelframe(frame, text="Portée", padding=8)
        scope.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Radiobutton(scope, text="Tout le fichier ouvert", value="file",
                        variable=scope_var).pack(anchor="w")
        if doc.top.archive is not None:
            ttk.Radiobutton(scope, text="Toute l'archive", value="all",
                            variable=scope_var).pack(anchor="w")
            ttk.Radiobutton(scope, text="Seulement la sélection", value="selection",
                            variable=scope_var).pack(anchor="w")
            ttk.Checkbutton(scope, text="Descendre dans les fichiers .zs / archives imbriquées",
                            variable=nested_var).pack(anchor="w", pady=(4, 0))

        ttk.Label(frame, style="Muted.TLabel", wraplength=520,
                  text="Le remplacement doit conserver la même longueur : on modifie des "
                       "octets sur place (noms de matériaux, chemins, valeurs).\n"
                       "Les fichiers compressés sont décompressés, patchés puis recompressés."
                  ).grid(row=4, column=0, columnspan=2, sticky="w", pady=8)

        def convert(text: str) -> bytes:
            if hex_var.get():
                cleaned = re.sub(r"[^0-9a-fA-F]", "", text)
                if len(cleaned) % 2:
                    raise HookyError("Le nombre de chiffres hexadécimaux est impair.")
                return bytes.fromhex(cleaned)
            return text.encode("utf-8")

        def run_patch(data: bytes, needle: bytes, replacement: bytes) -> tuple[bytes, int]:
            count = data.count(needle)
            if count:
                data = data.replace(needle, replacement)
            return data, count

        def walk_entry(blob: bytes, needle: bytes, replacement: bytes,
                       depth: int = 0) -> tuple[bytes, int]:
            """Patche un blob, éventuellement compressé / archive."""
            total = 0
            original_compressed = is_zstd(blob)
            data = blob
            if original_compressed:
                try:
                    data = self.dicts.decompress(blob)
                except HookyError:
                    return blob, 0
            if data[:4] == b"SARC" and depth < 4:
                archive = SarcArchive.parse(data)
                changed = False
                for entry in archive.entries:
                    new_blob, count = walk_entry(entry.data, needle, replacement, depth + 1)
                    if count:
                        entry.data = new_blob
                        total += count
                        changed = True
                if changed:
                    data = archive.build()
            data, count = run_patch(data, needle, replacement)
            total += count
            if not total:
                return blob, 0
            if original_compressed:
                dict_id = frame_info(blob)[0]
                return self.dicts.compress(
                    data, dict_id, int(self.config_data.get("compress_level", DEFAULT_LEVEL))), total
            return data, total

        def apply() -> None:
            try:
                needle = convert(find_var.get())
                replacement = convert(replace_var.get())
                if not needle:
                    raise HookyError("Le motif à chercher est vide.")
                if len(needle) != len(replacement):
                    raise HookyError(
                        f"Longueurs différentes ({len(needle)} ≠ {len(replacement)} octets).\n"
                        "Un patch sur place doit garder la même longueur.")
                level = doc.top
                total = 0
                scope_value = scope_var.get()
                if level.archive is not None:
                    if scope_value == "selection":
                        indices = self._selected_indices()
                        if not indices:
                            raise HookyError("Aucun fichier sélectionné.")
                    elif scope_value == "all":
                        indices = list(range(len(level.archive)))
                    else:
                        indices = []
                    if indices:
                        for index in indices:
                            entry = level.archive.entries[index]
                            new_blob, count = walk_entry(entry.data, needle, replacement)
                            if count:
                                entry.data = new_blob
                                total += count
                    if scope_value == "file" or not indices:
                        new_data, count = run_patch(level.data, needle, replacement)
                        if count:
                            level.data = new_data
                            level.archive = SarcArchive.parse(new_data)
                            total += count
                    if not total:
                        # Noms de fichiers / en-tête de l'archive : patch brut.
                        new_data, count = run_patch(level.data, needle, replacement)
                        if count:
                            level.data = new_data
                            try:
                                level.archive = SarcArchive.parse(new_data)
                            except FormatError:
                                pass
                            total += count
                else:
                    # ``level.data`` est déjà décompressé : on patche directement.
                    new_data, count = run_patch(level.data, needle, replacement)
                    if count:
                        level.data = new_data
                        if new_data[:4] == b"SARC":
                            level.archive = SarcArchive.parse(new_data)
                        total += count
                if total:
                    level.modified = True
                self._refresh_all()
                win.destroy()
                self.status(f"Patch : {total} occurrence(s) remplacée(s).")
                if not total:
                    messagebox.showinfo("Patch", "Aucune occurrence trouvée.", parent=self)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Patch", str(exc), parent=win)

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Annuler", command=win.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Appliquer", style="Accent.TButton",
                   command=apply).pack(side="right")
        win.grab_set()

    # ------------------------------------------------------------------ sauvegarde / mod
    @_action
    def save(self) -> None:
        doc = self.current
        if not doc:
            return
        path = filedialog.asksaveasfilename(
            title="Enregistrer (recompressé comme à l'origine)",
            initialfile=doc.top.name, defaultextension=Path(doc.top.name).suffix)
        if not path:
            return
        self._set_busy(True, "Enregistrement…")
        try:
            write_atomic(Path(path), doc.pack())
        finally:
            self._set_busy(False)
        self._refresh_all()
        self.status(f"Enregistré : {path}")

    def _dirty_documents(self) -> list[Document]:
        return [doc for doc in self.documents.values() if doc.modified()]

    @_action
    def export_mod(self) -> None:
        if not self.documents:
            messagebox.showinfo("Exporter en mod", "Ouvre d'abord un fichier.", parent=self)
            return
        dirty = self._dirty_documents()
        docs = dirty or ([self.current] if self.current else [])
        if not docs:
            return
        if len(docs) > 1:
            choice = messagebox.askyesnocancel(
                "Exporter en mod",
                f"{len(docs)} fichiers modifiés sont ouverts.\n\n"
                "Oui = exporter les modifications de tous les fichiers\n"
                "Non  = exporter seulement le fichier courant", parent=self)
            if choice is None:
                return
            if not choice:
                docs = [self.current] if self.current else []
        folder = filedialog.askdirectory(
            title="Dossier du mod (le sous-dossier romfs/ sera créé dedans)")
        if not folder:
            return
        base = Path(folder)
        written = []
        warnings = []
        sizes: dict[str, int] = {}
        for doc in docs:
            try:
                root = doc.collapse()
                rel = root.rel
                if not rel or "!/" in rel or rel.startswith("/"):
                    rel = root.name
                    warnings.append(f"{root.name} : chemin romfs inconnu, placé à la racine.")
                target = safe_child(base / "romfs", rel)
                if target is None:
                    warnings.append(f"{root.name} : chemin invalide.")
                    continue
                payload = doc.pack()
                write_atomic(target, payload)
                written.append(str(target))
                if rel and "!/" not in rel:
                    sizes[rel] = len(payload)
            except (HookyError, OSError) as exc:
                warnings.append(f"{doc.root.name} : {exc}")
        self._refresh_all()
        if not written:
            messagebox.showerror("Exporter en mod",
                                 "Aucun fichier écrit.\n\n" + "\n".join(warnings), parent=self)
            return
        notes = self._update_size_table(base, sizes)
        message = (f"{len(written)} fichier(s) écrit(s) dans :\n{base / 'romfs'}\n\n"
                   "Pour Hookshot, place ce dossier dans « mods/<nom>/romfs » ; "
                   "pour un émulateur, copie son contenu dans le mod LayeredFS.")
        if notes:
            message += "\n\nTable de tailles :\n" + "\n".join(notes)
        if warnings:
            message += "\n\nAvertissements :\n" + "\n".join(warnings)
        self.status(f"Mod exporté : {len(written)} fichier(s)")
        messagebox.showinfo("Mod exporté", message, parent=self)

    @_action
    def show_modifications(self) -> None:
        win = tk.Toplevel(self)
        win.title("Modifications en cours")
        win.geometry("820x420")
        win.configure(bg=BG)
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Fichiers ouverts avec des modifications non exportées",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        tree = ttk.Treeview(frame, columns=("file", "rel", "state"), show="headings")
        for column, title, width in (("file", "Fichier", 260), ("rel", "Chemin romfs", 380),
                                     ("state", "État", 120)):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w")
        tree.pack(fill="both", expand=True)
        for key, doc in self.documents.items():
            tree.insert("", "end", iid=key,
                        values=(doc.root.name, doc.root.rel,
                                "modifié" if doc.modified() else "inchangé"))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))

        def close() -> None:
            win.destroy()

        def discard() -> None:
            selection = tree.selection()
            if not selection:
                return
            for key in selection:
                doc = self.documents.get(key)
                if doc and doc.modified() and messagebox.askyesno(
                        "Abandonner", f"Abandonner les modifications de {doc.root.name} ?",
                        parent=win):
                    self.documents.pop(key, None)
                    if self.current_key == key:
                        self.current_key = next(iter(self.documents), None)
            win.destroy()
            self._refresh_all()

        ttk.Button(buttons, text="Fermer", command=close).pack(side="right")
        ttk.Button(buttons, text="Abandonner la sélection", command=discard).pack(side="right",
                                                                                padx=6)
        ttk.Button(buttons, text="Exporter en mod…", style="Accent.TButton",
                   command=lambda: (win.destroy(), self.export_mod())).pack(side="right", padx=6)

    # ------------------------------------------------------------------ textures
    def _reset_textures(self) -> None:
        self.texture_file = None
        self.texture_tree.delete(*self.texture_tree.get_children())
        self.texture_canvas.delete("all")
        self._photo = None
        self.texture_info_var.set("Ouvre un fichier BNTX pour voir ses textures.")
        self.mip_box.configure(values=["0"])
        self.mip_var.set("0")

    def _load_textures(self, level: Level) -> None:
        self._load_textures_from_data(level.data)

    def _load_textures_from_data(self, data: bytes) -> None:
        if not data or data[:4] != b"BNTX":
            return
        try:
            file = bntx_mod.BntxFile.parse(data)
        except HookyError as exc:
            self.texture_info_var.set(f"BNTX illisible : {exc}")
            return
        self.texture_file = file
        self.texture_tree.delete(*self.texture_tree.get_children())
        for index, texture in enumerate(file.textures):
            self.texture_tree.insert("", "end", iid=str(index),
                                     values=(texture.name or f"#{texture.index}",
                                             f"{texture.width}×{texture.height}",
                                             texture.format_name))
        self.texture_info_var.set(f"{len(file.textures)} texture(s) — "
                                  f"{file.name or 'BNTX'}")
        children = self.texture_tree.get_children()
        if children:
            self.texture_tree.selection_set(children[0])
            self.texture_tree.focus(children[0])
            self._redraw_texture()

    def _selected_texture(self):
        selection = self.texture_tree.selection()
        if not selection or not self.texture_file:
            return None
        try:
            return self.texture_file.textures[int(selection[0])]
        except (ValueError, IndexError):
            return None

    def _redraw_texture(self) -> None:
        texture = self._selected_texture()
        if texture is None:
            return
        levels = [str(i) for i in range(max(1, texture.mip_count))]
        self.mip_box.configure(values=levels)
        try:
            mip = int(self.mip_var.get())
        except ValueError:
            mip = 0
        if mip >= len(levels):
            mip = 0
            self.mip_var.set("0")
        max_size = int(self.config_data.get("preview_max", 1024) or 1024)
        self.texture_info_var.set(f"{texture.name or '#%d' % texture.index} — décodage…")

        def work():
            width, height, rgba = texture.decode(mip, max_size=max_size)
            from .util import png_bytes
            return texture, width, height, png_bytes(width, height, rgba)

        def done(result):
            tex, width, height, blob = result
            folder = Path(tempfile.gettempdir()) / "hookymod_preview"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"tex_{tex.index}_{mip}.png"
            path.write_bytes(blob)
            self._preview_png = path
            try:
                photo = tk.PhotoImage(file=str(path))
            except tk.TclError:
                try:
                    from PIL import Image, ImageTk  # type: ignore
                    image = Image.open(path)
                    photo = ImageTk.PhotoImage(image)
                except Exception:
                    self.texture_info_var.set(
                        f"{tex.name} — aperçu PNG non supporté par ce Tk "
                        f"(export PNG possible)")
                    return
            self._photo = photo
            canvas = self.texture_canvas
            canvas.delete("all")
            canvas.configure(scrollregion=(0, 0, width, height))
            canvas.create_image(0, 0, anchor="nw", image=photo)
            if tex.warnings:
                self.texture_info_var.set(
                    f"{tex.name or '#%d' % tex.index} — {width}×{height} — "
                    f"{tex.format_name} — mip {mip}   ⚠ " + " · ".join(tex.warnings))
            else:
                self.texture_info_var.set(
                    f"{tex.name or '#%d' % tex.index} — {width}×{height} — "
                    f"{tex.format_name} — mip {mip}")

        def failed(exc):
            self.texture_info_var.set(f"{texture.name or texture.index} : {exc}")

        self._run_bg(f"Décodage de la texture {texture.name}…", work, done, failed)

    @_action
    def export_selected_texture(self) -> None:
        texture = self._selected_texture()
        if texture is None:
            messagebox.showinfo("Textures", "Aucune texture sélectionnée.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Exporter la texture en PNG",
            initialfile=f"{texture.name or 'texture'}.png",
            defaultextension=".png", filetypes=[("PNG", "*.png")])
        if not path:
            return
        try:
            mip = int(self.mip_var.get())
        except ValueError:
            mip = 0
        self._set_busy(True, "Décodage…")
        try:
            texture.save_png(path, mip)
        finally:
            self._set_busy(False)
        self.status(f"Texture exportée : {path}")

    @_action
    def export_textures(self) -> None:
        if not self.texture_file:
            messagebox.showinfo("Textures",
                                "Ouvre d'abord un fichier BNTX.", parent=self)
            return
        folder = filedialog.askdirectory(title="Dossier d'export des textures")
        if not folder:
            return
        base = Path(folder)
        written = 0
        errors = []
        self._set_busy(True, "Export des textures…")
        try:
            for texture in self.texture_file.textures:
                name = re.sub(r"[^\w\-. ]+", "_", texture.name or f"tex{texture.index}")
                try:
                    texture.save_png(base / f"{name}.png")
                    written += 1
                except (HookyError, OSError) as exc:
                    errors.append(f"{name} : {exc}")
        finally:
            self._set_busy(False)
        self.status(f"{written} texture(s) exportée(s) vers {base}")
        if errors:
            messagebox.showwarning("Textures", "\n".join(errors[:20]), parent=self)

    # ------------------------------------------------------------------ modèle 3D
    def _load_bfres(self, level: Level) -> None:
        self._load_bfres_from_data(level.data)

    def _load_bfres_from_data(self, data: bytes) -> None:
        if not data or data[:4] != b"FRES":
            return
        try:
            file = bfres_mod.BfresFile.parse(data)
        except HookyError as exc:
            self._set_model_text(f"BFRES illisible : {exc}")
            return
        self.bfres_file = file
        self._set_model_text(file.info())
        lods = 1
        for model in file.models:
            for shape in model.shapes:
                lods = max(lods, shape.lod_count)
        self.lod_box.configure(values=[str(i) for i in range(lods)])
        self.lod_var.set("0")

    def _current_lod(self) -> int:
        try:
            return max(0, int(self.lod_var.get()))
        except ValueError:
            return 0

    def _model_source(self) -> bfres_mod.BfresFile | None:
        """BFRES courant ou sélectionné."""
        if self.bfres_file is not None:
            return self.bfres_file
        doc = self.current
        if not doc:
            return None
        data = doc.top.data
        if data[:4] == b"FRES":
            try:
                self.bfres_file = bfres_mod.BfresFile.parse(data)
                return self.bfres_file
            except HookyError:
                return None
        return None

    def _meshes_for_view(self) -> list[MeshData]:
        file = self._model_source()
        if file is None:
            raise HookyError(
                "Aucun modèle BFRES chargé.\n\n"
                "Ouvre un .bfres (ou une entrée .bfres) puis réessaie.")
        lod = self._current_lod()
        meshes: list[MeshData] = []
        for model in file.models:
            meshes += model.build_meshes(lod, file.warnings)
        if not meshes:
            raise HookyError("Aucun maillage exploitable dans ce BFRES.\n\n"
                             + "\n".join(file.warnings[:8]))
        return meshes

    @_action
    def view_model(self) -> None:
        doc = self.current
        if not doc:
            raise HookyError("Ouvre d'abord un fichier (BFRES ou OBJ).")
        indices = self._selected_indices()
        if doc and doc.top.archive is not None and len(indices) == 1:
            entry = doc.top.archive.entries[indices[0]]
            raw = entry.data
            if is_zstd(raw):
                raw = self.dicts.decompress(raw)
            if raw[:4] == b"FRES":
                bfres = bfres_mod.BfresFile.parse(raw)
                meshes = []
                for model in bfres.models:
                    meshes += model.build_meshes(self._current_lod(), bfres.warnings)
                if meshes:
                    open_viewer(self, meshes, f"Viewer 3D — {entry.name}")
                    return
            if entry.name.lower().endswith(".obj"):
                open_viewer(self, [load_obj(raw)], f"Viewer 3D — {entry.name}")
                return
        if doc and doc.top.archive is None:
            if doc.top.data[:4] == b"FRES":
                meshes = self._meshes_for_view()
                open_viewer(self, meshes, f"Viewer 3D — {doc.top.name}")
                return
            if doc.top.name.lower().endswith(".obj"):
                open_viewer(self, [load_obj(doc.top.data)], f"Viewer 3D — {doc.top.name}")
                return
        meshes = self._meshes_for_view()
        open_viewer(self, meshes, "Viewer 3D")

    @_action
    def export_model_obj(self) -> None:
        meshes = self._meshes_for_view()
        path = filedialog.asksaveasfilename(title="Exporter en OBJ", defaultextension=".obj",
                                            initialfile="modele.obj",
                                            filetypes=[("Wavefront OBJ", "*.obj")])
        if not path:
            return
        export_obj(meshes, path)
        self.status(f"Modèle exporté : {path}")

    @_action
    def convert_blend(self) -> None:
        doc = self.current
        if not doc:
            messagebox.showinfo("Convertir", "Ouvre d'abord un fichier.", parent=self)
            return

        temp_dir = Path(tempfile.mkdtemp(prefix="hookymod_blend_"))
        source: Path | None = None
        self.bfres_file = None
        if doc.top.archive is None and doc.top.data[:4] == b"FRES":
            try:
                meshes = self._meshes_for_view()
            except HookyError as exc:
                raise HookyError(
                    f"{exc}\n\nAstuce : « Exporter OBJ » permet d'importer le maillage "
                    "manuellement dans Blender.") from exc
            source = temp_dir / "modele.obj"
            export_obj(meshes, source)
        elif doc.top.archive is None:
            name = doc.top.name
            suffix = Path(name).suffix.lower()
            if suffix not in (".obj", ".fbx", ".glb", ".gltf", ".bfres", ".sbfres"):
                raise HookyError(
                    f"Format « {suffix or name} » non convertible en .blend.\n"
                    "Formats gérés : .bfres (via OBJ), .obj, .fbx, .glb/.gltf.")
            target = temp_dir / name
            write_atomic(target, doc.top.data)
            source = target
        else:
            indices = self._selected_indices()
            if len(indices) == 1:
                entry = doc.top.archive.entries[indices[0]]
                raw = entry.data
                if is_zstd(raw):
                    raw = self.dicts.decompress(raw)
                if raw[:4] == b"FRES":
                    bfres = bfres_mod.BfresFile.parse(raw)
                    meshes = []
                    for model in bfres.models:
                        meshes += model.build_meshes(self._current_lod(), bfres.warnings)
                    if meshes:
                        source = temp_dir / (Path(entry.name).stem + ".obj")
                        export_obj(meshes, source)
                else:
                    target = temp_dir / entry.name
                    write_atomic(target, raw)
                    source = target
        if source is None:
            raise HookyError(
                "Sélectionne un modèle (.bfres) ou un fichier .obj/.fbx/.glb à convertir.")

        blender = find_blender(str(self.config_data.get("blender", "")))
        if not blender:
            chosen = filedialog.askopenfilename(
                title="Blender introuvable — sélectionne l'exécutable blender")
            if not chosen:
                return
            blender = chosen
            self.config_data["blender"] = chosen
            self._save_config()

        out = filedialog.asksaveasfilename(title="Enregistrer le .blend",
                                           defaultextension=".blend",
                                           initialfile=source.stem + ".blend",
                                           filetypes=[("Blender", "*.blend")])
        if not out:
            return
        script = temp_dir / "to_blend.py"
        script.write_text(BLEND_SCRIPT)

        def work():
            result = subprocess.run(
                [blender, "-b", "--python", str(script), "--", str(source), out],
                capture_output=True, text=True, timeout=900)
            return result

        def done(result):
            log = (result.stdout or "") + "\n" + (result.stderr or "")
            if "CONVERSION_OK" in (result.stdout or "") and Path(out).exists():
                self.status(f"Converti : {out}")
                messagebox.showinfo("Conversion", f"Fichier créé :\n{out}\n\n"
                                    f"Source temporaire : {source}", parent=self)
            else:
                self.status("Conversion échouée")
                messagebox.showerror(
                    "Conversion échouée",
                    "Blender n'a pas produit de .blend.\n\n"
                    + log[-2000:] +
                    "\n\nEssaie « Exporter OBJ » puis importe le fichier dans Blender.",
                    parent=self)

        self._run_bg("Conversion Blender en cours…", work, done)

    # ------------------------------------------------------------------ paramètres
    @_action
    def settings_dialog(self) -> None:
        win = tk.Toplevel(self)
        win.title("Paramètres")
        win.configure(bg=BG)
        win.transient(self)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)

        blender_var = tk.StringVar(value=str(self.config_data.get("blender", "")))
        level_var = tk.StringVar(value=str(self.config_data.get("compress_level", DEFAULT_LEVEL)))
        preview_var = tk.StringVar(value=str(self.config_data.get("preview_max", 1024)))

        ttk.Label(frame, text="Exécutable Blender").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=blender_var, width=54).grid(row=0, column=1, pady=4)

        def browse_blender() -> None:
            path = filedialog.askopenfilename(title="Sélectionner blender")
            if path:
                blender_var.set(path)

        ttk.Button(frame, text="…", width=3, command=browse_blender).grid(row=0, column=2,
                                                                          padx=(4, 0))
        ttk.Label(frame, text="Niveau de compression zstd (1-22)").grid(row=1, column=0,
                                                                       sticky="w", pady=4)
        ttk.Spinbox(frame, from_=1, to=22, textvariable=level_var, width=6).grid(
            row=1, column=1, sticky="w", pady=4)
        ttk.Label(frame, text="Résolution max d'aperçu des textures").grid(
            row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(frame, from_=128, to=4096, increment=128, textvariable=preview_var,
                    width=8).grid(row=2, column=1, sticky="w", pady=4)

        info = (f"romfs : {self.romfs or 'non défini'}\n"
                f"dictionnaires zstd : {len(self.dicts)}\n"
                f"module zstandard : {'oui' if zstd_available() else 'NON (pip install zstandard)'}\n"
                f"texture2ddecoder (BC7/ASTC) : "
                f"{'oui' if bntx_mod._t2d is not None else 'non (optionnel)'}")
        ttk.Label(frame, text=info, style="Muted.TLabel", justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(10, 4))
        if self.dicts.sources:
            ttk.Label(frame, text="Fichiers de dictionnaires :\n" +
                      "\n".join(str(p) for p in self.dicts.sources),
                      style="Muted.TLabel", justify="left").grid(
                row=4, column=0, columnspan=3, sticky="w")

        def save_settings() -> None:
            self.config_data["blender"] = blender_var.get().strip()
            try:
                level = int(level_var.get())
                self.config_data["compress_level"] = max(1, min(22, level))
            except ValueError:
                pass
            try:
                preview = int(preview_var.get())
                self.config_data["preview_max"] = max(128, min(4096, preview))
            except ValueError:
                pass
            self._save_config()
            win.destroy()
            self.status("Paramètres enregistrés.")

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Annuler", command=win.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Enregistrer", style="Accent.TButton",
                   command=save_settings).pack(side="right")
        win.grab_set()

    # ------------------------------------------------------------------ aide
    @_action
    def show_shortcuts(self) -> None:
        messagebox.showinfo(
            "Raccourcis",
            "Ctrl+O  ouvrir un fichier\n"
            "Ctrl+S  enregistrer sous\n"
            "Ctrl+E  exporter en mod\n"
            "Ctrl+R  choisir le dossier romfs\n"
            "Ctrl+P  patch chercher/remplacer\n"
            "Ctrl+B  convertir en .blend\n"
            "Alt+←   remonter d'un niveau\n"
            "F5      rafraîchir l'aperçu\n\n"
            "Viewer 3D : glisser = tourner, clic droit = déplacer, molette = zoom,\n"
            "W = fil de fer, B = faces arrière, F = recadrer.", parent=self)

    @_action
    def show_about(self) -> None:
        messagebox.showinfo(
            "À propos",
            f"{APP_NAME}\n\n"
            "Explorateur et outilleur d'assets pour dumps romfs Switch :\n"
            "SARC, zstd (.zs + dictionnaires), BFRES, BNTX.\n\n"
            "Aucun asset du jeu n'est inclus : fournis ton propre dump.\n"
            "Projet indépendant, non affilié à Nintendo.", parent=self)

    # ------------------------------------------------------------------ fermeture
    def _on_close(self) -> None:
        dirty = self._dirty_documents()
        if dirty and not messagebox.askyesno(
                "Quitter",
                f"{len(dirty)} fichier(s) contiennent des modifications non exportées.\n"
                "Quitter quand même ?", parent=self):
            return
        self._save_config()
        self.destroy()


def main() -> int:
    """Point d'entrée de l'application graphique."""
    try:
        app = App()
    except tk.TclError as exc:
        print("Impossible d'ouvrir la fenêtre Tkinter :", exc, file=sys.stderr)
        print("Astuce : utilise l'interface en ligne de commande : "
              "python -m hookymod.cli --help", file=sys.stderr)
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
