"""Tests du traducteur romfs ↔ PC (projet, pack, mod Hookshot)."""

from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import bfres, bntx, cli, translate, util, zs  # noqa: E402
from hookymod.util import HookyError  # noqa: E402

HAS_ZSTD = zs.zstd_available()


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.romfs = self.root / "romfs"
        (self.romfs / "Pack").mkdir(parents=True)
        (self.romfs / "Model").mkdir()
        (self.romfs / "Textures").mkdir()
        dictionary = synth.train_dict([b"asset %d" % i * 40 for i in range(64)], 1024)
        (self.romfs / "Pack" / "ZsDic.pack.zs").write_bytes(
            synth.compress(synth.make_sarc([("zs.zsdic", dictionary)])))
        self.store = zs.ZstdDicts()
        self.store.load_file(self.romfs / "Pack" / "ZsDic.pack.zs")
        self.dict_id = next(iter(self.store.by_id))
        self.bfres_data = synth.make_bfres()
        self.bntx_data = synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8, seed=1))
        (self.romfs / "Model" / "test.bfres.zs").write_bytes(
            self.store.compress(self.bfres_data, self.dict_id))
        (self.romfs / "Textures" / "tex.bntx.zs").write_bytes(
            self.store.compress(self.bntx_data, self.dict_id))
        (self.romfs / "readme.txt").write_bytes(b"hello\n")
        self.project = self.root / "projet"

    # ------------------------------------------------------------------ helpers
    def unpack(self) -> translate.Manifest:
        return translate.unpack(self.romfs, self.project)

    def entry(self, manifest, rel):
        return next(item for item in manifest.entries if item.rel == rel)

    # ------------------------------------------------------------------ tests
    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_unpack_builds_editable_tree(self):
        manifest = self.unpack()
        kinds = {entry.rel: entry.kind for entry in manifest.entries}
        self.assertEqual(kinds["Model/test.bfres.zs"], "bfres")
        self.assertEqual(kinds["Textures/tex.bntx.zs"], "bntx")
        self.assertEqual(kinds["readme.txt"], "raw")
        self.assertTrue((self.project / "pc" / "Textures" / "tex.bntx.png").exists())
        self.assertTrue((self.project / "pc" / "Model" / "test.bfres_m0_s0.obj").exists())
        self.assertEqual((self.project / "pc" / "readme.txt").read_bytes(), b"hello\n")
        self.assertTrue((self.project / "origin" / "Textures" / "tex.bntx.zs").exists())
        saved = json.loads((self.project / "manifest.json").read_text())
        self.assertEqual(saved["romfs"], str(self.romfs))
        self.assertEqual(len(saved["entries"]), 4)

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_status_starts_clean(self):
        manifest = self.unpack()
        project = translate.Project(self.project).open()
        self.assertFalse(any(project.changed(entry) for entry in manifest.entries))
        self.assertIn("0 modifiée(s)", " ".join(translate.status(self.project)))

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_pack_unmodified_is_byte_identical(self):
        self.unpack()
        result = translate.pack(self.project, self.root / "rebuild", all_files=True)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.written), 4)
        for rel in ("Model/test.bfres.zs", "Textures/tex.bntx.zs", "readme.txt"):
            self.assertEqual((self.root / "rebuild" / "romfs" / rel).read_bytes(),
                             (self.romfs / rel).read_bytes(), rel)

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_pack_skips_unmodified_by_default(self):
        self.unpack()
        result = translate.pack(self.project, self.root / "mods")
        self.assertEqual(result.written, [])
        self.assertEqual(len(result.skipped), 4)

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_edit_png_produces_new_texture(self):
        self.unpack()
        red = bytes([255, 0, 0, 255]) * 64
        (self.project / "pc" / "Textures" / "tex.bntx.png").write_bytes(
            util.png_bytes(8, 8, red))
        result = translate.pack(self.project, self.root / "mods", mod_name="MonMod")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.written, ["Textures/tex.bntx.zs"])
        packed = (self.root / "mods" / "MonMod" / "romfs" / "Textures" / "tex.bntx.zs")
        self.assertTrue(packed.exists())
        data = self.store.decompress(packed.read_bytes())
        texture = bntx.BntxFile.parse(data).textures[0]
        self.assertEqual(texture.decode(0)[2][:4], red[:4])
        self.assertEqual(len(data), len(self.bntx_data) + 0)   # même taille (1 mip)

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_edited_v10_obj_is_refused_without_partial_output(self):
        self.unpack()
        obj = self.project / "pc" / "Model" / "test.bfres_m0_s0.obj"
        obj.write_text(obj.read_text().replace("v 0 0 0", "v 5 5 5", 1))
        result = translate.pack(self.project, self.root / "mods", mod_name="MonMod")
        self.assertTrue(any("10.x" in error for error in result.errors), result.errors)
        packed = self.root / "mods" / "MonMod" / "romfs" / "Model" / "test.bfres.zs"
        self.assertFalse(packed.exists())

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_compatible_v9_obj_produces_new_geometry_in_output_only(self):
        original = bytearray(self.bfres_data)
        struct.pack_into("<I", original, 8, 0x00090002)
        source = self.romfs / "Model" / "test.bfres.zs"
        source.write_bytes(self.store.compress(bytes(original), self.dict_id))
        source_bytes = source.read_bytes()
        self.unpack()
        obj = self.project / "pc" / "Model" / "test.bfres_m0_s0.obj"
        obj.write_text("v 5 5 5\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        result = translate.pack(self.project, self.root / "mods", mod_name="MonMod")
        self.assertEqual(result.errors, [])
        packed = self.root / "mods" / "MonMod" / "romfs" / "Model" / "test.bfres.zs"
        data = self.store.decompress(packed.read_bytes())
        mesh = bfres.BfresFile.parse(data).models[0].build_meshes(0)[0]
        self.assertEqual(mesh.positions[0], (5.0, 5.0, 5.0))
        self.assertEqual(len(data), len(original))
        self.assertEqual(source.read_bytes(), source_bytes)

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_raw_edit_is_recompressed(self):
        self.unpack()
        (self.project / "pc" / "readme.txt").write_bytes(b"hello world\n")
        result = translate.pack(self.project, self.root / "mods", mod_name="MonMod")
        self.assertEqual(result.written, ["readme.txt"])
        self.assertEqual((self.root / "mods" / "MonMod" / "romfs" / "readme.txt")
                         .read_bytes(), b"hello world\n")

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_filter_and_limit(self):
        manifest = translate.unpack(self.romfs, self.project, only="textures")
        self.assertEqual([entry.rel for entry in manifest.entries],
                         ["Textures/tex.bntx.zs"])
        manifest = translate.unpack(self.romfs, self.root / "p2", limit=2)
        self.assertEqual(len(manifest.entries) >= 1, True)

    def test_missing_romfs(self):
        with self.assertRaises(HookyError):
            translate.unpack(self.root / "absent", self.project)

    def test_project_without_manifest(self):
        with self.assertRaises(HookyError):
            translate.Project(self.project).open()

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_size_table_is_grown_when_needed(self):
        resource = self.romfs / "System" / "Resource"
        resource.mkdir(parents=True)
        (resource / "ResourceSizeTable.Product.100.rsizetable").write_bytes(
            synth.make_restbl({"readme.txt": 4, "Model/test.bfres": 10}))
        self.unpack()
        (self.project / "pc" / "readme.txt").write_bytes(b"beaucoup plus long\n")
        result = translate.pack(self.project, self.root / "mods", mod_name="MonMod")
        self.assertIn("Table de tailles", result.table_note)
        table = self.root / "mods" / "MonMod" / "romfs" / "System" / "Resource" / \
            "ResourceSizeTable.Product.100.rsizetable"
        self.assertTrue(table.exists())
        from hookymod import restbl
        parsed = restbl.ResourceSizeTable.parse(table.read_bytes())
        self.assertGreaterEqual(parsed.get("readme.txt"), len(b"beaucoup plus long\n"))


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.romfs = self.root / "romfs"
        (self.romfs / "Model").mkdir(parents=True)
        (self.romfs / "Model" / "test.bfres").write_bytes(synth.make_bfres())
        self.project = self.root / "projet"

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_unpack_status_pack(self):
        code, out, err = self.run_cli(["translate", "unpack", "--romfs", str(self.romfs),
                                       "-p", str(self.project)])
        self.assertEqual(code, 0, err)
        self.assertIn("Projet créé", out)
        code, out, err = self.run_cli(["translate", "status", "-p", str(self.project)])
        self.assertEqual(code, 0, err)
        self.assertIn("0 modifiée(s)", out)
        code, out, err = self.run_cli(["translate", "pack", "-p", str(self.project),
                                       "-o", str(self.root / "mods"),
                                       "--mod-name", "MonMod"])
        self.assertEqual(code, 0, err)
        self.assertIn("inchangé", out)


if __name__ == "__main__":
    unittest.main()
