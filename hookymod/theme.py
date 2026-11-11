"""Thèmes de l'interface (sombre / clair) — une seule source de couleurs.

Tout passe par un :class:`Palette` : les styles ttk sont configurés ici, et les
quelques widgets Tk « bruts » (``Text``, ``Canvas``, ``Frame``, ``Label``) sont
recolorés par :meth:`hookymod.app.App._recolor_widgets` à partir de la même
palette. Changer de thème ne reconstruit donc aucun widget : on réapplique les
couleurs, l'état de l'application est conservé.
"""

from __future__ import annotations

from dataclasses import dataclass

FONT = "Segoe UI"


@dataclass(frozen=True)
class Palette:
    """Couleurs d'un thème, nommées par usage et non par teinte."""

    name: str
    label: str
    bg: str            # fond de la fenêtre
    surface: str       # panneaux (aperçu, listes)
    surface2: str      # barres, en-têtes
    surface3: str      # survol, éléments actifs
    fg: str            # texte principal
    muted: str         # texte secondaire
    accent: str        # action principale
    accent_fg: str     # texte sur l'accent
    accent_soft: str   # fonds teintés accent
    border: str        # bordures fines
    ok: str
    warn: str
    error: str
    canvas: str        # fond du canevas de texture
    row_alt: str       # ligne paire des listes

    @property
    def is_dark(self) -> bool:
        return self.name == "sombre"


DARK = Palette(
    name="sombre", label="Sombre",
    bg="#14161c", surface="#1b1f27", surface2="#222732", surface3="#2c3341",
    fg="#e8eaf0", muted="#98a2b3",
    accent="#5b8cff", accent_fg="#ffffff", accent_soft="#1e2a45",
    border="#2e3542", ok="#3ecf8e", warn="#e0a44a", error="#e5697a",
    canvas="#101216", row_alt="#1f242e",
)

LIGHT = Palette(
    name="clair", label="Clair",
    bg="#f3f5fa", surface="#ffffff", surface2="#eaeef6", surface3="#dde3ee",
    fg="#1b2130", muted="#5a6577",
    accent="#2f6bff", accent_fg="#ffffff", accent_soft="#dfe8ff",
    border="#cfd7e4", ok="#129167", warn="#a8730e", error="#c43b4b",
    canvas="#e7ebf3", row_alt="#f6f8fc",
)

THEMES: dict[str, Palette] = {DARK.name: DARK, LIGHT.name: LIGHT}
DEFAULT_THEME = DARK.name


def get_palette(name: str | None) -> Palette:
    """Palette demandée, ou sombre par défaut (jamais d'exception)."""
    return THEMES.get((name or "").strip().lower(), DARK)


def apply_theme(style, palette: Palette) -> None:
    """Configure tous les styles ttk utilisés par l'application."""
    try:
        style.theme_use("clam")
    except Exception:  # noqa: BLE001 - thème absent sur certaines installations
        pass
    style.configure(".", background=palette.bg, foreground=palette.fg,
                    fieldbackground=palette.surface, bordercolor=palette.border,
                    lightcolor=palette.bg, darkcolor=palette.bg,
                    troughcolor=palette.surface2, focuscolor=palette.accent,
                    font=(FONT, 10))
    style.map(".", foreground=[("disabled", palette.muted)])

    # --- conteneurs
    style.configure("TFrame", background=palette.bg)
    style.configure("Surface.TFrame", background=palette.surface)
    style.configure("Toolbar.TFrame", background=palette.surface2)
    style.configure("Header.TFrame", background=palette.bg)
    style.configure("TLabelframe", background=palette.bg, bordercolor=palette.border)
    style.configure("TLabelframe.Label", background=palette.bg,
                    foreground=palette.muted, font=(FONT, 9, "bold"))

    # --- textes
    style.configure("TLabel", background=palette.bg, foreground=palette.fg)
    style.configure("Muted.TLabel", background=palette.bg, foreground=palette.muted)
    style.configure("Panel.TLabel", background=palette.surface, foreground=palette.fg)
    style.configure("PanelMuted.TLabel", background=palette.surface,
                    foreground=palette.muted)
    style.configure("Brand.TLabel", background=palette.bg, foreground=palette.fg,
                    font=(FONT, 13, "bold"))
    style.configure("BrandAccent.TLabel", background=palette.bg,
                    foreground=palette.accent, font=(FONT, 13, "bold"))
    style.configure("Badge.TLabel", background=palette.accent_soft,
                    foreground=palette.accent, font=(FONT, 9),
                    padding=(8, 3))
    style.configure("Warn.TLabel", background=palette.bg, foreground=palette.warn,
                    font=(FONT, 9))
    style.configure("Status.TLabel", background=palette.surface2,
                    foreground=palette.muted, padding=(10, 5))

    # --- boutons
    style.configure("TButton", background=palette.surface3, foreground=palette.fg,
                    padding=(12, 6), borderwidth=0, focusthickness=0,
                    focuscolor=palette.surface3, relief="flat")
    style.map("TButton",
              background=[("pressed", palette.border), ("active", palette.surface2),
                          ("disabled", palette.surface)],
              foreground=[("disabled", palette.muted)])
    style.configure("Accent.TButton", background=palette.accent,
                    foreground=palette.accent_fg, padding=(14, 6), borderwidth=0,
                    focusthickness=0, font=(FONT, 10, "bold"))
    style.map("Accent.TButton",
              background=[("pressed", palette.accent), ("active", palette.accent),
                          ("disabled", palette.surface3)],
              foreground=[("disabled", palette.muted)])
    style.configure("Ghost.TButton", background=palette.bg, foreground=palette.muted,
                    padding=(8, 4), borderwidth=0)
    style.map("Ghost.TButton",
              background=[("active", palette.surface3)],
              foreground=[("active", palette.fg)])
    style.configure("Tool.TButton", background=palette.surface3,
                    foreground=palette.fg, padding=(11, 6))
    style.map("Tool.TButton",
              background=[("active", palette.accent_soft), ("pressed", palette.border)],
              foreground=[("active", palette.accent)])

    # --- champs
    style.configure("TEntry", fieldbackground=palette.surface, foreground=palette.fg,
                    insertcolor=palette.fg, bordercolor=palette.border,
                    lightcolor=palette.surface, darkcolor=palette.surface,
                    padding=6)
    style.map("TEntry", bordercolor=[("focus", palette.accent)])
    style.configure("TCombobox", fieldbackground=palette.surface,
                    background=palette.surface3, foreground=palette.fg,
                    arrowcolor=palette.muted, bordercolor=palette.border,
                    padding=4)
    style.map("TCombobox",
              fieldbackground=[("readonly", palette.surface)],
              foreground=[("readonly", palette.fg)],
              selectbackground=[("readonly", palette.surface)],
              selectforeground=[("readonly", palette.fg)])
    style.configure("TCheckbutton", background=palette.bg, foreground=palette.fg)
    style.map("TCheckbutton", background=[("active", palette.bg)])

    # --- listes
    style.configure("Treeview", background=palette.surface,
                    fieldbackground=palette.surface, foreground=palette.fg,
                    rowheight=26, borderwidth=0, relief="flat")
    style.configure("Treeview.Heading", background=palette.surface2,
                    foreground=palette.muted, relief="flat", padding=(6, 6),
                    font=(FONT, 9, "bold"))
    style.map("Treeview.Heading", background=[("active", palette.surface3)])
    style.map("Treeview",
              background=[("selected", palette.accent)],
              foreground=[("selected", palette.accent_fg)])

    # --- onglets
    style.configure("TNotebook", background=palette.bg, borderwidth=0,
                    tabmargins=(6, 6, 6, 0))
    style.configure("TNotebook.Tab", background=palette.surface2,
                    foreground=palette.muted, padding=(18, 8), borderwidth=0,
                    font=(FONT, 10))
    style.map("TNotebook.Tab",
              background=[("selected", palette.surface), ("active", palette.surface3)],
              foreground=[("selected", palette.fg), ("active", palette.fg)],
              expand=[("selected", (0, 0, 0, 0))])

    # --- séparateurs, barres
    style.configure("TSeparator", background=palette.border)
    style.configure("TPanedwindow", background=palette.bg, sashwidth=8,
                    sashrelief="flat")
    style.configure("Sash", background=palette.bg, sashwidth=8)
    style.configure("Vertical.TScrollbar", background=palette.surface3,
                    troughcolor=palette.bg, bordercolor=palette.bg,
                    arrowcolor=palette.muted, width=12)
    style.configure("Horizontal.TScrollbar", background=palette.surface3,
                    troughcolor=palette.bg, bordercolor=palette.bg,
                    arrowcolor=palette.muted)
    style.map("Vertical.TScrollbar", background=[("active", palette.border)])
    style.map("Horizontal.TScrollbar", background=[("active", palette.border)])
    style.configure("TProgressbar", background=palette.accent,
                    troughcolor=palette.surface2, bordercolor=palette.border,
                    lightcolor=palette.accent, darkcolor=palette.accent)
