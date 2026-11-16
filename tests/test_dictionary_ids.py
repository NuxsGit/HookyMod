"""Dictionary naming and loading, using only generated test data."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hookymod import zs


class DictionaryIdTests(unittest.TestCase):
    def test_known_names_have_expected_ids(self):
        self.assertEqual(zs.dict_name_id("zs.zsdic"), 1)
        self.assertEqual(zs.dict_name_id("bkprb.zsdic"), 2)
        self.assertEqual(zs.dict_name_id("bcett.byml.zsdic"), 3)
        self.assertEqual(zs.dict_name_id("pack.zsdic"), 4)
        self.assertEqual(zs.dict_name_id("mon_dico.zsdic"), 0)

    @unittest.skipUnless(zs.zstd_available(), "module zstandard requis")
    def test_store_indexes_raw_dictionaries_by_name(self):
        raw = b"alpha beta gamma delta " * 200  # synthetic raw bytes, no dictionary ID
        store = zs.ZstdDicts()
        self.assertTrue(store.add("zs.zsdic", raw))
        self.assertIn(1, store.by_id)
        self.assertIsNotNone(store._dict_for(1))

    @unittest.skipUnless(zs.zstd_available(), "module zstandard requis")
    def test_unknown_names_do_not_get_an_id(self):
        from tests import synth

        store = zs.ZstdDicts()
        self.assertTrue(store.add("autre.zsdic", synth.train_dict(
            [b"autre %d" % i * 40 for i in range(64)], 512)))
        self.assertNotIn(2, store.by_id)

    @unittest.skipUnless(zs.zstd_available(), "module zstandard requis")
    def test_synthetic_dictionary_archive_loads_all_named_ids(self):
        from tests import synth

        names = ("zs.zsdic", "bkprb.zsdic", "bcett.byml.zsdic", "pack.zsdic")
        entries = [(name, ("synthetic-" + name).encode() * 60) for name in names]
        archive = synth.compress(synth.make_sarc(entries))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ZsDic.pack.zs"
            path.write_bytes(archive)
            store = zs.ZstdDicts()
            self.assertEqual(store.load_file(path), 4)
            self.assertEqual(sorted(store.by_id), [1, 2, 3, 4])
            self.assertEqual(len(store.by_name), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
