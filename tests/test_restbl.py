"""Tests de la table des tailles de ressources (ResourceSizeTable / RESTBL)."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import cli, restbl, zs  # noqa: E402
from hookymod.util import FormatError, HookyError  # noqa: E402

HAS_ZSTD = zs.zstd_available()

ENTRIES = {
    "Model/Test.bfres": 4096,
    "Textures/Test.bntx": 2048,
    "Pack/Data.pack": 1024,
}


class TableTests(unittest.TestCase):
    def setUp(self):
        self.table = restbl.ResourceSizeTable.parse(synth.make_restbl(ENTRIES))

    def test_key_strips_zs(self):
        self.assertEqual(restbl.key_for("Model/Test.bfres"),
                         restbl.key_for("Model/Test.bfres.zs"))
        self.assertNotEqual(restbl.key_for("Model/Test.bfres"),
                            restbl.key_for("Model/Autre.bfres"))

    def test_parse_entries(self):
        self.assertEqual(len(self.table), 3)
        self.assertEqual(self.table.version, 3)
        self.assertEqual(self.table.get("Model/Test.bfres.zs"), 4096)

    def test_build_roundtrip_is_sorted(self):
        data = self.table.build()
        again = restbl.ResourceSizeTable.parse(data)
        self.assertEqual(again.sizes, self.table.sizes)
        keys = [restbl.struct.unpack_from("<II", data, restbl.HEADER_SIZE + 8 * i)[0]
                for i in range(len(self.table))]
        self.assertEqual(keys, sorted(keys))

    def test_ensure_only_grows(self):
        self.assertFalse(self.table.ensure("Model/Test.bfres", 4096))
        self.assertFalse(self.table.ensure("Model/Test.bfres", 100))
        self.assertTrue(self.table.ensure("Model/Test.bfres", 8192))
        self.assertEqual(self.table.get("Model/Test.bfres"), 8192)

    def test_ensure_adds_missing_entry(self):
        self.assertTrue(self.table.ensure("Model/New.bfres", 500))
        self.assertGreaterEqual(self.table.get("Model/New.bfres"), 500)
        self.assertEqual(len(self.table), 4)

    def test_check_and_grow_for(self):
        files = {"Model/Test.bfres": 8192, "Textures/Test.bntx": 128}
        problems = self.table.check(files)
        self.assertEqual(len(problems), 1)
        self.assertIn("Model/Test.bfres", problems[0])
        changes = self.table.grow_for(files)
        self.assertEqual(len(changes), 1)
        self.assertEqual(self.table.check(files), [])
        self.assertGreaterEqual(self.table.get("Model/Test.bfres"), 8192)

    def test_named_entries_are_preserved(self):
        named = b"some/Name.bin" + bytes(16)
        table = restbl.ResourceSizeTable.parse(
            synth.make_restbl(ENTRIES, named=named, named_count=1))
        table.ensure("Model/Test.bfres", 9999)
        rebuilt = restbl.ResourceSizeTable.parse(table.build())
        self.assertEqual(rebuilt.tail, named)
        self.assertEqual(rebuilt.named_count, 1)

    def test_not_a_table(self):
        with self.assertRaises(FormatError):
            restbl.ResourceSizeTable.parse(b"NOPE" + bytes(32))
        with self.assertRaises(FormatError):
            restbl.ResourceSizeTable.parse(b"RESTBL" + bytes(4))


class RomfsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.romfs = self.root / "romfs"
        self.folder = self.romfs / "System" / "Resource"
        self.folder.mkdir(parents=True)
        self.raw_table = synth.make_restbl(ENTRIES)

    def test_find_tables(self):
        (self.folder / "ResourceSizeTable.Product.100.rsizetable").write_bytes(self.raw_table)
        self.assertEqual([path.name for path in restbl.find_tables(self.romfs)],
                         ["ResourceSizeTable.Product.100.rsizetable"])
        self.assertEqual(restbl.find_tables(self.root / "absent"), [])

    def test_build_and_grow(self):
        (self.folder / "ResourceSizeTable.Product.100.rsizetable").write_bytes(self.raw_table)
        table, path, raw = restbl.load_table(self.romfs)
        self.assertTrue(path.name.startswith("ResourceSizeTable."))
        self.assertEqual(raw, self.raw_table)
        self.assertEqual(table.get("Pack/Data.pack"), 1024)

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_zs_table_without_dictionary(self):
        packed = synth.compress(self.raw_table)
        (self.folder / "ResourceSizeTable.Product.100.rsizetable.zs").write_bytes(packed)
        table, _, raw = restbl.load_table(self.romfs)
        self.assertEqual(raw, packed)
        self.assertEqual(table.get("Model/Test.bfres"), 4096)

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_zs_table_with_dictionary(self):
        dictionary = synth.train_dict([b"asset %d" % i * 40 for i in range(64)], 1024)
        store = zs.ZstdDicts()
        store.add("zs.zsdic", dictionary)
        did = next(iter(store.by_id))
        (self.folder / "ResourceSizeTable.Product.100.rsizetable.zs").write_bytes(
            store.compress(self.raw_table, did))
        table, _, _ = restbl.load_table(self.romfs, store)
        self.assertEqual(table.get("Textures/Test.bntx"), 2048)

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_zs_table_without_dictionary_fails_helpfully(self):
        dictionary = synth.train_dict([b"asset %d" % i * 40 for i in range(64)], 1024)
        store = zs.ZstdDicts()
        store.add("zs.zsdic", dictionary)
        did = next(iter(store.by_id))
        (self.folder / "ResourceSizeTable.Product.100.rsizetable.zs").write_bytes(
            store.compress(self.raw_table, did))
        with self.assertRaises(HookyError):
            restbl.load_table(self.romfs)

    def test_no_table(self):
        with self.assertRaises(HookyError):
            restbl.load_table(self.romfs)


@unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.romfs = self.root / "romfs"
        folder = self.romfs / "System" / "Resource"
        folder.mkdir(parents=True)
        (folder / "ResourceSizeTable.Product.100.rsizetable").write_bytes(
            synth.make_restbl(ENTRIES))

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_list(self):
        code, out, err = self.run_cli(["restbl", str(self.romfs), "--list"])
        self.assertEqual(code, 0, err)
        self.assertIn("3 entrée(s)", out)

    def test_get(self):
        code, out, err = self.run_cli(["restbl", str(self.romfs),
                                       "--get", "Model/Test.bfres"])
        self.assertEqual(code, 0, err)
        self.assertIn("4096", out)

    def test_ensure_and_write(self):
        target = self.root / "new_table"
        code, out, err = self.run_cli(["restbl", str(self.romfs),
                                       "--ensure", "Model/Test.bfres=9000",
                                       "-o", str(target)])
        self.assertEqual(code, 0, err)
        table = restbl.ResourceSizeTable.parse(target.read_bytes())
        self.assertGreaterEqual(table.get("Model/Test.bfres"), 9000)

    def test_missing_path_in_table(self):
        code, out, err = self.run_cli(["restbl", str(self.romfs),
                                       "--get", "Absent/File.bin"])
        self.assertEqual(code, 1)
        self.assertIn("absente", out.lower() + err.lower())


if __name__ == "__main__":
    unittest.main()
