"""Parité du pont SARC Rust et de l'implémentation Python indépendante.

Aucun fichier du jeu n'est nécessaire. Les tests du pont sont ignorés si la
bibliothèque n'a pas été compilée ; ceux des replis fonctionnent dans les deux
configurations.
"""
from __future__ import annotations

import random
import struct
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from hookymod import native, sarc, zs
from hookymod.container import Document, Level
from hookymod.util import FormatError


def python_parse(data):
    with patch.object(native, "available", return_value=False):
        return sarc.SarcArchive.parse(data)


def python_build(archive, verify=True):
    with patch.object(native, "available", return_value=False):
        return archive.build(verify=verify)


def outcome(fn):
    try:
        return ("ok", fn())
    except Exception as exc:  # comparer aussi les erreurs et messages publics
        return ("error", type(exc), str(exc))


class SarcFallbackTests(unittest.TestCase):
    def test_disabled_and_declined_operations_keep_python_behaviour(self):
        original = sarc.SarcArchive([sarc.SarcEntry("fichier.txt", b"\0ancien\xff")])
        expected = python_build(original)
        reference = python_parse(expected)
        with patch.object(native, "available", return_value=False), \
             patch.object(native, "sarc_parse", side_effect=AssertionError("appel inattendu")), \
             patch.object(native, "sarc_build", side_effect=AssertionError("appel inattendu")):
            self.assertEqual(original.build(), expected)
            self.assertEqual(sarc.SarcArchive.parse(expected), reference)
        with patch.object(native, "available", return_value=True), \
             patch.object(native, "sarc_parse", return_value=None) as read, \
             patch.object(native, "sarc_build", return_value=None) as write:
            self.assertEqual(original.build(), expected)
            self.assertEqual(sarc.SarcArchive.parse(expected), reference)
            self.assertTrue(read.called)
            self.assertTrue(write.called)
            with self.assertRaisesRegex(FormatError, "magic"):
                sarc.SarcArchive.parse(b"invalide")

    def test_mutable_buffers_and_archive_subclasses_keep_historical_types(self):
        class SpecialArchive(sarc.SarcArchive):
            @staticmethod
            def _guess_alignment(blob, name, abs_offset):
                return 123

        reference = sarc.SarcArchive([sarc.SarcEntry("fichier.txt", b"contenu")])
        raw = python_build(reference)
        with patch.object(native, "available", return_value=True), \
             patch.object(native, "sarc_parse", side_effect=AssertionError("appel inattendu")), \
             patch.object(native, "sarc_build", side_effect=AssertionError("appel inattendu")):
            mutable = sarc.SarcArchive.parse(bytearray(raw))
            self.assertIs(type(mutable.entries[0].data), bytearray)
            subclass = SpecialArchive.parse(raw)
            self.assertIsInstance(subclass, SpecialArchive)
            self.assertEqual(subclass.entries[0].alignment, 123)
            self.assertEqual(subclass.build(verify=False), python_build(subclass, verify=False))

    def test_corrupt_native_metadata_is_declined_before_using_offsets(self):
        fake = SimpleNamespace(hookymod_core_sarc_parse=object())
        header = lambda count, warnings=0: struct.pack("<4sBHIII", b"HSP1", 0, 256, 101,
                                                       count, warnings)
        entry = lambda n, start, size: struct.pack("<IIIQQ", n, 4, 1, start, size)
        bad = [b"", header(1), header(1) + entry(1, 1000, 1) + b"x",
               header(1) + entry(1, 0, 1) + b"\xff", header(0, 1),
               header(0) + b"donnees supplementaires", b"BAD!" + header(0)[4:]]
        with patch.object(native, "_lib", fake):
            for metadata in bad:
                with self.subTest(metadata=metadata[:30]), \
                     patch.object(native, "_owned_result", return_value=metadata):
                    self.assertIsNone(native.sarc_parse(b"SARC"))


@unittest.skipUnless(native.available(), "moteur Rust non compilé")
class RustSarcBridgeTests(unittest.TestCase):
    def test_randomized_build_and_parse_use_the_native_paths(self):
        rng = random.Random(4805)
        for seed in range(140):
            count = rng.randint(1, 16)
            entries = [sarc.SarcEntry(
                f"{i:02}_{rng.choice(['fichier.txt', 'été.dat', '😀.bntx', 'a.bin', 'Σ.BNCH'])}",
                rng.randbytes(rng.randrange(80)), alignment=rng.choice([0, 1, 3, 4, 8, 32, 8192]))
                       for i in range(count)]
            archive = sarc.SarcArchive(entries, endian=rng.choice(["<", ">", "invalide"]),
                                       hash_mult=rng.choice([0, 1, 101, 255, 0xffffffff]),
                                       version=rng.randrange(65536))
            with self.subTest(seed=seed):
                expected = python_build(archive)
                direct = native.sarc_build(entries, archive.endian, archive.hash_mult,
                                           archive.version, verify=True)
                self.assertEqual(direct, expected)  # pas de repli invisible
                with patch.object(native, "sarc_build", wraps=native.sarc_build) as called:
                    self.assertEqual(archive.build(), expected)
                    called.assert_called_once()
                metadata = native.sarc_parse(expected)
                self.assertIsNotNone(metadata)
                with patch.object(native, "sarc_parse", wraps=native.sarc_parse) as called:
                    parsed = sarc.SarcArchive.parse(expected)
                    called.assert_called_once()
                self.assertEqual(parsed, python_parse(expected))

    def test_collision_order_duplicate_names_and_unverifiable_names(self):
        entries = [sarc.SarcEntry("bz", b"premier"),
                   sarc.SarcEntry("az", b"\x00second"),
                   sarc.SarcEntry("az", b"troisieme")]
        archive = sarc.SarcArchive(entries, hash_mult=0, endian=">")
        expected = python_build(archive)
        self.assertEqual(native.sarc_build(entries, ">", 0, 256), expected)
        parsed = sarc.SarcArchive.parse(expected)
        self.assertEqual([(e.name, e.data) for e in parsed],
                         [("az", b"\x00second"), ("az", b"troisieme"), ("bz", b"premier")])
        # La construction non vérifiée accepte les noms que la relecture ne
        # peut pas préserver ; la version vérifiée doit toujours lever l'erreur
        # historique plutôt que fournir une archive modifiée silencieusement.
        for name in ("", "a\\b", "avant\x00apres"):
            with self.subTest(name=repr(name)):
                candidate = sarc.SarcArchive([sarc.SarcEntry(name, b"contenu")])
                self.assertEqual(native.sarc_build(candidate.entries, "<", 101, 256, verify=False),
                                 python_build(candidate, verify=False))
                self.assertIsNone(native.sarc_build(candidate.entries, "<", 101, 256, verify=True))
                self.assertEqual(outcome(lambda: candidate.build()),
                                 outcome(lambda: python_build(candidate)))

    def test_warnings_zero_hash_invalid_utf8_and_truncated_names(self):
        original = sarc.SarcArchive([sarc.SarcEntry("alpha.txt", b"un"),
                                     sarc.SarcEntry("beta.bin", b"deux"),
                                     sarc.SarcEntry("gamma.bin", b"trois")])
        raw = bytearray(python_build(original))
        # Nœud 0 : nom hors fichier ; nœud 1 : bornes inversées ; nœud 2 :
        # hash nul et début d'un nom UTF-8 invalide. Les avertissements, l'ordre
        # et le recalcul du hash doivent rester exactement ceux de Python.
        struct.pack_into("<I", raw, 0x20 + 4, 0x01ffffff)
        struct.pack_into("<II", raw, 0x20 + 16 + 8, 9, 2)
        struct.pack_into("<I", raw, 0x20 + 32, 0)
        names_off = 0x20 + 16 * 3 + 8
        attrs = struct.unpack_from("<I", raw, 0x20 + 32 + 4)[0]
        name_off = names_off + (attrs & 0xffffff) * 4
        raw[name_off:name_off + 4] = b"\xf0\x80\x81\x80"
        raw = bytes(raw)
        self.assertIsNotNone(native.sarc_parse(raw))
        expected = python_parse(raw)
        self.assertGreaterEqual(len(expected.warnings), 2)
        self.assertEqual(sarc.SarcArchive.parse(raw), expected)
        self.assertTrue(any("�" in e.name for e in expected))

        # cstring coupe après 4 096 octets, même au milieu d'un caractère.
        long_name = "a" * 4095 + "😀"
        long_archive = sarc.SarcArchive([sarc.SarcEntry(long_name, b"donnees")])
        raw = python_build(long_archive, verify=False)
        self.assertIsNotNone(native.sarc_parse(raw))
        self.assertEqual(sarc.SarcArchive.parse(raw), python_parse(raw))
        self.assertTrue(python_parse(raw).entries[0].name.endswith("�"))
        self.assertIsNone(native.sarc_build(long_archive.entries, "<", 101, 256, verify=True))
        self.assertEqual(outcome(lambda: long_archive.build()),
                         outcome(lambda: python_build(long_archive)))

    def test_mutated_headers_and_nodes_match_including_public_errors(self):
        rng = random.Random(1904)
        raw = python_build(sarc.SarcArchive([
            sarc.SarcEntry("folder/alpha.bin", b"\0alpha"),
            sarc.SarcEntry("beta.txt", b"beta"),
            sarc.SarcEntry("été.dat", b"gamma")]))
        accepted = 0
        for seed in range(1800):
            mutated = bytearray(raw)
            if rng.random() < 0.18:
                mutated = mutated[:rng.randrange(len(mutated) + 1)]
            for _ in range(rng.randint(1, 5)):
                if mutated:
                    position = rng.randrange(len(mutated))
                    mutated[position] = rng.randrange(256)
            data = bytes(mutated)
            expected = outcome(lambda: python_parse(data))
            direct = native.sarc_parse(data)
            actual = outcome(lambda: sarc.SarcArchive.parse(data))
            with self.subTest(seed=seed):
                self.assertEqual(actual, expected)
                if expected[0] == "ok":
                    self.assertIsNotNone(direct)
                    accepted += 1
                else:
                    self.assertIsNone(direct)
        self.assertGreater(accepted, 300)

    def test_build_refusals_limits_and_unusual_python_values_fall_back(self):
        class ExtendedEntry(sarc.SarcEntry):
            pass

        examples = [sarc.SarcArchive([sarc.SarcEntry("x\ud800y.txt", b"donnees")]),
                    sarc.SarcArchive([sarc.SarcEntry("mutable.bin", bytearray(b"\0abc"))]),
                    sarc.SarcArchive([ExtendedEntry("derive.bin", b"donnees")]),
                    sarc.SarcArchive([sarc.SarcEntry("alignement.bin", b"abc", alignment=-2)])]
        for archive in examples:
            with self.subTest(name=repr(archive.entries[0].name)):
                self.assertIsNone(native.sarc_build(archive.entries, archive.endian,
                                                    archive.hash_mult, archive.version, verify=False))
                self.assertEqual(archive.build(verify=False), python_build(archive, verify=False))
                self.assertEqual(outcome(lambda: archive.build()),
                                 outcome(lambda: python_build(archive)))

        archive = sarc.SarcArchive([sarc.SarcEntry("data.bin", b"\x00hello\xff")])
        raw = python_build(archive)
        for limit_name in ("_SARC_INPUT_LIMIT", "_SARC_METADATA_LIMIT"):
            with self.subTest(limit=limit_name), patch.object(native, limit_name, 1):
                self.assertIsNone(native.sarc_parse(raw))
                self.assertEqual(sarc.SarcArchive.parse(raw), python_parse(raw))
        for limit_name in ("_SARC_BUILD_INPUT_LIMIT", "_SARC_BUILD_OUTPUT_LIMIT"):
            with self.subTest(limit=limit_name), patch.object(native, limit_name, 1):
                self.assertIsNone(native.sarc_build(archive.entries, "<", 101, 256))
                self.assertEqual(archive.build(), raw)
        self.assertIsNone(native.sarc_parse(bytearray(raw)))
        self.assertIsNone(native.sarc_build([], "<", 101, 256))
        self.assertIsNone(native.sarc_build(archive.entries, "<", -1, 256))
        self.assertIsNone(native.sarc_build(archive.entries, "<", 101, 65536))

    def test_parallel_calls_and_nul_bytes_in_borrowed_contents(self):
        rng = random.Random(924)
        archives = [sarc.SarcArchive([sarc.SarcEntry(f"dossier/{i}.bin",
                                                     b"\x00" + rng.randbytes(4096) + b"\x00\xff")])
                    for i in range(120)]
        expected = [python_build(archive) for archive in archives]

        def work(index):
            archive = archives[index]
            raw = native.sarc_build(archive.entries, archive.endian, archive.hash_mult, archive.version)
            if raw is None or native.sarc_parse(raw) is None:
                raise AssertionError("le cœur Rust a décliné une entrée valide")
            return raw

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(work, range(len(archives)))), expected)

    def test_nested_document_edits_match_the_python_path(self):
        inner = python_build(sarc.SarcArchive([sarc.SarcEntry("message.txt", b"avant")]))
        outer = python_build(sarc.SarcArchive([sarc.SarcEntry("child.sarc", inner),
                                               sarc.SarcEntry("autre.bin", b"intact")]))

        def edit():
            doc = Document(Level("outer.sarc", outer, zs.ZstdDicts()))
            child_index = doc.top.archive.index_of(doc.top.archive.get("child.sarc"))
            child = doc.push(child_index)
            text_index = child.archive.index_of(child.archive.get("message.txt"))
            child.replace_entry(text_index, "après".encode() + b"\x00\xff")
            return doc.pack()

        with patch.object(native, "available", return_value=False):
            expected = edit()
        with patch.object(native, "sarc_parse", wraps=native.sarc_parse) as reads, \
             patch.object(native, "sarc_build", wraps=native.sarc_build) as writes:
            actual = edit()
            self.assertGreaterEqual(reads.call_count, 2)
            self.assertGreaterEqual(writes.call_count, 2)
        self.assertEqual(actual, expected)
        parsed = python_parse(actual)
        rebuilt_child = python_parse(parsed.get("child.sarc").data)
        self.assertEqual(rebuilt_child.get("message.txt").data, "après".encode() + b"\x00\xff")
        self.assertEqual(parsed.get("autre.bin").data, b"intact")


if __name__ == "__main__":
    unittest.main()
