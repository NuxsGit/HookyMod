"""Diagnostics (environnement + anatomie de fichier) et thèmes de l'interface."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import bntx_encode, diagnose, theme, util  # noqa: E402


def _orin_bntx(size: int = 16) -> bytes:
    rgba = synth.rgba8_pixels(size, size, seed=size)
    return bntx_encode.png_to_bntx(util.png_bytes(size, size, rgba),
                                   format_name="RGBA8", orin=True)[0]


class EnvironmentReportTests(unittest.TestCase):
    def test_report_lists_modules_and_paths(self):
        report = diagnose.environment_report(Path("/dumps/romfs"), dicts=3,
                                             theme="Sombre")
        for expected in ("Python", "Tkinter", "zstandard", "texture2ddecoder",
                         "ASTC", "/dumps/romfs", "3", "Sombre"):
            self.assertIn(expected, report)

    def test_report_without_romfs(self):
        report = diagnose.environment_report()
        self.assertIn("non défini", report)

    def test_parse_u32_helper(self):
        self.assertEqual(diagnose.parse_u32(b"\x01\x00\x00\x00", 0), 1)
        self.assertIsNone(diagnose.parse_u32(b"\x01", 0))


class FileReportTests(unittest.TestCase):
    def test_bntx_report_shows_version_and_swizzle(self):
        report = diagnose.file_report(_orin_bntx(), "test.bntx")
        self.assertIn("BNTX", report)
        self.assertIn("0.4.1.0", report)
        self.assertIn("Orin", report)

    def test_bntx_report_forced_x1(self):
        report = diagnose.file_report(_orin_bntx(), "test.bntx", orin=False)
        self.assertIn("X1", report)

    def test_bntx_report_when_structure_unknown(self):
        blob = bytearray(_orin_bntx())
        blob[0x24:0x28] = (4096 + 5).to_bytes(4, "little")
        blob[0x28:0x30] = (0).to_bytes(8, "little")
        report = diagnose.file_report(bytes(blob), "casse.bntx")
        self.assertIn("BNTX", report)
        self.assertTrue("illisible" in report or "balayage" in report)

    def test_bfres_report(self):
        report = diagnose.file_report(synth.make_bfres(), "modele.bfres")
        self.assertIn("BFRES", report)
        self.assertIn("Version", report)

    def test_sarc_report(self):
        report = diagnose.file_report(synth.make_sarc([("a.bin", b"AAA")]), "pack.sarc")
        self.assertIn("SARC", report)

    def test_zstd_report_mentions_dictionary(self):
        blob = synth.compress(b"data", synth.train_dict([b"x %d" % i * 8 for i in range(16)], 256))
        report = diagnose.file_report(blob, "x.zs")
        self.assertIn("zstd", report)
        self.assertIn("dictionnaire", report)

    def test_unknown_format_shows_hexdump(self):
        report = diagnose.file_report(b"????" + b"\x11" * 80, "mystere.bin")
        self.assertIn("non reconnu", report)
        self.assertIn("0x0000", report)


class ThemeTests(unittest.TestCase):
    def test_palettes_are_distinct_and_readable(self):
        dark, light = theme.get_palette("sombre"), theme.get_palette("clair")
        self.assertTrue(dark.is_dark)
        self.assertFalse(light.is_dark)
        for palette in (dark, light):
            self.assertNotEqual(palette.bg, palette.fg)
            self.assertNotEqual(palette.surface, palette.fg)
            self.assertNotEqual(palette.accent, palette.accent_fg)

    def test_unknown_theme_falls_back_to_dark(self):
        self.assertEqual(theme.get_palette("fluo").name, "sombre")
        self.assertEqual(theme.get_palette(None).name, "sombre")
        self.assertEqual(theme.get_palette("CLAIR").name, "clair")

    def test_apply_theme_configures_expected_styles(self):
        calls = []

        class FakeStyle:
            def theme_use(self, name):
                calls.append(("theme_use", name))

            def configure(self, name, **kwargs):
                calls.append(("configure", name, tuple(sorted(kwargs))))

            def map(self, name, **kwargs):
                calls.append(("map", name, tuple(sorted(kwargs))))

        theme.apply_theme(FakeStyle(), theme.get_palette("clair"))
        configured = {call[1] for call in calls if call[0] == "configure"}
        for style in ("TButton", "Accent.TButton", "Tool.TButton", "Treeview",
                      "Treeview.Heading", "TNotebook", "TNotebook.Tab", "TEntry",
                      "TCombobox", "Status.TLabel", "Brand.TLabel", "Badge.TLabel",
                      "TProgressbar"):
            self.assertIn(style, configured)


class DoctorCliTests(unittest.TestCase):
    def test_doctor_runs_and_reports(self):
        from hookymod import cli
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("HookyMod", buffer.getvalue())

    def test_doctor_on_a_file_adds_its_report(self):
        from hookymod import cli
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "orin.bntx"
            path.write_bytes(_orin_bntx())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli.main(["doctor", str(path)])
            self.assertEqual(code, 0)
            self.assertIn("0.4.1.0", buffer.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
