"""Synthetic FSKA, skin transport, and HTTP-independent API tests."""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import pytest

from hookycreator.model_skin import SkinError, encode
from hookycreator.server import HookyCreatorServer
from hookymod import animation, bfres_v10, zs
from synth_animation import fska_fixture, named_rigid_model


def test_clips_and_cubic_keyframes():
    data = fska_fixture()
    listing = animation.list_clips(data)
    assert [(c["name"], c["frames"], c["loop"]) for c in listing] == [("Walk", 10, True)]
    clip = animation.read_clip(data, 0)
    assert clip["fps"] == 30 and clip["key_count"] == 4
    bone = clip["bones"][0]
    assert bone["name"] == "Root" and bone["base"]["scale"] == [1., 1., 1.]
    x, rotation = bone["curves"]
    assert x["target"] == "translation.x" and rotation["target"] == "rotation.z"
    assert animation.evaluate(x, -1) == 0
    assert animation.evaluate(x, 5) == pytest.approx(1)
    assert animation.evaluate(x, 20) == 2
    assert rotation["values"] == pytest.approx([0, 1], abs=1e-7)
    assert rotation["slopes"] == pytest.approx([0, .2], abs=1e-7)


def test_invalid_animation_is_rejected_without_partial_output():
    data = fska_fixture()
    for patch in (
        lambda d: struct.pack_into("<I", d, 0x94, 999999),
        lambda d: struct.pack_into("<Q", d, 0x128, len(d) - 1),
        lambda d: struct.pack_into("<H", d, 0x410, 0x80),  # quaternion
        lambda d: struct.pack_into("<I", d, 0x444, 0x10),  # duplicate target
        lambda d: struct.pack_into("<f", d, 0x500, math.nan),
        lambda d: struct.pack_into("<f", d, 0x504, -1),  # unordered frames
    ):
        bad = bytearray(data); patch(bad)
        with pytest.raises(animation.AnimationError):
            animation.read_clip(bytes(bad), 0)
    for index in (-1, 1, True):
        with pytest.raises(animation.AnimationError):
            animation.read_clip(data, index)
    with pytest.raises(animation.AnimationError):
        animation.list_clips(data[:0x130])


def test_skin_matches_geometry_and_carries_every_influence():
    data = named_rigid_model()
    meshes, report = bfres_v10.parse(data)
    packet = encode(data, meshes, structured=report.structured)
    assert packet[:4] == b"H3S1"
    n, = struct.unpack_from("<I", packet, 4)
    header = json.loads(packet[8:8+n])
    assert [b["name"] for b in header["bones"]] == ["Root", "Child"]
    assert [m["vertices"] for m in header["meshes"]] == [3, 4]
    start = (8 + n + 3) & ~3
    joints = struct.unpack_from("<8H", packet, start)
    assert joints == (1, 0, 0, 0, 0, 0, 0, 0)
    assert list(packet[start + 3 * 16:start + 3 * 16 + 8]) == [255, 0, 0, 0, 0, 0, 0, 0]
    with pytest.raises(SkinError):
        encode(data, meshes, structured=False)
    bad = bytearray(data); bad[0x2200 + 20] = 255
    with pytest.raises(SkinError):
        encode(bytes(bad), meshes, structured=True)


def test_animation_project_and_api_on_plain_and_zstd(tmp_path: Path):
    root = tmp_path / "dump"; root.mkdir()
    data = fska_fixture()
    (root / "Walk.anim.bfres").write_bytes(data)
    (root / "Rigid.bfres").write_bytes(named_rigid_model())
    if zs.zstd_available():
        (root / "WalkZ.anim.bfres.zs").write_bytes(zs.zstd.ZstdCompressor().compress(data))
    server = HookyCreatorServer(root, tmp_path / "work", host="127.0.0.1", port=0)
    for rel in ["Walk.anim.bfres"] + (["WalkZ.anim.bfres.zs"] if zs.zstd_available() else []):
        listing, _ = server.api("GET", "/api/animation/list", {"path": [rel]}, {})
        clip, _ = server.api("GET", "/api/animation/clip", {"path": [rel], "index": ["0"]}, {})
        assert json.loads(listing)["clips"][0]["name"] == "Walk"
        assert json.loads(clip)["bones"][0]["name"] == "Root"
    with pytest.raises(Exception):
        server.api("GET", "/api/animation/clip", {"path": ["Walk.anim.bfres"], "index": ["-1"]}, {})
    with pytest.raises(Exception):
        server.api("GET", "/api/animation/list", {"path": ["../Walk.anim.bfres"]}, {})
