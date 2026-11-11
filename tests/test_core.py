"""Tests unitaires des formats (aucun asset du jeu nécessaire)."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

from hookymod import bfres, bntx, sarc, util, zs  # noqa: E402
from hookymod.container import Document, Level, detect_kind  # noqa: E402


class UtilTests(unittest.TestCase):
    def test_align(self):
        self.assertEqual(util.align(1, 0x2000), 0x2000)
        self.assertEqual(util.align(0x2000, 0x2000), 0x2000)
        self.assertEqual(util.align(5, 4), 8)

    def test_safe_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertIsNotNone(util.safe_child(base, "a/b.txt"))
            self.assertIsNone(util.safe_child(base, "../evil.txt"))
            self.assertIsNone(util.safe_child(base, "/etc/passwd"))
            self.assertIsNone(util.safe_child(base, "a/../../evil"))

    def test_png_roundtrip(self):
        w, h = 5, 3
        rgba = bytes((i * 3) % 256 for i in range(w * h * 4))
        blob = util.png_bytes(w, h, rgba)
        self.assertTrue(blob.startswith(b"\x89PNG"))
        got_w, got_h, got = util.decode_png(blob)
        self.assertEqual((got_w, got_h), (w, h))
        self.assertEqual(got, rgba)

    def test_hexdump_and_strings(self):
        data = b"\x00\x01ABCDEFG\x00HelloWorld"
        self.assertIn("ABCDEFG", util.ascii_strings(data))
        self.assertTrue(any("ABCDEFG" in line for line in util.hexdump(data)))

    def test_human_size(self):
        self.assertEqual(util.human_size(512), "512 o")
        self.assertIn("Ko", util.human_size(4096))


class SarcTests(unittest.TestCase):
    def test_parse_independent_writer(self):
        raw = synth.make_sarc([("a.txt", b"hello"),
                               ("dir/b.bin", b"\x00\x01\x02\x03"),
                               ("c.bntx", b"BNTX" + b"\x00" * 60)])
        archive = sarc.SarcArchive.parse(raw)
        self.assertEqual(len(archive), 3)
        names = archive.names()
        self.assertIn("a.txt", names)
        self.assertEqual(archive.get("dir/b.bin").data, b"\x00\x01\x02\x03")

    def test_build_roundtrip(self):
        archive = sarc.SarcArchive.parse(synth.make_sarc([("a.txt", b"hello")]))
        archive.replace(0, b"world!")
        archive.add("new.bin", b"xyz")
        raw = archive.build()
        again = sarc.SarcArchive.parse(raw)
        self.assertEqual(again.get("a.txt").data, b"world!")
        self.assertEqual(again.get("new.bin").data, b"xyz")
        self.assertEqual(struct.unpack_from("<I", raw, 0x08)[0], len(raw))

    def test_big_alignment(self):
        payload = b"FRES" + b"\x00" * 0x1234
        archive = sarc.SarcArchive([sarc.SarcEntry("model.bfres", payload)])
        raw = archive.build()
        parsed = sarc.SarcArchive.parse(raw)
        self.assertEqual(parsed.get("model.bfres").data, payload)

    def test_not_sarc(self):
        with self.assertRaises(util.FormatError):
            sarc.SarcArchive.parse(b"nope" * 10)


class ZstdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        samples = [b"asset data %d " % i * 40 for i in range(64)]
        cls.dictionary = synth.train_dict(samples, 1024)

    def test_dict_roundtrip(self):
        store = zs.ZstdDicts()
        self.assertTrue(store.add("0.zsdic", self.dictionary))
        payload = b"hello world" * 100
        blob = store.compress(payload, next(iter(store.by_id)), level=10)
        self.assertEqual(store.decompress(blob), payload)

    def test_missing_dict_message(self):
        store = zs.ZstdDicts()
        payload = b"hookymod" * 50
        blob = synth.compress(payload, self.dictionary)
        with self.assertRaises(zs.DictMissing):
            store.decompress(blob)

    def test_fallback_tries_other_dicts(self):
        store = zs.ZstdDicts()
        store.add("other.zsdic", synth.train_dict([b"xyz %d" % i * 30 for i in range(32)], 512))
        store.add("real.zsdic", self.dictionary)
        payload = b"payload" * 64
        blob = synth.compress(payload, self.dictionary)
        self.assertEqual(store.decompress(blob), payload)

    def test_find_dict_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            romfs = Path(tmp) / "romfs"
            (romfs / "Pack").mkdir(parents=True)
            (romfs / "Pack" / "ZsDic.pack.zs").write_bytes(
                synth.compress(synth.make_sarc([("0.zsdic", self.dictionary)])))
            files, root = zs.find_dict_files(Path(tmp))
            self.assertEqual(root, romfs)
            self.assertEqual(len(files), 1)
            store = zs.ZstdDicts()
            self.assertEqual(store.load_romfs(Path(tmp))[0], 1)
            self.assertEqual(len(store), 1)

    def test_zs_pack_load(self):
        pack = synth.make_sarc([("0.zsdic", self.dictionary)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ZsDic.pack.zs"
            path.write_bytes(synth.compress(pack))
            store = zs.ZstdDicts()
            self.assertEqual(store.load_file(path), 1)
            self.assertEqual(len(store), 1)


class BntxTests(unittest.TestCase):
    def test_rgba8_linear(self):
        w, h = 8, 4
        pixels = synth.rgba8_pixels(w, h)
        raw = synth.make_bntx(w, h, pixels, fmt=0x0B01, tile_mode=1)
        file = bntx.BntxFile.parse(raw, "test.bntx")
        self.assertEqual(len(file), 1)
        texture = file.textures[0]
        self.assertEqual((texture.width, texture.height), (w, h))
        self.assertEqual(texture.name, "TexName0")
        got_w, got_h, rgba = texture.decode()
        self.assertEqual((got_w, got_h), (w, h))
        self.assertEqual(rgba, pixels)
        self.assertTrue(texture.to_png().startswith(b"\x89PNG"))

    def test_bc1_block_linear(self):
        # Un bloc BC1 blanc (c0 = 0xFFFF, indices 0) en tile mode block-linear.
        block = struct.pack("<HHI", 0xFFFF, 0x0000, 0)
        raw = synth.make_bntx(4, 4, block, fmt=0x1A01, tile_mode=0)
        file = bntx.BntxFile.parse(raw)
        w, h, rgba = file.textures[0].decode()
        self.assertEqual((w, h), (4, 4))
        self.assertEqual(rgba[:4], b"\xff\xff\xff\xff")

    def test_block_height_shift_does_not_crash(self):
        pixels = synth.rgba8_pixels(16, 16)
        raw = synth.make_bntx(16, 16, pixels, fmt=0x0B01, tile_mode=0,
                              block_height_log2=4)
        texture = bntx.BntxFile.parse(raw).textures[0]
        self.assertEqual(len(texture.decode()[2]), 16 * 16 * 4)

    def test_invalid(self):
        with self.assertRaises(util.FormatError):
            bntx.BntxFile.parse(b"BNTX" + b"\x00" * 10)


class BfresTests(unittest.TestCase):
    def test_parse_synthetic(self):
        raw = synth.make_bfres()
        file = bfres.BfresFile.parse(raw)
        self.assertEqual(len(file.models), 1)
        model = file.models[0]
        self.assertEqual(model.name, "TestModel")
        self.assertEqual(len(model.shapes), 1)
        shape = model.shapes[0]
        self.assertEqual(shape.name, "Shape0")
        self.assertIsNotNone(shape.vtx_buffer)
        self.assertEqual(shape.vtx_buffer.vertex_count, 3)
        self.assertEqual(len(shape.meshes), 1)

        meshes = file.build_meshes(0, 0)
        self.assertEqual(len(meshes), 1)
        mesh = meshes[0]
        self.assertEqual(len(mesh.positions), 3)
        self.assertEqual(mesh.faces, [(0, 1, 2)])
        self.assertEqual(mesh.positions[1], (1.0, 0.0, 0.0))
        self.assertIsNotNone(mesh.normals)

    def test_export_obj(self):
        file = bfres.BfresFile.parse(synth.make_bfres())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "mesh.obj"
            file.export_obj(out)
            text = out.read_text()
            self.assertIn("v 0 0 0", text)
            self.assertIn("f 1//1 2//2 3//3", text)
            self.assertTrue(out.with_suffix(".mtl").exists())

    def test_wiiu_rejected(self):
        raw = bytearray(synth.make_bfres())
        raw[0x0C:0x0E] = b"\xfe\xff"
        with self.assertRaises(util.FormatError):
            bfres.BfresFile.parse(bytes(raw))


class ContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        samples = [b"texture data %d" % i * 50 for i in range(64)]
        cls.dictionary = synth.train_dict(samples, 1024)

    def test_navigation_modification(self):
        dicts = zs.ZstdDicts()
        dicts.add("0.zsdic", self.dictionary)
        inner = synth.make_sarc([("a.bin", b"AAA"), ("b.bin", b"BBB")])
        entry_blob = dicts.compress(inner, next(iter(dicts.by_id)), 10)
        outer = synth.make_sarc([("inner.zs", entry_blob), ("other.txt", b"hello")])

        doc = Document(Level("pack.zs", synth.compress(outer), dicts, rel="Pack/pack.zs"))
        self.assertEqual(detect_kind(doc.top.data), "archive SARC")
        self.assertEqual(len(doc.top), 2)

        index = doc.top.archive.names().index("inner.zs")
        doc.push(index)
        self.assertIsNotNone(doc.top.archive)
        doc.top.replace_entry(0, b"ZZZ")
        self.assertTrue(doc.modified())

        packed = doc.pack()
        reparse = Document(Level("pack.zs", packed, dicts, rel="Pack/pack.zs"))
        idx = reparse.top.archive.names().index("inner.zs")
        reparse.push(idx)
        self.assertEqual(reparse.top.archive.get("a.bin").data, b"ZZZ")
        self.assertEqual(reparse.top.archive.get("b.bin").data, b"BBB")

    def test_unmodified_preserves_bytes(self):
        raw = synth.compress(b"hello")
        dicts = zs.ZstdDicts()
        level = Level("x.zs", raw, dicts)
        self.assertEqual(level.pack(), raw)

    def test_entry_name_and_kind(self):
        dicts = zs.ZstdDicts()
        archive = synth.make_sarc([("x.bntx", synth.make_bntx(2, 2, b"\0" * 16,
                                                              tile_mode=1))])
        level = Level("a.pack", archive, dicts)
        self.assertEqual(level.entry_kind(0), "BNTX (textures)")
        self.assertFalse(level.can_open_entry(0))
        self.assertIn("Archive", level.describe())


if __name__ == "__main__":
    unittest.main(verbosity=2)
