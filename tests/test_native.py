"""Parité du cœur Rust et de son repli, avec données synthétiques uniquement.

Sans bibliothèque native, les tests de repli tournent et ceux du cœur Rust sont
ignorés. La CI compile la bibliothèque et vérifie qu'elle est réellement
chargée avant d'exécuter cette suite.
"""

from __future__ import annotations

import math
import random
import struct
import tempfile
import unittest
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hookymod import bfres_v10, bntx, bntx_encode, native, render, util
from hookymod.mesh import MeshData
from tests import synth


def reference_deswizzle(width, height, blk_w, blk_h, bpp, tile_mode, log2,
                        data, round_pitch=True, orin=False):
    """Boucle d'origine, sans table d'offsets ni appel au moteur natif."""
    wb = (width + blk_w - 1) // blk_w
    hb = (height + blk_h - 1) // blk_h
    needed = wb * hb * bpp
    out = bytearray(needed)
    if not needed:
        return bytes(out)
    if tile_mode == 1:
        row = wb * bpp
        pitch = ((row + 31) // 32) * 32 if round_pitch else row
        for y in range(hb):
            src, dst = y * pitch, y * row
            chunk = data[src:src + min(row, max(0, len(data) - src))]
            out[dst:dst + len(chunk)] = chunk
        return bytes(out)
    block_height = 1 << max(0, min(5, log2))
    per_chunk = max(1, 16 // bpp)
    for y in range(hb):
        row_dst = y * wb * bpp
        row_end = row_dst + wb * bpp
        for x in range(0, wb, per_chunk):
            src = bntx.get_addr_block_linear(x, y, wb, bpp, 0, block_height, orin)
            if src >= len(data):
                break
            dst = row_dst + x * bpp
            count = min(16, (wb - x) * bpp, row_end - dst,
                        needed - dst, len(data) - src)
            if count > 0:
                out[dst:dst + count] = data[src:src + count]
    return bytes(out)


def swizzle_cases():
    shapes = ((0, 5, 1, 1, 1), (16, 1, 1, 1, 3), (13, 7, 1, 1, 5),
              (3, 2, 1, 1, 32), (8, 8, 1, 1, 1), (17, 9, 1, 1, 4),
              (64, 9, 1, 1, 16), (5, 7, 4, 4, 16),
              (70, 51, 4, 4, 8), (64, 16, 1, 1, 2))
    rng = random.Random(1042)
    for shape in shapes:
        width, height, bw, bh, bpp = shape
        for tile in (0, 1):
            for log2 in (-3, 1, 7):
                size = bntx.surface_size(width, height, bw, bh, bpp, tile, log2)
                raw = rng.randbytes(size + 3)
                for length in sorted({0, 1, 8, size // 2, max(0, size - 1), size, size + 3}):
                    for orin in (False, True):
                        for round_pitch in (False, True):
                            yield (*shape, tile, log2, raw[:length], round_pitch, orin)


class PythonFallbackTests(unittest.TestCase):
    def test_deswizzle_optimise_matches_original_including_truncated_surfaces(self):
        with patch.object(native, "available", return_value=False):
            for args in swizzle_cases():
                with self.subTest(geometry=args[:7], length=len(args[7]),
                                  round_pitch=args[8], orin=args[9]):
                    self.assertEqual(bntx.deswizzle(*args), reference_deswizzle(*args))

    def test_offset_cache_is_bounded_and_thread_safe(self):
        with bntx._OFFSET_LOCK:
            bntx._OFFSET_TABLES.clear()
        try:
            with patch.object(bntx, "_OFFSET_ENTRY_LIMIT", 64):
                def offsets(i):
                    return bntx.block_linear_offsets(16 * (1 + i % 3), 1 + i % 2,
                                                     1, 1, bool(i % 2))
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(offsets, range(200)))
                self.assertTrue(all(results))
                with bntx._OFFSET_LOCK:
                    self.assertLessEqual(sum(map(len, bntx._OFFSET_TABLES.values())), 64)
                    self.assertLessEqual(len(bntx._OFFSET_TABLES), bntx._OFFSET_TABLE_LIMIT)
        finally:
            with bntx._OFFSET_LOCK:
                bntx._OFFSET_TABLES.clear()

    def test_backend_reports_the_c_decoder_when_present(self):
        with patch.object(native, "available", return_value=False):
            info = bntx.decoder_backend()
            self.assertEqual(info["deswizzle"], "Python")
            self.assertEqual(info["name"], "c" if bntx._t2d is not None else "python")
            self.assertIn("BC1", info["formats"])

    def test_old_or_incomplete_native_library_falls_back_safely(self):
        class Function:
            def __init__(self, result):
                self.result = result

            def __call__(self):
                return self.result

        class Library:
            def __init__(self, version):
                self.hookymod_core_abi = Function(version)

        with tempfile.TemporaryDirectory() as folder:
            Path(folder, native._LIB_NAME).touch()
            with patch.object(native, "_LIB_DIR", Path(folder)), \
                 patch.object(native, "_reason", ""), \
                 patch.dict("os.environ", {"HOOKYMOD_DISABLE_RUST": ""}):
                for version in (1, 3, 4, native._ABI):
                    with self.subTest(version=version), \
                         patch.object(native.ctypes, "CDLL", return_value=Library(version)):
                        self.assertIsNone(native._load())
                        self.assertIn("incompatible", native._reason)
                        self.assertIn("recompiler", native._reason)


@unittest.skipUnless(native.available(), "moteur Rust non compilé")
class RustParityTests(unittest.TestCase):
    def test_deswizzle_matches_reference_for_all_layouts_and_truncation(self):
        for args in swizzle_cases():
            with self.subTest(geometry=args[:7], length=len(args[7]),
                              round_pitch=args[8], orin=args[9]):
                expected = reference_deswizzle(*args)
                self.assertEqual(native.deswizzle(*args), expected)
                self.assertEqual(bntx.deswizzle(*args), expected)

    def test_swizzle_for_import_matches_python_including_truncated_inputs(self):
        rng = random.Random(306)
        for width, height, bw, bh, bpp in ((0, 5, 1, 1, 4), (16, 1, 1, 1, 3),
                                          (13, 7, 1, 1, 5), (3, 2, 1, 1, 32),
                                          (17, 9, 1, 1, 4), (64, 16, 1, 1, 2),
                                          (70, 51, 4, 4, 8), (5, 7, 4, 4, 16)):
            count = ((width + bw - 1) // bw) * ((height + bh - 1) // bh) * bpp
            raw = rng.randbytes(count + 4)
            for tile in (0, 1):
                for log2 in (-3, 1, 7):
                    for length in sorted({0, max(0, count - 1), count, count + 4}):
                        for round_pitch in (False, True):
                            for orin in (False, True):
                                args = (width, height, bw, bh, bpp, tile, log2,
                                        raw[:length], round_pitch, orin)
                                with self.subTest(geometry=args[:7], length=length,
                                                  round_pitch=round_pitch, orin=orin):
                                    with patch.object(native, "available", return_value=False):
                                        expected = bntx.swizzle(*args)
                                    if length >= count:
                                        self.assertEqual(native.swizzle(*args), expected)
                                    else:
                                        self.assertIsNone(native.swizzle(*args))
                                    self.assertEqual(bntx.swizzle(*args), expected)

    def test_bcn_matches_python_for_odd_dimensions_signed_and_truncation(self):
        rng = random.Random(21)
        for fmt in ("BC1_UNORM", "BC2_UNORM", "BC3_UNORM", "BC4_UNORM",
                    "BC4_SNORM", "BC5_UNORM", "BC5_SNORM"):
            stride = 8 if fmt.startswith(("BC1", "BC4")) else 16
            for width, height in ((1, 1), (3, 5), (4, 4), (5, 8), (13, 9)):
                full = ((width + 3) // 4) * ((height + 3) // 4) * stride
                raw = rng.randbytes(full + 5)
                for length in sorted({0, 1, 7, 8, 9, 15, 16,
                                      max(0, full - 1), full, full + 5}):
                    data = raw[:length]
                    with self.subTest(fmt=fmt, size=(width, height), length=length):
                        with patch.object(bntx, "_t2d", None):
                            with patch.object(native, "available", return_value=False):
                                expected = bntx._decode_bcn(fmt, data, width, height, 4, 4)
                            self.assertEqual(native.decode_bc(fmt, data, width, height), expected)
                            self.assertEqual(bntx._decode_bcn(fmt, data, width, height, 4, 4),
                                             expected)

    def test_encode_all_existing_bntx_formats_matches_python(self):
        rng = random.Random(73)
        for kind in sorted(set(bntx_encode.ENCODABLE.values())):
            code = next(code for code, name in bntx_encode.ENCODABLE.items() if name == kind)
            for width, height in ((0, 4), (4, 0), (1, 1), (2, 1),
                                  (3, 5), (4, 4), (5, 8), (17, 13)):
                count = width * height
                patterns = (bytes(count * 4), bytes((255, 255, 255, 255)) * count,
                            bytes((10, 20, 30, 127)) * count,
                            bytes((10, 20, 30, 128)) * count, rng.randbytes(count * 4 + 2))
                for rgba in patterns:
                    with self.subTest(kind=kind, size=(width, height), data=rgba[:8]):
                        with patch.object(native, "available", return_value=False):
                            expected = bntx_encode.encode_pixels(rgba, width, height, code)
                        self.assertEqual(native.encode_pixels(kind, rgba, width, height), expected)
                        self.assertEqual(bntx_encode.encode_pixels(rgba, width, height, code),
                                         expected)
        with self.assertRaises(util.HookyError):
            bntx_encode.encode_pixels(b"\x00", 4, 4, bntx_encode.NAME_TO_CODE["BC3"])

    def test_mips_match_python_for_odd_dimensions_and_alpha(self):
        rng = random.Random(123)
        for width in range(1, 12):
            for height in range(1, 12):
                rgba = rng.randbytes(width * height * 4)
                with self.subTest(width=width, height=height):
                    with patch.object(native, "available", return_value=False):
                        expected = bntx_encode.downsample(rgba, width, height)
                    self.assertEqual(bntx_encode.downsample(rgba, width, height), expected)
        self.assertIsNone(native.downsample_mip(b"\x00", 4, 4))

    def test_bntx_creation_and_replacement_keep_identical_bytes(self):
        width, height = 17, 13
        rgba = synth.rgba8_pixels(width, height, seed=333)
        changed = synth.rgba8_pixels(width, height, seed=444)
        for kind in sorted(set(bntx_encode.ENCODABLE.values())):
            for orin in (False, True):
                with self.subTest(kind=kind, orin=orin):
                    with patch.object(native, "available", return_value=False):
                        expected = bntx_encode.build_bntx("test.bntx", width, height, rgba,
                                                          format_name=kind, mips=4, orin=orin)
                        replacement = bntx_encode.replace_texture(expected, changed, width, height)
                    actual = bntx_encode.build_bntx("test.bntx", width, height, rgba,
                                                    format_name=kind, mips=4, orin=orin)
                    self.assertEqual(actual, expected)
                    self.assertEqual(bntx_encode.replace_texture(actual, changed, width, height),
                                     replacement)

    def test_c_decoder_remains_the_priority(self):
        if bntx._t2d is None:
            self.skipTest("texture2ddecoder absent")
        with patch.object(native, "decode_bc", wraps=native.decode_bc) as rust_decode:
            data = bytes.fromhex("00 f8 00 00 00 00 00 00")
            result = bntx._decode_bcn("BC1_UNORM", data, 4, 4, 4, 4)
            self.assertEqual(result[:4], bytes((255, 0, 0, 255)))
            rust_decode.assert_not_called()

    def test_synthetic_bntx_complete_decodes_match(self):
        for fmt in ("RGBA8", "BC1", "BC3", "BC4", "BC5"):
            for orin in (False, True):
                rgba = synth.rgba8_pixels(17, 13, seed=43)
                encoded, _ = bntx_encode.png_to_bntx(util.png_bytes(17, 13, rgba),
                                                       format_name=fmt, orin=orin)
                texture = bntx.BntxFile.parse(encoded).textures[0]
                with patch.object(bntx, "_t2d", None):
                    with patch.object(native, "available", return_value=False):
                        expected = texture.decode(0)
                    self.assertEqual(texture.decode(0), expected, (fmt, orin))

    def test_bfres_triangle_guards_match_python(self):
        rng = random.Random(103)
        for seed in range(12):
            count = rng.randrange(5, 50)
            positions = [tuple(rng.uniform(-30, 30) for _ in range(3))
                         for _ in range(count)]
            if seed % 4 == 0:
                positions[0] = (float("nan"), float("inf"), -float("inf"))
            faces = [tuple(rng.randrange(-count - 2, count + 2) for _ in range(3))
                     for _ in range(600)]
            for function, args in ((bfres_v10._drop_oversized, (100.0, 0.15)),
                                   (bfres_v10._drop_outliers, (8.0, 100))):
                with self.subTest(seed=seed, function=function.__name__):
                    with patch.object(native, "available", return_value=False):
                        expected = function(positions, faces, *args)
                    self.assertEqual(function(positions, faces, *args), expected)
        # Égalité exacte au seuil, indices négatifs valides et refus des hors-limites.
        points = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (5.0, 0.0, 0.0)]
        faces = [(0, 1, -3), (0, 1, 2), (0, 1, 3), (0, 1, -4)] * 160
        with patch.object(native, "available", return_value=False):
            expected = bfres_v10._drop_oversized(points, faces, 1.0, 1.0)
        self.assertEqual(bfres_v10._drop_oversized(points, faces, 1.0, 1.0), expected)
        self.assertEqual(expected, [(0, 1, -3)] * 160)
        # Les entiers Python arbitraires ne doivent pas être convertis en f64.
        self.assertIsNone(native.filter_triangles([(2 ** 60, 0, 0)], [(0, 0, 0)] * 128, 1.0))
        self.assertIsNone(native.filter_triangles(points, [(2 ** 70, 0, 0)] * 128, 1.0))

    def test_bfres_index_segmentation_matches_python(self):
        rng = random.Random(610)
        for seed in range(300):
            length = rng.randrange(128, 1000)
            if seed % 4 == 0:
                words = array("H", (rng.randrange(5) for _ in range(length)))
            elif seed % 4 == 1:
                words = array("H", (rng.randrange(100) for _ in range(length)))
            elif seed % 4 == 2:
                words = array("H", [0, 1, 2, 3, 4, 5, 0, 0] * ((length + 7) // 8))
                del words[length:]
            else:
                words = array("H", (rng.randrange(65536) for _ in range(length)))
            bounds = [rng.choice([0, 2, 3, 5, 10, 50, 100, 65535, 65536])
                      for _ in range(rng.randrange(0, 8))]
            minimum = rng.choice([0, 1, 2, 4, 8, 20])
            with self.subTest(seed=seed, bounds=bounds, minimum=minimum):
                with patch.object(native, "available", return_value=False):
                    expected = bfres_v10._segment_indices(words, bounds, minimum)
                self.assertEqual(native.segment_indices(words, bounds, minimum), expected)
                self.assertEqual(bfres_v10._segment_indices(words, bounds, minimum), expected)
        self.assertIsNone(native.segment_indices([0, 1, 2] * 50, [3]))
        self.assertIsNone(native.segment_indices(array("H", [0, 1, 2] * 50), [-1]))
        self.assertIsNone(native.segment_indices(array("H", [0, 1, 2] * 50), [3], -1))
        with patch.object(native, "_BFRES_BUFFER_LIMIT", 64):
            self.assertIsNone(native.segment_indices(array("H", [0, 1, 2] * 50), [3]))
            self.assertIsNone(native.decode_positions(bytes(12 * 10), 0, 10, 12, 0, 0x1805))

    def test_bfres_positions_match_python_for_truncated_and_noisy_data(self):
        rng = random.Random(612)
        for seed in range(900):
            fmt = rng.choice([0x1805, 0x1505, 0x1802, 0x1502])
            stride, offset, start = rng.randrange(1, 33), rng.randrange(36), rng.randrange(30)
            count, size = rng.randrange(300), rng.randrange(3000)
            if seed % 3 == 0:
                data = bytes(size)
            elif seed % 3 == 1:
                data = rng.randbytes(size)
            else:
                data = (b"\x00\x00\x00\x3f" * ((size + 3) // 4))[:size]
            with self.subTest(seed=seed, fmt=fmt, stride=stride, offset=offset):
                with patch.object(native, "available", return_value=False):
                    expected = bfres_v10._decode_positions(data, start, count, stride, offset, fmt)
                actual = native.decode_positions(data, start, count, stride, offset, fmt)
                bits = lambda points: b"".join(struct.pack("<3d", *point) for point in points)
                self.assertEqual(bits(actual), bits(expected))  # compare aussi les zéros signés
                self.assertEqual(bits(bfres_v10._decode_positions(data, start, count, stride,
                                                                   offset, fmt)), bits(expected))
        self.assertIsNone(native.decode_positions(b"", -1, 100, 12, 0, 0x1805))
        self.assertIsNone(native.decode_positions(b"", 0, 100, 0, 0, 0x1805))

    def test_all_finite_half_float_positions_match_python_bit_for_bit(self):
        values = [v for v in range(65536) if v & 0x7c00 != 0x7c00]
        for first in range(0, len(values), 128):
            batch = values[first:first + 128]
            data = b"".join(struct.pack("<3H", value, value, value) for value in batch)
            actual = native.decode_positions(data, 0, len(batch), 6, 0, 0x1505)
            expected = list(struct.iter_unpack("<3e", data))
            self.assertEqual(b"".join(struct.pack("<3d", *point) for point in actual),
                             b"".join(struct.pack("<3d", *point) for point in expected))
        for bits in (0x7c00, 0xfc00, 0x7e00):
            data = struct.pack("<3H", 0x3c00, 0, 0) + struct.pack("<3H", bits, 0, 0)
            self.assertEqual(len(native.decode_positions(data, 0, 2, 6, 0, 0x1505)), 1)

    def test_bfres_runaway_and_exact_reference_threshold(self):
        for fmt, code, stride in ((0x1805, "f", 12), (0x1505, "e", 6)):
            for index, value in ((127, math.inf), (128, 50.0), (128, 51.0), (150, 1000.0)):
                data = bytearray(struct.pack(f"<3{code}", 1.0, -0.0, 0.5) * 200)
                struct.pack_into(f"<3{code}", data, index * stride, value, 0.0, 0.0)
                data = bytes(data)
                with patch.object(native, "available", return_value=False):
                    expected = bfres_v10._decode_positions(data, 0, 200, stride, 0, fmt)
                self.assertEqual(native.decode_positions(data, 0, 200, stride, 0, fmt), expected)
                self.assertEqual(bfres_v10._decode_positions(data, 0, 200, stride, 0, fmt), expected)

    def test_synthetic_bfres_keeps_geometry_and_warnings(self):
        from tests.test_bfres_v10 import DataDrivenTests, make_v10

        positions, uvs, triangles = DataDrivenTests()._grid()
        content = make_v10(positions, uvs, triangles)
        with patch.object(native, "available", return_value=False):
            expected = bfres_v10.parse(content)
        actual = bfres_v10.parse(content)
        self.assertIsNotNone(expected)
        self.assertIsNotNone(actual)
        self.assertEqual(actual[0][0].positions, expected[0][0].positions)
        self.assertEqual(actual[0][0].faces, expected[0][0].faces)
        self.assertEqual(actual[0][0].uvs, expected[0][0].uvs)
        self.assertEqual(actual[1], expected[1])

    def test_raster_matches_python_pixel_for_pixel(self):
        rng = random.Random(17)
        for seed in range(6):
            vertices = [tuple(rng.uniform(-3, 3) for _ in range(3)) for _ in range(24)]
            faces = [tuple(rng.sample(range(24), 3)) for _ in range(30)]
            mesh = MeshData(positions=vertices, faces=faces)
            for ss in (1, 2, 3, 4):
                for width, height in ((32, 32), (53, 41)):
                    options = dict(width=width, height=height, yaw=0.3 * seed,
                                   pitch=-0.15 * seed, cull=False, zoom=1.3,
                                   supersample=ss, background=(12, 23, 34, 111))
                    with self.subTest(seed=seed, ss=ss, width=width, height=height):
                        with patch.object(native, "available", return_value=False):
                            expected = render.render_rgba([mesh], **options)
                        self.assertEqual(render.render_rgba([mesh], **options), expected)

    def test_failed_native_call_returns_to_python(self):
        mesh = MeshData(positions=[(0, 0, 0), (2, 0, 0), (0, 2, 0)],
                        faces=[(0, 1, 2)])
        with patch.object(native, "available", return_value=False):
            expected = render.render_rgba([mesh], 32, 32, cull=False)
        with patch.object(native, "rasterize", return_value=None) as call:
            self.assertEqual(render.render_rgba([mesh], 32, 32, cull=False), expected)
            call.assert_called_once()

    def test_bad_native_arguments_are_refused(self):
        self.assertIsNone(native.deswizzle(5, 5, 0, 1, 4, 0, 0, b""))
        self.assertIsNone(native.deswizzle(-1, 5, 1, 1, 4, 0, 0, b""))
        self.assertIsNone(native.decode_bc("BC7", bytes(16), 4, 4))
        self.assertIsNone(native.rasterize(4, 4, 1, bytearray(5), (0, 0, 0, 255)))

    def test_parallel_calls_are_independent(self):
        rgba = synth.rgba8_pixels(32, 32, seed=123)
        encoded, _ = bntx_encode.png_to_bntx(util.png_bytes(32, 32, rgba),
                                               format_name="BC3", orin=True)
        texture = bntx.BntxFile.parse(encoded).textures[0]
        with patch.object(bntx, "_t2d", None):
            expected = texture.decode(0)
            with ThreadPoolExecutor(max_workers=8) as pool:
                outputs = list(pool.map(lambda _i: texture.decode(0), range(40)))
        self.assertTrue(all(result == expected for result in outputs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
