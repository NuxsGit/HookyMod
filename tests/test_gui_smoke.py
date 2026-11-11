"""Vérifie que les modules GUI s'importent et que leur logique pure fonctionne.

Tkinter n'est pas disponible sur toutes les machines (ni dans la CI headless),
donc on installe de petits modules factices avant d'importer ``hookymod.app``.
Les widgets ne font rien : on teste seulement la construction de l'application
et les méthodes qui ne dépendent pas du rendu.
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


# --------------------------------------------------------------------------- stub tkinter
class _TclError(Exception):
    pass


class _Widget:
    def __init__(self, master=None, **kwargs):
        self.master = master
        self.kwargs = dict(kwargs)
        self.options = {}

    def __getattr__(self, name):
        def noop(*args, **kwargs):
            return None
        return noop

    def winfo_exists(self):
        return True

    def winfo_width(self):
        return 800

    def winfo_height(self):
        return 600

    def winfo_children(self):
        return []

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return 0

    def get_children(self, *a, **k):
        return []

    def selection(self, *a, **k):
        return ()

    def focus(self, *a, **k):
        return ""

    def configure(self, *args, **kwargs):
        self.options.update(kwargs)

    config = configure

    def insert(self, *a, **k):
        return "iid"

    def delete(self, *a, **k):
        return None


class _Variable:
    def __init__(self, master=None, value="", **kw):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value

    def trace_add(self, *a, **k):
        return None


class _Tk(_Widget):
    def mainloop(self, *a, **k):
        return None

    def destroy(self):
        return None


def _install_stub_tkinter() -> None:
    tk = types.ModuleType("tkinter")
    classes = {
        "Tk": _Tk, "Toplevel": _Tk, "Canvas": _Widget, "Text": _Widget,
        "Menu": _Widget, "Frame": _Widget, "Label": _Widget, "Spinbox": _Widget,
        "PhotoImage": _Widget, "Widget": _Widget, "TclError": _TclError,
        "StringVar": _Variable, "BooleanVar": _Variable, "IntVar": _Variable,
    }
    for name, cls in classes.items():
        setattr(tk, name, cls)
    for name in ("END", "W", "E", "N", "S", "LEFT", "RIGHT", "BOTH", "X", "Y",
                 "TOP", "BOTTOM", "NORMAL", "DISABLED", "HORIZONTAL", "VERTICAL",
                 "NW", "NE"):
        setattr(tk, name, name.lower())
    sys.modules["tkinter"] = tk

    ttk = types.ModuleType("tkinter.ttk")
    for name in ("Frame", "Button", "Label", "Entry", "Treeview", "Scrollbar",
                 "PanedWindow", "Notebook", "Style", "Combobox", "Checkbutton",
                 "Radiobutton", "Spinbox", "Labelframe", "Progressbar", "Separator"):
        setattr(ttk, name, type(name, (_Widget,), {}))
    sys.modules["tkinter.ttk"] = ttk
    tk.ttk = ttk

    filedialog = types.ModuleType("tkinter.filedialog")
    for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
        setattr(filedialog, name, lambda *a, **k: "")
    sys.modules["tkinter.filedialog"] = filedialog
    tk.filedialog = filedialog

    messagebox = types.ModuleType("tkinter.messagebox")
    for name in ("showinfo", "showerror", "showwarning", "askyesno", "askyesnocancel"):
        setattr(messagebox, name, lambda *a, **k: True)
    sys.modules["tkinter.messagebox"] = messagebox
    tk.messagebox = messagebox

    font = types.ModuleType("tkinter.font")
    sys.modules["tkinter.font"] = font
    tk.font = font


_install_stub_tkinter()

import synth  # noqa: E402
import synth_nup  # noqa: E402

import hookymod.app as app_module  # noqa: E402
import hookymod.viewer as viewer_module  # noqa: E402
from hookymod import zs  # noqa: E402
from hookymod.container import Document, Level  # noqa: E402
from hookymod.mesh import load_obj  # noqa: E402


class GuiImportTests(unittest.TestCase):
    def test_modules_expose_expected_symbols(self):
        self.assertTrue(callable(app_module.main))
        self.assertTrue(hasattr(app_module, "App"))
        self.assertTrue(hasattr(viewer_module, "MeshViewer"))
        self.assertTrue(hasattr(viewer_module, "open_viewer"))

    def test_build_render_data(self):
        mesh = load_obj(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        data = viewer_module.build_render_data([mesh])
        self.assertEqual(len(data.faces), 1)
        self.assertEqual(len(data.positions), 3)
        self.assertEqual(len(data.colors), 1)
        self.assertEqual(len(data.normals), 1)
        self.assertAlmostEqual(abs(data.normals[0][2]), 1.0, places=5)

    def test_build_render_data_rejects_empty(self):
        from hookymod.mesh import MeshData
        with self.assertRaises(Exception):
            viewer_module.build_render_data([MeshData()])


@unittest.skipUnless(zs.zstd_available(), "module zstandard requis")
class AppLogicTests(unittest.TestCase):
    def setUp(self):
        self._tmp_config = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp_config.close()
        old = app_module.CONFIG_PATH
        app_module.CONFIG_PATH = Path(self._tmp_config.name)
        self.addCleanup(lambda: setattr(app_module, "CONFIG_PATH", old))
        self.app = app_module.App()
        self.app.dicts = zs.ZstdDicts()
        self.app.dicts.add("test.zsdic",
                           synth.train_dict([b"sample %d" % i * 30 for i in range(32)], 512))
        self.did = next(iter(self.app.dicts.by_id))

    def _pack(self):
        inner = synth.make_sarc([("a.bin", b"AAA"), ("b.bin", b"BBB")])
        return synth.make_sarc([
            ("inner.zs", self.app.dicts.compress(inner, self.did)),
            ("readme.txt", b"hello world"),
        ])

    def _open(self, pack: bytes) -> Document:
        doc = Document(Level("pack.zs", synth.compress(pack), self.app.dicts,
                             rel="Pack/pack.zs"))
        self.app.documents["k"] = doc
        self.app.current_key = "k"
        self.app._refresh_all()
        return doc

    def test_app_constructs_and_previews(self):
        doc = self._open(self._pack())
        self.assertEqual(len(doc.top.archive), 2)
        self.assertIn("archive SARC", self.app._describe_level(doc.top))

    def test_browsing_does_not_mark_modified(self):
        doc = self._open(self._pack())
        index = doc.top.archive.names().index("inner.zs")
        doc.push(index)
        doc.pop()
        self.assertFalse(doc.modified())

    def test_extract_entries_decompresses(self):
        doc = self._open(self._pack())
        entries = [(e.name, e.data) for e in doc.top.archive.entries]
        with tempfile.TemporaryDirectory() as tmp:
            written, skipped, errors = self.app._extract_entries(entries, Path(tmp), True)
            self.assertEqual((written, skipped, errors), (2, 0, []))
            self.assertTrue((Path(tmp) / "inner").exists())
            self.assertEqual((Path(tmp) / "readme.txt").read_text(), "hello world")

    def test_extract_rejects_unsafe_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            written, skipped, errors = self.app._extract_entries(
                [("../evil.txt", b"x")], Path(tmp), False)
            self.assertEqual(written, 0)
            self.assertEqual(skipped, 1)
            self.assertTrue(errors)
            self.assertFalse((Path(tmp).parent / "evil.txt").exists())

    def test_maybe_compress_roundtrip(self):
        old = app_module.messagebox.askyesno
        app_module.messagebox.askyesno = lambda *a, **k: True
        self.addCleanup(lambda: setattr(app_module.messagebox, "askyesno", old))
        original = self.app.dicts.compress(b"hello", self.did)
        result = self.app._maybe_compress(original, b"world" * 10, "x.zs")
        self.assertEqual(result[:4], b"\x28\xb5\x2f\xfd")
        self.assertEqual(self.app.dicts.decompress(result), b"world" * 10)

    def test_describe_entry_handles_missing_dict(self):
        blob = synth.compress(b"data", synth.train_dict(
            [b"other %d" % i * 20 for i in range(16)], 512))
        text = self.app._describe_entry("x.zs", blob)
        self.assertIn("dictionnaire", text.lower())

    def test_texture_and_model_tabs(self):
        self.app._load_textures_from_data(synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))
        self.assertEqual(len(self.app.texture_file.textures), 1)
        self.app._load_bfres_from_data(synth.make_bfres())
        self.assertEqual(len(self.app.bfres_file.models), 1)
        self.assertEqual(self.app._current_lod(), 0)

    def test_dirty_documents_and_pack(self):
        doc = self._open(self._pack())
        doc.push(0)
        doc.top.replace_entry(0, b"ZZZ")
        doc.pop()
        self.assertTrue(doc.modified())
        self.assertEqual(len(self.app._dirty_documents()), 1)
        self.assertGreater(len(doc.pack()), 0)

    def test_open_nup_extracts_and_loads_romfs(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        package = folder / "game.nup"
        package.write_bytes(synth_nup.make_sample_nup())
        out = folder / "extract"
        out.mkdir()

        old_open = app_module.filedialog.askopenfilename
        old_dir = app_module.filedialog.askdirectory
        app_module.filedialog.askopenfilename = lambda *a, **k: str(package)
        app_module.filedialog.askdirectory = lambda *a, **k: str(out)
        self.addCleanup(setattr, app_module.filedialog, "askopenfilename", old_open)
        self.addCleanup(setattr, app_module.filedialog, "askdirectory", old_dir)
        self.app._run_bg = lambda label, work, done, failed=None: done(work())
        self.app.open_nup()
        self.assertTrue((out / "romfs" / "Model" / "test.bfres").exists())
        self.assertEqual(self.app.romfs, out / "romfs")

    def test_update_size_table_grows_entries(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        romfs = folder / "romfs"
        resource = romfs / "System" / "Resource"
        resource.mkdir(parents=True)
        name = "ResourceSizeTable.Product.100.rsizetable"
        (resource / name).write_bytes(synth.make_restbl({"Model/Test.bfres": 64}))
        self.app.romfs = romfs
        mod = folder / "mod"
        notes = self.app._update_size_table(mod, {"Model/Test.bfres": 4096})
        self.assertTrue(notes)
        written = mod / "romfs" / "System" / "Resource" / name
        self.assertTrue(written.exists())
        from hookymod import restbl
        table = restbl.ResourceSizeTable.parse(written.read_bytes())
        self.assertGreaterEqual(table.get("Model/Test.bfres"), 4096)

    def test_update_size_table_noop_without_table(self):
        self.app.romfs = Path(tempfile.mkdtemp())
        self.assertEqual(self.app._update_size_table(Path("/tmp"), {"a": 10}), [])

    def test_translate_unpack_and_pack_from_gui(self):
        import shutil
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        romfs = folder / "romfs"
        (romfs / "Model").mkdir(parents=True)
        (romfs / "Model" / "test.bfres").write_bytes(synth.make_bfres())
        project = folder / "projet"
        mods = folder / "mods"
        mods.mkdir()

        old_dir = app_module.filedialog.askdirectory
        answers = [str(romfs), str(project)]
        app_module.filedialog.askdirectory = lambda *a, **k: answers.pop(0) if answers else ""
        self.addCleanup(setattr, app_module.filedialog, "askdirectory", old_dir)
        self.app._run_bg = lambda label, work, done, failed=None: done(work())
        self.app.translate_unpack()
        self.assertTrue((project / "pc" / "Model" / "test.bfres_m0_s0.obj").exists())

        answers.extend([str(project), str(mods)])
        self.app.translate_pack()   # rien de modifié : ne doit rien écrire
        self.assertFalse((mods / "romfs").exists())

        obj = project / "pc" / "Model" / "test.bfres_m0_s0.obj"
        obj.write_text(obj.read_text().replace("v 0 0 0", "v 2 2 2", 1))
        answers.extend([str(project), str(mods)])
        self.app.translate_pack()
        self.assertTrue((mods / "romfs" / "Model" / "test.bfres").exists())

    def test_scan_folder_flows_into_report_window(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        (folder / "Model").mkdir()
        (folder / "Model" / "test.bfres").write_bytes(synth.make_bfres())
        (folder / "Tex.bntx").write_bytes(synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))

        old_dialog = app_module.filedialog.askdirectory
        app_module.filedialog.askdirectory = lambda *a, **k: str(folder)
        self.addCleanup(setattr, app_module.filedialog, "askdirectory", old_dialog)

        received = []
        self.app._run_bg = lambda label, work, done, failed=None: done(work())
        self.app._show_scan_report = lambda report: received.append(report)
        self.app.scan_folder()
        report = received[-1]
        self.assertEqual(report["file_count"], 2)
        self.assertIn("Nintendo", report["engine"])
        # la fenêtre de rapport se construit sans erreur
        app_module.App._show_scan_report(self.app, report)


class ViewerMathTests(unittest.TestCase):
    """Les helpers de rendu doivent tenir sans Tk réel (culling, budget, projection)."""

    def setUp(self):
        self.VIEW = viewer_module

    def test_fit_view_centre_and_radius(self):
        center, radius = self.VIEW.fit_view([(0, 0, 0), (2, 4, 6)])
        self.assertEqual(center, (1.0, 2.0, 3.0))
        self.assertAlmostEqual(radius, 3.0)

    def test_project_points_identity_orientation(self):
        points = self.VIEW.project_points([(1.0, 2.0, 3.0)], (0, 0, 0), 0.0, 0.0, 1.0, 0, 0)
        x, y, z = points[0]
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, -2.0)   # l'axe Y de Tk descend
        self.assertAlmostEqual(z, 3.0)

    def test_order_visible_faces_culls_backfaces(self):
        faces = [(0, 1, 2), (0, 3, 1)]
        projected = [(0, 0, 0), (10, 0, 0), (0, 10, 0), (10, 10, 0)]
        front = self.VIEW.order_visible_faces(faces, projected, 100, cull=True)
        both = self.VIEW.order_visible_faces(faces, projected, 100, cull=False)
        self.assertEqual(len(front), 1)
        self.assertEqual(len(both), 2)
        self.assertNotEqual(set(front), set(both))

    def test_order_visible_faces_budget_and_depth_sort(self):
        faces = [(0, 1, 2)] * 30
        projected = [(0, 0, i) for i in range(3)]
        projected = [(0, 0, float(i), ) for i in range(30)]
        projected = [(0, 0, float(i)) for i in range(30)] + [(5, 0, 0), (0, 5, 0)]
        faces = [tuple(range(i, i + 3)) for i in range(0, 27)]
        ordered = self.VIEW.order_visible_faces(faces, projected, 5, cull=False,
                                                min_area=0.0)
        self.assertLessEqual(len(ordered), 5)
        self.assertTrue(all(0 <= i < len(faces) for i in ordered))

    def test_face_budget_scales_and_drops_while_dragging(self):
        small = self.VIEW.face_budget(300, 200)
        large = self.VIEW.face_budget(1600, 1000)
        self.assertLessEqual(small, large)
        self.assertLessEqual(large, self.VIEW.MAX_FACES)
        self.assertGreaterEqual(small, self.VIEW.MIN_BUDGET)
        self.assertLess(self.VIEW.face_budget(1600, 1000, dragging=True), large)

    def test_shade_color_returns_hex_and_varies(self):
        bright = self.VIEW.shade_color((1.0, 1.0, 1.0), (0.0, 0.0, -1.0), 0.0, 0.0)
        dark = self.VIEW.shade_color((1.0, 1.0, 1.0), (1.0, 0.0, 0.0), 0.0, 0.0)
        for value in (bright, dark):
            self.assertRegex(value, r"^#[0-9a-f]{6}$")
        self.assertNotEqual(bright, dark)

    def test_projector_only_computes_requested_vertices(self):
        positions = [(float(i), float(i % 3), float(i % 5)) for i in range(50)]
        projector = self.VIEW.Projector(positions, (0, 0, 0), 0.3, 0.2, 2.0, 10, 10)
        first = projector(7)
        self.assertIs(first, projector(7))
        self.assertEqual(len(projector._cache), 1)
        self.assertEqual(len(projector), 50)

    def test_order_visible_faces_accepts_projector(self):
        faces = [(0, 1, 2)]
        projector = self.VIEW.Projector([(0, 0, 0), (10, 0, 0), (0, 10, 0)],
                                       (0, 0, 0), 0.0, 0.0, 1.0, 0, 0)
        self.assertEqual(len(self.VIEW.order_visible_faces(faces, projector, 10)), 1)

    def test_huge_models_are_subsampled_before_culling(self):
        count = 40000
        faces = [(0, 1, 2)] * count
        projector = self.VIEW.Projector([(0, 0, 0), (10, 0, 0), (0, 10, 0)],
                                       (0, 0, 0), 0.0, 0.0, 1.0, 0, 0)
        ordered = self.VIEW.order_visible_faces(faces, projector, 500, min_area=0.0)
        self.assertLessEqual(len(ordered), 500)
        self.assertTrue(ordered)

    def test_view_scale_never_divides_by_zero(self):
        self.assertGreater(self.VIEW.view_scale(800, 600, 0.0, 0.0), 0)

    def test_mesh_face_count_matches_built_mesh(self):
        bfres = app_module.bfres_mod.BfresFile.parse(synth.make_bfres())
        shape = bfres.models[0].shapes[0]
        mesh = shape.build_mesh(0, [])
        self.assertEqual(shape.face_count(0), len(mesh.faces))
        self.assertEqual(shape.vtx_buffer.vertex_count, len(mesh.positions))

    def test_build_render_data_skips_invalid_faces(self):
        from hookymod.mesh import MeshData
        mesh = MeshData(positions=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                        faces=[(0, 1, 2), (0, 1, 99)])
        data = self.VIEW.build_render_data([mesh])
        self.assertEqual(len(data.faces), 1)


class ViewerTkStubTests(unittest.TestCase):
    """La fenêtre 3D doit se construire et se dessiner avec le Tk factice."""

    def setUp(self):
        self.mesh = load_obj(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\n"
                             b"f 1 2 3\nf 1 3 4\n")

    def test_viewer_constructs_and_draws(self):
        viewer = viewer_module.MeshViewer(None, [self.mesh], "test")
        viewer._draw()
        self.assertEqual(viewer._last_drawn, 1)     # une face vue de dos : culling
        viewer.cull = False
        viewer._draw()
        self.assertEqual(viewer._last_drawn, 2)
        self.assertIn("faces", viewer.info_var.get())

    def test_viewer_controls_do_not_raise(self):
        viewer = viewer_module.MeshViewer(None, [self.mesh])
        viewer.toggle_wire()
        viewer.toggle_cull()
        viewer.reset_view()
        viewer._zoom(0.5)
        viewer._drag(types.SimpleNamespace(x=4, y=6, num=1, state=0))
        viewer._release()
        viewer._draw()
        self.assertTrue(viewer.wire)

    def test_open_viewer_returns_window(self):
        window = viewer_module.open_viewer(None, [self.mesh], "test")
        self.assertIsInstance(window, viewer_module.MeshViewer)


class ToolbarLayoutTests(unittest.TestCase):
    """Garde-fou anti-doublons : une action = un seul emplacement."""

    def test_toolbar_labels_and_methods_are_unique(self):
        labels = [entry[0] for entry in app_module.TOOLBAR_ACTIONS]
        methods = [entry[1] for entry in app_module.TOOLBAR_ACTIONS]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(len(methods), len(set(methods)))
        for method in methods:
            self.assertTrue(callable(getattr(app_module.App, method, None)), method)

    def test_toolbar_holds_no_content_specific_actions(self):
        methods = {entry[1] for entry in app_module.TOOLBAR_ACTIONS}
        for interdit in ("view_model", "export_model_obj", "convert_blend",
                         "extract_selection", "replace_selection", "patch_dialog"):
            self.assertNotIn(interdit, methods)

    def test_menu_labels_and_methods_are_unique(self):
        labels = [entry[0] for entry in app_module.MENU_ACTIONS]
        methods = [entry[1] for entry in app_module.MENU_ACTIONS]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(len(methods), len(set(methods)))
        for method in methods:
            self.assertTrue(callable(getattr(app_module.App, method, None)), method)

    def test_menu_does_not_repeat_toolbar_labels(self):
        toolbar = {entry[0].replace("…", "") for entry in app_module.TOOLBAR_ACTIONS}
        for label, _ in app_module.MENU_ACTIONS:
            self.assertNotIn(label.replace("…", ""), toolbar)


@unittest.skipUnless(zs.zstd_available(), "module zstandard requis")
class LazyLoadingTests(unittest.TestCase):
    """Rien ne doit être analysé tant que l'onglet correspondant n'est pas ouvert."""

    def setUp(self):
        self._tmp_config = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp_config.close()
        old = app_module.CONFIG_PATH
        app_module.CONFIG_PATH = Path(self._tmp_config.name)
        self.addCleanup(lambda: setattr(app_module, "CONFIG_PATH", old))
        self.app = app_module.App()
        self.calls = []

    def _open_top(self, name: str, data: bytes) -> Document:
        doc = Document(Level(name, data, self.app.dicts, rel=name))
        self.app.documents["k"] = doc
        self.app.current_key = "k"
        self.app._refresh_all()
        return doc

    def _count_parses(self, module, name: str) -> None:
        original = getattr(module, name)

        def wrapper(*args, **kwargs):
            self.calls.append(name)
            return original(*args, **kwargs)

        setattr(module, name, wrapper)
        self.addCleanup(setattr, module, name, original)

    def test_bfres_is_parsed_only_when_model_tab_opens(self):
        self._count_parses(app_module.bfres_mod.BfresFile, "parse")
        bfres = synth.make_bfres()
        self._open_top("modele.bfres", bfres)
        self.assertIsNone(self.app.bfres_file)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.app._bfres_cache, {})

        self.app._ensure_model()
        self.assertEqual(len(self.calls), 1)
        self.assertIsNotNone(self.app.bfres_file)
        self.app._ensure_model()          # second affichage : cache, aucune analyse
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.app._current_lod(), 0)

    def test_bntx_is_parsed_only_when_texture_tab_opens(self):
        self._count_parses(app_module.bntx_mod.BntxFile, "parse")
        self._open_top("textures.bntx", synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))
        self.assertIsNone(self.app.texture_file)
        self.assertEqual(self.calls, [])
        self.app._ensure_textures()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.app.texture_file.textures), 1)
        self.assertIn("texture", self.app.texture_info_var.get().lower())
        self.app._ensure_textures()
        self.assertEqual(len(self.calls), 1)

    def test_tabs_report_missing_source_without_crashing(self):
        self._open_top("notes.txt", b"bonjour")
        written = []
        original = self.app._set_model_text
        self.app._set_model_text = lambda text: (written.append(text), original(text))
        self.app.texture_file = None
        self.app._ensure_textures()
        self.assertIn("bntx", self.app.texture_info_var.get().lower())
        self.app.bfres_file = None
        self.app._ensure_model()
        self.assertIn("bfres", " ".join(written).lower())
        self.app.select_tab(0)            # ne lève pas
        self.app.select_tab(1)
        self.app.select_tab(2)

    def test_preview_does_not_build_meshes(self):
        self._open_top("modele.bfres", synth.make_bfres())
        self.app._ensure_model()
        self.assertEqual(self.app._mesh_cache, {})
        file = self.app.bfres_file
        meshes = self.app._meshes_for_view(file=file)
        self.assertTrue(meshes)
        self.assertTrue(self.app._mesh_cache)
        again = self.app._meshes_for_view(file=file)
        self.assertIs(meshes, again)

    def test_background_end_closes_popups(self):
        popup = app_module.LoadingPopup(self.app, "Chargement…")
        self.app._popups.append(popup)
        self.app._bg_done("ok", lambda result: None)
        self.assertEqual(self.app._popups, [])
        self.assertTrue(getattr(popup, "_destroyed", True))
        popup2 = app_module.LoadingPopup(self.app, "Chargement…")
        self.app._popups.append(popup2)
        self.app._bg_failed(app_module.HookyError("boum"), lambda exc: None)
        self.assertEqual(self.app._popups, [])
        self.assertEqual(self.app.status_var.get().count("Erreur"), 0)

    def test_loading_popup_is_safe_without_display(self):
        popup = app_module.LoadingPopup(self.app, "Chargement…")
        popup.set_progress(3, 10)
        popup.set_text("suite")
        popup.request_close()
        popup.close()
        popup.close()                     # idempotent
        self.app._popups.append(popup)


class TexturePreviewTests(unittest.TestCase):
    """Le chemin d'affichage doit rester utilisable sans Pillow ni PhotoImage."""

    def test_texture_preview_target_is_bounded(self):
        app = app_module.App.__new__(app_module.App)
        app.config_data = {"preview_max": 4096}
        app.texture_canvas = types.SimpleNamespace(winfo_width=lambda: 320,
                                                   winfo_height=lambda: 240)
        self.assertGreaterEqual(app._preview_target(), 320)
        self.assertLessEqual(app._preview_target(), 4096)
        app.config_data = {"preview_max": 128}
        self.assertEqual(app._preview_target(), 128)


@unittest.skipUnless(zs.zstd_available(), "module zstandard requis")
class TextureRedrawTests(unittest.TestCase):
    """La visionneuse de textures doit réellement décoder et écrire un PNG."""

    def setUp(self):
        self._tmp_config = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp_config.close()
        old = app_module.CONFIG_PATH
        app_module.CONFIG_PATH = Path(self._tmp_config.name)
        self.addCleanup(lambda: setattr(app_module, "CONFIG_PATH", old))
        self.app = app_module.App()
        self.shown = []
        self.app._show_texture = lambda path, tex, mip, w, h, keep_view=True:             self.shown.append((Path(path), w, h))
        def run_sync(label, work, done, failed=None):
            try:
                done(work())
            except Exception as exc:  # noqa: BLE001 - imite _run_bg
                if failed is not None:
                    failed(exc)
                else:
                    raise
        self.app._run_bg = run_sync
        self.decoded = 0

    def _prepare(self, size: int = 8):
        data = synth.make_bntx(size, size, synth.rgba8_pixels(size, size))
        self.app._load_textures_from_data(data, key="test.bntx")
        texture = self.app.texture_file.textures[0]
        self.app._selected_texture = lambda: texture
        return texture

    def test_redraw_writes_png_and_uses_cache(self):
        texture = self._prepare()
        original_decode = texture.decode

        def counting(*args, **kwargs):
            self.decoded += 1
            return original_decode(*args, **kwargs)

        texture.decode = counting
        self.app._redraw_texture()
        self.assertEqual(self.decoded, 1)
        self.assertEqual(len(self.shown), 1)
        path, width, height = self.shown[0]
        self.assertTrue(path.exists())
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual((width, height), (8, 8))

        # même texture : servi par le cache, aucun décodage supplémentaire
        self.app._redraw_texture()
        self.assertEqual(self.decoded, 1)
        self.assertEqual(len(self.shown), 2)

    def test_redraw_reports_decode_errors(self):
        self._prepare()
        messages = []

        def boom(*args, **kwargs):
            raise app_module.HookyError("BC7 non supporté")

        self.app.texture_file.textures[0].decode = boom
        self.app._redraw_texture()
        self.assertFalse(self.shown)
        self.assertEqual(self.app._texture_previews, {})
        self.assertIn("bc7", self.app.texture_info_var.get().lower())
        messages.append(self.app.texture_info_var.get())
        self.assertTrue(messages)

    def test_reset_textures_clears_view(self):
        self._prepare()
        self.app._redraw_texture()
        self.app._reset_textures()
        self.assertIsNone(self.app.texture_file)
        self.assertEqual(self.app.texture_tree.selection(), ())


@unittest.skipUnless(zs.zstd_available(), "module zstandard requis")
class ModelViewerFlowTests(unittest.TestCase):
    """« Voir en 3D » doit fournir des maillages au viewer, sans analyse préalable."""

    def setUp(self):
        self._tmp_config = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp_config.close()
        old = app_module.CONFIG_PATH
        app_module.CONFIG_PATH = Path(self._tmp_config.name)
        self.addCleanup(lambda: setattr(app_module, "CONFIG_PATH", old))
        self.app = app_module.App()
        self.opened = []
        old_viewer = app_module.open_viewer
        app_module.open_viewer = lambda master, meshes, title="": \
            self.opened.append((list(meshes), title))
        self.addCleanup(setattr, app_module, "open_viewer", old_viewer)

    def _open_top(self, name: str, data: bytes) -> Document:
        doc = Document(Level(name, data, self.app.dicts, rel=name))
        self.app.documents["k"] = doc
        self.app.current_key = "k"
        self.app._refresh_all()
        return doc

    def test_view_model_opens_viewer_with_meshes(self):
        self._open_top("modele.bfres", synth.make_bfres())
        self.app.view_model()
        self.assertEqual(len(self.opened), 1)
        meshes, title = self.opened[0]
        self.assertTrue(meshes)
        self.assertTrue(all(m.faces for m in meshes))
        self.assertIn("modele.bfres", title)

    def test_view_model_on_non_model_reports_cleanly(self):
        self._open_top("notes.txt", b"bonjour")
        errors = []
        self.app.error = lambda exc, context="": errors.append(str(exc))
        self.app.view_model()
        self.assertFalse(self.opened)
        self.assertTrue(errors)
        self.assertIn("modèle", errors[0])

    def test_second_view_reuses_cached_meshes(self):
        self._open_top("modele.bfres", synth.make_bfres())
        self.app.view_model()
        first = self.app._meshes_for_view()
        self.app.view_model()
        self.assertIs(first, self.app._meshes_for_view())


if __name__ == "__main__":
    unittest.main(verbosity=2)
