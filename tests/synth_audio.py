"""BWAV synthétique, sans données du jeu. Le paquet Opus est un silence standard."""
from __future__ import annotations

import struct


def make_opus_bwav(*, packets: tuple[bytes, ...] | None = None,
                   samples: int = 2712, pre_skip: int = 120,
                   loop: tuple[int, int] | None = None) -> bytes:
    packets = packets or (b"\xf8\xff\xfe",) * 3  # Opus silence, 20 ms à 48 kHz
    framed = b"".join(struct.pack(">II", len(packet), 0) + packet for packet in packets)
    blob = bytearray(0x80)
    blob[:4] = b"BWAV"
    blob[4:6] = b"\xff\xfe"
    struct.pack_into("<H", blob, 6, 1)
    struct.pack_into("<HH", blob, 0x0C, 0, 1)
    struct.pack_into("<HHIII", blob, 0x10, 2, 2, 48000, samples, samples)
    struct.pack_into("<II", blob, 0x40, 0x80, 0x80)
    struct.pack_into("<I", blob, 0x48, 1)
    struct.pack_into("<II", blob, 0x4C, loop[1] if loop else 0xFFFFFFFF,
                     loop[0] if loop else 0)
    nx = bytearray(0x20)
    struct.pack_into("<II", nx, 0, 0x80000001, 0x18)
    nx[9] = 1
    struct.pack_into("<II", nx, 0x0C, 48000, 0x20)
    struct.pack_into("<H", nx, 0x1C, pre_skip)
    return bytes(blob + nx + struct.pack("<II", 0x80000004, len(framed)) + framed)
