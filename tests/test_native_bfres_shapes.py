"""Parité et replis pour la recherche native des formes BFRES 10.x.

Les données sont synthétiques ; aucun fichier du jeu n'est nécessaire. Le
lecteur Python reste la référence indépendante et tous les cas refusés par le
pont doivent y revenir sans changer le résultat ni les exceptions.
"""
from __future__ import annotations

import random
import struct
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from hookymod import bfres_v10, native
from tests.test_bfres_v10 import DataDrivenTests, make_v10


BASE = 0x4000


def fixture():
    return make_v10(*DataDrivenTests()._grid())


def python_shapes(data, base=BASE):
    with patch.object(native, "available", return_value=False):
        return bfres_v10.find_shapes(data, base)


class ShapeFallbackTests(unittest.TestCase):
    def test_disabled_declined_mutable_and_unusual_arguments_keep_python(self):
        data = fixture()
        expected = python_shapes(data)
        with patch.object(native, "available", return_value=False), \
             patch.object(native, "bfres_find_shapes", side_effect=AssertionError("appel inattendu")):
            self.assertEqual(bfres_v10.find_shapes(data, BASE), expected)
        with patch.object(native, "available", return_value=True), \
             patch.object(native, "bfres_find_shapes", return_value=None) as probe:
            self.assertEqual(bfres_v10.find_shapes(data, BASE), expected)
            probe.assert_called_once_with(data, BASE)
        with patch.object(native, "available", return_value=True), \
             patch.object(native, "bfres_find_shapes", side_effect=AssertionError("appel inattendu")):
            self.assertEqual(bfres_v10.find_shapes(bytearray(data), BASE), expected)

            class BytesSubclass(bytes):
                pass

            self.assertEqual(bfres_v10.find_shapes(BytesSubclass(data), BASE), expected)
        self.assertEqual(bfres_v10.find_shapes(data, -8192), python_shapes(data, -8192))
        self.assertIsNone(native.bfres_find_shapes(data, -1))
        self.assertIsNone(native.bfres_find_shapes(data, 1 << 64))
        self.assertIsNone(native.bfres_find_shapes(bytearray(data), BASE))

    def test_changed_format_or_kind_rules_use_python(self):
        data = fixture()
        rules = (
            patch.dict(bfres_v10.FORMAT_SIZES, {0x1201: 8}),
            patch.object(bfres_v10, "_KIND_PREFIXES", (("_u", "position"),)),
        )
        for rule in rules:
            with rule:
                expected = python_shapes(data)
                with patch.object(native, "available", return_value=True), \
                     patch.object(native, "bfres_find_shapes", side_effect=AssertionError("appel inattendu")):
                    self.assertEqual(bfres_v10.find_shapes(data, BASE), expected)

    def test_corrupt_native_metadata_is_rejected(self):
        data = fixture()
        shape = struct.pack("<QQIQQQII", 0x1b0, 16, 0x1000, 0x5000, 144,
                            len(data), 2, 2)
        attr = (struct.pack("<IIIII", 0x2b2, 3, 0x1805, 0, 12)
                + struct.pack("<IIIII", 0x2ba, 3, 0x1201, 12, 4))
        valid = b"FSP1" + struct.pack("<I", 1) + shape + attr
        fake = SimpleNamespace(hookymod_core_bfres_find_shapes=object())
        with patch.object(native, "_lib", fake), \
             patch.object(native, "_owned_result", return_value=valid):
            decoded = native.bfres_find_shapes(data, BASE)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded[0][-1], [("_p0", 0x1805, 0, 12), ("_u0", 0x1201, 12, 4)])
        broken = [b"", b"BAD!" + valid[4:], valid[:-1], valid + b"extra"]
        for position, value in ((4, 250_001), (8, 0x1b1), (16, 20), (24, 0),
                                (28, len(data)), (36, 145), (52, 3),
                                (56, 33), (60, len(data)), (64, 64),
                                (76, 0), (92, 0)):
            changed = bytearray(valid)
            struct.pack_into("<I", changed, position, value)
            broken.append(bytes(changed))
        with patch.object(native, "_lib", fake):
            for metadata in broken:
                with self.subTest(metadata=metadata[:60]), \
                     patch.object(native, "_owned_result", return_value=metadata):
                    self.assertIsNone(native.bfres_find_shapes(data, BASE))


@unittest.skipUnless(native.available(), "moteur Rust non compilé")
class RustShapeTests(unittest.TestCase):
    def test_synthetic_shape_is_read_by_rust_and_matches_reference(self):
        data = fixture()
        expected = python_shapes(data)
        self.assertIsNotNone(native.bfres_find_shapes(data, BASE))
        with patch.object(native, "bfres_find_shapes", wraps=native.bfres_find_shapes) as probe:
            actual = bfres_v10.find_shapes(data, BASE)
        probe.assert_called_once_with(data, BASE)
        self.assertEqual(actual, expected)
        self.assertEqual([attr.name for attr in actual[0].attributes], ["_p0", "_u0"])
        self.assertEqual((actual[0].stride, actual[0].vertex_count), (16, 144))

    def test_duplicate_buffers_sorting_and_unaligned_signatures(self):
        data = bytearray(fixture())
        data[0x350:0x3a0] = data[0x1b0:0x200]
        data[0x3a0:0x3f0] = data[0x1b0:0x200]
        struct.pack_into("<I", data, 0x350 + 0x48, 0x1200)
        struct.pack_into("<I", data, 0x3a0 + 0x48, 0x1000)  # doublon de la première
        data[0x401:0x405] = b"FVTX"  # signature non alignée, ignorée
        data = bytes(data)
        expected = python_shapes(data)
        self.assertEqual([shape.offset for shape in expected], [0x1b0, 0x350])
        self.assertEqual([shape.vertex_count for shape in expected], [32, 112])
        self.assertIsNotNone(native.bfres_find_shapes(data, BASE))
        self.assertEqual(bfres_v10.find_shapes(data, BASE), expected)

    def test_attribute_priorities_counts_and_large_stride(self):
        original = fixture()
        for name, fmt, declared, last_offset in (
                (b"abc", 0x1805, 2, 12),   # position déduite du format
                (b"_n0", 0x1805, 2, 12),   # le préfixe normale prime sur le format
                (b"_p0", 0x1505, 0, 12),   # nombre non déclaré
                (b"_p0", 0x1505, 3, 12),   # déclaration incomplète
                (b"_p0", 0x1805, 2, 0),    # offsets non croissants
                (b"_p0", 0x1805, 2, 0xffffffff),
                (b"\xffp0", 0x1805, 2, 12)):
            data = bytearray(original)
            data[0x2b2:0x2b5] = name
            struct.pack_into("<I", data, 0x218, fmt)
            struct.pack_into("<I", data, 0x1b0 + 0x4c, declared)
            struct.pack_into("<I", data, 0x22c, last_offset)
            data = bytes(data)
            with self.subTest(name=name, fmt=fmt, declared=declared, last_offset=last_offset):
                expected = python_shapes(data)
                self.assertIsNotNone(native.bfres_find_shapes(data, BASE))
                self.assertEqual(bfres_v10.find_shapes(data, BASE), expected)
                if last_offset == 0xffffffff:
                    self.assertEqual(expected[0].stride, 0x100000004)

    def test_all_known_last_attribute_sizes_and_unknown_format_match(self):
        original = fixture()
        formats = list(bfres_v10.FORMAT_SIZES) + [0, 0xffffffff]
        for fmt in formats:
            data = bytearray(original)
            struct.pack_into("<I", data, 0x228, fmt)
            data = bytes(data)
            with self.subTest(fmt=fmt):
                self.assertIsNotNone(native.bfres_find_shapes(data, BASE))
                self.assertEqual(bfres_v10.find_shapes(data, BASE), python_shapes(data))

    def test_randomized_mutations_and_truncation_match_python_without_hidden_fallback(self):
        rng = random.Random(27391)
        original = fixture()
        nonempty = 0
        for iteration in range(2200):
            data = bytearray(original)
            if iteration % 3 == 0:
                data[0x350:0x3a0] = data[0x1b0:0x200]
                struct.pack_into("<I", data, 0x350 + 0x48,
                                 rng.choice([0x1000, 0x1100, 0x1250, 0, 0xffffffff]))
            for _ in range(rng.randint(1, 5)):
                offset = rng.choice([0x1b8, 0x1c8, 0x1f8, 0x1fc, 0x210, 0x218, 0x21c,
                                     0x220, 0x228, 0x22c, 0x2b0, 0x2b2, 0x2b8, 0x2ba,
                                     rng.randrange(len(data))])
                size = rng.choice([1, 1, 2, 4, 8])
                if offset + size <= len(data):
                    data[offset:offset + size] = rng.randbytes(size)
            if iteration % 9 == 0:
                data = data[:rng.randrange(len(data) + 1)]
            data = bytes(data)
            base = rng.choice([BASE, BASE, BASE, 0, 1, len(data), 1 << 31])
            expected = python_shapes(data, base)
            direct = native.bfres_find_shapes(data, base)
            actual = bfres_v10.find_shapes(data, base)
            with self.subTest(iteration=iteration):
                self.assertIsNotNone(direct)  # pas de repli silencieux
                self.assertEqual(actual, expected)
            nonempty += bool(expected)
        self.assertGreater(nonempty, 100)

    def test_parallel_calls_have_independent_owned_results(self):
        data = fixture()
        expected = native.bfres_find_shapes(data, BASE)
        self.assertIsNotNone(expected)
        with ThreadPoolExecutor(max_workers=8) as pool:
            actual = list(pool.map(lambda _: native.bfres_find_shapes(data, BASE), range(160)))
        self.assertTrue(all(value == expected for value in actual))


if __name__ == "__main__":
    unittest.main()
