"""Version consistency and safe assembly of platform beta artifacts."""
from __future__ import annotations

import hashlib
import json
import plistlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import assemble_beta_release as release  # noqa: E402
import smoke_desktop_package as package_smoke  # noqa: E402
from hookycreator import __version__ as creator_version  # noqa: E402
from hookymod import __version__ as core_version  # noqa: E402


def cargo_version(path: Path, name: str, *, lock: bool) -> str:
    text = path.read_text(encoding="utf-8")
    heading = r"\[\[package\]\]" if lock else r"\[package\]"
    match = re.search(rf'{heading}\s*\nname = "{re.escape(name)}"\s*\nversion = "([^"]+)"', text)
    assert match, path
    return match.group(1)


def test_product_versions_are_consistent():
    config = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    version = config["version"]
    assert release.VERSION.fullmatch(version)
    assert {package["version"], lock["version"], lock["packages"][""]["version"],
            creator_version, core_version,
            cargo_version(ROOT / "src-tauri/Cargo.toml", "hookycreator-desktop", lock=False),
            cargo_version(ROOT / "src-tauri/Cargo.lock", "hookycreator-desktop", lock=True),
            cargo_version(ROOT / "rust/Cargo.toml", "hookymod-core", lock=False),
            cargo_version(ROOT / "rust/Cargo.lock", "hookymod-core", lock=True)} == {version}


def make_artifacts(root: Path) -> list[Path]:
    paths = []
    for name, extension, _ in release.INSTALLERS:
        folder = root / name
        folder.mkdir(parents=True)
        path = folder / f"package{extension}"
        path.write_bytes(f"fixture for {name}".encode())
        paths.append(path)
    return paths


def test_assemble_requires_every_platform_and_writes_checksums(tmp_path):
    artifacts, output = tmp_path / "artifacts", tmp_path / "release"
    make_artifacts(artifacts)
    files = release.assemble(artifacts, output, "11.26.1")
    assert [path.name for path in files] == [
        "HookyCreator-11.26.1-linux-x86_64.deb",
        "HookyCreator-11.26.1-windows-x86_64-setup.exe",
        "HookyCreator-11.26.1-macos-arm64.dmg",
        "HookyCreator-11.26.1-macos-x86_64.dmg",
        "SHA256SUMS.txt",
    ]
    expected = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                       for path in files[:-1])
    assert files[-1].read_text(encoding="utf-8") == expected
    with pytest.raises(FileExistsError):
        release.assemble(artifacts, output, "11.26.1")


@pytest.mark.parametrize("problem", ["missing", "duplicate", "empty", "bad-version"])
def test_incomplete_or_unsafe_inputs_do_not_create_a_release(tmp_path, problem):
    artifacts, output = tmp_path / "artifacts", tmp_path / "release"
    first = make_artifacts(artifacts)[0]
    version = "11.26.1"
    if problem == "missing":
        first.unlink()
    elif problem == "duplicate":
        first.with_name("second.deb").write_bytes(b"duplicate")
    elif problem == "empty":
        first.write_bytes(b"")
    else:
        version = "../unsafe"
    with pytest.raises(ValueError):
        release.assemble(artifacts, output, version)
    assert not output.exists()


def test_macos_bundle_uses_info_plist_and_requires_one_app(tmp_path):
    contents = tmp_path / "HookyCreator.app" / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as file:
        plistlib.dump({"CFBundleExecutable": "HookyCreator"}, file)
    app, backend, licenses = package_smoke.macos_bundle_paths(tmp_path)
    assert app == contents / "MacOS" / "HookyCreator"
    assert backend == contents / "MacOS" / "hookycreator-backend"
    assert licenses == contents / "Resources" / "licenses"
    (tmp_path / "Extra.app").mkdir()
    with pytest.raises(RuntimeError, match="Une seule application"):
        package_smoke.macos_bundle_paths(tmp_path)


def test_macos_bundle_rejects_executable_path_traversal(tmp_path):
    contents = tmp_path / "HookyCreator.app" / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as file:
        plistlib.dump({"CFBundleExecutable": "../outside"}, file)
    with pytest.raises(RuntimeError, match="CFBundleExecutable"):
        package_smoke.macos_bundle_paths(tmp_path)


def test_macos_dmg_is_detached_even_when_smoke_test_fails(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(package_smoke.subprocess, "run",
                        lambda args, **kwargs: calls.append((args, kwargs)))
    image, volume = tmp_path / "app.dmg", tmp_path / "mount"
    with pytest.raises(RuntimeError, match="failure"):
        with package_smoke.mounted_dmg(image, volume):
            raise RuntimeError("failure")
    assert calls[0][0][:4] == ["hdiutil", "attach", "-readonly", "-nobrowse"]
    assert calls[0][1]["input"] == "Y\n" and calls[0][1]["text"] is True
    assert calls[1][0] == ["hdiutil", "detach", str(volume)]
