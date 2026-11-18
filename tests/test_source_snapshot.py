"""Guard against accidentally adding history, dumps or symlinks to source ZIPs."""
from __future__ import annotations

import zipfile

import pytest

from tools import create_source_snapshot as snapshot
from tools.create_source_snapshot import read_safe_source, validate_relative_path


@pytest.mark.parametrize("name", [
    "../outside.txt", "/absolute.txt", ".git/config", "node_modules/pkg/index.js",
    "romfs/model.bfres.zs", "exefs/main", "audio/voice.bwav", "dump.nca", "game.nso",
    "payload.bin", "loader.exe", "archive.zip", "script.byml", ".env.local", "keys/prod.keys",
    "release-preparation/old.zip", "local\\secret.txt", "C:/outside.txt", "bad\0name.txt",
])
def test_reject_unsafe_paths(name):
    with pytest.raises(ValueError):
        validate_relative_path(name)


def test_reject_binary_signature_and_symlinks(tmp_path):
    data = tmp_path / "apparently-harmless.txt"
    for magic in (b"\x28\xb5\x2f\xfd", b"NUP\x01", b"NSO0", b"BNTX", b"FRES",
                  b"\x7fELF", b"MZ", b"PK\x03\x04", b"\x1a\x45\xdf\xa3"):
        data.write_bytes(magic + b"contents")
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


def test_known_notices_and_logo_variants_are_required():
    required = {
        "LICENSE", "THIRD_PARTY_NOTICES.md", "docs/PROVENANCE.md",
        "docs/licenses/NUP_EXPLORER_MIT.txt", "hookycreator/web/shaders/LICENSE-FSR1.txt",
        "rust/vendor/README.md", "rust/vendor/ruzstd/LICENSE", "rust/vendor/twox-hash/LICENSE",
        "rust/vendor/hmac-sha256/LICENSE", "assets/HookyMod-logo.png",
        "hookycreator/web/HookyMod.png", "src-tauri/desktop/HookyMod.png",
        "src-tauri/icons/32x32.png", "src-tauri/icons/128x128.png",
        "src-tauri/icons/128x128@2x.png", "src-tauri/icons/icon.ico", "src-tauri/icons/icon.icns",
    }
    assert required <= snapshot.REQUIRED_FILES


def test_snapshot_excludes_deleted_and_unselected_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "README.md").write_text("source")
    (tmp_path / "new.py").write_text("new source")
    (tmp_path / "unselected.py").write_text("not exported")
    output = tmp_path / "output" / "snapshot.zip"
    paths = {
        ("--cached",): {"README.md", "removed.py"},
        ("--deleted",): {"removed.py"},
        ("--others", "--exclude-standard"): {"new.py", "unselected.py"},
    }
    monkeypatch.setattr(snapshot, "ROOT", tmp_path)
    monkeypatch.setattr(snapshot, "OUTPUT", output)
    monkeypatch.setattr(snapshot, "REQUIRED_FILES", {"README.md"})
    monkeypatch.setattr(snapshot, "git_paths", lambda *args: paths[args])

    assert snapshot.main(["--include-untracked", "new.py"]) == 0
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"HookyMod-source/README.md", "HookyMod-source/new.py"}
    assert "1 tracked file(s) deleted" in capsys.readouterr().err
    with pytest.raises(ValueError, match="deleted from the working tree"):
        snapshot.main(["--include-untracked", "removed.py"])

    monkeypatch.setattr(snapshot, "REQUIRED_FILES", {"README.md", "removed.py"})
    with pytest.raises(ValueError, match="Required files missing"):
        snapshot.main([])
