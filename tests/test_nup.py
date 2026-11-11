"""Tests du lecteur de paquets .nup (dumps Switch 2)."""

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

import synth_nup  # noqa: E402

from hookymod import cli, identify, nup  # noqa: E402
from hookymod.util import FormatError, HookyError  # noqa: E402


class ParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = synth_nup.make_sample_nup()
        cls.package = nup.NupPackage.from_bytes(cls.data)

    def test_header_and_titles(self):
        package = self.package
        self.assertEqual(len(package), 1)
        title = package.titles[0]
        self.assertEqual(title.version, 1)
        self.assertTrue(title.has("romfs"))
        self.assertTrue(title.has("exefs"))
        self.assertIn("romfs : 4 fichier(s)", " ".join(package.summary()))

    def test_walk_romfs(self):
        paths = [rel for rel, _ in self.package.members()]
        self.assertIn("romfs/Model/test.bfres", paths)
        self.assertIn("romfs/Textures/test.bntx.zs", paths)
        self.assertIn("exefs/main", paths)
        self.assertEqual(len(paths), 6)

    def test_read_packed_and_raw(self):
        members = dict(self.package.members())
        packed = members["romfs/Pack/Data.pack"]
        self.assertEqual(packed.codec, 1)
        self.assertTrue(self.package.is_packed(packed))
        self.assertTrue(self.package.read(packed).startswith(b"SARC"))

        plain = members["romfs/Model/test.bfres"]
        self.assertEqual(plain.codec, 0)
        self.assertFalse(self.package.is_packed(plain))
        self.assertTrue(self.package.read(plain).startswith(b"FRES"))

    def test_game_zs_member_is_not_unpacked(self):
        """Un .zs du romfs est déjà une frame zstd : on n'y touche pas."""
        members = dict(self.package.members())
        entry = members["romfs/Textures/test.bntx.zs"]
        data = self.package.read(entry)
        self.assertEqual(data, self.package.raw(entry))
        self.assertTrue(data.startswith(nup.ZSTD_MAGIC))
        self.assertFalse(self.package.is_packed(entry))

    def test_verify(self):
        self.assertEqual(self.package.verify(), [])
        self.assertEqual(self.package.verify(deep=True), [])

    def test_frame_content_size(self):
        frame = synth_nup.synth.compress(b"A" * 4096)
        self.assertEqual(nup.frame_content_size(frame), 4096)

    def test_not_a_nup(self):
        with self.assertRaises(FormatError):
            nup.NupPackage.from_bytes(b"XXXX" + bytes(0x100))
        with self.assertRaises(FormatError):
            nup.NupPackage.from_bytes(b"NUP\x01")

    def test_truncated_package_is_reported(self):
        truncated = self.data[:0x100]
        with self.assertRaises(FormatError) as caught:
            nup.NupPackage.from_bytes(truncated)
        self.assertIn("incomplet", str(caught.exception))

    def test_title_lookup(self):
        self.assertEqual(self.package.title(0).index, 0)
        self.assertEqual(self.package.title("0100000000000001").index, 0)
        with self.assertRaises(HookyError):
            self.package.title("ff")


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.package = nup.NupPackage.from_bytes(synth_nup.make_sample_nup())

    def test_extract_romfs_only(self):
        result = self.package.extract(self.root, only="romfs")
        self.assertEqual(result.count, 4)
        self.assertEqual(result.problems, [])
        self.assertEqual((self.root / "romfs" / "readme.txt").read_bytes(),
                         b"sample romfs\n")
        self.assertTrue((self.root / "romfs" / "Model" / "test.bfres").exists())
        self.assertTrue((self.root / "romfs" / "Pack" / "Data.pack")
                        .read_bytes().startswith(b"SARC"))
        self.assertFalse((self.root / "exefs").exists())

    def test_extract_everything_matches_hashes(self):
        self.package.extract(self.root)
        for rel, entry in self.package.members():
            self.assertEqual((self.root / rel).read_bytes(), self.package.read(entry))

    def test_progress_callback(self):
        seen = []
        self.package.extract(self.root, progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen[-1], (6, 6))

    def test_extract_with_sha_mismatch_still_reads(self):
        package = nup.NupPackage.from_bytes(synth_nup.make_nup(
            {"romfs": {"a.bin": b"AAAA"}}))
        entry = dict(package.members())["romfs/a.bin"]
        entry.sha256 = b"\xff" * 32
        self.assertFalse(package.verify_sha256(entry))
        self.assertIn("SHA-256 différent", " ".join(package.verify(deep=True)))


class IdentifyTests(unittest.TestCase):
    def test_nup_magic(self):
        found = identify.sniff(synth_nup.make_sample_nup(), "game.nup")
        self.assertEqual(found[0].kind, "nup")
        self.assertIn("Switch 2", found[0].label + found[0].family)

    def test_engine_is_switch2(self):
        engine, _ = identify.guess_engine({"nup": 1}, {".nup"})
        self.assertIn("Switch 2", engine)


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.package_path = self.root / "game.nup"
        self.package_path.write_bytes(synth_nup.make_sample_nup())

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_list(self):
        code, out, err = self.run_cli(["nup", str(self.package_path), "--list"])
        self.assertEqual(code, 0, err)
        self.assertIn("romfs/Model/test.bfres", out)
        self.assertIn("titre #0", out)

    def test_extract(self):
        target = self.root / "out"
        code, out, err = self.run_cli(["nup", str(self.package_path),
                                       "--extract", str(target), "--only", "romfs"])
        self.assertEqual(code, 0, err)
        self.assertTrue((target / "romfs" / "Model" / "test.bfres").exists())
        self.assertIn("4 fichier(s)", out)

    def test_verify(self):
        code, out, err = self.run_cli(["nup", str(self.package_path), "--verify", "--deep"])
        self.assertEqual(code, 0, err)
        self.assertIn("aucune anomalie", out.lower())


if __name__ == "__main__":
    unittest.main()
