"""Tests de l'outil de couverture (aucun asset du jeu requis)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import synth

from tools import coverage


class FindModelsTests(unittest.TestCase):
    def test_collects_plain_and_compressed_recurse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sous").mkdir()
            (root / "a.bfres").write_bytes(b"x")
            (root / "sous" / "b.bfres.zs").write_bytes(b"x")
            (root / "note.txt").write_text("pas un modèle")
            found = coverage.find_models([root])
            self.assertEqual([p.name for p in found], ["a.bfres", "b.bfres.zs"])

    def test_same_file_twice_is_listed_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.bfres"
            path.write_bytes(b"x")
            found = coverage.find_models([Path(tmp), path])
            self.assertEqual(len(found), 1)


class InspectTests(unittest.TestCase):
    def test_synthetic_model_is_translated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Modele.bfres"
            path.write_bytes(synth.make_bfres())
            row = coverage.inspect(path, None)
            self.assertEqual(row["status"], "ok", row["reason"])
            self.assertGreaterEqual(row["faces"], 1)
            self.assertTrue(row["version"].startswith("10."))

    def test_compressed_model_is_decompressed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Modele.bfres.zs"
            path.write_bytes(synth.compress(synth.make_bfres()))
            row = coverage.inspect(path, None)
            self.assertEqual(row["status"], "ok", row["reason"])
            self.assertGreaterEqual(row["faces"], 1)

    def test_animation_is_not_reported_as_a_model(self) -> None:
        # BFRES d'animation : en-tête valide, squelette/animations, aucun maillage
        blob = bytearray(0x200)
        blob[0:4] = b"FRES"
        blob[8:12] = (0x000A0400).to_bytes(4, "little")
        blob[0x100:0x104] = b"FSKA"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Modele.anim.bfres"
            path.write_bytes(bytes(blob))
            row = coverage.inspect(path, None)
            self.assertEqual(row["status"], "animation")
            self.assertEqual(row["meshes"], 0)
            self.assertIn("animation", row["reason"])

    def test_garbage_file_says_why(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "casse.bfres"
            path.write_bytes(b"NOPE" + bytes(64))
            row = coverage.inspect(path, None)
            self.assertEqual(row["status"], "error")
            self.assertIn("BFRES", row["reason"])

    def test_truncated_header_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "court.bfres"
            path.write_bytes(synth.make_bfres()[:32])
            row = coverage.inspect(path, None)      # HookyError → refuse, jamais d'exception
            self.assertIn(row["status"], {"refuse", "error"})


class SweepTests(unittest.TestCase):
    def test_sweep_writes_png_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "modeles"
            root.mkdir()
            (root / "Modele.bfres").write_bytes(synth.make_bfres())
            (root / "casse.bfres").write_bytes(b"NOPE" + bytes(64))
            out = Path(tmp) / "rapport"
            rows = coverage.sweep([root], out, (160, 120))
            self.assertEqual(len(rows), 2)
            self.assertTrue((out / "coverage.md").is_file())
            payload = json.loads((out / "coverage.json").read_text())
            self.assertEqual(len(payload), 2)
            previews = list(out.glob("*.png"))
            self.assertEqual(len(previews), 1, "seul le modèle traduit a un aperçu")

    def test_markdown_counts_statuses(self) -> None:
        rows = [{"file": "a", "status": "ok", "meshes": 1, "faces": 2,
                 "vertices": 3, "bounds": [1, 1, 1], "warnings": [], "reason": ""},
                {"file": "b", "status": "refuse", "meshes": 0, "faces": 0,
                 "vertices": 0, "bounds": None, "warnings": [],
                 "reason": "aucune face"}]
        text = coverage.markdown(rows)
        self.assertIn("**1 traduit(s)**", text)
        self.assertIn("aucune face", text)

    def test_cli_returns_1_when_nothing_translates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "casse.bfres"
            path.write_bytes(b"NOPE" + bytes(64))
            self.assertEqual(coverage.main([str(path)]), 1)

    def test_cli_returns_2_on_empty_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(coverage.main([tmp]), 2)


class ZsDicTests(unittest.TestCase):
    def test_missing_dictionary_is_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(coverage._dictionaries(Path(tmp)))

    def test_empty_dictionary_file_is_ignored(self) -> None:
        # un fichier vide ne fournit aucun dictionnaire : autant dire « pas de
        # dictionnaires » plutôt que de faire échouer la décompression plus loin
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ZsDic.pack.zs").write_bytes(b"")
            self.assertIsNone(coverage._dictionaries(Path(tmp)))


if __name__ == "__main__":                       # pragma: no cover
    unittest.main()
