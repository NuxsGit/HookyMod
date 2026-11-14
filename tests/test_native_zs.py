"""Décompression zstd Rust utilisée par le projet Python, avec repli C.

Les données et dictionnaires sont synthétiques. Python zstandard sert de
référence indépendante ; le chargeur Rust reste facultatif.
"""
from __future__ import annotations

import random
import struct
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover - dépend de l'environnement
    zstd = None

from hookymod import native, zs


@unittest.skipUnless(zstd is not None, "module zstandard absent")
class ZstdFallbackTests(unittest.TestCase):
    def test_backend_diagnostic_distinguishes_missing_and_fallback(self):
        with patch.object(native, "available", return_value=True):
            self.assertEqual(zs.decoder_backend()["name"], "rust")
        with patch.object(native, "available", return_value=False):
            self.assertEqual(zs.decoder_backend()["name"], "zstandard")
        with patch.object(zs, "zstd", None):
            self.assertEqual(zs.decoder_backend()["name"], "missing")

    def test_python_fallback_when_rust_is_unavailable_or_declines(self):
        content = b"repli existant" * 1000
        frame = zstd.ZstdCompressor(write_checksum=True).compress(content)
        dicts = zs.ZstdDicts()
        with patch.object(native, "available", return_value=False), \
             patch.object(native, "zstd_decompress", side_effect=AssertionError("appel inattendu")):
            self.assertEqual(dicts.decompress(frame), content)
        with patch.object(native, "available", return_value=True), \
             patch.object(native, "zstd_decompress", return_value=None) as declined:
            self.assertEqual(dicts.decompress(frame), content)
            self.assertTrue(declined.called)
        self.assertEqual(native.zstd_decompress(b""), None)


@unittest.skipUnless(zstd is not None and native.available(),
                     "moteur Rust compilé ou module zstandard absent")
class RustZstdBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(912)
        cls.samples = [(f"modele {i:04d} fichier parametres ".encode()
                        + rng.randbytes(80) + b" geometrie texture couleurs ") * 4
                       for i in range(500)]
        cls.trained = zstd.train_dictionary(4096, cls.samples, dict_id=17)
        cls.raw_dictionary = (b"dictionnaire contenu brut et references " * 500
                              + rng.randbytes(500))
        cls.raw_object = zstd.ZstdCompressionDict(cls.raw_dictionary,
                                                  dict_type=zstd.DICT_TYPE_RAWCONTENT)

    def compare_app(self, dictionary, frame, wanted, explicit_id=None):
        with patch.object(native, "available", return_value=False):
            expected = dictionary.decompress(frame, explicit_id)
        self.assertEqual(expected, wanted)
        with patch.object(native, "zstd_decompress", wraps=native.zstd_decompress) as called:
            actual = dictionary.decompress(frame, explicit_id)
            self.assertTrue(called.called)
            args, kwargs = called.call_args
            self.assertEqual(native.zstd_decompress(*args, **kwargs), wanted)
        self.assertEqual(actual, expected)

    def test_frames_without_dictionary_and_both_size_modes(self):
        dictionary = zs.ZstdDicts()
        for size in (0, 1, 17, 65, 131072, 131073):
            content = (b"Couleurs Rust\x00" * (size // 14 + 1))[:size]
            for known in (True, False):
                for checksum in (True, False):
                    frame = zstd.ZstdCompressor(write_content_size=known,
                                                write_checksum=checksum).compress(content)
                    with self.subTest(size=size, known=known, checksum=checksum):
                        self.compare_app(dictionary, frame, content)

    def test_trained_dictionary_implicit_id_and_concatenated_frames(self):
        dictionary = zs.ZstdDicts()
        self.assertTrue(dictionary.add("trained.zsdic", self.trained.as_bytes()))
        first, second = b"".join(self.samples[:40]), b"".join(self.samples[40:80])
        named = zstd.ZstdCompressor(dict_data=self.trained, write_checksum=True).compress(first)
        unnamed = zstd.ZstdCompressor(dict_data=self.trained, write_dict_id=False,
                                     write_checksum=True).compress(second)
        self.assertEqual(zstd.get_frame_parameters(named).dict_id, 17)
        self.assertEqual(zstd.get_frame_parameters(unnamed).dict_id, 0)
        self.compare_app(dictionary, named, first)
        self.compare_app(dictionary, unnamed, second, explicit_id=17)
        skip = struct.pack("<II", 0x184D2A5A, 5) + b"notes"
        self.compare_app(dictionary, named + skip + unnamed, first + second)

    def test_raw_dictionary_without_id_and_rejecting_false_alias(self):
        dictionary = zs.ZstdDicts()
        self.assertTrue(dictionary.add("zs.zsdic", self.raw_dictionary))
        content = self.raw_dictionary[400:8000] * 2
        frame = zstd.ZstdCompressor(dict_data=self.raw_object, write_checksum=True).compress(content)
        self.assertEqual(zstd.get_frame_parameters(frame).dict_id, 0)
        self.compare_app(dictionary, frame, content, explicit_id=1)
        self.assertEqual(native.zstd_decompress(frame, self.raw_dictionary, 0,
                                                 force_dictionary=True), content)

        # Une frame qui annonce un id non nul n'est pas équivalente pour le
        # décodeur zstandard C à un dictionnaire brut dont l'id réel vaut 0.
        descriptor = frame[4]
        self.assertEqual(descriptor & 3, 0)
        position = 5 + (0 if descriptor & 0x20 else 1)
        alias_frame = (frame[:4] + bytes((descriptor | 1,)) + frame[5:position]
                       + b"\x01" + frame[position:])
        self.assertEqual(zstd.get_frame_parameters(alias_frame).dict_id, 1)
        with patch.object(native, "available", return_value=False):
            with self.assertRaises(zs.FormatError):
                dictionary.decompress(alias_frame)
        with patch.object(native, "zstd_decompress", wraps=native.zstd_decompress) as called:
            with self.assertRaises(zs.FormatError):
                dictionary.decompress(alias_frame)
            called.assert_not_called()

    def test_raw_dictionary_alias_in_a_later_concatenated_frame_falls_back(self):
        dictionary = zs.ZstdDicts()
        self.assertTrue(dictionary.add("zs.zsdic", self.raw_dictionary))
        content = self.raw_dictionary[400:8000] * 2
        frame = zstd.ZstdCompressor(dict_data=self.raw_object, write_checksum=True).compress(content)
        descriptor = frame[4]
        self.assertEqual(descriptor & 3, 0)
        position = 5 + (0 if descriptor & 0x20 else 1)
        alias = (frame[:4] + bytes((descriptor | 1,)) + frame[5:position]
                 + b"\x01" + frame[position:])
        skip = struct.pack("<II", 0x184D2A5A, 5) + b"notes"
        combined = frame + skip + alias
        # La première trame n'annonce pas d'id, mais la seconde en annonce un
        # alors que le dictionnaire zstandard réellement fourni est brut (id 0).
        # Le natif ne doit pas accepter cet alias à travers la concaténation.
        with patch.object(native, "available", return_value=False):
            with self.assertRaises(zs.FormatError):
                dictionary.decompress(combined, 1)
        self.assertIsNone(native.zstd_decompress(combined, self.raw_dictionary, 0,
                                                 force_dictionary=True))
        with patch.object(native, "zstd_decompress", wraps=native.zstd_decompress) as called:
            with self.assertRaises(zs.FormatError):
                dictionary.decompress(combined, 1)
            self.assertTrue(called.called)

    def test_reserved_sequence_bits_are_rejected_even_with_a_valid_checksum(self):
        # Trame synthétique valide dont le byte 53 décrit les modes de
        # séquence. Les deux bits de poids faible sont réservés par RFC 8878.
        frame = bytes.fromhex(
            "28b52ffd042095010094026ff138a0d2d22d5112d23cd1d29e3df99b826c882f"
            "b692a19bfd7af3f55aca3bed96317362769da19b0100d9388c5765c9063152")
        self.assertTrue(zstd.get_frame_parameters(frame).has_checksum)
        expected = zs.ZstdDicts().decompress(frame)
        self.assertEqual(len(expected), 10055)
        self.assertEqual(frame[53], 0)
        for reserved in (1, 2, 3):
            broken = bytearray(frame)
            broken[53] |= reserved
            with self.subTest(reserved=reserved):
                with patch.object(native, "available", return_value=False):
                    with self.assertRaises(zs.FormatError):
                        zs.ZstdDicts().decompress(bytes(broken))
                self.assertIsNone(native.zstd_decompress(bytes(broken)))
                with patch.object(native, "zstd_decompress", wraps=native.zstd_decompress) as called:
                    with self.assertRaises(zs.FormatError):
                        zs.ZstdDicts().decompress(bytes(broken))
                    self.assertTrue(called.called)

    def test_corruption_limits_and_input_validation_do_not_leak_results(self):
        content = b"donnees de test" * 1000
        frame = zstd.ZstdCompressor(write_checksum=True).compress(content)
        corrupt = bytearray(frame)
        corrupt[-1] ^= 1
        self.assertIsNone(native.zstd_decompress(bytes(corrupt)))
        self.assertIsNone(native.zstd_decompress(frame[:-1]))
        self.assertIsNone(native.zstd_decompress(frame, max_output=len(content) - 1))
        self.assertEqual(native.zstd_decompress(frame, max_output=len(content)), content)
        self.assertIsNone(native.zstd_decompress(frame, b"", force_dictionary=True))
        self.assertIsNone(native.zstd_decompress(frame, dictionary_id=-1))
        self.assertIsNone(native.zstd_decompress(frame, max_output=-1))
        self.assertIsNone(native.zstd_decompress(frame, max_output=2 ** 40))
        self.assertIsNone(native.zstd_decompress("pas des octets"))
        with patch.object(native, "_ZSTD_DICTIONARY_LIMIT", 4):
            self.assertIsNone(native.zstd_decompress(frame, b"trop long"))
        with self.assertRaises(zs.FormatError):
            zs.ZstdDicts().decompress(bytes(corrupt))
        # Une limite propre au moteur Rust ne limite pas le repli historique.
        with patch.object(native, "zstd_decompress", return_value=None):
            self.assertEqual(zs.ZstdDicts().decompress(frame), content)

    def test_calls_are_independent_in_parallel(self):
        contents = [b"".join(self.samples[i:i + 30]) for i in range(60)]
        frames = [zstd.ZstdCompressor(dict_data=self.trained, write_checksum=True).compress(data)
                  for data in contents]
        def run(index):
            return native.zstd_decompress(frames[index], self.trained.as_bytes(), 17,
                                          force_dictionary=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(run, range(len(frames)))), contents)
        # Les entrées mutables sont copiées avant la libération du GIL.
        self.assertEqual(native.zstd_decompress(bytearray(frames[0]),
                                                 bytearray(self.trained.as_bytes()), 17,
                                                 force_dictionary=True), contents[0])


if __name__ == "__main__":
    unittest.main()
