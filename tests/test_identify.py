"""Tests de l'identification de formats et du scan de dossier."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import cli, identify  # noqa: E402

HAS_ZSTD = __import__("hookymod.zs", fromlist=["zs"]).zstd_available()


class SniffTests(unittest.TestCase):
    def kinds(self, data: bytes, name: str = "", tail: bytes = b"") -> list[str]:
        return [found.kind for found in identify.sniff(data, name, tail)]

    def test_sarc(self):
        self.assertEqual(self.kinds(synth.make_sarc([("a.bin", b"A")])), ["sarc"])

    def test_bfres_and_bntx(self):
        self.assertEqual(self.kinds(synth.make_bfres()), ["bfres"])
        self.assertEqual(self.kinds(synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4))),
                         ["bntx"])

    def test_unreal_asset_and_pak(self):
        self.assertEqual(self.kinds(b"\x9e\x2a\x83\xc1" + b"\x00" * 32)[0], "uasset")
        self.assertEqual(self.kinds(b"payload" * 8, "data.pak", b"XXXX\xe1\x12\x6f\x5a"),
                         ["ue_pak"])

    def test_unity_bundle(self):
        self.assertEqual(self.kinds(b"UnityFS\x00\x00\x00\x00\x05\x00")[0], "unityfs")

    def test_extension_fallback(self):
        found = identify.sniff(b"\x00\x01\x02\x03", "model.bfres")
        self.assertEqual(found[0].kind, "bfres")
        self.assertEqual(found[0].confidence, "extension")

    def test_text_detection(self):
        found = identify.sniff(b"name: Link\nhp: 20\ndescription: heros\n" * 4)
        self.assertEqual(found[0].confidence, "texte")

    def test_unknown(self):
        found = identify.sniff(b"\x00\xff" * 64, "blob.dat")
        self.assertEqual(found[0].kind, "unknown")

    def test_extension_of(self):
        self.assertEqual(identify.extension_of("a/b/Text.bntx.zs"), ".bntx.zs")
        self.assertEqual(identify.extension_of("test.bfres"), ".bfres")
        self.assertEqual(identify.extension_of("README"), "")

    @unittest.skipUnless(HAS_ZSTD, "module zstandard requis")
    def test_zstd_outranks_inner_archive(self):
        packed = synth.compress(synth.make_sarc([("a.bin", b"AAA")]))
        found = identify.sniff(packed, "pack.zs")
        self.assertEqual(found[0].kind, "zstd")
        self.assertIn("sarc", [item.kind for item in found])

    def test_engine_guess(self):
        engine, _ = identify.guess_engine({"sarc": 10, "bfres": 3}, {".zs"})
        self.assertEqual(engine, "SARC / BFRES / BNTX")
        engine, _ = identify.guess_engine({"uasset": 40}, {".uasset"})
        self.assertIn("Unreal", engine)
        engine, _ = identify.guess_engine({"unityfs": 5}, {".bundle"})
        self.assertIn("Unity", engine)
        engine, _ = identify.guess_engine({"zar": 4, "cmb": 2}, {".zar"})
        self.assertEqual(engine, "ZAR / CMB / CTEX")
        engine, _ = identify.guess_engine({}, set())
        self.assertEqual(engine, "Inconnu")


class CarveTests(unittest.TestCase):
    def test_carve_finds_embedded_structures(self):
        blob = b"\x00" * 64 + synth.make_sarc([("a.bin", b"A")]) + b"\x11" * 8
        found = identify.carve(blob)
        self.assertTrue(found)
        self.assertEqual(found[0].kind, "sarc")
        self.assertEqual(found[0].offset, 64)

    def test_carve_ignores_offset_zero(self):
        self.assertEqual(identify.carve(synth.make_sarc([("a.bin", b"A")])), [])

    def test_cli_carve(self):
        path = Path(tempfile.mkdtemp()) / "blob.dat"
        self.addCleanup(lambda: __import__("shutil").rmtree(path.parent, ignore_errors=True))
        path.write_bytes(b"\x00" * 32 + synth.make_bfres())
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["carve", str(path)])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("0x00000020", out.getvalue())
        self.assertIn("BFRES", out.getvalue())


class ScanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "romfs" / "Pack").mkdir(parents=True)
        (self.root / "romfs" / "Model").mkdir()
        (self.root / "romfs" / "Model" / "test.bfres").write_bytes(synth.make_bfres())
        (self.root / "romfs" / "Textures").mkdir()
        (self.root / "romfs" / "Textures" / "tex.bntx").write_bytes(
            synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))
        (self.root / "romfs" / "Pack" / "Data.pack").write_bytes(
            synth.make_sarc([("a.bin", b"AAA")]))
        (self.root / "romfs" / "System" ).mkdir()
        (self.root / "romfs" / "System" / "params.bin").write_bytes(b"\x00\xff" * 32)
        (self.root / "Game.exe").write_bytes(b"MZ" + b"\x00" * 512)
        (self.root / "level.dat").write_bytes(b"\x00\xff" * 16)

    def test_scan_counts_and_engine(self):
        report = identify.scan(self.root)
        self.assertEqual(report["file_count"], 6)
        self.assertEqual(report["engine"], "SARC / BFRES / BNTX")
        self.assertEqual(report["extensions"][".bfres"]["count"], 1)
        self.assertEqual(report["extensions"][".bin"]["count"], 1)
        self.assertGreater(report["total_size"], 0)

    def test_scan_reports_carved_structures(self):
        (self.root / "container.dat").write_bytes(b"\x00" * 32 + synth.make_bfres())
        report = identify.scan(self.root)
        sample = report["extensions"][".dat"]["samples"][0]
        self.assertTrue(sample["carved"])
        self.assertEqual(sample["carved"][0]["kind"], "bfres")

    def test_scan_is_json_serializable(self):
        report = identify.scan(self.root, hexdump_bytes=16, strings=4)
        text = identify.report_to_json(report)
        data = json.loads(text)
        self.assertEqual(data["file_count"], 6)
        sample = data["extensions"][".bfres"]["samples"][0]
        self.assertEqual(sample["kind"], "bfres")
        self.assertTrue(sample["hexdump"][0].startswith("00000000"))
        self.assertIn("TestModel", " ".join(sample.get("strings", [])))

    def test_scan_single_file(self):
        report = identify.scan(self.root / "Game.exe")
        self.assertTrue(report["is_file"])
        self.assertEqual(report["file_count"], 1)
        self.assertEqual(report["extensions"][".exe"]["samples"][0]["kind"], "mz")

    def test_scan_depth_and_limit(self):
        shallow = identify.scan(self.root, max_depth=1)
        self.assertNotIn(".bfres", shallow["extensions"])
        limited = identify.scan(self.root, limit=2)
        self.assertEqual(limited["file_count"], 2)
        self.assertTrue(limited["truncated"])

    def test_scan_missing_path(self):
        from hookymod.util import HookyError
        with self.assertRaises(HookyError):
            identify.scan(self.root / "absent")

    def test_report_contains_sections(self):
        text = identify.format_report(identify.scan(self.root))
        for expected in ("Moteur probable", "Familles détectées",
                         "Extensions (par nombre)", "Échantillons"):
            self.assertIn(expected, text)

    def test_cli_scan_json(self):
        out_file = self.root / "rapport.json"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["scan", str(self.root), "--json", "-o", str(out_file)])
        self.assertEqual(code, 0, err.getvalue())
        self.assertTrue(out_file.exists())
        self.assertEqual(json.loads(out_file.read_text())["file_count"], 6)

    def test_cli_scan_text(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["scan", str(self.root), "--hexdump", "16"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("Moteur probable", out.getvalue())


if __name__ == "__main__":
    unittest.main()
