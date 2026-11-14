"""Parité RESTBL du pont Rust avec le lecteur/écrivain Python historique."""
from __future__ import annotations

import random
import struct
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from hookymod import native, restbl
from hookymod.util import FormatError


def python_parse(data):
    with patch.object(native, "available", return_value=False):
        return restbl.ResourceSizeTable.parse(data)


def python_build(table):
    with patch.object(native, "available", return_value=False):
        return table.build()


def outcome(fn):
    try:
        return ("ok", fn())
    except Exception as exc:  # conserver le type et le message publics
        return ("error", type(exc), str(exc))


def raw_table(pairs, version=3, name_length=64, named=2, tail=b"opaque\x00\xff"):
    return (b"RESTBL" + struct.pack("<IIII", version, name_length, len(pairs), named)
            + b"".join(struct.pack("<II", key, size) for key, size in pairs) + tail)


def snapshot(table):
    return (table.version, table.name_length, table.named_count,
            list(table.sizes.items()), table.tail)


class RestblFallbackTests(unittest.TestCase):
    def test_disabled_and_declined_use_the_python_reference(self):
        table = restbl.ResourceSizeTable(version=3, name_length=64,
                                         sizes={10: 2, 1: 4}, tail=b"reste")
        raw = python_build(table)
        with patch.object(native, "available", return_value=False), \
             patch.object(native, "restbl_parse", side_effect=AssertionError("appel inattendu")), \
             patch.object(native, "restbl_build", side_effect=AssertionError("appel inattendu")):
            self.assertEqual(table.build(), raw)
            self.assertEqual(restbl.ResourceSizeTable.parse(raw), python_parse(raw))
        with patch.object(native, "available", return_value=True), \
             patch.object(native, "restbl_parse", return_value=None) as read, \
             patch.object(native, "restbl_build", return_value=None) as write:
            self.assertEqual(table.build(), raw)
            self.assertEqual(restbl.ResourceSizeTable.parse(raw), python_parse(raw))
            self.assertTrue(read.called)
            self.assertTrue(write.called)
            with self.assertRaisesRegex(FormatError, "RESTBL"):
                restbl.ResourceSizeTable.parse(b"casse")

    def test_mutable_buffer_and_subclass_bypass_native(self):
        class Derived(restbl.ResourceSizeTable):
            pass

        raw = raw_table([(2, 8), (3, 9)])
        with patch.object(native, "available", return_value=True), \
             patch.object(native, "restbl_parse", side_effect=AssertionError("appel inattendu")), \
             patch.object(native, "restbl_build", side_effect=AssertionError("appel inattendu")):
            self.assertEqual(snapshot(restbl.ResourceSizeTable.parse(bytearray(raw))),
                             snapshot(python_parse(raw)))
            table = Derived.parse(raw)
            self.assertIsInstance(table, Derived)
            self.assertEqual(table.build(), python_build(table))

    def test_inconsistent_native_metadata_is_not_trusted(self):
        fake = SimpleNamespace(hookymod_core_restbl_parse=object())
        raw = raw_table([(1, 2), (3, 4)])
        header = lambda original, unique, end, mode: struct.pack(
            "<4sIIIIIQB", b"RSP1", 3, 64, 2, original, unique, end, mode)
        bad = [b"", b"BAD!" + header(2, 2, 38, 0)[4:], header(2, 2, 38, 2),
               header(2, 2, 99, 0), header(2, 3, 38, 1),
               header(2, 1, 38, 0), header(2, 2, 38, 0) + b"extra",
               header(2, 2, 38, 1) + struct.pack("<IIII", 1, 2, 1, 3)]
        with patch.object(native, "_lib", fake):
            for metadata in bad:
                with self.subTest(metadata=metadata[:32]), \
                     patch.object(native, "_owned_result", return_value=metadata):
                    self.assertIsNone(native.restbl_parse(raw))


@unittest.skipUnless(native.available(), "moteur Rust non compilé")
class RustRestblBridgeTests(unittest.TestCase):
    def test_unsorted_duplicates_and_named_tail_are_preserved(self):
        raw = raw_table([(9, 1), (2, 10), (9, 7), (4, 20), (2, 8)],
                        version=0xffffffff, name_length=0, named=0xffffffff,
                        tail=b"nomme\x00\xffreste")
        expected = python_parse(raw)
        direct = native.restbl_parse(raw)
        self.assertIsNotNone(direct)
        with patch.object(native, "restbl_parse", wraps=native.restbl_parse) as called:
            actual = restbl.ResourceSizeTable.parse(raw)
            called.assert_called_once()
        self.assertEqual(snapshot(actual), snapshot(expected))
        self.assertEqual(list(actual.sizes.items()), [(9, 7), (2, 8), (4, 20)])
        self.assertEqual(actual.tail, b"nomme\x00\xffreste")
        built = python_build(expected)
        self.assertEqual(native.restbl_build(actual.sizes, actual.version, actual.name_length,
                                             actual.named_count, actual.tail), built)
        with patch.object(native, "restbl_build", wraps=native.restbl_build) as called:
            self.assertEqual(actual.build(), built)
            called.assert_called_once()
        self.assertEqual(snapshot(restbl.ResourceSizeTable.parse(built)),
                         snapshot(python_parse(built)))

    def test_randomized_sorted_and_unsorted_tables(self):
        rng = random.Random(1402)
        for seed in range(220):
            count = rng.randrange(0, 100)
            pairs = [(rng.randrange(40), rng.randrange(2 ** 32)) for _ in range(count)]
            if seed % 3 == 0:
                pairs = [(key, rng.randrange(2 ** 32)) for key in range(count)]
            tail = rng.randbytes(rng.randrange(40))
            raw = raw_table(pairs, version=rng.randrange(2 ** 32),
                            name_length=rng.randrange(2 ** 32), named=rng.randrange(2 ** 32),
                            tail=tail)
            with self.subTest(seed=seed):
                expected = python_parse(raw)
                self.assertIsNotNone(native.restbl_parse(raw))
                actual = restbl.ResourceSizeTable.parse(raw)
                self.assertEqual(snapshot(actual), snapshot(expected))
                output = python_build(expected)
                self.assertEqual(native.restbl_build(actual.sizes, actual.version,
                                                     actual.name_length, actual.named_count,
                                                     actual.tail), output)
                self.assertEqual(actual.build(), output)

    def test_mutated_headers_and_truncated_tables_keep_errors(self):
        rng = random.Random(4208)
        raw = raw_table([(1, 0), (3, 10), (6, 50), (6, 99), (7, 10)])
        accepted = 0
        for seed in range(1400):
            broken = bytearray(raw)
            if rng.random() < 0.25:
                broken = broken[:rng.randrange(len(broken) + 1)]
            for _ in range(rng.randint(1, 4)):
                if broken:
                    broken[rng.randrange(len(broken))] = rng.randrange(256)
            data = bytes(broken)
            expected = outcome(lambda: python_parse(data))
            direct = native.restbl_parse(data)
            actual = outcome(lambda: restbl.ResourceSizeTable.parse(data))
            with self.subTest(seed=seed):
                self.assertEqual(actual, expected)
                if expected[0] == "ok":
                    self.assertIsNotNone(direct)
                    accepted += 1
                else:
                    self.assertIsNone(direct)
        self.assertGreater(accepted, 200)

    def test_operations_and_rebuild_match_with_the_same_sizes_object(self):
        original = raw_table([(restbl.key_for("Model/Test.bfres"), 100), (0, 7)])
        with patch.object(native, "available", return_value=False):
            expected = restbl.ResourceSizeTable.parse(original)
            self.assertTrue(expected.ensure("Model/Test.bfres.zs", 400))
            self.assertTrue(expected.ensure("Model/Nouveau.bfres", 1000))
            expected.set("Tex/Autre.bntx", 44)
            changes = expected.grow_for({"Model/Test.bfres": 700})
            result = expected.build()
        actual = restbl.ResourceSizeTable.parse(original)
        sizes = actual.sizes
        self.assertTrue(actual.ensure("Model/Test.bfres.zs", 400))
        self.assertTrue(actual.ensure("Model/Nouveau.bfres", 1000))
        actual.set("Tex/Autre.bntx", 44)
        self.assertEqual(actual.grow_for({"Model/Test.bfres": 700}), changes)
        self.assertIs(actual.sizes, sizes)
        self.assertEqual(snapshot(actual), snapshot(expected))
        self.assertEqual(actual.build(), result)

    def test_unusual_python_values_and_native_limits_fall_back(self):
        class Mapping(dict):
            pass

        examples = [restbl.ResourceSizeTable(sizes={-1: 10}),
                    restbl.ResourceSizeTable(sizes={1: 2 ** 32}),
                    restbl.ResourceSizeTable(sizes={True: 5}),
                    restbl.ResourceSizeTable(sizes={1: 1.0}),
                    restbl.ResourceSizeTable(sizes=Mapping({1: 2})),
                    restbl.ResourceSizeTable(sizes={1: 2}, version=-1),
                    restbl.ResourceSizeTable(sizes={1: 2}, tail=bytearray(b"x"))]
        for table in examples:
            with self.subTest(table=repr(table)):
                self.assertIsNone(native.restbl_build(table.sizes, table.version,
                                                      table.name_length, table.named_count, table.tail))
                self.assertEqual(outcome(table.build), outcome(lambda: python_build(table)))
        table = restbl.ResourceSizeTable(version=3, sizes={1: 2}, tail=b"\0reste")
        raw = python_build(table)
        for limit_name in ("_RESTBL_INPUT_LIMIT", "_RESTBL_METADATA_LIMIT", "_RESTBL_ENTRY_LIMIT"):
            with self.subTest(limit=limit_name), patch.object(native, limit_name, 0):
                self.assertIsNone(native.restbl_parse(raw))
                self.assertEqual(snapshot(restbl.ResourceSizeTable.parse(raw)), snapshot(python_parse(raw)))
        for limit_name in ("_RESTBL_OUTPUT_LIMIT", "_RESTBL_ENTRY_LIMIT"):
            with self.subTest(limit=limit_name), patch.object(native, limit_name, 0):
                self.assertIsNone(native.restbl_build(table.sizes, table.version,
                                                      table.name_length, table.named_count, table.tail))
                self.assertEqual(table.build(), raw)
        self.assertIsNone(native.restbl_parse(bytearray(raw)))

    def test_parallel_native_reads_and_builds_are_independent(self):
        rng = random.Random(398)
        tables = [restbl.ResourceSizeTable(version=i, sizes={j: rng.randrange(100000)
                                                           for j in range(150)},
                                          tail=bytes([i, 0, 255]), named_count=i)
                  for i in range(100)]
        expected = [python_build(table) for table in tables]

        def work(index):
            table = tables[index]
            raw = native.restbl_build(table.sizes, table.version, table.name_length,
                                      table.named_count, table.tail)
            if raw is None or native.restbl_parse(raw) is None:
                raise AssertionError("le cœur Rust a décliné une table valide")
            return raw

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(work, range(len(tables)))), expected)


if __name__ == "__main__":
    unittest.main()
