"""Small synthetic BFRES 10.x / FSKA clip, with no game data."""
from __future__ import annotations

import struct


def named_rigid_model() -> bytes:
    """Synthetic structured model with names matching the FSKA fixture."""
    from tests.test_bfres_v10_rigid import rigid_model

    data = bytearray(rigid_model())
    struct.pack_into("<Q", data, 0xb0, 0x168)  # buffer info points at the existing base at 0x170
    for i, (at, value) in enumerate(((0x1800, b"Root"), (0x1820, b"Child"))):
        struct.pack_into("<H", data, at, len(value))
        data[at + 2:at + 2 + len(value)] = value
        struct.pack_into("<Q", data, 0x1580 + i * 0x58, at)
    return bytes(data)


def fska_fixture() -> bytes:
    data = bytearray(0x800)
    data[:4] = b"FRES"
    data[0x0a] = 10
    data[0x0c:0x0e] = b"\xff\xfe"
    struct.pack_into("<QQ", data, 0x58, 0x100, 0x90)
    struct.pack_into("<I", data, 0x94, 1)
    data[0x100:0x104] = b"FSKA"
    struct.pack_into("<I", data, 0x104, 0x1004)
    struct.pack_into("<Q", data, 0x108, 0x280)
    struct.pack_into("<Q", data, 0x128, 0x300)
    struct.pack_into("<ii", data, 0x140, 10, 2)
    struct.pack_into("<H", data, 0x14c, 1)
    for at, name in ((0x280, b"Walk"), (0x290, b"Root")):
        struct.pack_into("<H", data, at, len(name))
        data[at + 2:at + 2 + len(name)] = name
    struct.pack_into("<QQQ", data, 0x300, 0x290, 0x400, 0x360)
    struct.pack_into("<I", data, 0x328, 8 | 16 | 32)
    data[0x32e] = 2
    struct.pack_into("<3f4f3f", data, 0x360, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0)
    # Non-quantized float keys: x moves linearly 0 -> 2 in 10 frames.
    struct.pack_into("<QQHHI5f", data, 0x400, 0x500, 0x520, 0, 2, 0x10, 0, 10, 0, 0, 0)
    struct.pack_into("<2f", data, 0x500, 0, 10)
    struct.pack_into("<4f", data, 0x520, 0, .2, 2, .2)
    # Quantized 8-bit frames and unsigned 16-bit values/slopes.
    struct.pack_into("<QQHHI5f", data, 0x430, 0x540, 0x550, 0x806, 2, 0x28, 0, 10, 0, .01, -.5)
    data[0x540:0x542] = b"\0\x0a"
    struct.pack_into("<2f4H", data, 0x550, .1, -.1, 50, 1, 150, 3)
    return bytes(data)


def fska_with_jump() -> bytes:
    """An FSKA scalar curve with distinct keys at frame 5 (a jump)."""
    data = bytearray(fska_fixture())
    struct.pack_into("<H", data, 0x412, 4)
    struct.pack_into("<4f", data, 0x500, 0., 5., 5., 10.)
    struct.pack_into("<8f", data, 0x520, 0., .2, 1., .2, 3., .2, 4., .2)
    return bytes(data)


def fska_two_clips_with_jump() -> bytes:
    """Two named clips sharing the same synthetic track; neither is repeated."""
    data = bytearray(fska_with_jump())
    data[0x150:0x1a0] = data[0x100:0x150]
    struct.pack_into("<I", data, 0x94, 2)
    struct.pack_into("<Q", data, 0x158, 0x2a0)
    struct.pack_into("<H", data, 0x2a0, 3)
    data[0x2a2:0x2a5] = b"Run"
    return bytes(data)
