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


if __name__ == "__main__":
    unittest.main(verbosity=2)
