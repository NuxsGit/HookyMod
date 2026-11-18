"""WebM header checks, range streaming and safe working-copy replacement."""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from hookycreator.server import HookyCreatorServer
from hookymod import video


def webm(payload=b"some fake track data"):
    # EBML header with DocType=webm and unknown-size Segment. It is only a
    # container-header fixture, deliberately NOT a decodable video.
    return b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm\x18\x53\x80\x67\xff" + payload


def test_generated_video_fixture_has_the_documented_hash_and_webm_header():
    from tests.synth_video import make_webm

    data = make_webm()
    assert hashlib.sha256(data).hexdigest() == (
        "df0670266ba2488c92e453ffb633c2f5c4abbeead2636467b973c2b29daf3f13"
    )
    assert b"V_VP8" in data
    video.check_header(data, len(data))


def test_header_check_rejects_non_webm_and_truncation():
    data = webm()
    video.check_header(data, len(data))
    for bad in (b"", b"NDVE" + data, data[:10], data.replace(b"webm", b"test"),
                data.replace(b"\x18\x53\x80\x67", b"NOPE")):
        with pytest.raises(video.VideoError):
            video.check_header(bad, len(bad))
    with pytest.raises(video.VideoError):
        video.check_header(data, video.MAX_STREAM + 1)


def test_import_writes_a_copy_without_mutating_source_and_limits(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    original = webm(b"original")
    source = root / "intro.webm"; source.write_bytes(original)
    server = HookyCreatorServer(root, tmp_path / "work", host="127.0.0.1", port=0)
    assert [a.kind for a in server.project.by_kind("video")] == ["video"]
    info = server.project.video_info("intro.webm")
    assert info["format"] == "WebM" and not info["edited"]
    replacement = webm(b"replacement")
    body = {"path": "intro.webm", "video": base64.b64encode(replacement).decode()}
    result, _ = server.api("POST", "/api/video/import", {}, body)
    assert json.loads(result)["size"] == len(replacement)
    assert source.read_bytes() == original
    assert (tmp_path / "work" / "assets" / "intro.webm").read_bytes() == replacement
    assert server.project.video_info("intro.webm")["edited"]
    assert "intro.webm" in server.project.edited_files()
    assert not (root / "mods").exists()
    assert source.read_bytes() == original
    for value in ("!", base64.b64encode(b"not video").decode()):
        with pytest.raises(Exception):
            server.api("POST", "/api/video/import", {}, {"path": "intro.webm", "video": value})
        assert source.read_bytes() == original
        assert (tmp_path / "work" / "assets" / "intro.webm").read_bytes() == replacement
    with pytest.raises(Exception):
        server.project.video_info("../intro.webm")
    with pytest.raises(Exception):
        server.project.import_video("intro.webm", b"a" * (video.MAX_IMPORT + 1))


def test_stream_supports_standard_ranges_and_rejects_invalid_ranges(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    data = webm(b"0123456789abcdefghijklmnopqrstuvwxyz")
    (root / "intro.webm").write_bytes(data)
    server = HookyCreatorServer(root, tmp_path / "work", host="127.0.0.1", port=0).start()
    def get(range_=None, path="intro.webm"):
        request = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/video/stream?path={path}",
                                         headers={"Range": range_} if range_ else {})
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()
    try:
        status, headers, body = get()
        assert status == 200 and body == data and headers["Content-Type"] == "video/webm"
        assert headers["Accept-Ranges"] == "bytes"
        status, headers, body = get("bytes=4-12")
        assert status == 206 and body == data[4:13]
        assert headers["Content-Range"] == f"bytes 4-12/{len(data)}"
        assert get("bytes=-5")[2] == data[-5:]
        assert get("bytes=8-")[2] == data[8:]
        for value in ("bytes=999-", "bytes=-0", "bytes=3-1", "bytes=1-3,5-6", "bytes=" + "9" * 30 + "-"):
            assert get(value)[0] == 416
        assert get(path="../intro.webm")[0] == 400
    finally:
        server.stop()
