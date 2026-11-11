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
from . import diagnose
from . import identify
from . import nup as nup_mod
from . import theme as theme_mod
from . import restbl as restbl_mod
from . import translate as translate_mod
from .container import Document, Level, detect_kind
from .mesh import MeshData, export_obj, load_obj
from .sarc import SarcArchive
from .util import (FormatError, HookyError, ascii_strings, hexdump, human_size,
                   safe_child, write_atomic)
from .viewer import open_viewer
from .zs import DEFAULT_LEVEL, ZstdDicts, find_dict_files, frame_info, is_zstd, zstd_available

APP_NAME = "HookyMod · Asset Explorer"
APP_TAGLINE = "Assets Switch 2 · Hookshot"

#: Palette de départ (les widgets « bruts » sont recolorés au changement de thème).
_DEFAULT = theme_mod.get_palette(theme_mod.DEFAULT_THEME)
BG, PANEL, PANEL2, FG, MUTED, ACC = (_DEFAULT.bg, _DEFAULT.surface,
                                     _DEFAULT.surface2, _DEFAULT.fg,
                                     _DEFAULT.muted, _DEFAULT.accent)
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

#: Dispositions de pixels proposées dans l'onglet Textures (Switch 1 / Switch 2).
SWIZZLE_CHOICES = ("Auto", "Switch 1 (X1)", "Switch 2 (Orin)")
_SWIZZLE_TO_CONFIG = {"Auto": "auto", "Switch 1 (X1)": "x1", "Switch 2 (Orin)": "orin"}
_CONFIG_TO_SWIZZLE = {value: label for label, value in _SWIZZLE_TO_CONFIG.items()}

TEXT_EXT = {".txt", ".xml", ".json", ".yml", ".yaml", ".csv", ".md", ".ini",
            ".html", ".css", ".js", ".py", ".byml", ".aamp"}


#: Boutons de la barre d'outils : (libellé, méthode, style, infobulle).
#: Chaque action n'apparaît qu'ici : les actions de contenu (extraction, modèle 3D,
#: textures) vivent dans les menus ou dans l'onglet correspondant.
TOOLBAR_ACTIONS = (
    ("romfs…", "set_romfs", "Accent.TButton",
     "Choisir le dossier romfs du jeu (Ctrl+R)"),
    ("Ouvrir…", "open_file", "TButton", "Ouvrir un asset (Ctrl+O)"),
    (".nup…", "open_nup", "TButton",
     "Ouvrir un paquet Switch 2 .nup et extraire son romfs"),
    ("← Retour", "back", "TButton", "Remonter d'un niveau (Alt+←)"),
    ("Enregistrer", "save", "TButton", "Enregistrer le fichier courant (Ctrl+S)"),
    ("Exporter en mod", "export_mod", "Accent.TButton",
     "Créer mods/<nom>/romfs/… pour LayeredFS (Ctrl+E)"),
)

#: Actions des menus : chaque libellé français n'est défini qu'une fois.
MENU_ACTIONS = (
    ("Choisir un dossier romfs…", "set_romfs"),
    ("Ouvrir un fichier…", "open_file"),
    ("Ouvrir un paquet .nup (Switch 2)…", "open_nup"),
    ("Enregistrer sous…", "save"),
    ("Voir les modifications…", "show_modifications"),
    ("Extraire la sélection…", "extract_selection"),
    ("Tout extraire…", "extract_all"),
    ("Remplacer la sélection…", "replace_selection"),
    ("Remplacement par lot…", "batch_replace"),
    ("Patch chercher/remplacer…", "patch_dialog"),
    ("Exporter toutes les textures (PNG)…", "export_textures"),
    ("Recharger les dictionnaires", "reload_dicts"),
    ("Scanner un dossier (formats)…", "scan_folder"),
    ("Traduire un romfs pour l'édition PC…", "translate_unpack"),
    ("Reconstruire un mod depuis un projet…", "translate_pack"),
    ("Paramètres…", "settings_dialog"),
)


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


class LoadingPopup(tk.Toplevel):
    """Petite fenêtre « Chargement… » non bloquante, avec barre de progression.

    Utilisée pour les opérations longues et opaques (ouverture/extraction d'un
    paquet ``.nup``, gros romfs). Elle ne prend pas la main (pas de ``grab``) :
    l'utilisateur garde la main, et elle se ferme toujours, même en cas d'erreur.

    Les mises à jour venant d'un fil de travail passent par un simple dictionnaire
    lu par une boucle ``after`` du fil Tk : aucun appel Tk hors du fil principal.
    """

    def __init__(self, master, text: str = "Chargement…", title: str = "HookyMod") -> None:
        super().__init__(master)
        self._state: dict = {"done": 0, "total": None, "text": text, "closed": False}
        self._after_id = None
        self._destroyed = False
        try:
            self.title(title)
            self.configure(bg=BG)
            self.resizable(False, False)
            try:
                self.transient(master)
                self.attributes("-topmost", True)
            except tk.TclError:
                pass
            frame = ttk.Frame(self, padding=(18, 14))
            frame.pack(fill="both", expand=True)
            self.text_var = tk.StringVar(value=text)
            ttk.Label(frame, textvariable=self.text_var, style="Panel.TLabel").pack(
                anchor="w")
            self.bar = ttk.Progressbar(frame, mode="indeterminate", length=340)
            self.bar.pack(fill="x", pady=(10, 0))
            self.bar.start(14)
            self.update_idletasks()
            self._center(master)
            self._after_id = self.after(120, self._poll)
        except Exception:  # noqa: BLE001 - un popup ne doit jamais bloquer l'app
            pass

    def _center(self, master) -> None:
        try:
            width = max(360, int(self.winfo_reqwidth()))
            height = max(90, int(self.winfo_reqheight()))
            x = int(master.winfo_rootx()) + max(0, (int(master.winfo_width()) - width) // 2)
            y = int(master.winfo_rooty()) + max(0, (int(master.winfo_height()) - height) // 3)
            self.geometry(f"+{x}+{y}")
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- fil de travail
    def set_text(self, text: str) -> None:
        """Appelable depuis n'importe quel fil (écriture d'état uniquement)."""
        self._state["text"] = text

    def set_progress(self, done: int, total: int | None = None) -> None:
        self._state["done"] = int(done)
        if total:
            self._state["total"] = int(total)

    def request_close(self) -> None:
        """Demande la fermeture (le fil Tk détruit réellement la fenêtre)."""
        self._state["closed"] = True

    # ---------------------------------------------------------------- fil Tk
    def _poll(self) -> None:
        self._after_id = None
        if self._destroyed:
            return
        if self._state.get("closed"):
            self.close()
            return
        try:
            text = str(self._state["text"])
            done, total = self._state["done"], self._state["total"]
            if total:
                if str(self.bar.cget("mode")) != "determinate":
                    self.bar.stop()
                    self.bar.configure(mode="determinate", maximum=total)
                self.bar["value"] = min(done, total)
                self.text_var.set(f"{text}   {done}/{total}")
            else:
                self.text_var.set(text if done <= 0 else f"{text}   {done} fichier(s)")
            self._after_id = self.after(120, self._poll)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._state["closed"] = True
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:  # noqa: BLE001
                pass
            self._after_id = None
        try:
            self.bar.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except Exception:  # noqa: BLE001
            pass


class App(tk.Tk):
    # ------------------------------------------------------------------ init
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1520x880")
        self.minsize(1020, 620)
        self.configure(bg=BG)

        # --- état
        self.config_data = self._load_config()
        self.palette = theme_mod.get_palette(self.config_data.get("theme"))
        self.configure(bg=self.palette.bg)
        self._apply_theme()
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

        # --- caches (chargement paresseux : rien n'est analysé sans affichage)
        self._bntx_cache: dict[str, bntx_mod.BntxFile] = {}
        self._bfres_cache: dict[str, bfres_mod.BfresFile] = {}
        self._texture_previews: dict[tuple, tuple[Path, int, int]] = {}
        self._mesh_cache: dict[tuple, list[MeshData]] = {}
        self._popups: list[LoadingPopup] = []
        self._striped_rows: list = []
        self._active_source: str = ""

        self.filter_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Prêt.")
        self.romfs_var = tk.StringVar(value="romfs : non défini")

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status()
        self._apply_theme()   # les widgets Tk bruts (Text, Canvas) naissent colorés
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
        """Applique la palette courante (ttk + widgets Tk bruts)."""
        theme_mod.apply_theme(ttk.Style(self), self.palette)
        self._recolor_widgets()

    def set_theme(self, name: str) -> None:
        """Change de thème à chaud, sans reconstruire l'interface."""
        self.palette = theme_mod.get_palette(name)
        self.config_data["theme"] = self.palette.name
        self.configure(bg=self.palette.bg)
        self._save_config()
        try:
            self._apply_theme()
            if self.theme_var.get() != self.palette.name:
                self.theme_var.set(self.palette.name)
            self.status(f"Thème {self.palette.label.lower()} appliqué.")
        except tk.TclError:
            pass

    def _recolor_widgets(self) -> None:
        """Recolore les widgets Tk « bruts » que ttk ne gère pas."""
        p = self.palette
        for widget, options in (
            (getattr(self, "preview", None),
             dict(bg=p.surface, fg=p.fg, insertbackground=p.fg,
                  selectbackground=p.accent, selectforeground=p.accent_fg)),
            (getattr(self, "model_text", None),
             dict(bg=p.surface, fg=p.fg, insertbackground=p.fg,
                  selectbackground=p.accent, selectforeground=p.accent_fg)),
            (getattr(self, "texture_canvas", None),
             dict(bg=p.canvas, highlightthickness=0)),
        ):
            if widget is None or callable(widget):
                continue
            try:
                widget.configure(**options)
            except tk.TclError:
                pass
        for tree in (getattr(self, "entry_tree", None), getattr(self, "texture_tree", None),
                     getattr(self, "fs", None)):
            if tree is None or callable(tree):
                continue
            try:
                tree.tag_configure("pair", background=p.row_alt)
                tree.tag_configure("impair", background=p.surface)
            except tk.TclError:
                pass

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
        outils.add_command(label="Exporter toutes les textures (PNG)…",
                           command=self.export_textures)
        outils.add_command(label="Recharger les dictionnaires", command=self.reload_dicts)
        outils.add_command(label="Scanner un dossier (formats)…", command=self.scan_folder)
        outils.add_separator()
        outils.add_command(label="Traduire un romfs pour l'édition PC…",
                           command=self.translate_unpack)
        outils.add_command(label="Reconstruire un mod depuis un projet…",
                           command=self.translate_pack)
        outils.add_command(label="Paramètres…", command=self.settings_dialog)
        menubar.add_cascade(label="Outils", menu=outils)

        affichage = tk.Menu(menubar, tearoff=0)
        self.theme_var = tk.StringVar(value=self.palette.name)
        for key, label in (("sombre", "Thème sombre"), ("clair", "Thème clair")):
            affichage.add_radiobutton(label=label, value=key, variable=self.theme_var,
                                      command=lambda k=key: self.set_theme(k))
        affichage.add_separator()
        affichage.add_command(label="Onglet Aperçu", command=lambda: self.select_tab(0),
                              accelerator="Ctrl+1")
        affichage.add_command(label="Onglet Textures", command=lambda: self.select_tab(1),
                              accelerator="Ctrl+2")
        affichage.add_command(label="Onglet Modèle 3D", command=lambda: self.select_tab(2),
                              accelerator="Ctrl+3")
        affichage.add_separator()
        affichage.add_command(label="Diagnostic du fichier courant…",
                              command=self.show_diagnostics)
        menubar.add_cascade(label="Affichage", menu=affichage)

        aide = tk.Menu(menubar, tearoff=0)
        aide.add_command(label="Raccourcis clavier", command=self.show_shortcuts)
        aide.add_command(label="Diagnostic de l'installation…",
                         command=self.show_environment_diagnostics)
        aide.add_command(label="À propos", command=self.show_about)
        menubar.add_cascade(label="Aide", menu=aide)
        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        """Bandeau de marque + barre d'outils des actions globales.

        Les actions liées à un contenu précis vivent à un seul endroit :
        extraction/remplacement dans le menu « Édition », textures et modèle 3D
        dans leurs onglets respectifs.
        """
        head = ttk.Frame(self, style="Header.TFrame", padding=(14, 10, 14, 2))
        head.pack(fill="x")
        brand = ttk.Frame(head, style="Header.TFrame")
        brand.pack(side="left")
        ttk.Label(brand, text="HookyMod", style="BrandAccent.TLabel").pack(side="left")
        ttk.Label(brand, text="Asset Explorer", style="Brand.TLabel").pack(
            side="left", padx=(6, 0))
        ttk.Label(brand, text=APP_TAGLINE, style="Muted.TLabel").pack(
            side="left", padx=(14, 0))
        self.romfs_badge = ttk.Label(head, textvariable=self.romfs_var,
                                     style="Badge.TLabel")
        self.romfs_badge.pack(side="right")

        bar = ttk.Frame(self, style="Toolbar.TFrame", padding=(10, 8, 10, 8))
        bar.pack(fill="x", pady=(6, 0))
        for text, method, style, tooltip in TOOLBAR_ACTIONS:
            button = ttk.Button(bar, text=text, command=getattr(self, method), style=style)
            button.pack(side="left", padx=(0, 6))
            self._tooltip(button, tooltip)
        ttk.Button(bar, text="⚙", width=3, style="Tool.TButton",
                   command=self.settings_dialog).pack(side="right")
        self.env_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.env_var, style="Muted.TLabel").pack(
            side="right", padx=(0, 10))

    def _build_body(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=14, pady=(10, 8))

        # --- 1. explorateur romfs
        left = ttk.Frame(paned)
        search_bar = ttk.Frame(left)
        search_bar.pack(fill="x", pady=(0, 4))
        ttk.Label(search_bar, text="romfs", style="Muted.TLabel").pack(side="left", padx=(0, 6))
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
        head = ttk.Frame(middle)
        head.pack(fill="x", pady=(0, 4))
        self.header_var = tk.StringVar(value="Aucun fichier ouvert")
        ttk.Label(head, textvariable=self.header_var, style="Muted.TLabel").pack(side="left")
        ttk.Entry(head, textvariable=self.filter_var, width=18).pack(side="right")
        ttk.Label(head, text="Filtrer :", style="Muted.TLabel").pack(side="right", padx=(0, 6))
        self.filter_var.trace_add("write", lambda *a: self._refresh_entries())
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
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        paned.add(self.notebook, weight=4)

    def _build_texture_tab(self) -> None:
        self.texture_tab = ttk.Frame(self.notebook, padding=8, style="Surface.TFrame")
        top = ttk.Frame(self.texture_tab, style="Surface.TFrame")
        top.pack(fill="x")
        self.texture_info_var = tk.StringVar(
            value="Sélectionne un fichier .bntx pour afficher ses textures.")
        ttk.Label(top, textvariable=self.texture_info_var, style="Panel.TLabel").pack(
            side="left", fill="x", expand=True)
        ttk.Label(top, text="Mip", style="Panel.TLabel").pack(side="left", padx=(6, 2))
        self.mip_var = tk.StringVar(value="0")
        self.mip_box = ttk.Combobox(top, textvariable=self.mip_var, width=4, values=["0"],
                                    state="readonly")
        self.mip_box.pack(side="left")
        self.mip_box.bind("<<ComboboxSelected>>", lambda e: self._redraw_texture())
        ttk.Label(top, text="Pixels", style="Panel.TLabel").pack(side="left", padx=(8, 2))
        self.swizzle_var = tk.StringVar(value=self._swizzle_choice())
        self.swizzle_box = ttk.Combobox(top, textvariable=self.swizzle_var, width=10,
                                        values=list(SWIZZLE_CHOICES), state="readonly")
        self.swizzle_box.pack(side="left")
        self.swizzle_box.bind("<<ComboboxSelected>>",
                              lambda e: self._set_swizzle_choice(self.swizzle_var.get()))
        ttk.Button(top, text="Ajuster", width=8,
                   command=lambda: self._redraw_texture(keep_view=False)).pack(
            side="left", padx=(6, 0))
        ttk.Button(top, text="PNG…", width=6, command=self.export_selected_texture).pack(
            side="left", padx=(6, 0))

        body = ttk.Frame(self.texture_tab, style="Surface.TFrame")
        body.pack(fill="both", expand=True, pady=(8, 0))
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

        canvas_frame = ttk.Frame(body, style="Surface.TFrame")
        canvas_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))
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
        self.model_tab = ttk.Frame(self.notebook, padding=10, style="Surface.TFrame")
        controls = ttk.Frame(self.model_tab, style="Surface.TFrame")
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
        self.lod_box.bind("<<ComboboxSelected>>", lambda e: self._mesh_cache.clear())
        ttk.Label(controls, text="le BFRES n'est analysé qu'à l'ouverture de cet onglet",
                  style="PanelMuted.TLabel").pack(side="right")
        self.model_text = tk.Text(self.model_tab, bg=PANEL, fg=FG, relief="flat",
                                  font=("Consolas", 10), wrap="word", padx=8, pady=6)
        self.model_text.pack(fill="both", expand=True, pady=(8, 0))
        self.notebook.add(self.model_tab, text="Modèle 3D")

    # ------------------------------------------------------------------ onglets
    def _tab_index(self, widget) -> int:
        try:
            return self.notebook.index(widget)
        except Exception:  # noqa: BLE001 - stub Tk des tests
            return -1

    def select_tab(self, index: int) -> None:
        """Affiche un onglet (0 = Aperçu, 1 = Textures, 2 = Modèle 3D) et le charge."""
        try:
            self.notebook.select(index)
        except Exception:  # noqa: BLE001
            return
        self._on_tab_changed()

    def _on_tab_changed(self, _event=None) -> None:
        """Charge l'onglet visible seulement : pas de travail inutile en arrière-plan."""
        try:
            index = self.notebook.index(self.notebook.select())
        except Exception:  # noqa: BLE001 - stub Tk des tests
            return
        if not isinstance(index, int):
            return
        if index == self._tab_index(self.texture_tab):
            self._ensure_textures()
        elif index == self._tab_index(self.model_tab):
            self._ensure_model()

    def _tab_source(self, magic: bytes) -> tuple[bytes, str] | None:
        """Octets de la source affichée si elle commence par ``magic``.

        Priorité à l'entrée sélectionnée dans l'archive, sinon au fichier
        ouvert. Les .zs sont décompressés avec les dictionnaires du jeu.
        """
        doc = self.current
        if doc is None:
            return None
        indices = self._selected_indices()
        if doc.top.archive is not None:
            if len(indices) != 1:
                return None
            entry = doc.top.archive.entries[indices[0]]
            data = self._entry_payload(entry)
            if data[:4] != magic:
                return None
            return data, f"{self.current_key}!{entry.name}"
        try:
            data = doc.top.data
        except HookyError:
            return None
        if data[:4] != magic:
            return None
        return data, f"{self.current_key}"

    def _swizzle_choice(self) -> str:
        """Libellé courant du sélecteur de disposition des pixels."""
        value = str(self.config_data.get("texture_orin", "auto")).lower()
        return _CONFIG_TO_SWIZZLE.get(value, "Auto")

    def _set_swizzle_choice(self, label: str) -> None:
        """Force la disposition des pixels puis recharge les textures affichées."""
        self.config_data["texture_orin"] = _SWIZZLE_TO_CONFIG.get(label, "auto")
        self._save_config()
        self._bntx_cache.clear()
        self._texture_previews.clear()
        self.texture_file = None
        self.swizzle_var.set(self._swizzle_choice())
        self.status(f"Disposition des pixels : {label.lower()}.")
        self._ensure_textures()

    def _orin_setting(self) -> bool | None:
        """Disposition des pixels demandée par l'utilisateur (None = automatique)."""
        value = str(self.config_data.get("texture_orin", "auto")).lower()
        if value in ("x1", "switch1", "switch 1", "non", "false"):
            return False
        if value in ("orin", "switch2", "switch 2", "oui", "true"):
            return True
        return None

    def _entry_payload(self, entry) -> bytes:
        """Données d'une entrée, décompressées si possible (jamais d'exception)."""
        data = entry.data
        if is_zstd(data):
            try:
                return self.dicts.decompress(data)
            except HookyError:
                return b""
        return data

    def _ensure_textures(self) -> None:
        if self.texture_file is not None:
            self._redraw_texture_if_needed()
            return
        source = self._tab_source(b"BNTX")
        if source is None:
            self.texture_info_var.set(
                "Aucune texture ici : ouvre un fichier .bntx ou sélectionne une entrée .bntx.")
            return
        data, key = source
        self._load_textures_from_data(data, key=key)

    def _ensure_model(self) -> None:
        if self.bfres_file is not None:
            return
        source = self._tab_source(b"FRES")
        if source is None:
            self._set_model_text(
                "Aucun modèle BFRES à afficher.\n\n"
                "Ouvre un fichier .bfres ou sélectionne une entrée .bfres :\n"
                "l'analyse se fait uniquement ici, quand cet onglet est ouvert.")
            return
        data, key = source
        self._load_bfres_from_data(data, key=key)

    def _build_status(self) -> None:
        bar = ttk.Frame(self, style="Surface.TFrame")
        bar.pack(fill="x")
        self.state_dot = ttk.Label(bar, text="●", style="Status.TLabel")
        self.state_dot.configure(foreground=self.palette.ok)
        self.state_dot.pack(side="left", padx=(4, 0))
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
        self.bind("<Control-Key-1>", lambda e: self.select_tab(0))
        self.bind("<Control-Key-2>", lambda e: self.select_tab(1))
        self.bind("<Control-Key-3>", lambda e: self.select_tab(2))
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
            "Édite les assets du remake Ocarina of Time (Switch 2) et prépare des mods\n"
            "pour Hookshot.\n\n"
            "1. « romfs… » : choisis le dossier romfs de ton dump (les dictionnaires zstd\n"
            "   de Pack/ZsDic.pack.zs sont chargés automatiquement).\n"
            "   « .nup… » : ouvre directement un paquet Switch 2 et extrais son romfs.\n"
            "2. Sélectionne un fichier à gauche : il s'ouvre dans la colonne du milieu.\n"
            "3. Double-clique sur un fichier .zs ou une archive SARC pour entrer dedans.\n"
            "4. Les onglets s'ouvrent tout seuls selon le contenu : « Textures » pour un\n"
            "   .bntx, « Modèle 3D » pour un .bfres (analysés à l'affichage seulement).\n"
            "5. Modifie (menu Édition) puis « Exporter en mod ».\n\n"
            "--- Si un fichier ne s'affiche pas --------------------------------------\n"
            "• Affichage → « Diagnostic du fichier courant… » (magic, version, disposition\n"
            "  des pixels, sections) : c'est le premier réflexe à avoir.\n"
            "• Aide → « Diagnostic de l'installation… » : vérifie texture2ddecoder\n"
            "  (ASTC/BC7 des jeux Switch 2), zstandard et l'aperçu PNG de Tk.\n"
            "• Onglet Textures : le sélecteur « Pixels » force Switch 1 (X1) ou Switch 2\n"
            "  (Orin) si la version du BNTX est atypique.\n\n"
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
        """Badge romfs (court) + état de l'environnement (discret)."""
        if self.romfs:
            self.romfs_var.set(f"romfs · {self.romfs.name or self.romfs}")
            try:
                self.romfs_badge.configure(textvariable=self.romfs_var)
            except tk.TclError:
                pass
        else:
            self.romfs_var.set("romfs · non défini")
        parts = [f"{len(self.dicts)} dictionnaire(s)"]
        if not zstd_available():
            parts.append("⚠ zstandard absent")
        if bntx_mod._t2d is None:
            parts.append("ASTC/BC7 non décodables")
        if self.romfs:
            parts.insert(0, str(self.romfs))
        env = getattr(self, "env_var", None)
        if env is not None:
            env.set("   ·   ".join(parts))

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = max(0, self._busy + (1 if busy else -1))
        if busy and label:
            self.status(label)
        self.configure(cursor="watch" if self._busy else "")
        self._update_state_dot()
        self.update_idletasks()

    def _update_state_dot(self) -> None:
        """Pastille d'état : bleu = travail en cours, vert = prêt."""
        dot = getattr(self, "state_dot", None)
        if dot is None:
            return
        try:
            dot.configure(foreground=self.palette.accent if self._busy
                          else self.palette.ok)
        except tk.TclError:
            pass

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
        self._close_popups()
        try:
            done(result)
        except Exception as exc:  # noqa: BLE001
            self.error(exc)

    def _bg_failed(self, exc, failed) -> None:
        self._set_busy(False)
        self._close_popups()
        if failed:
            failed(exc)
        else:
            self.error(exc)

    def _close_popups(self) -> None:
        """Ferme les fenêtres de chargement encore ouvertes (fin de tâche)."""
        for popup in getattr(self, "_popups", []):
            try:
                popup.close()
            except Exception:  # noqa: BLE001 - jamais bloquant
                pass
        self._popups.clear()

    def error(self, exc: Exception, context: str = "") -> None:
        if isinstance(exc, HookyError):
            message = str(exc)
        else:
            message = f"{exc}\n\n{traceback.format_exc(limit=3)}"
        self.status_var.set(f"Erreur : {exc}")
        try:  # journal disque : indispensable quand la boîte de dialogue passe inaperçue
            log = Path(tempfile.gettempdir()) / "hookymod_errors.log"
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"--- {context or 'erreur'} ---\n{message}\n")
        except OSError:
            pass
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

    def _invalidate_caches(self) -> None:
        """Les données ont changé (fichier ouvert, remplacé, patché) : caches vidés."""
        self._bntx_cache.clear()
        self._bfres_cache.clear()
        self._mesh_cache.clear()
        for path, _width, _height in self._texture_previews.values():
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        self._texture_previews.clear()
        self.texture_file = None
        self.bfres_file = None
        self._texture_view_key = None

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
        shown = 0
        for index, entry in enumerate(level.archive.entries):
            if query and query not in entry.name.lower():
                continue
            self.entry_tree.insert("", "end", iid=str(index),
                                   values=(entry.name, human_size(len(entry.data)),
                                           detect_kind(entry.data)),
                                   tags=("pair" if shown % 2 else "impair",))
            shown += 1

    def _refresh_preview(self) -> None:
        """Rafraîchit l'aperçu **sans** analyser BNTX/BFRES (chargement à la demande)."""
        doc = self.current
        self._reset_textures()
        self._set_model_text("")
        self.lod_box.configure(values=["0"])
        self.lod_var.set("0")
        self._invalidate_caches()
        if not doc:
            self._show_welcome()
            return
        level = doc.top
        if level.archive is not None:
            self._active_source = ""
            self._set_preview(
                f"{level.name}\n"
                f"Archive SARC · {len(level.archive)} entrées · "
                f"{human_size(len(level.data))}\n\n"
                "Sélectionne un fichier pour l'aperçu, double-clique pour entrer dans un\n"
                ".zs / une archive imbriquée.\n\n"
                "Menu Édition : « Extraire la sélection… » garde les octets d'origine,\n"
                "« Remplacer la sélection… » recompresse les .zs si besoin.")
            return

        try:
            data = level.data
        except HookyError as exc:
            self._set_preview(f"{level.name}\n\n⚠ {exc}")
            return
        kind = data[:4]
        self._active_source = str(self.current_key or "")
        if kind == b"BNTX":
            self.texture_info_var.set(
                "Onglet « Textures » : clique pour décoder les textures de ce fichier.")
            self.select_tab(1)
        elif kind == b"FRES":
            self._set_model_text(
                "Onglet « Modèle 3D » : le BFRES sera analysé au premier affichage.")
            self.select_tab(2)
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
        # Nouvelle source : les onglets sont invalidés, puis rechargés seulement
        # si l'un d'eux est réellement affiché (aucune analyse en arrière-plan).
        self._reset_textures()
        self.bfres_file = None
        self._set_model_text("")
        self.lod_box.configure(values=["0"])
        self.lod_var.set("0")
        data = self._entry_payload(entry)
        if data[:4] == b"BNTX":
            self.texture_info_var.set(
                "Onglet « Textures » : clique pour décoder les textures de cette entrée.")
        elif data[:4] == b"FRES":
            self._set_model_text(
                "Onglet « Modèle 3D » : le BFRES sera analysé quand tu ouvriras cet onglet.")
        self._on_tab_changed()

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
            # En-tête BNTX seulement (rapide) : le décodage se fait dans l'onglet Textures.
            try:
                info = bntx_mod.BntxFile.parse(data).info()
                lines += ["--- BNTX ---", info,
                          "(les images se décodent dans l'onglet « Textures »)", ""]
            except HookyError as exc:
                lines += [f"⚠ BNTX illisible : {exc}", ""]
        elif data[:4] == b"FRES":
            # Pas d'analyse complète ici : elle coûte cher et se fait à la demande.
            lines += ["--- BFRES ---",
                      "Modèle 3D : ouvre l'onglet « Modèle 3D » pour l'analyser et le voir.",
                      ""]
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

    # ------------------------------------------------------------------ traduction
    @_action
    def translate_unpack(self) -> None:
        """Convertit un romfs en projet PC (textures PNG, modèles OBJ)."""
        source = filedialog.askdirectory(
            title="romfs à traduire (ou son parent)",
            initialdir=str(self.romfs) if self.romfs else "")
        if not source:
            return
        folder = filedialog.askdirectory(title="Dossier du projet (pc/ et origin/ seront créés)")
        if not folder:
            return
        project = Path(folder)

        popup = LoadingPopup(
            self, f"Traduction de {Path(source).name}…\n"
                  "Les textures partent en PNG et les modèles en OBJ dans pc/.",
            title="Traduction romfs → PC")
        self._popups.append(popup)

        def work():
            try:
                return translate_mod.unpack(source, project,
                                           progress=lambda done, total:
                                           popup.set_progress(done, total))
            finally:
                popup.request_close()

        def done(manifest):
            kinds: dict[str, int] = {}
            for entry in manifest.entries:
                kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
            message = (f"Projet créé : {project}\n\n"
                       f"{len(manifest.entries)} entrée(s) : "
                       + ", ".join(f"{kind} ×{count}"
                                    for kind, count in sorted(kinds.items()))
                       + f"\n\nÀ éditer : {project / 'pc'}"
                       "\n(les PNG s'ouvrent dans GIMP/Photoshop, les OBJ dans Blender)")
            if manifest.errors:
                message += (f"\n\n⚠ {len(manifest.errors)} avertissement(s) :\n"
                            + "\n".join(manifest.errors[:8]))
            self.status(f"Projet de traduction : {project}")
            messagebox.showinfo("Traduction pour PC", message, parent=self)

        self._run_bg("Traduction du romfs…", work, done)

    @_action
    def translate_pack(self) -> None:
        """Reconstruit les fichiers modifiés d'un projet en mod romfs."""
        folder = filedialog.askdirectory(title="Projet de traduction (manifest.json)")
        if not folder:
            return
        project = Path(folder)
        destination = filedialog.askdirectory(
            title="Dossier du mod (le sous-dossier romfs/ sera créé dedans)")
        if not destination:
            return

        popup = LoadingPopup(self, "Reconstruction du mod depuis le projet…",
                             title="Reconstruire le mod")
        self._popups.append(popup)

        def work():
            try:
                return translate_mod.pack(project, destination,
                                          progress=lambda done, total:
                                          popup.set_progress(done, total))
            finally:
                popup.request_close()

        def done(result):
            message = "\n".join(result.summary())
            if result.written:
                message += f"\n\nÉcrit sous : {Path(destination) / 'romfs'}"
                self.status(f"Mod reconstruit : {len(result.written)} fichier(s)")
            else:
                self.status("Aucun fichier modifié dans le projet.")
            messagebox.showinfo("Reconstruire le mod", message, parent=self)

        self._run_bg("Reconstruction du mod…", work, done)

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

        package_size = package_path.stat().st_size if package_path.exists() else 0
        popup = LoadingPopup(
            self, f"Extraction de {package_path.name} ({human_size(package_size)})…\n"
                  "Les fichiers du paquet sont décompressés vers le dossier choisi.",
            title="Chargement du paquet .nup")
        self._popups.append(popup)

        def work():
            try:
                package = nup_mod.NupPackage.open(package_path)
                with package:
                    summary = package.summary()
                    result = package.extract(target, progress=popup.set_progress)
                return summary, result, target
            finally:
                # fermeture garantie, même en cas d'exception
                popup.request_close()

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
        self._texture_view_key = None
        self.texture_info_var.set(
            "Sélectionne un fichier .bntx pour afficher ses textures.")
        self.mip_box.configure(values=["0"])
        self.mip_var.set("0")

    def _load_textures(self, level: Level) -> None:
        self._load_textures_from_data(level.data, key=str(self.current_key or ""))

    def _load_textures_from_data(self, data: bytes, key: str = "") -> None:
        """Analyse un BNTX et affiche la première texture (idempotent grâce au cache)."""
        if not data or data[:4] != b"BNTX":
            self.texture_info_var.set("Ce fichier n'est pas un BNTX (.bntx attendu).")
            return
        key = key or self._active_source or str(len(data))
        try:
            file = self._bntx_cache.get(key)
            if file is None:
                file = bntx_mod.BntxFile.parse(data, orin=self._orin_setting())
                self._bntx_cache[key] = file
        except HookyError as exc:
            self.texture_info_var.set(f"BNTX illisible : {exc}")
            return
        self._active_source = key
        self.texture_file = file
        self.texture_tree.delete(*self.texture_tree.get_children())
        for index, texture in enumerate(file.textures):
            self.texture_tree.insert("", "end", iid=str(index),
                                     values=(texture.name or f"#{texture.index}",
                                             f"{texture.width}×{texture.height}",
                                             texture.format_name),
                                     tags=("pair" if index % 2 else "impair",))
        detail = f"BNTX {file.version_label} · {file.oriented_label}"
        self.texture_info_var.set(
            f"{len(file.textures)} texture(s) — {file.name or 'BNTX'} — {detail}"
            + ("" if file.textures else "   (aucune texture dans ce fichier)"))
        children = self.texture_tree.get_children()
        if children:
            self.texture_tree.selection_set(children[0])
            self.texture_tree.focus(children[0])
            self._redraw_texture()

    def _redraw_texture_if_needed(self) -> None:
        """Redessine seulement si la sélection a changé (évite les décodages répétés)."""
        texture = self._selected_texture()
        if texture is None:
            return
        key = (self._active_source, texture.index, self.mip_var.get())
        if key != getattr(self, "_texture_view_key", None):
            self._redraw_texture()

    def _selected_texture(self):
        selection = self.texture_tree.selection()
        if not selection or not self.texture_file:
            return None
        try:
            return self.texture_file.textures[int(selection[0])]
        except (ValueError, IndexError):
            return None

    def _preview_target(self) -> int:
        """Taille de décodage : jamais plus grand que l'écran (aperçu rapide)."""
        try:
            width = max(64, int(self.texture_canvas.winfo_width()))
            height = max(64, int(self.texture_canvas.winfo_height()))
        except (tk.TclError, TypeError, ValueError):
            width, height = 900, 600
        configured = int(self.config_data.get("preview_max", 1024) or 1024)
        return max(128, min(configured, max(width, height, 256)))

    def _redraw_texture(self, keep_view: bool = True) -> None:
        """Décode et affiche la texture sélectionnée (cache disque + ajustement)."""
        texture = self._selected_texture()
        if texture is None:
            if self.texture_file is None:
                self.texture_info_var.set(
                    "Aucune texture chargée : ouvre un .bntx ou sélectionne une entrée .bntx.")
            return
        levels = [str(i) for i in range(max(1, texture.mip_count))]
        self.mip_box.configure(values=levels)
        try:
            mip = int(self.mip_var.get())
        except (TypeError, ValueError):
            mip = 0
        if mip >= len(levels):
            mip = 0
            self.mip_var.set("0")
        target = self._preview_target()
        key = (self._active_source, texture.index, mip, target)
        self._texture_view_key = (self._active_source, texture.index, str(mip))

        cached = self._texture_previews.get(key)
        if cached is not None and cached[0].exists():
            self._show_texture(cached[0], texture, mip, cached[1], cached[2],
                               keep_view=keep_view)
            return

        self.texture_info_var.set(
            f"{texture.name or '#%d' % texture.index} — décodage ({texture.format_name})…")

        def work():
            width, height, rgba = texture.decode(mip, max_size=target)
            from .util import png_bytes
            return width, height, png_bytes(width, height, rgba)

        def done(result):
            width, height, blob = result
            folder = Path(tempfile.gettempdir()) / "hookymod_preview"
            folder.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w\-.]+", "_", texture.name or f"tex{texture.index}")
            path = folder / f"{safe}_{texture.index}_m{mip}_{target}.png"
            try:
                path.write_bytes(blob)
            except OSError:
                path = folder / f"tex_{texture.index}_m{mip}.png"
                path.write_bytes(blob)
            self._texture_previews[key] = (path, width, height)
            self._preview_png = path
            self._show_texture(path, texture, mip, width, height, keep_view=keep_view)

        def failed(exc):
            message = str(exc)
            if "texture2ddecoder" in message:
                message += ("\n\nAstuce : pip install texture2ddecoder "
                            "(ou exporte la texture une fois décodée).")
            self.texture_info_var.set(
                f"{texture.name or '#%d' % texture.index} : {message}")
            self.texture_canvas.delete("all")
            self.texture_canvas.create_text(
                12, 12, anchor="nw", fill=MUTED, width=420,
                text=f"Aperçu impossible pour {texture.format_name} :\n{message}")

        self._run_bg(f"Décodage de la texture {texture.name}…", work, done, failed)

    def _show_texture(self, path: Path, texture, mip: int, width: int, height: int,
                      keep_view: bool = True) -> None:
        """Charge le PNG en mémoire Tk (PIL en secours) et l'ajuste au canevas."""
        photo = None
        try:
            photo = tk.PhotoImage(file=str(path))
        except Exception:  # noqa: BLE001 - Tk sans PNG intégré
            photo = None
        if photo is None:
            try:
                from PIL import Image, ImageTk  # type: ignore
                photo = ImageTk.PhotoImage(Image.open(path))
            except Exception:  # noqa: BLE001
                self._photo = None
                self.texture_canvas.delete("all")
                self.texture_canvas.create_text(
                    12, 12, anchor="nw", fill=FG, width=460,
                    text=("Ce Tk ne sait pas afficher de PNG et Pillow est absent.\n\n"
                          "Installe Pillow (pip install pillow) pour l'aperçu intégré ;\n"
                          f"l'image est déjà exportée ici :\n{path}"))
                self.texture_info_var.set(
                    f"{texture.name or '#%d' % texture.index} — {width}×{height} — "
                    f"{texture.format_name} — aperçu non affichable (PNG écrit)")
                return
        canvas = self.texture_canvas
        try:
            image_w, image_h = int(photo.width()), int(photo.height())
        except (TypeError, ValueError, tk.TclError):
            image_w, image_h = width, height
        try:
            canvas_w = max(96, int(canvas.winfo_width()))
            canvas_h = max(96, int(canvas.winfo_height()))
        except (TypeError, ValueError, tk.TclError):
            canvas_w, canvas_h = 640, 480
        # ajustement : réduit l'image (sous-échantillonnage) pour tenir dans le canevas
        factor = max(1, -(-max(image_w, image_h) // max(64, min(canvas_w, canvas_h) - 8)))
        if factor > 1:
            try:
                photo = photo.subsample(factor, factor)
            except Exception:  # noqa: BLE001 - stub Tk des tests
                pass
        self._photo = photo
        canvas.delete("all")
        canvas.configure(scrollregion=(0, 0, max(image_w, canvas_w), max(image_h, canvas_h)))
        canvas.create_image(canvas_w // 2, canvas_h // 2, image=photo, anchor="center")
        note = f" — ×{factor} pour l'affichage" if factor > 1 else ""
        suffix = f"   ⚠ {' · '.join(texture.warnings)}" if getattr(texture, "warnings", None) else ""
        self.texture_info_var.set(
            f"{texture.name or '#%d' % texture.index} — {width}×{height} — "
            f"{texture.format_name} — mip {mip}{note}{suffix}")

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
    #: Nombre de triangles construits au maximum pour une session de visualisation.
    MESH_BUDGET = 250_000

    def _load_bfres(self, level: Level) -> None:
        self._load_bfres_from_data(level.data, key=str(self.current_key or ""))

    def _load_bfres_from_data(self, data: bytes, key: str = "") -> None:
        """Analyse un BFRES (une seule fois par source) et remplit l'onglet Modèle."""
        if not data or data[:4] != b"FRES":
            self._set_model_text("Ce fichier n'est pas un BFRES (.bfres attendu).")
            return
        key = key or self._active_source or str(len(data))
        try:
            file = self._bfres_cache.get(key)
            if file is None:
                file = bfres_mod.BfresFile.parse(data)
                self._bfres_cache[key] = file
        except HookyError as exc:
            self._set_model_text(f"BFRES illisible : {exc}")
            return
        self._active_source = key
        self.bfres_file = file
        lines = [file.info()]
        vertices = sum(s.vtx_buffer.vertex_count for m in file.models
                       for s in m.shapes if s.vtx_buffer)
        faces = sum(s.face_count(0) for m in file.models for s in m.shapes)
        lines += ["", f"Total : {vertices:,} sommets · {faces:,} triangles (LOD 0)",
                  "« Voir en 3D » dessine uniquement la partie visible du modèle "
                  "(budget adapté à la taille de la fenêtre)."]
        self._set_model_text("\n".join(lines))
        lods = 1
        for model in file.models:
            for shape in model.shapes:
                lods = max(lods, shape.lod_count)
        self.lod_box.configure(values=[str(i) for i in range(lods)])
        self.lod_var.set("0")

    def _current_lod(self) -> int:
        try:
            return max(0, int(self.lod_var.get()))
        except (TypeError, ValueError):
            return 0

    def _model_source(self) -> bfres_mod.BfresFile | None:
        """BFRES courant, sinon celui de la sélection (analysé à la demande)."""
        if self.bfres_file is not None:
            return self.bfres_file
        source = self._tab_source(b"FRES")
        if source is None:
            return None
        data, key = source
        self._load_bfres_from_data(data, key=key)
        return self.bfres_file

    def _meshes_for_view(self, file=None, lod: int | None = None,
                         budget: int | None = None) -> list[MeshData]:
        """Construit les maillages d'un BFRES, à la demande et sous budget.

        Seules les formes nécessaires au budget sont converties : un modèle de
        plusieurs centaines de milliers de triangles reste utilisable.
        """
        file = file if file is not None else self._model_source()
        if file is None:
            raise HookyError(
                "Aucun modèle BFRES chargé.\n\n"
                "Ouvre un .bfres (ou une entrée .bfres) puis réessaie.")
        lod = self._current_lod() if lod is None else lod
        budget = self.MESH_BUDGET if budget is None else budget
        cache_key = (id(file), lod, budget)
        cached = self._mesh_cache.get(cache_key)
        if cached is not None:
            return cached

        meshes: list[MeshData] = []
        total = 0
        for model in file.models:
            for shape in model.shapes:
                if shape.face_count(lod) and total >= budget:
                    break
                try:
                    mesh = shape.build_mesh(lod, file.warnings)
                except HookyError:
                    continue
                if mesh is None or not mesh.faces:
                    continue
                if 0 <= shape.material_index < len(model.materials):
                    mesh.material = model.materials[shape.material_index].name
                meshes.append(mesh)
                total += len(mesh.faces)
            if total >= budget:
                break
        if not meshes:
            raise HookyError("Aucun maillage exploitable dans ce BFRES.\n\n"
                             + "\n".join(file.warnings[:8]))
        self._mesh_cache[cache_key] = meshes
        if total >= budget and budget < self.MESH_BUDGET * 4:
            self.status(f"{len(meshes)} maillage(s) chargé(s) ({total:,} faces) — "
                        "au-delà du budget, la vue 3D affiche la partie visible.")
        return meshes

    @_action
    def view_model(self) -> None:
        """Ouvre le viewer 3D de l'élément courant (BFRES ou OBJ)."""
        doc = self.current
        if doc is None:
            raise HookyError("Ouvre d'abord un fichier (BFRES ou OBJ).")
        indices = self._selected_indices()
        if doc.top.archive is not None and len(indices) == 1:
            entry = doc.top.archive.entries[indices[0]]
            payload = self._entry_payload(entry)
            if payload[:4] == b"FRES":
                file = self._bfres_cache.get(f"{self.current_key}!{entry.name}")
                if file is None:
                    try:
                        file = bfres_mod.BfresFile.parse(payload)
                    except HookyError as exc:
                        raise HookyError(f"BFRES illisible : {exc}") from exc
                    self._bfres_cache[f"{self.current_key}!{entry.name}"] = file
                meshes = self._meshes_for_view(file=file)
                self._open_viewer(meshes, f"Viewer 3D — {entry.name}")
                return
            if entry.name.lower().endswith(".obj"):
                self._open_viewer([load_obj(payload)], f"Viewer 3D — {entry.name}")
                return
            raise HookyError(
                f"« {entry.name} » n'est pas un modèle : sélectionne un .bfres ou un .obj.")

        if doc.top.archive is None:
            name = doc.top.name
            if doc.top.data[:4] == b"FRES":
                self._ensure_model()
                meshes = self._meshes_for_view()
                self._open_viewer(meshes, f"Viewer 3D — {name}")
                return
            if name.lower().endswith(".obj"):
                self._open_viewer([load_obj(doc.top.data)], f"Viewer 3D — {name}")
                return
        self._open_viewer(self._meshes_for_view(), "Viewer 3D")

    def _open_viewer(self, meshes, title: str):
        """Ouvre la fenêtre 3D avec les couleurs du thème courant."""
        p = self.palette
        return open_viewer(self, meshes, title, palette={
            "bg": p.canvas, "panel": p.surface2, "fg": p.fg,
            "muted": p.muted, "accent": p.accent,
        })

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
        if doc.top.archive is None:
            name = doc.top.name
            suffix = Path(name).suffix.lower()
            if doc.top.data[:4] == b"FRES":
                try:
                    meshes = self._meshes_for_view()
                except HookyError as exc:
                    raise HookyError(
                        f"{exc}\n\nAstuce : « Exporter OBJ » permet d'importer le maillage "
                        "manuellement dans Blender.") from exc
                source = temp_dir / "modele.obj"
                export_obj(meshes, source)
            elif suffix in (".obj", ".fbx", ".glb", ".gltf", ".bfres", ".sbfres"):
                target = temp_dir / name
                write_atomic(target, doc.top.data)
                source = target
            else:
                raise HookyError(
                    f"Format « {suffix or name} » non convertible en .blend.\n"
                    "Formats gérés : .bfres (via OBJ), .obj, .fbx, .glb/.gltf.")
        else:
            indices = self._selected_indices()
            if len(indices) == 1:
                entry = doc.top.archive.entries[indices[0]]
                payload = self._entry_payload(entry)
                if payload[:4] == b"FRES":
                    key = f"{self.current_key}!{entry.name}"
                    bfres = self._bfres_cache.get(key)
                    if bfres is None:
                        bfres = bfres_mod.BfresFile.parse(payload)
                        self._bfres_cache[key] = bfres
                    meshes = self._meshes_for_view(file=bfres)
                    source = temp_dir / (Path(entry.name).stem + ".obj")
                    export_obj(meshes, source)
                else:
                    target = temp_dir / entry.name
                    write_atomic(target, payload)
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

        theme_var = tk.StringVar(value=self.palette.label)
        ttk.Label(frame, text="Thème").grid(row=3, column=0, sticky="w", pady=4)
        theme_box = ttk.Combobox(frame, textvariable=theme_var, width=12, state="readonly",
                                 values=[p.label for p in theme_mod.THEMES.values()])
        theme_box.grid(row=3, column=1, sticky="w", pady=4)
        theme_box.bind("<<ComboboxSelected>>", lambda e: self.set_theme(
            "clair" if theme_var.get().lower().startswith("clair") else "sombre"))

        swizzle_var = tk.StringVar(value=self._swizzle_choice())
        ttk.Label(frame, text="Disposition des pixels (textures)").grid(
            row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(frame, textvariable=swizzle_var, width=18, state="readonly",
                     values=list(SWIZZLE_CHOICES)).grid(row=4, column=1, sticky="w", pady=4)

        info = (f"romfs : {self.romfs or 'non défini'}\n"
                f"dictionnaires zstd : {len(self.dicts)}\n"
                f"module zstandard : {'oui' if zstd_available() else 'NON (pip install zstandard)'}\n"
                f"texture2ddecoder (ASTC/BC7) : "
                f"{'oui' if bntx_mod._t2d is not None else 'NON (pip install texture2ddecoder)'}\n"
                "« Auto » convient à la plupart des fichiers : la version du BNTX\n"
                "décide de la disposition (Switch 1 = X1, Switch 2 = Orin).")
        ttk.Label(frame, text=info, style="Muted.TLabel", justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(10, 4))
        if self.dicts.sources:
            ttk.Label(frame, text="Fichiers de dictionnaires :\n" +
                      "\n".join(str(p) for p in self.dicts.sources),
                      style="Muted.TLabel", justify="left").grid(
                row=6, column=0, columnspan=3, sticky="w")

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
            self.config_data["texture_orin"] = _SWIZZLE_TO_CONFIG.get(
                swizzle_var.get(), "auto")
            if hasattr(self, "swizzle_var"):
                self.swizzle_var.set(self._swizzle_choice())
            self._bntx_cache.clear()
            self._texture_previews.clear()
            self.texture_file = None
            self._save_config()
            win.destroy()
            self.status("Paramètres enregistrés.")

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Annuler", command=win.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Enregistrer", style="Accent.TButton",
                   command=save_settings).pack(side="right")
        win.grab_set()

    # ------------------------------------------------------------------ diagnostics
    def _current_payload(self) -> tuple[bytes, str]:
        """Octets et nom de ce qui est affiché (entrée sélectionnée sinon fichier)."""
        doc = self.current
        if doc is None:
            return b"", ""
        indices = self._selected_indices()
        if doc.top.archive is not None and len(indices) == 1:
            entry = doc.top.archive.entries[indices[0]]
            return self._entry_payload(entry), entry.name
        try:
            return doc.top.data, doc.top.name
        except HookyError:
            return b"", doc.top.name

    @_action
    def show_diagnostics(self) -> None:
        """Anatomie du fichier courant : magic, version, disposition, sections."""
        data, name = self._current_payload()
        if not data:
            messagebox.showinfo("Diagnostic",
                                "Aucun fichier ouvert : ouvre d'abord un asset.",
                                parent=self)
            return
        text = diagnose.file_report(data, name, orin=self._orin_setting())
        self._show_text_window(f"Diagnostic — {name}", text)

    def environment_report(self) -> str:
        return diagnose.environment_report(self.romfs, len(self.dicts),
                                           self.palette.label)

    @_action
    def show_environment_diagnostics(self) -> None:
        self._show_text_window("Diagnostic de l'installation",
                              self.environment_report())

    def _show_text_window(self, title: str, text: str) -> None:
        """Fenêtre de texte monospace, lecture seule, avec copie."""
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("860x620")
        win.configure(bg=self.palette.bg)
        win.transient(self)
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        body = tk.Text(frame, wrap="none", font=("Consolas", 10),
                       bg=self.palette.surface, fg=self.palette.fg, relief="flat",
                       padx=12, pady=10, insertbackground=self.palette.fg,
                       selectbackground=self.palette.accent)
        scroll = ttk.Scrollbar(frame, command=body.yview)
        body.configure(yscrollcommand=scroll.set)
        body.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        body.insert("1.0", text)
        body.configure(state="disabled")
        buttons = ttk.Frame(win, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Fermer", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Copier",
                   command=lambda: (self.clipboard_clear(),
                                    self.clipboard_append(text),
                                    self.status("Diagnostic copié dans le presse-papiers."))
                   ).pack(side="right", padx=(0, 6))

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
            "Ctrl+1  onglet Aperçu\n"
            "Ctrl+2  onglet Textures\n"
            "Ctrl+3  onglet Modèle 3D\n"
            "Alt+←   remonter d'un niveau\n"
            "F5      rafraîchir l'aperçu\n\n"
            "Viewer 3D : glisser = tourner, clic droit = déplacer, molette = zoom,\n"
            "W = fil de fer, B = faces arrière, F = recadrer.", parent=self)

    @_action
    def show_about(self) -> None:
        messagebox.showinfo(
            "À propos",
            f"{APP_NAME}\n\n"
            "Assets du remake Switch 2 d'Ocarina of Time pour le port PC Hookshot :\n"
            "SARC, zstd (.zs + dictionnaires), BFRES, BNTX (Switch 1 X1 et Switch 2 Orin).\n\n"
            f"Thème : {self.palette.label} (Affichage → thème, sans redémarrer)\n"
            "Diagnostics : Affichage → fichier courant, Aide → installation.\n\n"
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
