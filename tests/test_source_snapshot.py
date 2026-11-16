"""Guard against accidentally adding history, dumps or symlinks to source ZIPs."""
from __future__ import annotations

import pytest

from tools.create_source_snapshot import read_safe_source, validate_relative_path


@pytest.mark.parametrize("name", [
    "../outside.txt", "/absolute.txt", ".git/config", "node_modules/pkg/index.js",
    "romfs/model.bfres.zs", "audio/voice.bwav", "dump.nca", ".env.local", "keys/prod.keys",
    "release-preparation/old.zip", "local\\secret.txt", "C:/outside.txt", "bad\0name.txt",
])
def test_reject_unsafe_paths(name):
    with pytest.raises(ValueError):
        validate_relative_path(name)


def test_reject_binary_signature_and_symlinks(tmp_path):
    data = tmp_path / "apparently-harmless.txt"
    data.write_bytes(b"\x28\xb5\x2f\xfd\x00")
    with pytest.raises(ValueError, match="signature"):
        read_safe_source(tmp_path, data.name)
    data.write_bytes(b"ordinary source")
    assert read_safe_source(tmp_path, data.name)[0] == b"ordinary source"
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(data)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    with pytest.raises(ValueError, match="Symbolic link"):
        read_safe_source(tmp_path, link.name)
