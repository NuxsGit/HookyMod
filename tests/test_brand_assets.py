"""The web, splash and desktop icons must all use the supplied master logo."""
from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image")

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "assets" / "HookyMod-logo.png"
ICONS = ROOT / "src-tauri" / "icons"


def master_image():
    with Image.open(MASTER) as image:
        assert image.format == "PNG"
        assert image.size == (512, 512)
        return image.convert("RGBA")


def expected(master, size):
    return master if size == 512 else master.resize((size, size), Image.Resampling.LANCZOS)


def assert_same_image(actual, reference):
    converted = actual.convert("RGBA")
    assert converted.size == reference.size
    assert converted.tobytes() == reference.tobytes()


def test_splash_and_web_use_original_png():
    original = MASTER.read_bytes()
    for path in (ROOT / "hookycreator/web/HookyMod.png", ROOT / "src-tauri/desktop/HookyMod.png"):
        assert path.read_bytes() == original


@pytest.mark.parametrize("filename,size", [("32x32.png", 32), ("128x128.png", 128), ("128x128@2x.png", 256)])
def test_png_icons_match_master(filename, size):
    with Image.open(ICONS / filename) as icon:
        assert_same_image(icon, expected(master_image(), size))


def test_windows_ico_has_all_expected_resolutions():
    sizes = (16, 24, 32, 48, 64, 128, 256)
    master = master_image()
    with Image.open(ICONS / "icon.ico") as icon:
        assert icon.format == "ICO"
        assert icon.ico.sizes() == {(size, size) for size in sizes}
        for size in sizes:
            assert_same_image(icon.ico.getimage((size, size)), expected(master, size))


def test_macos_icns_has_valid_png_frames_from_master():
    data = (ICONS / "icon.icns").read_bytes()
    assert data[:4] == b"icns"
    assert struct.unpack_from(">I", data, 4)[0] == len(data)
    frame_sizes = {
        b"ic07": 128, b"ic08": 256, b"ic09": 512, b"ic10": 1024,
        b"ic11": 32, b"ic12": 64, b"ic13": 256, b"ic14": 512,
    }
    seen = set()
    master = master_image()
    position = 8
    while position < len(data):
        chunk_type, length = struct.unpack_from(">4sI", data, position)
        assert 8 <= length <= len(data) - position
        if chunk_type in frame_sizes:
            with Image.open(io.BytesIO(data[position + 8:position + length])) as icon:
                assert icon.format == "PNG"
                assert_same_image(icon, expected(master, frame_sizes[chunk_type]))
            seen.add(chunk_type)
        position += length
    assert position == len(data)
    assert seen == set(frame_sizes)
