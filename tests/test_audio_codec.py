"""Standard audio conversion and experimental BWAV working copies (synthetic data)."""
from __future__ import annotations

import base64
import io
import json
import struct
import tempfile
import zlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from hookymod import audio, audio_codec, sarc, zs
from hookycreator.server import HookyCreatorServer
from tests.synth_audio import make_opus_bwav


def soundfile_bytes(pcm, rate=48000, fmt="WAV", subtype="PCM_16"):
    out = io.BytesIO()
    sf.write(out, pcm, rate, format=fmt, subtype=subtype)
    return out.getvalue()


def test_opus_converts_to_standard_formats_with_same_duration():
    source = make_opus_bwav()
    for fmt in ("wav", "flac", "mp3", "ogg"):
        result, mime, ext = audio_codec.convert(source, fmt)
        decoded, rate = sf.read(io.BytesIO(result), dtype="int16", always_2d=True)
        assert mime.startswith("audio/") and ext == fmt
        assert rate == 48000 and decoded.shape == (2712, 1)
    output, _, _ = audio_codec.convert(source, "opus")
    assert output == audio.to_ogg_opus(source)[0]


def test_pcm_mono_and_stereo_roundtrip_and_loop():
    for channels in (1, 2):
        pcm = np.arange(94 * channels, dtype=np.int16).reshape(-1, channels) * 111
        data = audio_codec.encode_bwav_pcm(pcm, 48000, 5, 90)
        header = audio_codec.read_bwav_pcm(data)
        assert header.loop_start == 5 and header.loop_end == 90
        assert all(ch.offset % 64 == 0 for ch in header.channels)
        decoded, rate, info = audio_codec.decode(data)
        assert rate == 48000 and info["format"] == "BWAV PCM16"
        np.testing.assert_array_equal(decoded, pcm)
        out = audio_codec.convert(data, "wav")[0]
        converted, _ = sf.read(io.BytesIO(out), dtype="int16", always_2d=True)
        np.testing.assert_array_equal(converted, pcm)
    with pytest.raises(audio.AudioError):
        audio_codec.encode_bwav_pcm(pcm, 48000, 10, 100)


def test_stereo_crc_excludes_interchannel_alignment_padding():
    pcm = np.arange(34, dtype=np.int16).reshape(17, 2) * 73
    data = audio_codec.encode_bwav_pcm(pcm, 48000)
    header = audio_codec.read_bwav_pcm(data)
    first, second = header.channels
    assert second.offset > first.offset + first.length  # real padding is present
    sample_bytes = data[first.offset:first.offset + first.length] + data[second.offset:second.offset + second.length]
    crc, = struct.unpack_from("<I", data, 8)
    assert crc == zlib.crc32(sample_bytes)
    assert crc != zlib.crc32(data[first.offset:second.offset + second.length])


@pytest.mark.skipif(not zs.zstd_available(), reason="zstandard not installed")
@pytest.mark.parametrize("with_dictionary", [False, True])
def test_compressed_sound_import_preserves_original_and_frame_type(tmp_path, with_dictionary):
    root = tmp_path / "dump"; root.mkdir()
    dictionary = None
    if with_dictionary:
        samples = [(f"synthetic audio {i:04d} voice data ".encode() * 8) for i in range(200)]
        dictionary = zs.zstd.train_dictionary(2048, samples, dict_id=11)
        (root / "Pack").mkdir()
        archive = sarc.SarcArchive([sarc.SarcEntry("audio.zsdic", dictionary.as_bytes())]).build()
        (root / "Pack" / "ZsDic.pack.zs").write_bytes(zs.zstd.ZstdCompressor().compress(archive))
    source = zs.zstd.ZstdCompressor(dict_data=dictionary).compress(make_opus_bwav())
    expected_id = 11 if with_dictionary else 0
    assert zs.frame_info(source)[0] == expected_id
    path = root / "voice.bwav.zs"; path.write_bytes(source)
    server = HookyCreatorServer(root, tmp_path / "work", host="127.0.0.1", port=0)
    assert server.project.sound_info(path.name)["format"] == "BWAV Opus mono"
    pcm = np.arange(3000, dtype=np.int16).reshape(-1, 1)
    server.project.import_sound(path.name, soundfile_bytes(pcm))
    assert path.read_bytes() == source
    copy = (tmp_path / "work" / "assets" / path.name).read_bytes()
    assert zs.is_zstd(copy) and zs.frame_info(copy)[0] == expected_id
    decoded = zs.zstd.ZstdDecompressor(dict_data=dictionary).decompress(copy)
    np.testing.assert_array_equal(audio_codec.decode(decoded)[0], pcm)
    assert server.project.sound_info(path.name)["format"] == "BWAV PCM16"


def test_dsp_predictor_and_header_history():
    # 14 samples; predictor 0 with coefs (1024, 0), previous sample 1000.
    data = bytearray(0x88)
    data[:6] = b"BWAV\xff\xfe"
    struct.pack_into("<H", data, 6, 1)
    struct.pack_into("<HH", data, 0xc, 0, 1)
    struct.pack_into("<HHIII", data, 0x10, 1, 2, 48000, 14, 14)
    struct.pack_into("<h", data, 0x20, 1024)
    struct.pack_into("<III", data, 0x40, 0x80, 0x80, 1)
    struct.pack_into("<I", data, 0x4c, 0xffffffff)
    struct.pack_into("<hh", data, 0x56, 1000, 0)
    data[0x80:0x88] = b"\x00\x11\x11\x11\x11\x11\x11\x11"
    pcm, _, info = audio_codec.decode(bytes(data))
    assert info["format"] == "BWAV DSP-ADPCM"
    assert pcm[:5, 0].tolist() == [501, 252, 127, 65, 34]
    data[0x80] = 0x80
    with pytest.raises(audio.AudioError, match="Prédicteur"):
        audio_codec.decode(bytes(data))


def test_bad_lengths_channels_and_import_rejected():
    pcm = np.zeros((20, 2), np.int16)
    data = audio_codec.encode_bwav_pcm(pcm, 48000)
    for bad in (data[:-1], data[:70], b"BARS" + data[4:]):
        with pytest.raises(audio.AudioError):
            audio_codec.info(bad)
    overlap = bytearray(data)
    off = struct.unpack_from("<I", overlap, 0x44)[0]
    struct.pack_into("<II", overlap, 0x10 + 0x4c + 0x30, off, off)
    with pytest.raises(audio.AudioError, match="chevauchants"):
        audio_codec.info(bytes(overlap))
    with pytest.raises(audio.AudioError, match="canal"):
        audio_codec.read_import(soundfile_bytes(pcm, 44100), 48000, 2)
    with pytest.raises(audio.AudioError):
        audio_codec.read_import(b"not audio", 48000, 2)


def test_sound_import_writes_only_working_copy():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "dump"
        root.mkdir()
        original = make_opus_bwav()
        (root / "voice.bwav").write_bytes(original)
        server = HookyCreatorServer(root, tmp / "work", host="127.0.0.1", port=0)
        pcm = np.arange(3000, dtype=np.int16).reshape(-1, 1)
        body = {"path": "voice.bwav", "audio": base64.b64encode(soundfile_bytes(pcm)).decode()}
        response, _ = server.api("POST", "/api/sound/import", {}, body)
        result = json.loads(response)
        assert result["format"] == "BWAV PCM16"
        assert (root / "voice.bwav").read_bytes() == original
        work = tmp / "work" / "assets" / "voice.bwav"
        np.testing.assert_array_equal(audio_codec.decode(work.read_bytes())[0], pcm)
        assert server.project.sound_info("voice.bwav")["format"] == "BWAV PCM16"
        assert "voice.bwav" in server.project.edited_files()
        assert not (root / "mods").exists()
        assert (root / "voice.bwav").read_bytes() == original
        with pytest.raises(Exception):
            server.api("POST", "/api/sound/import", {}, {"path": "voice.bwav", "audio": "!"})
        assert (root / "voice.bwav").read_bytes() == original
        assert work.is_file()


def test_no_truncated_loop_or_rate_mismatch_is_published():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "dump"
        root.mkdir()
        (root / "voice.bwav").write_bytes(make_opus_bwav(loop=(100, 2400)))
        server = HookyCreatorServer(root, tmp / "work", host="127.0.0.1", port=0)
        with pytest.raises(audio.AudioError, match="boucle"):
            server.project.import_sound("voice.bwav", soundfile_bytes(np.zeros((2000, 1), np.int16)))
        with pytest.raises(audio.AudioError, match="48000"):
            server.project.import_sound("voice.bwav", soundfile_bytes(np.zeros((3000, 1), np.int16), 44100))
        assert not (tmp / "work" / "assets" / "voice.bwav").exists()
