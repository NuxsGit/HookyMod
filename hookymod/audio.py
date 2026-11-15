"""Première conversion audio, en lecture seule : BWAV Opus mono → Ogg Opus.

Le BWAV observé dans le dump contient des paquets Opus standard précédés de
longueurs big-endian, dans un conteneur Nintendo distinct d'Ogg. On les
remultiplexe sans les réencoder ; aucune bibliothèque de codec n'est requise.
Cette version refuse les banques BARS, le préchargement, PCM/DSP-ADPCM, le
multicanal et les variantes non vérifiées. Elle ne réécrit pas le jeu.

Structures BWAV : https://gota7.github.io/Citric-Composer/specs/binaryWav.html
Paquets Opus : RFC 6716 §3 ; conteneur Ogg Opus : RFC 7845.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .util import HookyError

MAX_BWAV_BYTES = 64 * 1024 * 1024
MAX_PACKETS = 1_000_000


class AudioError(HookyError):
    """Format audio incomplet ou non encore pris en charge."""


@dataclass(frozen=True)
class BwavOpus:
    sample_rate: int
    samples: int
    pre_skip: int
    packets: tuple[bytes, ...]
    durations: tuple[int, ...]
    loop_start: int | None = None
    loop_end: int | None = None

    def info(self) -> dict:
        return {"format": "BWAV Opus mono", "sample_rate": self.sample_rate,
                "channels": 1, "samples": self.samples,
                "seconds": self.samples / self.sample_rate,
                "loop_start": self.loop_start, "loop_end": self.loop_end,
                "output": "opus"}


def _opus_size(packet: bytes, position: int, end: int) -> tuple[int, int]:
    if position >= end:
        raise AudioError("Paquet Opus tronqué : longueur absente.")
    value = packet[position]
    position += 1
    if value >= 252:
        if position >= end:
            raise AudioError("Paquet Opus tronqué : longueur incomplète.")
        value += 4 * packet[position]
        position += 1
    return value, position


def _opus_samples(packet: bytes) -> int:
    """Vérifie le cadrage RFC 6716 et retourne sa durée en échantillons à 48 kHz."""
    if not packet:
        raise AudioError("Paquet Opus vide.")
    config = packet[0] >> 3
    if config < 12:
        frame = (480, 960, 1920, 2880)[config % 4]
    elif config < 16:
        frame = (480, 960)[config % 2]
    else:
        frame = (120, 240, 480, 960)[config % 4]
    code = packet[0] & 3
    position, end = 1, len(packet)
    if code == 0:
        count = 1
        sizes = [end - position]
    elif code == 1:
        count = 2
        if (end - position) % 2:
            raise AudioError("Paquet Opus CBR irrégulier.")
        sizes = [(end - position) // 2] * 2
    elif code == 2:
        count = 2
        size, position = _opus_size(packet, position, end)
        sizes = [size, end - position - size]
    else:
        if position >= end:
            raise AudioError("Paquet Opus tronqué : nombre de trames absent.")
        flags = packet[position]
        position += 1
        count = flags & 63
        if not 1 <= count <= 48:
            raise AudioError("Nombre de trames Opus invalide.")
        padding = 0
        if flags & 64:
            while True:
                if position >= end:
                    raise AudioError("Padding Opus tronqué.")
                value = packet[position]
                position += 1
                padding += 254 if value == 255 else value
                if value != 255:
                    break
            end -= padding
            if end < position:
                raise AudioError("Padding Opus hors du paquet.")
        if flags & 128:
            sizes = []
            for _ in range(count - 1):
                size, position = _opus_size(packet, position, end)
                sizes.append(size)
            sizes.append(end - position - sum(sizes))
        else:
            if (end - position) % count:
                raise AudioError("Paquet Opus CBR irrégulier.")
            sizes = [(end - position) // count] * count
    if frame * count > 5760 or any(size < 0 or size > 1275 for size in sizes):
        raise AudioError("Durée ou taille de trame Opus invalide.")
    return frame * count


def read_bwav_opus(data: bytes) -> BwavOpus:
    """Lit un BWAV complet en Opus mono 48 kHz, sans jamais deviner le format."""
    if len(data) > MAX_BWAV_BYTES:
        raise AudioError("Son trop volumineux pour cette première conversion (64 Mio maximum).")
    if len(data) < 0x5C or data[:4] != b"BWAV" or data[4:6] != b"\xff\xfe":
        raise AudioError("BWAV little-endian complet attendu.")
    version, = struct.unpack_from("<H", data, 6)
    prefetch, channels = struct.unpack_from("<HH", data, 0x0C)
    codec, _pan, rate, original_samples, samples = struct.unpack_from("<HHIII", data, 0x10)
    if version != 1 or prefetch or channels != 1 or codec != 2 or rate != 48000:
        raise AudioError("Seul le BWAV Opus mono complet à 48 kHz est pris en charge.")
    if not samples or samples != original_samples:
        raise AudioError("Ce BWAV ne contient pas le son complet (en-tête ou préchargement).")
    offset, = struct.unpack_from("<I", data, 0x44)
    if offset < 0x5C or offset + 0x20 > len(data):
        raise AudioError("En-tête Opus BWAV absent ou tronqué.")
    magic, header_size = struct.unpack_from("<II", data, offset)
    nx_rate, block_offset = struct.unpack_from("<II", data, offset + 0x0C)
    pre_skip, = struct.unpack_from("<H", data, offset + 0x1C)
    if (magic != 0x80000001 or header_size < 0x18 or data[offset + 8] != 0
            or data[offset + 9] != 1 or nx_rate != 48000 or block_offset < 0x20):
        raise AudioError("En-tête Opus Nintendo non pris en charge.")
    block = offset + block_offset
    if block + 8 > len(data):
        raise AudioError("Bloc Opus absent.")
    block_magic, block_size = struct.unpack_from("<II", data, block)
    if block_magic != 0x80000004 or block_size > len(data) - block - 8:
        raise AudioError("Bloc Opus invalide ou tronqué.")
    position, end = block + 8, block + 8 + block_size
    packets: list[bytes] = []
    durations: list[int] = []
    while position < end:
        if position + 8 > end:
            raise AudioError("En-tête de paquet Opus tronqué.")
        length, _checksum = struct.unpack_from(">II", data, position)
        position += 8
        if not length or length > end - position or length > 65024:
            raise AudioError("Longueur de paquet Opus invalide ou tronquée.")
        packet = data[position:position + length]
        packets.append(packet)
        durations.append(_opus_samples(packet))
        if len(packets) > MAX_PACKETS:
            raise AudioError("Trop de paquets Opus.")
        position += length
    total = sum(durations)
    final_granule = samples + pre_skip
    if not packets or final_granule > total or final_granule < total - durations[-1]:
        raise AudioError("Durée BWAV incohérente avec les paquets Opus.")
    # La valeur sentinelle 0xffffffff signifie « pas de boucle », même quand
    # le mot à 0x48 vaut 1 (cas observé). Les marqueurs restent expérimentaux.
    loop_end, loop_start = struct.unpack_from("<II", data, 0x4C)
    if loop_end == 0xFFFFFFFF or not (0 <= loop_start < loop_end <= samples):
        loop_start = loop_end = None
    return BwavOpus(rate, samples, pre_skip, tuple(packets), tuple(durations),
                    loop_start, loop_end)


def _crc_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        value = byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04C11DB7 if value & 0x80000000 else 0)) & 0xFFFFFFFF
        table.append(value)
    return tuple(table)


_OGG_CRC_TABLE = _crc_table()


def _ogg_page(packet: bytes, serial: int, sequence: int, granule: int, flags: int = 0) -> bytes:
    full, remainder = divmod(len(packet), 255)
    segments = bytes([255] * full + [remainder])
    if len(segments) > 255:
        raise AudioError("Paquet trop grand pour une page Ogg.")
    page = bytearray(struct.pack("<4sBBQIIIB", b"OggS", 0, flags, granule,
                                 serial, sequence, 0, len(segments)) + segments + packet)
    checksum = 0
    for value in page:
        checksum = ((checksum << 8) & 0xFFFFFFFF) ^ _OGG_CRC_TABLE[((checksum >> 24) ^ value) & 255]
    struct.pack_into("<I", page, 22, checksum)
    return bytes(page)


def to_ogg_opus(data: bytes) -> tuple[bytes, dict]:
    """Produit un Ogg/Opus standard et ses métadonnées, sans perte supplémentaire."""
    sound = read_bwav_opus(data)
    serial = zlib.crc32(data) & 0xFFFFFFFF
    head = struct.pack("<8sBBHIhB", b"OpusHead", 1, 1, sound.pre_skip,
                       sound.sample_rate, 0, 0)
    vendor = b"HookyCreator"
    comments: list[bytes] = []
    if sound.loop_start is not None:
        comments = [f"LOOPSTART={sound.loop_start}".encode(), f"LOOPEND={sound.loop_end}".encode()]
    tags = bytearray(b"OpusTags" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments)))
    for comment in comments:
        tags += struct.pack("<I", len(comment)) + comment
    pages = [_ogg_page(head, serial, 0, 0, 2), _ogg_page(tags, serial, 1, 0)]
    granule = 0
    for index, (packet, duration) in enumerate(zip(sound.packets, sound.durations)):
        granule += duration
        last = index == len(sound.packets) - 1
        pages.append(_ogg_page(packet, serial, index + 2,
                               sound.samples + sound.pre_skip if last else granule,
                               4 if last else 0))
    return b"".join(pages), sound.info()
