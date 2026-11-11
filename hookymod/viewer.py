"""Visualiseur 3D Tkinter (fil de fer / ombrage plat), sans dépendance externe.

Le rendu est un « peintre » : les faces sont triées de l'arrière vers l'avant
puis dessinées en polygones sur un canevas.  C'est volontairement simple (pas
d'OpenGL) pour rester portable et fonctionner sur un simple Python + Tk.
"""

from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import messagebox, ttk

from .mesh import MeshData
from .util import HookyError

BG = "#16181d"
PANEL = "#23262e"
FG = "#e6e8ec"
MUTED = "#8b93a1"
ACCENT = "#4c8dff"

#: Au-delà, le dessin devient trop lent : on n'affiche qu'une partie des faces.
MAX_FACES = 60000

PALETTE = [(0.82, 0.82, 0.86), (0.86, 0.72, 0.45), (0.55, 0.78, 0.6),
           (0.72, 0.55, 0.82), (0.5, 0.7, 0.88), (0.9, 0.6, 0.55)]


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


class MeshViewer(tk.Toplevel):
    """Fenêtre de prévisualisation 3D.

    Commandes : glisser = tourner · clic droit / Maj+glisser = déplacer ·
    molette = zoom · W = fil de fer · B = faces arrière · F = recadrer.
    """

    def __init__(self, master, meshes, title: str = "Viewer 3D") -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("900x700")
        self.minsize(480, 360)
        self.configure(bg=BG)

        if isinstance(meshes, MeshData):
            meshes = [meshes]
        self.meshes: list[MeshData] = [m for m in meshes if m.faces]
        if not self.meshes:
            raise HookyError("Aucun maillage à afficher.")

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

    # ------------------------------------------------------------------ interface
    def _build_ui(self) -> None:
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        self.info_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.info_var, style="Viewer.TLabel").pack(side="left")
        for text, command in (("Recadrer", self.reset_view),
                              ("Fil de fer", self.toggle_wire),
                              ("Cull", self.toggle_cull),
                              ("Fermer", self.destroy)):
            ttk.Button(bar, text=text, command=command).pack(side="right", padx=(4, 0))

        style = ttk.Style(self)
        style.configure("Viewer.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Viewer.TButton", background=PANEL, foreground=FG)

        holder = tk.Frame(self, bg=BG)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self.hint = tk.Label(self, bg=BG, fg=MUTED, anchor="w",
                             text="glisser : tourner · clic droit : déplacer · molette : zoom · "
                                  "W fil de fer · B faces arrière · F recadrer")
        self.hint.pack(fill="x", padx=8, pady=(0, 4))

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonPress-3>", self._press)
        self.canvas.bind("<B3-Motion>", self._drag)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom(1.1))
        self.canvas.bind("<Button-5>", lambda e: self._zoom(1 / 1.1))
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.bind("w", lambda e: self.toggle_wire())
        self.bind("b", lambda e: self.toggle_cull())
        self.bind("f", lambda e: self.reset_view())
        self.bind("<Escape>", lambda e: self.destroy())

        self.yaw = 0.6
        self.pitch = -0.3
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.wire = False
        self.cull = False
        self._drag_origin = (0, 0)
        self._drag_mode = "rotate"
        self._drawing = False

    # ------------------------------------------------------------------ caméra
    def reset_view(self) -> None:
        xs = [p[0] for p in self.positions]
        ys = [p[1] for p in self.positions]
        zs = [p[2] for p in self.positions]
        self.center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                       (min(zs) + max(zs)) / 2)
        self.radius = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 2 or 1.0
        self.yaw, self.pitch, self.zoom = 0.6, -0.3, 1.0
        self.pan_x = self.pan_y = 0.0
        self.draw()

    def _press(self, event) -> None:
        self._drag_origin = (event.x, event.y)
        self._drag_mode = "pan" if (event.num == 3 or event.state & 0x0001) else "rotate"
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
        self.draw()

    def _wheel(self, event) -> None:
        self._zoom(1.12 if event.delta > 0 else 1 / 1.12)

    def _zoom(self, factor: float) -> None:
        self.zoom = max(0.05, min(40.0, self.zoom * factor))
        self.draw()

    def toggle_wire(self) -> None:
        self.wire = not self.wire
        self.draw()

    def toggle_cull(self) -> None:
        self.cull = not self.cull
        self.draw()

    # ------------------------------------------------------------------ rendu
    def draw(self) -> None:
        if self._drawing:
            return
        self._drawing = True
        try:
            self._draw()
        finally:
            self._drawing = False

    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 64)
        height = max(canvas.winfo_height(), 64)

        total_faces = len(self.faces)
        step = max(1, total_faces // MAX_FACES)
        scale = min(width, height) / 2.4 / self.radius * self.zoom

        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cx, cyy, cz = self.center
        ox = width / 2 + self.pan_x
        oy = height / 2 + self.pan_y

        projected: list[tuple[float, float, float]] = []
        for x, y, z in self.positions:
            x -= cx
            y -= cyy
            z -= cz
            # rotation autour de Y puis X
            x1 = x * cy + z * sy
            z1 = -x * sy + z * cy
            y1 = y * cp - z1 * sp
            z2 = y * sp + z1 * cp
            projected.append((ox + x1 * scale, oy - y1 * scale, z2))

        # Lumière fixe dans l'espace caméra.
        light = (0.35, -0.55, -0.76)
        order = list(range(0, total_faces, step))
        order.sort(key=lambda i: (projected[self.faces[i][0]][2]
                                  + projected[self.faces[i][1]][2]
                                  + projected[self.faces[i][2]][2]) / 3.0, reverse=True)

        yaw_c, yaw_s = math.cos(self.yaw), math.sin(self.yaw)
        pitch_c, pitch_s = math.cos(self.pitch), math.sin(self.pitch)
        drawn = 0
        for i in order:
            face = self.faces[i]
            p0, p1, p2 = projected[face[0]], projected[face[1]], projected[face[2]]
            if self.cull:
                cross = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
                if cross >= 0:
                    continue
            if self.wire:
                canvas.create_polygon(p0[0], p0[1], p1[0], p1[1], p2[0], p2[1],
                                      fill="", outline="#6aa1ff")
                drawn += 1
                continue
            nx, ny, nz = self.face_normals[i]
            # normale dans l'espace caméra (même rotation que les sommets)
            x1 = nx * yaw_c + nz * yaw_s
            z1 = -nx * yaw_s + nz * yaw_c
            y1 = ny * pitch_c - z1 * pitch_s
            z1 = ny * pitch_s + z1 * pitch_c
            lambert = abs(x1 * light[0] + y1 * light[1] + z1 * light[2])
            shade = 0.28 + 0.72 * lambert
            base = self.face_colors[i]
            if isinstance(base, (tuple, list)) and len(base) >= 3:
                r, g, b = base[0], base[1], base[2]
                if max(r, g, b) > 1.5:  # couleurs 0..255
                    r, g, b = r / 255, g / 255, b / 255
            else:
                r = g = b = 0.8
            color = "#%02x%02x%02x" % (
                max(0, min(255, int(255 * r * shade))),
                max(0, min(255, int(255 * g * shade))),
                max(0, min(255, int(255 * b * shade))),
            )
            canvas.create_polygon(p0[0], p0[1], p1[0], p1[1], p2[0], p2[1],
                                  fill=color, outline=color)
            drawn += 1

        vertices = sum(len(m.positions) for m in self.meshes)
        text = (f"{len(self.meshes)} maillage(s) · {vertices:,} sommets · "
                f"{total_faces:,} faces")
        if step > 1:
            text += f" · affichage limité à {drawn:,} faces"
        if self.wire:
            text += " · fil de fer"
        self.info_var.set(text)


def open_viewer(master, meshes, title: str = "Viewer 3D") -> MeshViewer | None:
    """Ouvre le viewer en gérant proprement les erreurs (retourne None si échec)."""
    try:
        return MeshViewer(master, meshes, title)
    except HookyError as exc:
        messagebox.showerror("Viewer 3D", str(exc), parent=master)
        return None
    except Exception as exc:  # noqa: BLE001 - l'UI ne doit jamais planter
        messagebox.showerror("Viewer 3D", f"Impossible d'afficher le modèle :\n{exc}",
                             parent=master)
        return None
