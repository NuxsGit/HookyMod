"""BWAV Opus mono : validation stricte et remultiplexage Ogg sans données du jeu."""
from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

import zstandard as zstd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import synth  # noqa: E402
from synth_audio import make_opus_bwav  # noqa: E402
from hookymod import audio, identify, zs  # noqa: E402
from hookycreator.server import HookyCreatorServer  # noqa: E402


def pages(data: bytes) -> list[tuple[int, int, bytes]]:
    """Lit les pages Ogg et vérifie leur CRC avec l'algorithme bit par bit."""
    result = []
    offset = 0
    sequence = 0
    serial = None
    while offset < len(data):
        assert data[offset:offset + 4] == b"OggS"
        count = data[offset + 26]
        segments = data[offset + 27:offset + 27 + count]
        end = offset + 27 + count + sum(segments)
        page = bytearray(data[offset:end])
        flags = page[5]
        granule, page_serial, number, expected = struct.unpack_from("<QIII", page, 6)
        assert number == sequence
        if serial is None:
            serial = page_serial
        assert page_serial == serial
        sequence += 1
        page[22:26] = b"\0" * 4
        crc = 0
        for value in page:
            crc ^= value << 24
            for _ in range(8):
                crc = ((crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
        assert crc == expected
        result.append((flags, granule, bytes(page[27 + count:])))
        offset = end
    assert offset == len(data)
    return result


class AudioTests(unittest.TestCase):
    def test_opus_packets_are_preserved_with_precise_trim_and_loop_metadata(self) -> None:
        source = make_opus_bwav(loop=(100, 2400))
        output, info = audio.to_ogg_opus(source)
        result = pages(output)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0][0:2], (2, 0))
        self.assertEqual(result[0][2][:8], b"OpusHead")
        self.assertEqual(struct.unpack_from("<H", result[0][2], 10)[0], 120)
        self.assertEqual(result[1][0:2], (0, 0))
        self.assertIn(b"LOOPSTART=100", result[1][2])
        self.assertIn(b"LOOPEND=2400", result[1][2])
        self.assertEqual([page[1] for page in result[2:]], [960, 1920, 2832])
        self.assertEqual(result[-1][0], 4)
        self.assertEqual([page[2] for page in result[2:]], [b"\xf8\xff\xfe"] * 3)
        self.assertEqual(info["samples"], 2712)
        self.assertEqual(info["output"], "opus")

    def test_truncated_unsupported_and_prefetch_are_rejected(self) -> None:
        source = make_opus_bwav()
        cases = [b"", b"BARS" + source, source[:40], source[:-1]]
        for offset, fmt, value in ((6, "<H", 2), (0x0C, "<H", 1), (0x0E, "<H", 2),
                                   (0x10, "<H", 1), (0x14, "<I", 44100),
                                   (0x1C, "<I", 0), (0x44, "<I", 0x7FFFFFFF),
                                   (0xA4, "<I", 0x7FFFFFFF)):
            mutated = bytearray(source)
            struct.pack_into(fmt, mutated, offset, value)
            cases.append(bytes(mutated))
        for sample in cases:
            with self.subTest(size=len(sample)), self.assertRaises(audio.AudioError):
                audio.to_ogg_opus(sample)

    def test_invalid_packet_cannot_be_reported_as_playable(self) -> None:
        for bad in (b"", b"\xfb", b"\xf9\x01", b"\xfa\xff", b"\xfb\x00"):
            with self.subTest(packet=bad), self.assertRaises(audio.AudioError):
                audio._opus_samples(bad)
        with self.assertRaises(audio.AudioError):
            audio.to_ogg_opus(make_opus_bwav(packets=(b"\xfb\x00",) * 3))

    def test_bounded_zstd_handles_unknown_size_and_concatenated_frames(self) -> None:
        dictionaries = zs.ZstdDicts(require_complete=True)
        for advertised in (True, False):
            compressor = zstd.ZstdCompressor(write_content_size=advertised)
            data = compressor.compress(b"x" * 1024)
            with self.subTest(advertised=advertised), self.assertRaises(zs.DecompressionLimit):
                dictionaries.decompress(data, max_output_size=32)
            self.assertEqual(dictionaries.decompress(data, max_output_size=1024), b"x" * 1024)
            with self.assertRaises(zs.FormatError):
                dictionaries.decompress(data[:-1], max_output_size=2048)
        first = zstd.ZstdCompressor().compress(b"a" * 20)
        second = zstd.ZstdCompressor().compress(b"b" * 20)
        with self.assertRaises(zs.DecompressionLimit):
            dictionaries.decompress(first + second, max_output_size=32)
        self.assertEqual(dictionaries.decompress(first + second, max_output_size=40), b"a" * 20 + b"b" * 20)
        samples = [(f"sound {i:04d} parametres voix ambiances ".encode() * 5) for i in range(200)]
        dictionary = zstd.train_dictionary(2048, samples, dict_id=11)
        dictionaries.add("test.zsdic", dictionary.as_bytes())
        frame = zstd.ZstdCompressor(dict_data=dictionary, write_content_size=False).compress(samples[0])
        self.assertEqual(dictionaries.decompress(frame, max_output_size=1024), samples[0])
        with self.assertRaises(zs.DecompressionLimit):
            dictionaries.decompress(frame, max_output_size=32)

        # Une sortie de 1 Kio avec une fenêtre zstd de 128 Mio doit être
        # refusée avant que le décodeur ne réserve une fenêtre disproportionnée.
        params = zstd.ZstdCompressionParameters.from_level(
            1, window_log=27, write_content_size=False)
        encoder = zstd.ZstdCompressor(compression_params=params).compressobj()
        huge_window = encoder.compress(b"x" * 1024) + encoder.flush()
        self.assertGreater(zstd.get_frame_parameters(huge_window).window_size, 1 << 20)
        with self.assertRaises(zs.FormatError):
            dictionaries.decompress(huge_window, max_output_size=1024)

    def test_magic_recognises_real_container_families_without_claiming_support(self) -> None:
        self.assertEqual(identify.sniff(b"BWAV", "file.bwav")[0].kind, "bwav")
        self.assertEqual(identify.sniff(b"BARS", "file.bars")[0].kind, "bars")
        self.assertEqual(identify.extension_of("file.bwav.zs"), ".bwav.zs")

    def test_optional_real_decoder_reads_the_synthetic_ogg(self) -> None:
        try:
            import av
        except ImportError:
            self.skipTest("PyAV non installé : validation externe optionnelle")
        output, info = audio.to_ogg_opus(make_opus_bwav())
        with av.open(io.BytesIO(output)) as container:
            frames = list(container.decode(audio=0))
        self.assertEqual(sum(frame.samples for frame in frames), info["samples"])


class SoundHttpTests(unittest.TestCase):
    def test_compressed_sound_limit_applies_before_unbounded_decompression(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "romfs"
            root.mkdir()
            data = zstd.ZstdCompressor(write_content_size=False).compress(b"BWAV" + b"x" * 4096)
            (root / "large.bwav.zs").write_bytes(data)
            server = HookyCreatorServer(root, Path(temp) / "work", host="127.0.0.1", port=0)
            with patch.object(audio, "MAX_BWAV_BYTES", 256):
                result = server.project.check_asset("large.bwav.zs", "sound")
            self.assertFalse(result["valid"])
            self.assertIn("volumineux", result["reason"])

    def test_scan_validation_info_and_conversion_for_plain_and_compressed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "romfs"
            root.mkdir()
            source = make_opus_bwav()
            (root / "ok.bwav").write_bytes(source)
            (root / "ok2.bwav.zs").write_bytes(synth.compress(source))
            (root / "unsupported.bwav").write_bytes(b"BWAV" + b"\0" * 128)
            (root / "bank.bars").write_bytes(b"BARS" + b"\0" * 32)
            server = HookyCreatorServer(root, Path(temp) / "work", host="127.0.0.1", port=0).start()
            try:
                self.assertEqual(server.scan_summary["sounds"], 3)
                origin = f"http://127.0.0.1:{server.port}"
                def get(route: str, rel: str):
                    return urllib.request.urlopen(origin + route + "?path=" + urllib.parse.quote(rel), timeout=10)
                for rel in ("ok.bwav", "ok2.bwav.zs"):
                    with urllib.request.urlopen(origin + "/api/asset/check?kind=sound&path=" + rel) as response:
                        self.assertTrue(json.load(response)["valid"])
                    with get("/api/sound/info", rel) as response:
                        self.assertEqual(json.load(response)["samples"], 2712)
                    with get("/api/sound/ogg", rel) as response:
                        self.assertEqual(response.headers.get_content_type(), "audio/ogg")
                        self.assertIsNone(response.headers.get("Content-Disposition"))
                        self.assertEqual(pages(response.read())[0][2][:8], b"OpusHead")
                    with urllib.request.urlopen(origin + "/api/sound/ogg?path=" +
                                                urllib.parse.quote(rel) + "&download=1", timeout=10) as response:
                        self.assertIn("attachment", response.headers["Content-Disposition"])
                        self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])
                with urllib.request.urlopen(origin + "/api/asset/check?kind=sound&path=unsupported.bwav") as response:
                    self.assertFalse(json.load(response)["valid"])
                with self.assertRaises(urllib.error.HTTPError):
                    get("/api/sound/ogg", "bank.bars")
                self.assertEqual((root / "ok.bwav").read_bytes(), source)
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
