"""Tests de l'interface en ligne de commande (sans Tkinter)."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import __version__ as core_version, cli, zs  # noqa: E402
from hookycreator import __version__ as creator_version, __main__ as creator_main  # noqa: E402

HAS_ZSTD = zs.zstd_available()


class VersionTests(unittest.TestCase):
    def test_core_cli_reports_the_package_version(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as result:
            cli.main(["--version"])
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"HookyMod {core_version}")

    def test_creator_cli_reports_the_package_version(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as result:
            creator_main.main(["--version"])
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"HookyCreator {creator_version}")

    def test_creator_startup_is_local_by_default_and_warns_for_public_binding(self):
        for options, expected_host, warning in (([], "127.0.0.1", False),
                                                 (["--host", "0.0.0.0"], "0.0.0.0", True)):
            with self.subTest(options=options):
                fake = mock.MagicMock()
                fake.scan_summary = {"count": 0, "textures": 0, "models": 0,
                                     "bytecode": 0, "root": "."}
                fake.stopped = False
                fake.url = "http://127.0.0.1:8765/"
                out = io.StringIO()
                with mock.patch.object(creator_main, "HookyCreatorServer", return_value=fake) as factory, \
                     mock.patch("time.sleep", side_effect=KeyboardInterrupt), redirect_stdout(out):
                    self.assertEqual(creator_main.main(options), 0)
                self.assertEqual(factory.call_args.args[2], expected_host)
                self.assertIn(f"HookyCreator {creator_version}", out.getvalue())
                self.assertEqual("sans authentification" in out.getvalue(), warning)


@unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.romfs = cls.root / "romfs"
        (cls.romfs / "Pack").mkdir(parents=True)
        dictionary = synth.train_dict([b"asset %d" % i * 40 for i in range(64)], 1024)
        cls.dictionary = dictionary
        (cls.romfs / "Pack" / "ZsDic.pack.zs").write_bytes(
            synth.compress(synth.make_sarc([("0.zsdic", dictionary)])))
        store = zs.ZstdDicts()
        store.load_file(cls.romfs / "Pack" / "ZsDic.pack.zs")
        did = next(iter(store.by_id))
        cls.bntx = synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8))
        cls.bfres = synth.make_bfres()
        pack = synth.make_sarc([
            ("Textures/test.bntx.zs", store.compress(cls.bntx, did)),
            ("Model/test.bfres", cls.bfres),
            ("readme.txt", b"hello world"),
        ])
        cls.pack = cls.romfs / "Pack" / "Test.pack.zs"
        cls.pack.write_bytes(synth.compress(pack))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_info_directory(self):
        code, out, _ = self.run_cli(["info", str(self.romfs)])
        self.assertEqual(code, 0)
        self.assertIn("Dictionnaires : 1", out)
        self.assertIn("romfs", out)

    def test_info_archive(self):
        code, out, _ = self.run_cli(["info", str(self.pack)])
        self.assertEqual(code, 0)
        self.assertIn("archive SARC", out)
        self.assertIn("readme.txt", out)

    def test_list(self):
        code, out, _ = self.run_cli(["list", str(self.pack)])
        self.assertEqual(code, 0)
        self.assertIn("Model/test.bfres", out)
        self.assertIn("zstd", out)

    def test_list_filter(self):
        code, out, _ = self.run_cli(["list", str(self.pack), "--filter", "bfres"])
        self.assertIn("Model/test.bfres", out)
        self.assertNotIn("readme.txt", out)

    def test_extract_decompress(self):
        out_dir = self.root / "extract"
        code, out, _ = self.run_cli(["extract", str(self.pack), "-o", str(out_dir),
                                     "--decompress", "-v"])
        self.assertEqual(code, 0)
        self.assertTrue((out_dir / "readme.txt").exists())
        self.assertTrue((out_dir / "Textures" / "test.bntx").exists())
        data = (out_dir / "Textures" / "test.bntx").read_bytes()
        self.assertEqual(data, self.bntx)

    def test_export_textures(self):
        out_dir = self.root / "textures"
        code, out, _ = self.run_cli(["textures", str(self.pack),
                                     "--entry", "Textures/test.bntx.zs",
                                     "-o", str(out_dir)])
        self.assertEqual(code, 0)
        files = list(out_dir.glob("*.png"))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].read_bytes().startswith(b"\x89PNG"))

    def test_export_obj(self):
        target = self.root / "model.obj"
        code, out, _ = self.run_cli(["obj", str(self.pack), "--entry", "Model/test.bfres",
                                     "-o", str(target)])
        self.assertEqual(code, 0)
        text = target.read_text()
        self.assertIn("f 1 2 3", text)
        self.assertNotIn("\nvn ", text)

    def test_patch_entry_content(self):
        target = self.root / "patched_entry.zs"
        code, out, _ = self.run_cli(["patch", str(self.pack), "--find", "hello world",
                                     "--replace", "HELLO WORLD", "-o", str(target)])
        self.assertEqual(code, 0)
        code, listing, _ = self.run_cli(["extract", str(target), "-o",
                                        str(self.root / "patched_entry"), "--decompress"])
        self.assertEqual((self.root / "patched_entry" / "readme.txt").read_text(),
                         "HELLO WORLD")

    def test_patch_archive_names(self):
        target = self.root / "patched_name.zs"
        code, out, _ = self.run_cli(["patch", str(self.pack), "--find", "readme",
                                     "--replace", "README", "-o", str(target)])
        self.assertEqual(code, 0)
        _, listing, _ = self.run_cli(["list", str(target)])
        self.assertIn("README.txt", listing)

    def test_replace_entry(self):
        replacement = self.root / "replacement.bin"
        replacement.write_bytes(synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))
        target = self.root / "replaced.zs"
        code, out, _ = self.run_cli(["replace", str(self.pack), "--entry",
                                     "Textures/test.bntx.zs", "--file", str(replacement),
                                     "-o", str(target)])
        self.assertEqual(code, 0)
        _, listing, _ = self.run_cli(["info", str(target)])
        self.assertIn("Textures/test.bntx.zs", listing)
        # l'entrée remplacée doit contenir le nouveau BNTX (4×4, non compressé)
        out_dir = self.root / "replaced_out"
        self.run_cli(["extract", str(target), "-o", str(out_dir), "--decompress",
                      "--romfs", str(self.romfs)])
        data = (out_dir / "Textures" / "test.bntx").read_bytes()
        self.assertEqual(data, replacement.read_bytes())

    def test_dicts(self):
        code, out, _ = self.run_cli(["dicts", str(self.romfs)])
        self.assertEqual(code, 0)
        self.assertIn("Total : 1 dictionnaire(s)", out)

    def test_unknown_path_error(self):
        code, out, err = self.run_cli(["info", str(self.root / "absent.bin")])
        self.assertEqual(code, 1)
        self.assertIn("Erreur", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
