"""Interopérabilité Zstandard des commandes Rust autonomes.

Les jeux d'essai sont synthétiques ; les octets du jeu ne sont jamais inclus.
La bibliothèque Python zstandard sert de référence indépendante pour les deux
sens, y compris les dictionnaires et les sommes de contrôle.
"""
from __future__ import annotations

import io
import json
import random
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import zstandard as zstd
except ImportError:  # Le reste de la suite peut fonctionner sans ce paquet.
    zstd = None

from hookymod import native, restbl, sarc
from tests.test_rust_cli import EXE


@unittest.skipUnless(EXE.is_file() and zstd is not None,
                     "exécutable Rust release ou module zstandard manquant")
class StandaloneZstdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(84)
        samples = [((f"ressource {i:04d} nom=fichier parametres ".encode()
                     + rng.randbytes(70) + b" couleurs textures geometrie ") * 3)
                   for i in range(500)]
        cls.samples = samples
        # Identifiants proches de ceux du pack du jeu, sans données du jeu.
        cls.dict1 = zstd.train_dictionary(4096, samples, dict_id=1)
        cls.dict4 = zstd.train_dictionary(4096, list(reversed(samples)), dict_id=4)
        cls.raw_dict = (b"contenu du dictionnaire brut " * 400) + rng.randbytes(4096)

    def setUp(self):
        # Construire les conteneurs de référence avec le Python historique.
        reference = patch.object(native, "available", return_value=False)
        reference.start()
        self.addCleanup(reference.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="hooky-rust-zs-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "entrée.zs"
        self.output = self.root / "résultat.bin"

    def run_cli(self, *args, ok=True):
        proc = subprocess.run([str(EXE), *map(str, args)], capture_output=True,
                              text=True, encoding="utf-8", timeout=60)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
        return proc

    def make_pack(self, compressed_entry=False):
        (self.root / "Pack").mkdir(exist_ok=True)
        second = self.dict4.as_bytes()
        second_name = "pack.zsdic"
        if compressed_entry:
            second = zstd.ZstdCompressor(dict_data=self.dict1, write_checksum=True).compress(second)
            second_name += ".zs"
        archive = sarc.SarcArchive([
            sarc.SarcEntry("zs.zsdic", self.dict1.as_bytes()),
            sarc.SarcEntry(second_name, second),
        ]).build()
        path = self.root / "Pack" / "ZsDic.pack.zs"
        path.write_bytes(zstd.ZstdCompressor(write_checksum=True).compress(archive))
        return path

    @staticmethod
    def py_decompress(frame):
        # Le compresseur Rust n'annonce pas nécessairement la taille de sortie.
        with zstd.ZstdDecompressor().stream_reader(io.BytesIO(frame),
                                                    read_across_frames=True) as reader:
            return reader.read()

    def test_basic_frames_and_checksum_rejection(self):
        data = b"Modele et ressources sans donnees du jeu\x00" * 9000
        for known_size in (True, False):
            for checksum in (True, False):
                with self.subTest(known_size=known_size, checksum=checksum):
                    frame = zstd.ZstdCompressor(level=7, write_content_size=known_size,
                                                write_checksum=checksum).compress(data)
                    self.input.write_bytes(frame)
                    self.run_cli("zs", "decompress", self.input, self.output)
                    self.assertEqual(self.output.read_bytes(), data)
                    self.output.unlink()
                    if checksum:
                        broken = bytearray(frame)
                        broken[-1] ^= 1
                        self.input.write_bytes(broken)
                        proc = self.run_cli("zs", "decompress", self.input, self.output, ok=False)
                        self.assertIn("contrôle", proc.stderr)
                        self.assertFalse(self.output.exists())

    def test_concatenated_frames_skippable_and_distinct_dictionaries(self):
        self.make_pack(compressed_entry=True)
        first = b"".join(self.samples[:70])
        second = b"".join(self.samples[80:160])
        frame1 = zstd.ZstdCompressor(dict_data=self.dict1, write_checksum=True).compress(first)
        frame2 = zstd.ZstdCompressor(dict_data=self.dict4, write_checksum=True).compress(second)
        skip = struct.pack("<II", 0x184D2A5A, 5) + b"notes"
        self.input.write_bytes(skip + frame1 + skip + frame2)
        self.run_cli("--romfs", self.root, "zs", "decompress", self.input, self.output)
        self.assertEqual(self.output.read_bytes(), first + second)
        self.output.unlink()
        # La seconde passe doit aussi charger une entrée compressée du pack.
        self.input.write_bytes(frame2)
        self.run_cli("--dict", self.root / "Pack" / "ZsDic.pack.zs",
                     "zs", "decompress", self.input, self.output)
        self.assertEqual(self.output.read_bytes(), second)

    def test_tagged_and_raw_dictionary_with_no_frame_id(self):
        data = self.raw_dict[1000:8000] + b"fichier de test " * 5000 + self.raw_dict[-3000:]
        raw = self.root / "inconnu.dict"
        raw.write_bytes(self.raw_dict)
        raw_dict = zstd.ZstdCompressionDict(self.raw_dict, dict_type=zstd.DICT_TYPE_RAWCONTENT)
        self.input.write_bytes(zstd.ZstdCompressor(dict_data=raw_dict, write_checksum=True)
                               .compress(data))
        self.run_cli("--dict", raw, "--force-dict", "0", "zs", "decompress",
                     self.input, self.output)
        self.assertEqual(self.output.read_bytes(), data)
        self.output.unlink()
        # Avec checksum, l'absence du dictionnaire est détectée plutôt que
        # d'écrire un résultat incorrect.
        self.run_cli("zs", "decompress", self.input, self.output, ok=False)
        self.assertFalse(self.output.exists())

        tagged = self.root / "trained.zsdic"
        tagged.write_bytes(self.dict1.as_bytes())
        content = b"".join(self.samples[:80])
        self.input.write_bytes(zstd.ZstdCompressor(dict_data=self.dict1, write_dict_id=False,
                                                  write_checksum=True).compress(content))
        self.run_cli("--dict", tagged, "--force-dict", "1", "zs", "decompress",
                     self.input, self.output)
        self.assertEqual(self.output.read_bytes(), content)
        self.output.unlink()
        self.run_cli("--dict", tagged, "--force-dict", "0", "zs", "decompress",
                     self.input, self.output, ok=False)
        self.assertFalse(self.output.exists())

    def test_missing_wrong_or_truncated_dictionary_is_rejected(self):
        content = b"".join(self.samples[:50])
        self.input.write_bytes(zstd.ZstdCompressor(dict_data=self.dict1).compress(content))
        proc = self.run_cli("zs", "decompress", self.input, self.output, ok=False)
        self.assertIn("id 1", proc.stderr)
        self.assertFalse(self.output.exists())
        wrong = self.root / "mauvais.zsdic"
        wrong.write_bytes(self.dict4.as_bytes())
        self.run_cli("--dict", wrong, "zs", "decompress", self.input, self.output, ok=False)
        self.assertFalse(self.output.exists())
        wrong.write_bytes(b"\x37\xa4\x30\xec\x01")
        self.run_cli("--dict", wrong, "zs", "decompress", self.input, self.output, ok=False)
        self.assertFalse(self.output.exists())

    def test_limits_truncation_and_existing_output(self):
        content = "limite vérifiée ".encode() * 2000
        for known_size in (True, False):
            with self.subTest(known_size=known_size):
                frame = zstd.ZstdCompressor(write_content_size=known_size,
                                            write_checksum=True).compress(content)
                self.input.write_bytes(frame)
                self.run_cli("--max-output", len(content) - 1, "zs", "decompress",
                             self.input, self.output, ok=False)
                self.assertFalse(self.output.exists())
                self.run_cli("--max-output", len(content), "zs", "decompress",
                             self.input, self.output)
                self.assertEqual(self.output.read_bytes(), content)
                self.output.unlink()
                for broken in (frame[:-1], frame + b"erreur", frame[:7]):
                    self.input.write_bytes(broken)
                    self.run_cli("zs", "decompress", self.input, self.output, ok=False)
                    self.assertFalse(self.output.exists())
        # Fenêtre trop grande (> 128 Mio) : rejet avant allocation du tampon.
        self.input.write_bytes(b"\x28\xb5\x2f\xfd\x00\xff\x01\x00\x00")
        self.run_cli("zs", "decompress", self.input, self.output, ok=False)
        self.assertFalse(self.output.exists())
        self.input.write_bytes(zstd.ZstdCompressor().compress(content))
        self.output.write_bytes(b"fichier existant")
        self.run_cli("zs", "decompress", self.input, self.output, ok=False)
        self.assertEqual(self.output.read_bytes(), b"fichier existant")
        other = self.root / "pas-une-sortie.ZS"
        self.run_cli("zs", "decompress", self.input, other, ok=False)
        self.assertFalse(other.exists())

    def test_block_growth_and_reserved_bits_are_rejected(self):
        def frame(body):
            # Fenêtre 1 Kio, taille inconnue, un bloc compressé final.
            return bytes.fromhex("28b52ffd0000") + ((len(body) << 3) | 5).to_bytes(3, "little") + body
        def literals(size, sequences):
            return ((size << 4) | 13).to_bytes(3, "little") + b"x" + sequences
        self.input.write_bytes(frame(literals(1024, b"\x00")))
        self.run_cli("zs", "decompress", self.input, self.output)
        self.assertEqual(self.output.read_bytes(), b"x" * 1024)
        self.output.unlink()
        invalid = (frame(literals(1025, b"\x00")),
                   frame(literals(1048575, b"\x00")),
                   frame(literals(0, b"\xff\xff\xff\x00")),
                   frame(bytes(1025)))
        for item in invalid:
            with self.subTest(length=len(item)):
                self.input.write_bytes(item)
                result = self.run_cli("zs", "decompress", self.input, self.output, ok=False)
                self.assertIn("DecompressedSizeTooLarge", result.stderr)
                self.assertFalse(self.output.exists())
                with self.assertRaises(zstd.ZstdError):
                    self.py_decompress(item)
        # Trame synthétique de bytes(range(41)) répétés, avec checksum ;
        # l'octet 53 a été modifié pour activer un bit réservé de séquence.
        bad = bytes.fromhex(
            "28b52ffd04209501009402000102030405060708090a0b0c0d0e0f1011121314"
            "15161718191a1b1c1d1e1f2021222324252627280102d9388c5765b5e41ad3")
        self.input.write_bytes(bad)
        self.run_cli("zs", "decompress", self.input, self.output, ok=False)
        self.assertFalse(self.output.exists())
        with self.assertRaises(zstd.ZstdError):
            self.py_decompress(bad)

    def test_rust_compression_is_explicit_and_python_can_decode(self):
        rng = random.Random(833)
        for size in (0, 1, 10, 131071, 131072, 131073, 300000):
            content = rng.randbytes(size) if size % 2 else (b"HookyModRust" * (size // 12 + 1))[:size]
            with self.subTest(size=size):
                self.input.write_bytes(content)
                self.run_cli("zs", "compress", self.input, self.output, ok=False)
                self.assertFalse(self.output.exists())
                result = self.run_cli("zs", "compress", self.input, self.output, "--no-dict")
                self.assertIn("sans dictionnaire", result.stderr)
                frame = self.output.read_bytes()
                self.assertEqual(zstd.get_frame_parameters(frame).dict_id, 0)
                self.assertEqual(self.py_decompress(frame), content)
                self.output.unlink()

    def test_sarc_and_restbl_accept_zs_but_write_only_raw(self):
        self.make_pack()
        archive = sarc.SarcArchive([sarc.SarcEntry("data/test.txt", b"ancien")])
        raw = archive.build()
        original = zstd.ZstdCompressor(dict_data=self.dict4, write_checksum=True).compress(raw)
        self.input.write_bytes(original)
        info = self.run_cli("--romfs", self.root, "sarc", "list", self.input, "--json")
        self.assertEqual(json.loads(info.stdout)["entries"][0]["name"], "data/test.txt")
        replacement = self.root / "replacement"
        replacement.write_bytes(b"nouveau contenu")
        self.run_cli("--romfs", self.root, "sarc", "replace", self.input, "data/test.txt",
                     replacement, self.output)
        expected = sarc.SarcArchive.parse(raw)
        expected.replace(0, replacement.read_bytes())
        self.assertEqual(self.output.read_bytes(), expected.build())
        self.assertEqual(self.input.read_bytes(), original)
        self.output.unlink()
        bad_output = self.root / "mod.pack.zs"
        self.run_cli("--romfs", self.root, "sarc", "replace", self.input, "data/test.txt",
                     replacement, bad_output, ok=False)
        self.assertFalse(bad_output.exists())

        table = restbl.ResourceSizeTable(version=1, name_length=64, sizes={1: 100},
                                         tail=b"named\x00", named_count=1)
        self.input.write_bytes(zstd.ZstdCompressor(write_checksum=True).compress(table.build()))
        self.run_cli("restbl", "ensure", self.input, "Model/new.bfres.zs", "200", self.output)
        table.ensure("Model/new.bfres.zs", 200)
        self.assertEqual(self.output.read_bytes(), table.build())
        self.output.unlink()
        self.run_cli("restbl", "ensure", self.input, "Model/new.bfres.zs", "200",
                     bad_output, ok=False)
        self.assertFalse(bad_output.exists())


if __name__ == "__main__":
    unittest.main()
