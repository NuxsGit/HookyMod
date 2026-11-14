"""Visualiseur 3D Tkinter (fil de fer / ombrage plat), sans dépendance externe.

Le rendu est un « peintre » : les faces sont projetées, triées de l'arrière vers
l'avant, puis dessinées en polygones sur un canevas. Pour rester fluide sur de
vrais modèles (souvent 100 000 faces), le viewer :

* **écarte les faces invisibles** (dos tourné, aire nulle hors écran) ;
* **borne le nombre de polygones** dessinés selon la taille du canevas, avec un
  budget réduit pendant les glissements (le rendu complet revient au relâchement) ;
* **regroupe les redessins** : un seul `after` en attente, jamais une file de
  callbacks (sinon l'interface se fige pendant la rotation) ;
* ne construit les maillages demandés qu'à l'ouverture de la fenêtre, shape par
  shape, et s'arrête quand le budget est atteint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .mesh import MeshData
from .util import HookyError

try:  # Tkinter est absent de certaines installations (Windows sans tcl/tk)
    import tkinter as tk
    from tkinter import messagebox, ttk
    TK_AVAILABLE = True
    TK_ERROR = ""
except Exception as exc:  # noqa: BLE001 - on veut la cause exacte
    tk = None            # type: ignore[assignment]
    messagebox = None    # type: ignore[assignment]
    ttk = None           # type: ignore[assignment]
    TK_AVAILABLE = False
    TK_ERROR = f"{type(exc).__name__}: {exc}"

#: Sans Tkinter, la géométrie et le rendu hors écran restent utilisables :
#: seule la fenêtre est indisponible.
_TkBase = tk.Toplevel if TK_AVAILABLE else object

BG = "#16181d"
PANEL = "#23262e"
FG = "#e6e8ec"
MUTED = "#8b93a1"
ACCENT = "#4c8dff"

#: Plafond absolu de polygones dessinés en une passe (qualité maximale).
#: Tk dessine chaque polygone un par un : au-delà, la rotation saccade.
MAX_FACES = 12000
#: Budget minimal, même sur une petite fenêtre.
MIN_BUDGET = 1200
#: Budget pendant un glissement (l'utilisateur tourne : on privilégie la fluidité).
DRAG_BUDGET = 3000

PALETTE = [(0.82, 0.82, 0.86), (0.86, 0.72, 0.45), (0.55, 0.78, 0.6),
           (0.72, 0.55, 0.82), (0.5, 0.7, 0.88), (0.9, 0.6, 0.55)]

LIGHT = (0.35, -0.55, -0.76)


@dataclass
class RenderData:
    """Géométrie aplatie prête à dessiner (plusieurs maillages fusionnés)."""

    positions: list[tuple[float, float, float]] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    colors: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)


def build_render_data(meshes: list[MeshData]) -> RenderData:
    """Fusionne des maillages en une seule liste de faces (testable sans Tk)."""
    data = RenderData()
    for index, mesh in enumerate(meshes):
        if not mesh.faces:
            continue
        offset = len(data.positions)
        data.positions.extend(mesh.positions)
        base_color = PALETTE[index % len(PALETTE)]
        for face in mesh.faces:
            if len(face) < 3:
                continue
            if any(i < 0 or i >= len(mesh.positions) for i in face):
                continue
            data.faces.append((face[0] + offset, face[1] + offset, face[2] + offset))
            if mesh.colors and face[0] < len(mesh.colors):
                color = mesh.colors[face[0]]
            else:
                color = base_color
            if isinstance(color, (tuple, list)) and len(color) >= 3 and max(color) > 1.5:
                color = (color[0] / 255, color[1] / 255, color[2] / 255)
            data.colors.append(tuple(color[:3]))  # type: ignore[arg-type]
            data.normals.append(mesh.face_normal(face))
    if not data.faces:
        raise HookyError("Aucune face exploitable.")
    return data


# --------------------------------------------------------------------------- maths
def fit_view(positions: list[tuple[float, float, float]]) -> tuple[tuple[float, float, float], float]:
    """Centre et rayon englobant d'un nuage de points."""
    if not positions:
        raise HookyError("Aucun sommet à afficher.")
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    radius = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 2 or 1.0
    return center, radius


def view_scale(width: int, height: int, radius: float, zoom: float) -> float:
    """Facteur pixels/unité, sans division par zéro sur une petite fenêtre/zoom."""
    radius = radius if radius > 1e-6 else 1.0
    return max(1, min(width, height)) / 2.4 / radius * max(0.01, zoom)


def project_points(positions, center, yaw: float, pitch: float, scale: float,
                   ox: float, oy: float) -> list[tuple[float, float, float]]:
    """Projette les sommets (rotation Y puis X, projection orthographique)."""
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cx, cyy, cz = center
    out: list[tuple[float, float, float]] = []
    for x, y, z in positions:
        x -= cx
        y -= cyy
        z -= cz
        x1 = x * cy + z * sy
        z1 = -x * sy + z * cy
        y1 = y * cp - z1 * sp
        z2 = y * sp + z1 * cp
        out.append((ox + x1 * scale, oy - y1 * scale, z2))
    return out


def face_budget(width: int, height: int, dragging: bool = False) -> int:
    """Nombre de polygones à dessiner selon la surface du canevas."""
    area = max(1, width) * max(1, height)
    budget = int(area / 380)
    budget = max(MIN_BUDGET, min(MAX_FACES, budget))
    if dragging:
        budget = max(MIN_BUDGET // 2, min(DRAG_BUDGET, budget // 4))
    return budget


class Projector:
    """Projection à la demande, avec cache : seuls les sommets visibles sont calculés.

    Sur un modèle de 250 000 faces, la projection complète coûterait plus cher que
    le dessin lui-même ; ici seuls les sommets des faces candidates sont projetés,
    et une seule fois par image.
    """

    def __init__(self, positions, center, yaw: float, pitch: float, scale: float,
                 ox: float, oy: float) -> None:
        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        self._positions = positions
        self._center = center
        self._cos_y, self._sin_y = cy, sy
        self._cos_p, self._sin_p = cp, sp
        self._scale = scale
        self._ox = ox
        self._oy = oy
        self._cache: dict[int, tuple[float, float, float]] = {}

    def __call__(self, index: int) -> tuple[float, float, float]:
        cached = self._cache.get(index)
        if cached is not None:
            return cached
        x, y, z = self._positions[index]
        cx, cy, cz = self._center
        x -= cx
        y -= cy
        z -= cz
        x1 = x * self._cos_y + z * self._sin_y
        z1 = -x * self._sin_y + z * self._cos_y
        y1 = y * self._cos_p - z1 * self._sin_p
        z2 = y * self._sin_p + z1 * self._cos_p
        point = (self._ox + x1 * self._scale, self._oy - y1 * self._scale, z2)
        self._cache[index] = point
        return point

    def __len__(self) -> int:
        return len(self._positions)


def order_visible_faces(faces, projected, budget: int, cull: bool = True,
                        min_area: float = 0.35,
                        screen: tuple[int, int] | None = None) -> list[int]:
    """Faces à dessiner, de l'arrière vers l'avant, limitées à ``budget``.

    ``projected`` est soit une liste de points, soit un :class:`Projector`
    (projection paresseuse). Les faces trop petites ou vues de dos sont écartées
    avant le tri ; au-delà de ``8 × budget`` faces, un sous-échantillonnage
    régulier garde une image fluide sur les très gros modèles. ``screen``
    (largeur, hauteur) active l'élagage des faces entièrement hors du cadre :
    sur un modèle zoomé, cela évite de projeter et de trier ce qui ne sera
    jamais dessiné.
    """
    visible: list[tuple[float, int]] = []
    total = len(faces)
    get = projected if callable(projected) else projected.__getitem__
    stride = 1 if total <= budget * 8 else -(-total // max(1, budget * 8))
    for index in range(0, total, stride):
        a, b, c = faces[index]
        p0, p1, p2 = get(a), get(b), get(c)
        cross = ((p1[0] - p0[0]) * (p2[1] - p0[1])
                 - (p1[1] - p0[1]) * (p2[0] - p0[0]))
        if cull and cross >= 0:
            continue
        if abs(cross) * 0.5 < min_area:
            continue
        if screen is not None:
            sw, sh = screen
            if (max(p0[0], p1[0], p2[0]) < 0 or min(p0[0], p1[0], p2[0]) > sw
                    or max(p0[1], p1[1], p2[1]) < 0 or min(p0[1], p1[1], p2[1]) > sh):
                continue                    # entièrement hors du cadre
        visible.append(((p0[2] + p1[2] + p2[2]) / 3.0, index))
    if not visible:
        return []
    # La profondeur projetée croît vers la caméra : on dessine donc du plus
    # petit z (le plus loin) au plus grand, pour que le peintre respecte
    # l'occlusion (une face proche recouvre bien une face lointaine).
    visible.sort()
    step = max(1, len(visible) // max(1, budget))
    return [index for _, index in visible[::step][:budget]]


def shade_color(color, normal, yaw: float, pitch: float) -> str:
    """Couleur éclairée (Lambert) d'une face, normale donnée en espace objet."""
    nx, ny, nz = normal
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    x1 = nx * cy + nz * sy
    z1 = -nx * sy + nz * cy
    y1 = ny * cp - z1 * sp
    z1 = ny * sp + z1 * cp
    lambert = abs(x1 * LIGHT[0] + y1 * LIGHT[1] + z1 * LIGHT[2])
    shade = 0.28 + 0.72 * lambert
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        r, g, b = color[0], color[1], color[2]
        if max(r, g, b) > 1.5:      # couleurs 0..255
            r, g, b = r / 255, g / 255, b / 255
    else:
        r = g = b = 0.8
    return "#%02x%02x%02x" % (
        max(0, min(255, int(255 * r * shade))),
        max(0, min(255, int(255 * g * shade))),
        max(0, min(255, int(255 * b * shade))),
    )


# --------------------------------------------------------------------------- fenêtre
class MeshViewer(_TkBase):  # type: ignore[misc,valid-type]
    """Fenêtre de prévisualisation 3D.

    Commandes : glisser = tourner · clic droit / Maj+glisser = déplacer ·
    molette = zoom · W = fil de fer · B = faces arrière · F = recadrer.
    """

    def __init__(self, master, meshes, title: str = "Viewer 3D",
                 palette: dict | None = None) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("900x700")
        self.minsize(480, 360)
        #: couleurs de l'application (thème sombre/clair) ; retombe sur les valeurs
        #: historiques quand le viewer est ouvert seul.
        colors = {"bg": BG, "panel": PANEL, "fg": FG, "muted": MUTED, "accent": ACCENT}
        if palette:
            colors.update({k: v for k, v in palette.items() if v})
        self.colors = colors
        self.configure(bg=colors["bg"])

        if isinstance(meshes, MeshData):
            meshes = [meshes]
        self.meshes: list[MeshData] = [m for m in meshes if m.faces]
        if not self.meshes:
            raise HookyError("Aucun maillage à afficher.")

        # état de la caméra AVANT l'interface : <Configure> peut arriver tôt
        self._pending = None
        self._drawing = False
        self._dragging = False
        self.wire = False
        self.cull = True
        self.yaw = 0.6
        self.pitch = -0.3
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._drag_origin = (0, 0)
        self._drag_mode = "rotate"
        self._last_budget = 0
        self._last_drawn = 0

        self._build_data()
        self._build_ui()
        self.reset_view()

    # ------------------------------------------------------------------ données
    def _build_data(self) -> None:
        data = build_render_data(self.meshes)
        self.positions = data.positions
        self.faces = data.faces
        self.face_colors = data.colors
        self.face_normals = data.normals
        self.center, self.radius = fit_view(self.positions)

    # ------------------------------------------------------------------ interface
    def _build_ui(self) -> None:
        bg, muted = self.colors["bg"], self.colors["muted"]
        style = ttk.Style(self)
        try:
            style.configure("Viewer.TLabel", background=bg, foreground=muted)
        except tk.TclError:
            pass

        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        self.info_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.info_var, style="Viewer.TLabel").pack(side="left")
        for text, command in (("Fermer", self.destroy),
                              ("Recadrer", self.reset_view),
                              ("Fil de fer", self.toggle_wire),
                              ("Cull", self.toggle_cull)):
            ttk.Button(bar, text=text, command=command).pack(side="right", padx=(4, 0))

        holder = tk.Frame(self, bg=bg)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=bg, highlightthickness=0, cursor="fleur",
                                takefocus=True)
        self.canvas.pack(fill="both", expand=True)
        self.hint = tk.Label(self, bg=bg, fg=muted, anchor="w",
                             text="glisser : tourner · clic droit : déplacer · molette : zoom · "
                                  "W fil de fer · B faces arrière · F recadrer")
        self.hint.pack(fill="x", padx=8, pady=(0, 4))

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-3>", self._press)
        self.canvas.bind("<B3-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-3>", self._release)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom(1.12))
        self.canvas.bind("<Button-5>", lambda e: self._zoom(1 / 1.12))
        self.canvas.bind("<Configure>", lambda e: self.schedule_draw())
        self.canvas.bind("<Enter>", lambda e: self.canvas.focus_set())
        for key, handler in (("w", self.toggle_wire), ("b", self.toggle_cull),
                             ("f", self.reset_view), ("<Escape>", self.destroy)):
            self.bind(key, lambda e, h=handler: h())

    # ------------------------------------------------------------------ caméra
    def reset_view(self) -> None:
        self.yaw, self.pitch, self.zoom = 0.6, -0.3, 1.0
        self.pan_x = self.pan_y = 0.0
        self.schedule_draw()

    def _press(self, event) -> None:
        self.canvas.focus_set()
        self._drag_origin = (event.x, event.y)
        self._dragging = True
        self._drag_mode = "pan" if (getattr(event, "num", 1) == 3
                                    or event.state & 0x0001) else "rotate"
        self.canvas.config(cursor="fleur" if self._drag_mode == "pan" else "hand2")

    def _drag(self, event) -> None:
        dx = event.x - self._drag_origin[0]
        dy = event.y - self._drag_origin[1]
        self._drag_origin = (event.x, event.y)
        if self._drag_mode == "pan":
            self.pan_x += dx
            self.pan_y += dy
        else:
            self.yaw += dx * 0.01
            self.pitch = max(-math.pi / 2, min(math.pi / 2, self.pitch + dy * 0.01))
        self.schedule_draw()

    def _release(self, _event=None) -> None:
        self._dragging = False
        self.canvas.config(cursor="fleur")
        self.schedule_draw()

    def _wheel(self, event) -> None:
        self._zoom(1.12 if getattr(event, "delta", 0) > 0 else 1 / 1.12)

    def _zoom(self, factor: float) -> None:
        self.zoom = max(0.05, min(40.0, self.zoom * factor))
        self.schedule_draw()

    def toggle_wire(self) -> None:
        self.wire = not self.wire
        self.schedule_draw()

    def toggle_cull(self) -> None:
        self.cull = not self.cull
        self.schedule_draw()

    # ------------------------------------------------------------------ rendu
    def schedule_draw(self) -> None:
        """Un seul redessin en attente : les événements en rafale sont fusionnés."""
        if self._pending is None:
            self._pending = self.after(1, self._draw_pending)

    def _draw_pending(self) -> None:
        self._pending = None
        self.draw()

    def draw(self) -> None:
        if self._drawing:
            return
        self._drawing = True
        try:
            self._draw()
        except tk.TclError:
            pass
        finally:
            self._drawing = False

    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 64)
        height = max(canvas.winfo_height(), 64)

        total_faces = len(self.faces)
        scale = view_scale(width, height, self.radius, self.zoom)
        projected = Projector(self.positions, self.center, self.yaw, self.pitch, scale,
                              width / 2 + self.pan_x, height / 2 + self.pan_y)
        budget = face_budget(width, height, self._dragging)
        order = order_visible_faces(self.faces, projected, budget, cull=self.cull)

        drawn = 0
        for index in order:
            a, b, c = self.faces[index]
            p0, p1, p2 = projected(a), projected(b), projected(c)
            if self.wire:
                canvas.create_polygon(p0[0], p0[1], p1[0], p1[1], p2[0], p2[1],
                                      fill="", outline=self.colors["accent"])
                drawn += 1
                continue
            color = shade_color(self.face_colors[index], self.face_normals[index],
                                self.yaw, self.pitch)
            area = abs((p1[0] - p0[0]) * (p2[1] - p0[1])
                       - (p1[1] - p0[1]) * (p2[0] - p0[0])) * 0.5
            canvas.create_polygon(p0[0], p0[1], p1[0], p1[1], p2[0], p2[1],
                                  fill=color, outline=color if area > 24 else "")
            drawn += 1

        self._last_budget = budget
        self._last_drawn = drawn
        self._update_info(drawn, total_faces)

    def _update_info(self, drawn: int, total: int) -> None:
        vertices = len(self.positions)
        text = (f"{len(self.meshes)} maillage(s) · {vertices:,} sommets · "
                f"{total:,} faces")
        if drawn and drawn < total:
            text += f" · {drawn:,} affichée{'s' if drawn > 1 else ''}"
        if self.wire:
            text += " · fil de fer"
        self.info_var.set(text)


def open_viewer(master, meshes, title: str = "Viewer 3D",
                palette: dict | None = None) -> MeshViewer | None:
    """Ouvre le viewer en gérant proprement les erreurs (retourne None si échec)."""
    if not TK_AVAILABLE:
        messagebox_text = ("Tkinter est indisponible sur cette installation "
                           f"({TK_ERROR}). Le viewer 3D ne peut pas s'ouvrir ; "
                           "utilise « python -m hookymod.cli view fichier.bfres "
                           "-o apercu.png » pour obtenir une image.")
        try:
            from tkinter import messagebox as box
            box.showerror("Viewer 3D", messagebox_text, parent=master)
        except Exception:  # noqa: BLE001
            print(messagebox_text)
        return None
    try:
        return MeshViewer(master, meshes, title, palette=palette)
    except HookyError as exc:
        messagebox.showerror("Viewer 3D", str(exc), parent=master)
        return None
    except Exception as exc:  # noqa: BLE001 - l'UI ne doit jamais planter
        messagebox.showerror("Viewer 3D", f"Impossible d'afficher le modèle :\n{exc}",
                             parent=master)
        return None
