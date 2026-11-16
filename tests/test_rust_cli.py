"""Parité des commandes Rust autonomes sur des conteneurs synthétiques.

La suite est exécutée si l'exécutable release a été compilé. Aucune donnée du
jeu ni bibliothèque Python native n'est nécessaire pour lancer l'exécutable.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hookymod import native, restbl, sarc

ROOT = Path(__file__).resolve().parent.parent
TARGET = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "rust" / "target"))
if not TARGET.is_absolute():
    TARGET = ROOT / TARGET
EXE = TARGET / "release" / ("hookymod-rs.exe" if sys.platform == "win32" else "hookymod-rs")


@unittest.skipUnless(EXE.is_file(), "exécutable Rust release non compilé")
class StandaloneRustTests(unittest.TestCase):
    def setUp(self):
        # La référence doit rester indépendante de l'exécutable Rust, même si
        # la bibliothèque native de l'application est aussi compilée.
        reference = patch.object(native, "available", return_value=False)
        reference.start()
        self.addCleanup(reference.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="hooky-rust-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive_path = self.root / "archive origine.sarc"
        self.replacement = self.root / "nouveau.bin"
        self.output = self.root / "résultat.sarc"

    def run_cli(self, *args, ok=True):
        result = subprocess.run([str(EXE), *map(str, args)], capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def write_archive(self, endian="<", multiplier=0x65):
        entries = [sarc.SarcEntry("dossier/été.txt", b"du texte", alignment=4),
                   sarc.SarcEntry("Tex/texture.bntx", b"BNTX\x00\x01", alignment=0x2000),
                   sarc.SarcEntry("guillemet\"\t.txt", b"\x00\xff", alignment=3)]
        archive = sarc.SarcArchive(entries, endian=endian, hash_mult=multiplier, version=0x100)
        self.archive_path.write_bytes(archive.build())
        return sarc.SarcArchive.parse(self.archive_path.read_bytes())

    def test_sarc_list_extract_and_replace_match_python(self):
        for endian in ("<", ">"):
            with self.subTest(endian=endian):
                reference = self.write_archive(endian)
                result = self.run_cli("sarc", "list", self.archive_path, "--json")
                actual = json.loads(result.stdout)
                expected = [{"name": entry.name, "size": len(entry.data),
                             "alignment": entry.alignment, "hash": entry.hash}
                            for entry in reference]
                self.assertEqual(actual["entries"], expected)
                self.assertEqual(actual["warnings"], reference.warnings)
                self.assertEqual(actual["endian"], "little" if endian == "<" else "big")
                self.assertEqual(actual["hash_mult"], reference.hash_mult)
                self.assertEqual(actual["version"], reference.version)

                target = self.root / "extrait.bin"
                self.run_cli("sarc", "extract", self.archive_path, "dossier/été.txt", target)
                self.assertEqual(target.read_bytes(), b"du texte")
                target.unlink()
                self.replacement.write_bytes(b"FRESnouveau")
                self.run_cli("sarc", "replace", self.archive_path, "dossier/été.txt",
                             self.replacement, self.output)
                idx = reference.index_of(reference.get("dossier/été.txt"))
                reference.replace(idx, self.replacement.read_bytes())
                self.assertEqual(self.output.read_bytes(), reference.build())
                self.output.unlink()

    def test_sarc_add_remove_and_collision_order_match_python(self):
        reference = self.write_archive(multiplier=0)
        self.replacement.write_bytes(b"BNTXtest")
        self.run_cli("sarc", "add", self.archive_path, "ajout.bntx", self.replacement, self.output)
        reference.add("ajout.bntx", self.replacement.read_bytes())
        self.assertEqual(self.output.read_bytes(), reference.build())
        second = self.root / "sans.sarc"
        self.run_cli("sarc", "remove", self.output, "dossier/été.txt", second)
        reference = sarc.SarcArchive.parse(self.output.read_bytes())
        reference.remove(reference.index_of(reference.get("dossier/été.txt")))
        self.assertEqual(second.read_bytes(), reference.build())

    def test_warnings_and_renamed_nodes_match_python(self):
        reference = self.write_archive()
        raw = bytearray(self.archive_path.read_bytes())
        endian = reference.endian
        # premier nœud : nom hors du fichier ; second : bornes inversées
        import struct
        struct.pack_into(endian + "I", raw, 0x20 + 4, 0x01ffffff)
        struct.pack_into(endian + "II", raw, 0x20 + 16 + 8, 10, 2)
        self.archive_path.write_bytes(raw)
        parsed = sarc.SarcArchive.parse(raw)
        result = self.run_cli("sarc", "list", self.archive_path, "--json")
        actual = json.loads(result.stdout)
        self.assertEqual(actual["warnings"], parsed.warnings)
        self.assertEqual([entry["name"] for entry in actual["entries"]], parsed.names())

    def test_sarc_randomized_reconstruction_and_malformed_headers(self):
        rng = random.Random(482)
        for seed in range(55):
            count = rng.randrange(1, 15)
            entries = [sarc.SarcEntry(f"{i:02}_{rng.choice(['a', 'é', '😀', 'x.bntx'])}",
                                      rng.randbytes(rng.randrange(70)),
                                      alignment=rng.choice([1, 3, 4, 8, 32, 8192]))
                       for i in range(count)]
            archive = sarc.SarcArchive(entries, endian=rng.choice(["<", ">"]),
                                       hash_mult=rng.choice([0, 1, 101, 255, 0xffffffff]),
                                       version=rng.randrange(65536))
            self.archive_path.write_bytes(archive.build())
            reference = sarc.SarcArchive.parse(self.archive_path.read_bytes())
            index = rng.randrange(count)
            name = reference.entries[index].name
            new = rng.randbytes(rng.randrange(70))
            self.replacement.write_bytes(new)
            with self.subTest(seed=seed):
                self.run_cli("sarc", "replace", self.archive_path, name, self.replacement, self.output)
                reference.replace(index, new)
                self.assertEqual(self.output.read_bytes(), reference.build())
            self.output.unlink()
        for data in (b"", b"SARC", b"nope" * 20, b"SARC" + bytes(40),
                     b"\x28\xb5\x2f\xfd" + bytes(20)):
            self.archive_path.write_bytes(data)
            with self.subTest(data=data[:8]):
                self.run_cli("sarc", "list", self.archive_path, ok=False)

    def test_never_overwrites_existing_file_or_original(self):
        reference = self.write_archive()
        original = self.archive_path.read_bytes()
        self.replacement.write_bytes(b"remplacement")
        self.output.write_bytes(b"ne pas modifier")
        result = self.run_cli("sarc", "replace", self.archive_path, reference.entries[0].name,
                              self.replacement, self.output, ok=False)
        self.assertIn("existant", result.stderr)
        self.assertEqual(self.output.read_bytes(), b"ne pas modifier")
        self.run_cli("sarc", "replace", self.archive_path, reference.entries[0].name,
                     self.replacement, self.archive_path, ok=False)
        self.assertEqual(self.archive_path.read_bytes(), original)
        self.run_cli("sarc", "extract", self.archive_path, "absent", self.root / "out", ok=False)
        self.assertFalse((self.root / "out").exists())

    def test_restbl_crc_lookup_and_updates_match_python(self):
        rng = random.Random(600)
        for seed in range(50):
            sizes = {rng.randrange(2 ** 32): rng.randrange(2 ** 32) for _ in range(12)}
            reference = restbl.ResourceSizeTable(version=3, name_length=64, sizes=sizes,
                                                  tail=b"raw named entries\x00", named_count=1)
            self.archive_path.write_bytes(reference.build())
            name = f"Model/été😀{seed}.bfres.ZS.zs"
            size = rng.randrange(1_000_000)
            with self.subTest(seed=seed):
                info = json.loads(self.run_cli("restbl", "info", self.archive_path, "--json").stdout)
                self.assertEqual(info["tail_bytes"], len(reference.tail))
                self.assertEqual(info["hashed"], len(reference))
                self.run_cli("restbl", "ensure", self.archive_path, name, size, self.output)
                reference.ensure(name, size)
                self.assertEqual(self.output.read_bytes(), reference.build())
                lookup = self.run_cli("restbl", "get", self.output, name[:-6])
                self.assertEqual(int(lookup.stdout.strip()), reference.get(name))
            self.output.unlink()
        self.run_cli("restbl", "ensure", self.archive_path, "overflow", str(2 ** 32 - 1),
                     self.output, ok=False)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
