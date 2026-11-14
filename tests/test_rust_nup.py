"""Parité du lecteur NUP Rust et garde-fous, sur paquets synthétiques."""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hookymod import nup
from tests.test_rust_cli import EXE

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synth_nup  # noqa: E402


def change_toc(data, edit):
    """Modifie une TOC synthétique et recalcule son empreinte d'en-tête."""
    data = bytearray(data)
    at, size = struct.unpack_from("<QQ", data, 8)
    toc = bytearray(data[at:at + size])
    edit(toc)
    data[at:at + size] = toc
    data[0x18:0x38] = hashlib.sha256(toc).digest()
    return bytes(data)


def offsets(toc):
    titles, dirs, files = struct.unpack_from("<IQQ", toc, 4)
    dirs_at = 0x30 + titles * 0x40
    files_at = dirs_at + dirs * 24
    kids_at = files_at + files * 80
    return dirs, files, dirs_at, files_at, kids_at


@unittest.skipUnless(EXE.is_file(), "exécutable Rust release non compilé")
class StandaloneNupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hooky-rust-nup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "paquet modèle.nup"
        self.output = self.root / "extrait.bin"
        self.original = synth_nup.make_sample_nup()
        self.path.write_bytes(self.original)

    def run_cli(self, *args, ok=True):
        proc = subprocess.run([str(EXE), *map(str, args)], capture_output=True,
                              text=True, encoding="utf-8", timeout=60)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
        return proc

    def test_info_list_and_extract_each_entry_match_python(self):
        reference = nup.NupPackage.from_bytes(self.original)
        info = json.loads(self.run_cli("nup", "info", self.path, "--json").stdout)
        self.assertTrue(info["toc_sha256"])
        self.assertEqual(info["files"], reference.file_count)
        self.assertEqual(info["directories"], reference.dir_count)
        self.assertEqual(info["titles"][0]["id"], "0100000000000001")
        listing = json.loads(self.run_cli("nup", "list", self.path, "--json").stdout)["entries"]
        members = list(reference.members())
        expected = [{"path": name, "size": entry.size, "stored_size": entry.stored_size,
                     "codec": entry.codec} for name, entry in members]
        self.assertEqual(listing, expected)
        for name, entry in members:
            with self.subTest(name=name):
                self.run_cli("nup", "extract", self.path, name, self.output)
                self.assertEqual(self.output.read_bytes(), reference.read(entry))
                if name.endswith(".zs"):
                    # Une ressource .zs du jeu ne doit pas être décompressée.
                    self.assertTrue(self.output.read_bytes().startswith(nup.ZSTD_MAGIC))
                    self.assertEqual(self.output.read_bytes(), reference.raw(entry))
                self.output.unlink()
        only = json.loads(self.run_cli("nup", "list", self.path, "--only", "romfs",
                                       "--json").stdout)["entries"]
        self.assertEqual([entry["path"] for entry in only],
                         [name for name, _ in reference.members(only="romfs")])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_verify_shallow_deep_multiple_titles_and_corruption(self):
        data = synth_nup.make_nup({"romfs": {"a.txt": b"un", "b.txt": b"deux"}},
                                   titles=[{"id": 1}, {"id": 2}])
        self.path.write_bytes(data)
        shallow = json.loads(self.run_cli("nup", "verify", self.path, "--json").stdout)
        self.assertEqual((shallow["titles"], shallow["files"]), (2, 4))
        self.assertEqual((shallow["hashes_verified"], shallow["problem_count"]), (0, 0))
        selected = json.loads(self.run_cli("nup", "verify", self.path, "--title", "1",
                                           "--only", "romfs", "--deep", "--json").stdout)
        self.assertEqual((selected["titles"], selected["files"]), (1, 2))
        self.assertEqual((selected["hashes_verified"], selected["hashes_absent"]), (2, 0))
        self.assertEqual(selected["bytes_read"], len(b"undeux"))
        self.run_cli("nup", "verify", self.path, "--title", "2", ok=False)
        self.run_cli("nup", "verify", self.path, "--deep", "--deep", ok=False)
        self.run_cli("nup", "list", self.path, "--deep", ok=False)

        # Le contrôle superficiel ne lit pas le contenu ; le contrôle profond
        # repère une corruption même lorsque la TOC est restée inchangée.
        ref = nup.NupPackage.from_bytes(data)
        corrupt = bytearray(data)
        corrupt[dict(ref.members())["romfs/a.txt"].offset] ^= 1
        self.path.write_bytes(corrupt)
        self.run_cli("nup", "verify", self.path)
        result = self.run_cli("nup", "verify", self.path, "--deep", "--json", ok=False)
        report = json.loads(result.stdout)
        self.assertEqual(report["problem_count"], 2)
        self.assertTrue(all("SHA-256" in issue["error"] for issue in report["problems"]))
        self.assertEqual([issue["title"] for issue in report["problems"]], [0, 1])
        self.run_cli("--max-output", "1", "nup", "verify", self.path, "--deep", ok=False)
        self.assertEqual(self.path.read_bytes(), bytes(corrupt))

        no_hash = synth_nup.make_nup({"romfs": {"sans-hash": b"x"}}, hash_members=False)
        self.path.write_bytes(no_hash)
        result = json.loads(self.run_cli("nup", "verify", self.path, "--deep", "--json").stdout)
        self.assertEqual((result["hashes_verified"], result["hashes_absent"]), (0, 1))
        self.path.write_bytes(synth_nup.make_nup({"romfs": {"mauvais.zs": b"pas zstd"}}))
        result = self.run_cli("nup", "verify", self.path, "--deep", "--json", ok=False)
        self.assertIn("en-tête zstd", json.loads(result.stdout)["problems"][0]["error"])
        self.path.write_bytes(synth_nup.make_nup({"romfs": {}}))
        self.run_cli("nup", "verify", self.path, ok=False)

    def test_verify_bounds_the_number_of_reported_problems(self):
        data = synth_nup.make_nup({"romfs": {f"{i:04}.bin": b"x" for i in range(1010)}})
        def bad_codecs(toc):
            _, count, _, files_at, _ = offsets(toc)
            for index in range(count):
                toc[files_at + index * 80 + 64] = 9
        self.path.write_bytes(change_toc(data, bad_codecs))
        result = self.run_cli("nup", "verify", self.path, "--json", ok=False)
        report = json.loads(result.stdout)
        self.assertEqual((report["files"], report["problem_count"], report["omitted"]),
                         (1010, 1010, 10))
        self.assertEqual(len(report["problems"]), 1000)
        self.assertTrue(all("Codec" in issue["error"] for issue in report["problems"]))

    def test_entry_sha256_across_block_boundaries(self):
        import random
        rng = random.Random(727)
        sizes = (0, 1, 55, 56, 63, 64, 65, 119, 120, 127, 128, 129, 4095, 4096)
        files = {f"item_{size}.bin": rng.randbytes(size) for size in sizes}
        self.path.write_bytes(synth_nup.make_nup({"romfs": files}))
        for name, content in files.items():
            with self.subTest(size=len(content)):
                self.run_cli("nup", "extract", self.path, f"romfs/{name}", self.output)
                self.assertEqual(self.output.read_bytes(), content)
                self.output.unlink()

    def test_multiple_titles_missing_titles_and_invalid_options(self):
        data = synth_nup.make_nup({"romfs": {"bonjour.txt": b"hello"}}, titles=[
            {"id": 1, "version": 1}, {"id": 2, "version": 42}])
        self.path.write_bytes(data)
        info = json.loads(self.run_cli("nup", "info", self.path, "--json").stdout)
        self.assertEqual([item["version"] for item in info["titles"]], [1, 42])
        listing = json.loads(self.run_cli("nup", "list", self.path, "--title", "1",
                                           "--json").stdout)["entries"]
        self.assertEqual([entry["path"] for entry in listing], ["romfs/bonjour.txt"])
        self.run_cli("nup", "extract", self.path, "romfs/bonjour.txt", self.output, "--title", "1")
        self.assertEqual(self.output.read_bytes(), b"hello")
        self.output.unlink()
        for args in (("--title", "2"), ("--title", "bad"), ("--only", "ailleurs"),
                     ("--json", "--json")):
            self.run_cli("nup", "list", self.path, *args, ok=False)
        self.run_cli("nup", "extract", self.path, "romfs/inconnu", self.output, ok=False)
        self.assertFalse(self.output.exists())

    def test_checksums_truncation_and_no_overwrite(self):
        # Une TOC modifiée ne doit pas être considérée valide si son SHA est présent.
        bad_toc = bytearray(self.original)
        toc_at, toc_size = struct.unpack_from("<QQ", bad_toc, 8)
        bad_toc[toc_at + toc_size - 1] ^= 1
        self.path.write_bytes(bad_toc)
        result = self.run_cli("nup", "info", self.path, ok=False)
        self.assertIn("SHA-256", result.stderr)
        self.path.write_bytes(self.original[:0x100])
        self.run_cli("nup", "info", self.path, ok=False)

        # L'empreinte nulle est autorisée, mais ne prouve pas l'intégrité de la TOC.
        no_hash = bytearray(self.original)
        no_hash[0x18:0x38] = bytes(32)
        self.path.write_bytes(no_hash)
        info = json.loads(self.run_cli("nup", "info", self.path, "--json").stdout)
        self.assertFalse(info["toc_sha256"])

        reference = nup.NupPackage.from_bytes(self.original)
        entry = dict(reference.members())["romfs/readme.txt"]
        bad_data = bytearray(self.original)
        bad_data[entry.offset] ^= 1
        self.path.write_bytes(bad_data)
        result = self.run_cli("nup", "extract", self.path, "romfs/readme.txt", self.output, ok=False)
        self.assertIn("SHA-256", result.stderr)
        self.assertFalse(self.output.exists())
        self.path.write_bytes(self.original)
        self.output.write_bytes(b"ne pas remplacer")
        self.run_cli("nup", "extract", self.path, "romfs/readme.txt", self.output, ok=False)
        self.assertEqual(self.output.read_bytes(), b"ne pas remplacer")
        self.run_cli("nup", "extract", self.path, "romfs/readme.txt", self.path, ok=False)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_malformed_tables_names_cycles_and_limits(self):
        reference = nup.NupPackage.from_bytes(self.original)
        romfs_index = reference.titles[0].romfs

        def cycle(toc):
            _, _, dirs_at, _, kids_at = offsets(toc)
            child = struct.unpack_from("<I", toc, dirs_at + romfs_index * 24 + 8)[0]
            struct.pack_into("<I", toc, kids_at + child * 4, romfs_index)

        def invalid_child(toc):
            dirs, files, dirs_at, _, kids_at = offsets(toc)
            child = struct.unpack_from("<I", toc, dirs_at + romfs_index * 24 + 8)[0]
            struct.pack_into("<I", toc, kids_at + child * 4, dirs + files + 10)

        def invalid_name(toc):
            _, _, _, files_at, _ = offsets(toc)
            struct.pack_into("<I", toc, files_at, 0xffffffff)

        for edit in (cycle, invalid_child, invalid_name):
            with self.subTest(edit=edit.__name__):
                self.path.write_bytes(change_toc(self.original, edit))
                self.run_cli("nup", "list", self.path, ok=False)
        for edit in (lambda toc: struct.pack_into("<Q", toc, 8, 2 ** 64 - 1),
                     lambda toc: struct.pack_into("<Q", toc, 24, 1)):
            self.path.write_bytes(change_toc(self.original, edit))
            self.run_cli("nup", "info", self.path, ok=False)
        self.path.write_bytes(self.original)
        self.run_cli("--max-output", "1", "nup", "extract", self.path, "romfs/readme.txt",
                     self.output, ok=False)
        self.assertFalse(self.output.exists())

        # Le premier fichier du paquet synthétique est exefs/main.
        for kind in ("codec", "alignment", "outside"):
            def bad_entry(toc):
                _, _, _, files_at, _ = offsets(toc)
                if kind == "codec":
                    toc[files_at + 64] = 9
                else:
                    value = (2 ** 64 - 16 if kind == "outside" else
                             struct.unpack_from("<Q", toc, files_at + 16)[0] + 1)
                    struct.pack_into("<Q", toc, files_at + 16, value)
            with self.subTest(kind=kind):
                self.path.write_bytes(change_toc(self.original, bad_entry))
                self.run_cli("nup", "extract", self.path, "exefs/main", self.output, ok=False)
                self.assertFalse(self.output.exists())

    def test_ambiguous_name_and_explicit_destination_for_traversal(self):
        raw = synth_nup.make_nup({"romfs": {"a.txt": b"first", "b.txt": b"second"}})
        def duplicate(toc):
            _, _, _, files_at, _ = offsets(toc)
            toc[files_at + 80:files_at + 88] = toc[files_at:files_at + 8]
        self.path.write_bytes(change_toc(raw, duplicate))
        self.run_cli("nup", "extract", self.path, "romfs/a.txt", self.output, ok=False)
        self.assertFalse(self.output.exists())

        traversal = synth_nup.make_nup({"romfs": {"..": {"escape.txt": b"contenu"}}})
        self.path.write_bytes(traversal)
        self.run_cli("nup", "extract", self.path, "romfs/../escape.txt", self.output)
        self.assertEqual(self.output.read_bytes(), b"contenu")
        self.assertFalse((self.root / "escape.txt").exists())
        self.assertFalse((self.root / "romfs").exists())

    def test_implicit_compression_and_double_layer_zs(self):
        import zstandard as zstd

        content = b"donnees NUP" * 500
        for known in (True, False):
            frame = zstd.ZstdCompressor(write_content_size=known, write_checksum=True).compress(content)
            raw = synth_nup.make_nup({"romfs": {"x.pack": frame}})
            def implicit(toc):
                _, _, _, files_at, _ = offsets(toc)
                struct.pack_into("<Q", toc, files_at + 8, len(content))
                toc[files_at + 32:files_at + 64] = hashlib.sha256(content).digest()
            self.path.write_bytes(change_toc(raw, implicit))
            with self.subTest(known=known):
                self.run_cli("nup", "extract", self.path, "romfs/x.pack", self.output)
                self.assertEqual(self.output.read_bytes(), content)
                self.output.unlink()
        inner = zstd.ZstdCompressor(write_checksum=True).compress(content)
        raw = synth_nup.make_nup({"romfs": {"x.bfres.zs": inner}},
                                  pack=frozenset({"romfs/x.bfres.zs"}))
        self.path.write_bytes(raw)
        self.run_cli("nup", "extract", self.path, "romfs/x.bfres.zs", self.output)
        self.assertEqual(self.output.read_bytes(), inner)
        self.output.unlink()

        # Une coïncidence de taille ne doit pas faire décompresser une
        # ressource .zs déjà stockée brute dans le paquet (codec 0).
        inner = bytes.fromhex("28b52ffd201145000010303001001dc002")
        self.assertEqual(len(inner), 17)
        self.assertEqual(zstd.ZstdDecompressor().decompress(inner), b"0" * 17)
        raw = synth_nup.make_nup({"romfs": {"taille-egale.zs": inner}})
        self.path.write_bytes(raw)
        self.run_cli("nup", "extract", self.path, "romfs/taille-egale.zs", self.output)
        self.assertEqual(self.output.read_bytes(), inner)


if __name__ == "__main__":
    unittest.main()
