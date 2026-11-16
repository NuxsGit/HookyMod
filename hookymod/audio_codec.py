"""Bounded BWAV PCM/DSP decoding and standard audio conversion.

The Opus container is handled by :mod:`hookymod.audio`.  Standard codecs use
soundfile/libsndfile; no external ffmpeg executable is invoked.  Replacement
files are PCM16 BWAVs, not re-encoded Nintendo Opus.  They must be treated as
experimental working copies: a game may impose additional bank, codec, loop,
or resource-table requirements.

The DSP predictor formula and BWAV layout were checked against vgmstream's
BWAV decoder and the MIT-licensed NupExplorer implementation; see provenance.
"""
from __future__ import annotations

import io
import struct
import sys
import zlib
from array import array
from dataclasses import dataclass

from . import audio

MAX_PCM_BYTES = 64 * 1024 * 1024
MAX_IMPORT_BYTES = 24 * 1024 * 1024
MAX_CHANNELS = 2


@dataclass(frozen=True)
class Channel:
    codec: int
    offset: int
    length: int
    coefficients: tuple[int, ...]
    hist1: int
    hist2: int


@dataclass(frozen=True)
class BwavPcm:
    sample_rate: int
    samples: int
    channels: tuple[Channel, ...]
    loop_start: int | None
    loop_end: int | None

    def info(self) -> dict:
        codec = "PCM16" if self.channels[0].codec == 0 else "DSP-ADPCM"
        return {"format": f"BWAV {codec}", "sample_rate": self.sample_rate,
                "channels": len(self.channels), "samples": self.samples,
                "seconds": self.samples / self.sample_rate,
                "loop_start": self.loop_start, "loop_end": self.loop_end,
                "output": "wav"}


def read_bwav_pcm(data: bytes) -> BwavPcm:
    """Validate complete, little-endian PCM16 or DSP-ADPCM BWAV channels."""
    if (len(data) > audio.MAX_BWAV_BYTES or len(data) < 0x5c
            or data[:6] != b"BWAV\xff\xfe"):
        raise audio.AudioError("BWAV complet little-endian attendu (64 Mio maximum).")
    version, = struct.unpack_from("<H", data, 6)
    prefetch, count = struct.unpack_from("<HH", data, 0x0c)
    if version != 1 or prefetch or not 1 <= count <= MAX_CHANNELS:
        raise audio.AudioError("BWAV préchargé, version ou nombre de canaux non pris en charge.")
    header_end = 0x10 + count * 0x4c
    if header_end > len(data):
        raise audio.AudioError("En-têtes de canaux BWAV tronqués.")
    channels: list[Channel] = []
    rate = samples = codec0 = None
    loop = None
    intervals = []
    for i in range(count):
        p = 0x10 + i * 0x4c
        codec, _pan, r, original, n = struct.unpack_from("<HHIII", data, p)
        if codec not in (0, 1) or not 8000 <= r <= 192000 or not n or n != original:
            raise audio.AudioError("Codec, fréquence ou nombre d'échantillons BWAV non pris en charge.")
        if n * count * 2 > MAX_PCM_BYTES:
            raise audio.AudioError("Son décodé trop volumineux (64 Mio maximum).")
        if i and (r, n, codec) != (rate, samples, codec0):
            raise audio.AudioError("Canaux BWAV incohérents.")
        rate, samples, codec0 = r, n, codec
        coefs = struct.unpack_from("<16h", data, p + 0x10)
        original_offset, offset = struct.unpack_from("<II", data, p + 0x30)
        end, start = struct.unpack_from("<II", data, p + 0x3c)
        this_loop = None if end == 0xffffffff else (start, end)
        if this_loop is not None and not (0 <= start < end <= n):
            raise audio.AudioError("Boucle BWAV invalide.")
        if i and this_loop != loop:
            raise audio.AudioError("Boucles BWAV incohérentes entre canaux.")
        loop = this_loop
        length = n * 2 if codec == 0 else ((n + 13) // 14) * 8
        if original_offset != offset or offset < header_end or offset + length > len(data):
            raise audio.AudioError("Données BWAV absentes, préchargées ou tronquées.")
        intervals.append((offset, offset + length))
        # Start history follows predictor/scale in the channel header.
        hist1, hist2 = struct.unpack_from("<hh", data, p + 0x46)
        channels.append(Channel(codec, offset, length, coefs, hist1, hist2))
    intervals.sort()
    if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
        raise audio.AudioError("Canaux BWAV chevauchants.")
    return BwavPcm(int(rate), int(samples), tuple(channels),
                   loop[0] if loop else None, loop[1] if loop else None)


def info(data: bytes) -> dict:
    if len(data) < 0x12:
        raise audio.AudioError("BWAV tronqué.")
    codec, = struct.unpack_from("<H", data, 0x10)
    if codec == 2:
        return audio.read_bwav_opus(data).info()
    return read_bwav_pcm(data).info()


def _dsp_channel(data: bytes, ch: Channel, samples: int) -> array:
    out = array("h")
    hist1, hist2 = ch.hist1, ch.hist2
    at = ch.offset
    while len(out) < samples:
        header = data[at]
        predictor, shift = header >> 4, header & 15
        if predictor >= 8:
            raise audio.AudioError("Prédicteur DSP-ADPCM invalide.")
        c1, c2 = ch.coefficients[predictor * 2:predictor * 2 + 2]
        for value in data[at + 1:at + 8]:
            for nibble in (value >> 4, value & 15):
                if len(out) == samples:
                    break
                if nibble >= 8:
                    nibble -= 16
                sample = (((nibble << shift) << 11) + 1024 + c1 * hist1 + c2 * hist2) >> 11
                sample = max(-32768, min(32767, sample))
                out.append(sample)
                hist2, hist1 = hist1, sample
        at += 8
    return out


def _sf():
    try:
        import soundfile
        return soundfile
    except (ImportError, OSError) as exc:
        raise audio.AudioError("Conversion audio indisponible : installe soundfile (pip install soundfile).") from exc


def decode(data: bytes):
    """Return (numpy int16 frames x channels, sample rate, info), bounded."""
    # numpy is a dependency of soundfile and is loaded only for audio work.
    sf = _sf()
    import numpy as np
    metadata = info(data)
    if metadata["samples"] * metadata["channels"] * 2 > MAX_PCM_BYTES:
        raise audio.AudioError("Son décodé trop volumineux (64 Mio maximum).")
    if metadata["output"] == "opus":
        ogg = audio.to_ogg_opus(data)[0]
        try:
            with sf.SoundFile(io.BytesIO(ogg)) as file:
                if (file.channels != 1 or file.samplerate != metadata["sample_rate"]
                        or file.frames > metadata["samples"] + 5760):
                    raise audio.AudioError("Flux Opus incohérent avec le BWAV.")
                pcm = file.read(metadata["samples"] + 1, dtype="int16", always_2d=True)
        except (RuntimeError, ValueError) as exc:
            raise audio.AudioError(f"Décodage Opus impossible : {exc}") from exc
        if len(pcm) != metadata["samples"]:
            raise audio.AudioError("Durée Opus décodée incohérente.")
        return pcm, metadata["sample_rate"], metadata
    sound = read_bwav_pcm(data)
    channels = []
    for ch in sound.channels:
        if ch.codec == 0:
            channels.append(np.frombuffer(data, dtype="<i2", count=sound.samples, offset=ch.offset))
        else:
            channels.append(np.asarray(_dsp_channel(data, ch, sound.samples), dtype=np.int16))
    return np.stack(channels, axis=1), sound.sample_rate, metadata


def convert(data: bytes, fmt: str) -> tuple[bytes, str, str]:
    """Convert BWAV to WAV, FLAC, MP3, Ogg Vorbis or lossless-remuxed Opus."""
    fmt = str(fmt).lower()
    if fmt == "opus":
        return audio.to_ogg_opus(data)[0], "audio/ogg", "opus"
    formats = {
        "wav": ("WAV", "PCM_16", "audio/wav"),
        "flac": ("FLAC", "PCM_16", "audio/flac"),
        "mp3": ("MP3", "MPEG_LAYER_III", "audio/mpeg"),
        "ogg": ("OGG", "VORBIS", "audio/ogg"),
    }
    if fmt not in formats:
        raise audio.AudioError("Format de sortie audio inconnu.")
    pcm, rate, _ = decode(data)
    sf = _sf()
    container, subtype, mime = formats[fmt]
    out = io.BytesIO()
    try:
        options = {"bitrate_mode": "CONSTANT", "compression_level": 0.0} if fmt == "mp3" else {}
        with sf.SoundFile(out, mode="w", samplerate=rate, channels=pcm.shape[1],
                          format=container, subtype=subtype, **options) as dest:
            # Bounded chunks also avoid oversized calls into libsndfile.
            for start in range(0, len(pcm), 16384):
                dest.write(pcm[start:start + 16384])
    except (RuntimeError, ValueError, TypeError) as exc:
        raise audio.AudioError(f"Encodage {fmt.upper()} indisponible pour cette fréquence : {exc}") from exc
    return out.getvalue(), mime, fmt


def read_import(data: bytes, expected_rate: int, expected_channels: int):
    """Decode a user-supplied standard audio file; no implicit remix or resample."""
    if not data or len(data) > MAX_IMPORT_BYTES:
        raise audio.AudioError("Fichier audio vide ou trop volumineux (24 Mio maximum).")
    sf = _sf()
    try:
        with sf.SoundFile(io.BytesIO(data)) as source:
            if source.format not in ("WAV", "WAVEX", "RF64", "FLAC", "OGG", "MP3", "AIFF"):
                raise audio.AudioError("Format source non pris en charge (WAV, MP3, FLAC, Ogg ou AIFF).")
            if source.samplerate != expected_rate or source.channels != expected_channels:
                raise audio.AudioError(
                    f"La source doit avoir {expected_channels} canal(aux) et {expected_rate} Hz ; "
                    f"elle contient {source.channels} canal(aux) à {source.samplerate} Hz.")
            limit = MAX_PCM_BYTES // (2 * expected_channels)
            if source.frames <= 0 or source.frames > limit:
                raise audio.AudioError("Durée audio invalide ou trop grande (64 Mio décodés maximum).")
            pcm = source.read(limit + 1, dtype="int16", always_2d=True)
    except (RuntimeError, ValueError) as exc:
        raise audio.AudioError(f"Lecture du fichier audio impossible : {exc}") from exc
    if not len(pcm) or len(pcm) > limit:
        raise audio.AudioError("Durée audio invalide ou trop grande.")
    return pcm


def encode_bwav_pcm(pcm, rate: int, loop_start: int | None = None,
                    loop_end: int | None = None) -> bytes:
    """Build an experimental complete planar PCM16 BWAV with aligned channels."""
    import numpy as np
    if (not isinstance(rate, int) or not 8000 <= rate <= 192000
            or not isinstance(pcm, np.ndarray) or pcm.dtype != np.int16 or pcm.ndim != 2
            or pcm.shape[1] not in (1, 2) or not len(pcm)
            or pcm.size * 2 > MAX_PCM_BYTES):
        raise audio.AudioError("PCM d'entrée invalide ou trop volumineux.")
    if (loop_start is None) != (loop_end is None):
        raise audio.AudioError("Boucle incomplète.")
    if loop_start is not None and not 0 <= loop_start < loop_end <= len(pcm):
        raise audio.AudioError("La nouvelle durée ne contient pas la boucle d'origine.")
    count, samples = pcm.shape[1], len(pcm)
    head_size = (0x10 + count * 0x4c + 0x3f) & ~0x3f
    output = bytearray(head_size)
    output[:6] = b"BWAV\xff\xfe"
    struct.pack_into("<H", output, 6, 1)
    struct.pack_into("<HH", output, 0x0c, 0, count)
    crc = 0
    for index in range(count):
        while len(output) % 0x40:
            output.append(0)
        offset = len(output)
        channel = np.ascontiguousarray(pcm[:, index], dtype="<i2").tobytes()
        output.extend(channel)
        crc = zlib.crc32(channel, crc)
        p = 0x10 + index * 0x4c
        struct.pack_into("<HHIII", output, p, 0, 2 if count == 1 else index,
                         rate, samples, samples)
        struct.pack_into("<III", output, p + 0x30, offset, offset, 1)
        struct.pack_into("<II", output, p + 0x3c,
                         0xffffffff if loop_end is None else loop_end,
                         0 if loop_start is None else loop_start)
    struct.pack_into("<I", output, 8, crc & 0xffffffff)
    result = bytes(output)
    # Never publish a file our strict reader cannot reopen.
    read_bwav_pcm(result)
    return result
