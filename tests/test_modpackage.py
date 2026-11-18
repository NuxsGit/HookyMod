"""Synthetic interoperability tests for the experimental .hooky ROMFS package.

These tests never include game data or claim in-game compatibility.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from hookycreator import modpackage as package
from modmaker import studio


META = {
    "id": "org.hookymod.example", "name": "Example mod", "mod_version": "0.1.0",
    "author": "Maker", "description": "Synthetic files only",
    "runtime_version": "", "game_version": "",
}
FILES = [("romfs/Story/existing.txt", b"changed synthetic content\n"),
         ("romfs/Story/new.txt", b"new synthetic content\n")]


def zip_bytes(entries, *, comment=b""):
    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.comment = comment
            for name, data in entries:
                archive.writestr(name, data)
    return out.getvalue()


def manifest_for(files=FILES):
    archive = package.make_package(META, files)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        return json.loads(source.read("manifest.json"))


def rezip(manifest, files=FILES, *, extra=(), comment=b""):
    raw = manifest if isinstance(manifest, bytes) else json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    return zip_bytes([("manifest.json", raw), *files, *extra], comment=comment)


def row(path, data):
    return {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class PackageTests(unittest.TestCase):
    def test_deterministic_round_trip_preserves_changed_and_added_files(self):
        data = package.make_package(META, FILES[::-1])
        self.assertEqual(data, package.make_package(META, FILES))
        manifest, contents = package.read_package(data)
        self.assertEqual(manifest["format"], "org.hookymod.mod")
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["kind"], "romfs-overlay")
        self.assertEqual(manifest["target"], {"runtime": "longshot", "platform": "pc",
                                              "runtime_version": "", "game_version": ""})
        self.assertEqual(manifest["files"], [row(*item) for item in FILES])
        self.assertEqual(contents, dict(FILES))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.namelist(), ["manifest.json", *[path for path, _ in FILES]])
            self.assertTrue(all(not info.is_dir() for info in archive.infolist()))
        # The manifest need not be the first ZIP entry.
        reordered = zip_bytes([*FILES, ("manifest.json", json.dumps(manifest).encode())])
        self.assertEqual(package.read_package(reordered), (manifest, dict(FILES)))
        # Unicode names and empty files are allowed, and their bytes stay unchanged.
        unicode_data = package.make_package(META, [("romfs/Éléments/clé.txt", b"")])
        self.assertEqual(package.read_package(unicode_data)[1], {"romfs/Éléments/clé.txt": b""})
        # No generated game structures or executable hooks appear by themselves.
        self.assertNotIn(b"source_root", data)

    def test_design_project_and_mod_are_distinct_even_with_the_same_extension(self):
        workspace = {"schema": 1, "id": "a" * 32, "name": "Idea", "description": "",
                     "author": "", "target": "", "revision": 0, "elements": []}
        design = studio.make_archive(workspace)
        with self.assertRaisesRegex(package.PackageError, "projet de conception"):
            package.read_package(design)
        mod = package.make_package(META, FILES)
        with self.assertRaises(studio.StudioError):
            studio.read_archive(mod)
        self.assertFalse(studio.check_workspace(workspace)["playable"])

    def test_metadata_and_path_validation(self):
        for identity in ("", "A-B", "-bad", ".hidden", "ends-", "space here", "é", "a" * 65):
            with self.subTest(identity=identity), self.assertRaises(package.PackageError):
                package.make_package({**META, "id": identity}, FILES)
        for changes in ({"name": "   "}, {"mod_version": ""}, {"author": "x" * 97},
                        {"description": "x" * 1201}, {"runtime_version": "x" * 97},
                        {"game_version": None}, {"name": "bad\x1b"}, {"name": "\ud800"}):
            with self.subTest(changes=changes), self.assertRaises(package.PackageError):
                package.make_package({**META, **changes}, FILES)
        with self.assertRaises(package.PackageError):
            package.make_package({**META, "extra": "unsupported"}, FILES)
        for path in (
            "../outside", "/romfs/file", "C:/romfs/file", "romfs/../file", "romfs/./file",
            "romfs//file", "romfs/file/", "romfs/.hidden", "romfs/one/.two/file",
            "romfs/file.", "romfs/file ", "romfs/file:stream", "romfs/file\\backslash",
            'romfs/a"b', "romfs/file\x00tail", "romfs/file\x7f", "romfs/file\ud800",
            "romfs/CON.txt", "romfs/CON .txt", "romfs/NUL  .dat", "romfs/aux", "romfs/COM1.dat",
            "romfs/LPT¹.txt", "romfs/LPT1 .txt", "romfs/CONIN$", "romfs/e\u0301.txt",
            "romfs/" + "a" * 256, "romfs/" + "/".join(["x"] * 24),
            "romfs/" + "/".join(["a" * 100] * 6),
        ):
            with self.subTest(path=repr(path)), self.assertRaises(package.PackageError):
                package.make_package(META, [(path, b"x")])
        with self.assertRaises(package.PackageError):
            package.make_package(META, [])
        with self.assertRaises(package.PackageError):
            package.make_package(META, [("romfs/file", "not bytes")])
        # A hostile archive must not bypass the same rules enforced on export.
        for path in ("romfs/CON .txt", "romfs/NUL  .dat", "romfs/../escape", "romfs/.hidden"):
            manifest = manifest_for()
            manifest["files"] = [row(path, b"x")]
            with self.subTest(read_path=path), self.assertRaises(package.PackageError):
                package.read_package(rezip(manifest, [(path, b"x")]))

    def test_case_and_file_directory_collisions_are_rejected(self):
        pairs = [
            [("romfs/a.txt", b"1"), ("romfs/a.txt", b"2")],
            [("romfs/A.txt", b"1"), ("romfs/a.TXT", b"2")],
            [("romfs/Straße/file", b"1"), ("romfs/STRASSE/other", b"2")],
            [("romfs/folder", b"1"), ("romfs/folder/file", b"2")],
            [("romfs/folder/file", b"1"), ("romfs/folder", b"2")],
            [("romfs/Dir/one", b"1"), ("romfs/dir/two", b"2")],
        ]
        for files in pairs:
            with self.subTest(paths=[x[0] for x in files]), self.assertRaises(package.PackageError):
                package.make_package(META, files)
            manifest = manifest_for()
            manifest["files"] = [row(*item) for item in files]
            with self.subTest(read=True, paths=[x[0] for x in files]), self.assertRaises(package.PackageError):
                package.read_package(rezip(manifest, files))

    def test_manifest_tampering_unknown_versions_and_json_errors_are_rejected(self):
        original = manifest_for()
        cases = []
        for key, value in [("format", "org.unknown"), ("version", 2), ("version", True),
                           ("kind", "script"), ("id", "../bad"), ("name", ""), ("extra", "no")]:
            obj = copy.deepcopy(original)
            obj[key] = value
            cases.append(obj)
        for key, value in [("runtime", "other"), ("platform", "switch"), ("runtime_version", False),
                           ("unknown", "no")]:
            obj = copy.deepcopy(original)
            obj["target"][key] = value
            cases.append(obj)
        for key, value in [("size", True), ("size", -1), ("size", 999),
                           ("sha256", "0" * 64), ("sha256", "A" * 64), ("extra", "no")]:
            obj = copy.deepcopy(original)
            obj["files"][0][key] = value
            cases.append(obj)
        obj = copy.deepcopy(original)
        del obj["name"]
        cases.append(obj)
        obj = copy.deepcopy(original)
        obj["files"] = []
        cases.append(obj)
        obj = copy.deepcopy(original)
        obj["files"][0]["path"] = "romfs/../escape"
        cases.append(obj)
        obj = copy.deepcopy(original)
        obj["files"].append(copy.deepcopy(obj["files"][0]))
        cases.append(obj)
        raw = json.dumps(original, sort_keys=True).encode()
        cases += [raw[:-1], b'{"format":"first",' + raw[1:], raw.replace(b'"version": 1', b'"version": NaN')]
        for case in cases:
            with self.subTest(case=repr(case)[:120]), self.assertRaises(package.PackageError):
                package.read_package(rezip(case))
        # A well-formed new ZIP with different file bytes still fails SHA-256.
        changed = [(FILES[0][0], b"tampered"), FILES[1]]
        with self.assertRaises(package.PackageError):
            package.read_package(rezip(original, changed))
        with self.assertRaises(package.PackageError):
            package.read_package(rezip(original, extra=[("romfs/unlisted", b"x")]))
        with self.assertRaises(package.PackageError):
            package.read_package(rezip(original, files=FILES[:1]))
        with self.assertRaises(package.PackageError):
            package.read_package(rezip(original, extra=[FILES[0]]))

    def test_archive_limits_are_checked_before_extraction(self):
        data = package.make_package(META, FILES)
        with patch.object(package, "MAX_ARCHIVE_BYTES", len(data) - 1):
            with self.assertRaises(package.PackageError):
                package.read_package(data)
        with patch.object(package, "MAX_MANIFEST_BYTES", 10):
            with self.assertRaises(package.PackageError):
                package.read_package(data)
        with patch.object(package, "MAX_FILE_BYTES", 10):
            with self.assertRaises(package.PackageError):
                package.make_package(META, FILES)
            with self.assertRaises(package.PackageError):
                package.read_package(data)
        with patch.object(package, "MAX_TOTAL_BYTES", 30):
            with self.assertRaises(package.PackageError):
                package.make_package(META, FILES)
            with self.assertRaises(package.PackageError):
                package.read_package(data)
        with patch.object(package, "MAX_FILES", 1):
            with self.assertRaises(package.PackageError):
                package.make_package(META, FILES)
            with self.assertRaises(package.PackageError):
                package.read_package(data)
        with patch.object(package, "MAX_CENTRAL_DIRECTORY_BYTES", 10):
            with self.assertRaises(package.PackageError):
                package.read_package(data)
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "new"
            with self.assertRaises(package.PackageError):
                package.unpack_new(rezip(manifest_for(), extra=[("romfs/other", b"x")]), destination)
            self.assertFalse(destination.exists())

    def test_zip_preflight_rejects_forged_count_comment_prefix_encryption_and_names(self):
        data = package.make_package(META, FILES)
        with self.assertRaises(package.PackageError):
            package.read_package(b"prefix" + data)
        with self.assertRaises(package.PackageError):
            package.read_package(rezip(manifest_for(), comment=b"comment"))
        with self.assertRaises(package.PackageError):
            package.read_package(data[:-8])
        for flag in (1, 0x40, 0x2000):
            bad = bytearray(data)
            central = bad.index(b"PK\x01\x02")
            flags = struct.unpack_from("<H", bad, central + 8)[0]
            struct.pack_into("<H", bad, central + 8, flags | flag)
            with self.subTest(flag=flag), self.assertRaises(package.PackageError):
                package.read_package(bytes(bad))
        # Corrupt a central filename with NUL or an unflagged non-ASCII byte.
        for value in (0, 0xE9):
            bad = bytearray(data)
            central = bad.index(b"PK\x01\x02")
            bad[central + 46] = value
            with self.subTest(value=value), self.assertRaises(package.PackageError):
                package.read_package(bytes(bad))
        # A forged end-record count is not trusted: detect too many central
        # entries before ZipFile is ever constructed.
        many = bytearray(zip_bytes([("manifest.json", b"{}"), *[(f"romfs/{i}", b"") for i in range(258)]]))
        struct.pack_into("<HH", many, len(many) - 22 + 8, 2, 2)
        with patch.object(package.zipfile, "ZipFile", side_effect=AssertionError("unbounded central parsing")):
            with self.assertRaises(package.PackageError):
                package.read_package(bytes(many))

    def test_zip_rejects_symlinks_special_entries_unsupported_compression_and_damage(self):
        manifest = manifest_for(files=FILES[:1])
        raw = json.dumps(manifest).encode()
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600, stat.S_IFDIR | 0o700):
            info = zipfile.ZipInfo(FILES[0][0])
            info.create_system = 3
            info.external_attr = mode << 16
            with self.subTest(mode=mode), self.assertRaises(package.PackageError):
                package.read_package(zip_bytes([("manifest.json", raw), (info, FILES[0][1])]))
        directory = zipfile.ZipInfo(FILES[0][0])
        directory.create_system = 0
        directory.external_attr = 0x10
        with self.assertRaises(package.PackageError):
            package.read_package(zip_bytes([("manifest.json", raw), (directory, FILES[0][1])]))
        bad_method = zipfile.ZipInfo(FILES[0][0])
        bad_method.compress_type = zipfile.ZIP_BZIP2
        with self.assertRaises(package.PackageError):
            package.read_package(zip_bytes([("manifest.json", raw), (bad_method, FILES[0][1])]))
        data = package.make_package(META, FILES)
        damaged = bytearray(data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo(FILES[0][0])
            name_size, extra_size = struct.unpack_from("<HH", damaged, info.header_offset + 26)
            start = info.header_offset + 30 + name_size + extra_size
            damaged[start] = 0x06  # reserved DEFLATE block type
        with self.assertRaises(package.PackageError):
            package.read_package(bytes(damaged))

    def test_zip_rejects_advertised_prefix_of_a_longer_stream(self):
        # The forged CRC and SHA-256 match the declared prefix. Some ZIP APIs
        # return only that prefix without checking whether the DEFLATE stream
        # continues to expand. Both payload and manifest must be rejected.
        for method in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED):
            for forged_name in ("manifest.json", "romfs/a"):
                manifest = manifest_for(files=[("romfs/a", b"A")])
                manifest_raw = json.dumps(manifest).encode("utf-8")
                out = io.BytesIO()
                with zipfile.ZipFile(out, "w", method) as archive:
                    archive.writestr("manifest.json", manifest_raw + (b"extra" * 1000 if forged_name == "manifest.json" else b""))
                    archive.writestr("romfs/a", b"A" * (10000 if forged_name == "romfs/a" else 1))
                data = bytearray(out.getvalue())
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    header = archive.getinfo(forged_name).header_offset
                directory = struct.unpack_from("<I", data, len(data) - 22 + 16)[0]
                while True:
                    name_size, extra_size, comment_size = struct.unpack_from("<HHH", data, directory + 28)
                    name = bytes(data[directory + 46:directory + 46 + name_size]).decode("ascii")
                    if name == forged_name:
                        break
                    directory += 46 + name_size + extra_size + comment_size
                prefix = manifest_raw if forged_name == "manifest.json" else b"A"
                crc = zlib.crc32(prefix)
                struct.pack_into("<I", data, header + 14, crc)
                struct.pack_into("<I", data, header + 22, len(prefix))
                struct.pack_into("<I", data, directory + 16, crc)
                struct.pack_into("<I", data, directory + 24, len(prefix))
                with self.subTest(method=method, forged_name=forged_name), self.assertRaises(package.PackageError):
                    package.read_package(bytes(data))

    def test_streaming_data_descriptors_and_inconsistent_headers(self):
        # A normal ZIP writer targeting a non-seekable stream uses signed data
        # descriptors; these are supported only when they match the directory.
        class NonSeekable:
            def __init__(self):
                self.data = bytearray()

            def write(self, chunk):
                self.data.extend(chunk)
                return len(chunk)

            def flush(self):
                pass

        sink = NonSeekable()
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest_for()).encode())
            for name, raw in FILES:
                archive.writestr(name, raw)
        data = bytes(sink.data)
        self.assertEqual(package.read_package(data)[1], dict(FILES))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entry = archive.getinfo(FILES[0][0])
            self.assertTrue(entry.flag_bits & 8)
            name_size, extra_size = struct.unpack_from("<HH", data, entry.header_offset + 26)
            descriptor = entry.header_offset + 30 + name_size + extra_size + entry.compress_size
        self.assertEqual(data[descriptor:descriptor + 4], b"PK\x07\x08")
        bad = bytearray(data)
        bad[descriptor + 4] ^= 1
        with self.assertRaises(package.PackageError):
            package.read_package(bytes(bad))
        # ZIP also permits an unsigned descriptor. Remove the final signature;
        # all local-header offsets remain unchanged, only the directory moves.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            last = archive.getinfo(FILES[-1][0])
            last_name, last_extra = struct.unpack_from("<HH", data, last.header_offset + 26)
            last_descriptor = last.header_offset + 30 + last_name + last_extra + last.compress_size
        directory = struct.unpack_from("<I", data, len(data) - 22 + 16)[0]
        self.assertEqual(data[last_descriptor:last_descriptor + 4], b"PK\x07\x08")
        unsigned = bytearray(data[:last_descriptor] + data[last_descriptor + 4:])
        struct.pack_into("<I", unsigned, len(unsigned) - 22 + 16, directory - 4)
        self.assertEqual(package.read_package(bytes(unsigned))[1], dict(FILES))
        bad = bytearray(data)
        struct.pack_into("<H", bad, entry.header_offset + 8, zipfile.ZIP_STORED)
        with self.assertRaises(package.PackageError):
            package.read_package(bytes(bad))
        # Smuggling unreferenced bytes between file data and central directory
        # remains forbidden even if the end-record offset is adjusted.
        ordinary = package.make_package(META, FILES)
        directory = struct.unpack_from("<I", ordinary, len(ordinary) - 22 + 16)[0]
        bad = bytearray(ordinary[:directory] + b"x" + ordinary[directory:])
        struct.pack_into("<I", bad, len(bad) - 22 + 16, directory + 1)
        with self.assertRaises(package.PackageError):
            package.read_package(bytes(bad))

    def test_unpack_does_not_replace_original_or_existing_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            original = base / "game" / "romfs" / "Story" / "existing.txt"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"original synthetic content\n")
            data = package.make_package(META, FILES)
            destination = base / "preview"
            manifest = package.unpack_new(data, destination)
            self.assertEqual(manifest["id"], META["id"])
            for path, raw in FILES:
                self.assertEqual((destination / path).read_bytes(), raw)
            self.assertEqual(original.read_bytes(), b"original synthetic content\n")
            (destination / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(package.PackageError):
                package.unpack_new(data, destination)
            self.assertEqual((destination / "keep.txt").read_text(encoding="utf-8"), "keep")
            link = base / "link"
            try:
                link.symlink_to(destination, target_is_directory=True)
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(package.PackageError):
                package.unpack_new(data, link)
            self.assertEqual(original.read_bytes(), b"original synthetic content\n")

    def test_collect_romfs_rejects_links_and_bounds_source_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            romfs = base / "romfs"
            (romfs / "Dir").mkdir(parents=True)
            (romfs / "Dir" / "one.txt").write_bytes(b"one")
            (romfs / "two.txt").write_bytes(b"two")
            self.assertEqual(dict(package.collect_romfs(romfs)),
                             {"romfs/Dir/one.txt": b"one", "romfs/two.txt": b"two"})
            with patch.object(package, "MAX_SOURCE_ENTRIES", 1), self.assertRaises(package.PackageError):
                package.collect_romfs(romfs)
            link = romfs / "external"
            try:
                link.symlink_to(base, target_is_directory=True)
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(package.PackageError):
                package.collect_romfs(romfs)
            with self.assertRaises(package.PackageError):
                package.collect_romfs(link)

    def test_cli_pack_inspect_unpack_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source" / "romfs"
            (source / "Story").mkdir(parents=True)
            for path, raw in FILES:
                (source / Path(path).relative_to("romfs")).write_bytes(raw)
            destination = root / "example.hooky"
            env = dict(os.environ, PYTHONUTF8="1")

            def run(*args):
                return subprocess.run([sys.executable, "-m", "hookycreator.modpackage", *map(str, args)],
                                      text=True, encoding="utf-8", capture_output=True, env=env, check=False)

            packed = run("pack", "--romfs", source, "--output", destination, "--id", META["id"],
                         "--name", META["name"], "--mod-version", META["mod_version"],
                         "--runtime-version", "test-release")
            self.assertEqual(packed.returncode, 0, packed.stderr)
            self.assertIn("non vérifiée", packed.stdout)
            raw = destination.read_bytes()
            self.assertNotIn(str(root).encode(), raw)
            inspected = run("inspect", destination)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            manifest = json.loads(inspected.stdout)
            self.assertEqual(manifest["target"]["runtime_version"], "test-release")
            self.assertEqual(manifest["files"], [row(*item) for item in FILES])
            unpacked = run("unpack", destination, "--destination", root / "preview")
            self.assertEqual(unpacked.returncode, 0, unpacked.stderr)
            self.assertIn("non installé", unpacked.stdout)
            self.assertEqual((root / "preview" / FILES[0][0]).read_bytes(), FILES[0][1])
            self.assertNotEqual(run("unpack", destination, "--destination", root / "preview").returncode, 0)
            self.assertNotEqual(run("pack", "--romfs", source, "--output", destination,
                                    "--id", META["id"], "--name", META["name"]).returncode, 0)
            self.assertEqual(destination.read_bytes(), raw)
            self.assertNotEqual(run("pack", "--romfs", source, "--output", source / "inside.hooky",
                                    "--id", META["id"], "--name", META["name"]).returncode, 0)
            self.assertFalse((source / "inside.hooky").exists())
            self.assertNotEqual(run("pack", "--romfs", source, "--output", root / "invalid.zip",
                                    "--id", META["id"], "--name", META["name"]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
