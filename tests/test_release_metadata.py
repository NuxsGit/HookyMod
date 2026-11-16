"""Ensure the project license and documented third-party notices travel together."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import smoke_desktop_package as package_smoke  # noqa: E402


def test_project_license_metadata_and_bundle_resources():
    config = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    bundle = config["bundle"]
    assert bundle["license"] == "LicenseRef-HookyMod-NonCommercial-1.0"
    assert bundle["licenseFile"] == "../LICENSE"
    for path in package_smoke.NOTICE_PATHS:
        assert (ROOT / path).is_file()
        assert bundle["resources"][f"../{path}"] == f"licenses/{path}"
    for manifest in (ROOT / "rust/Cargo.toml", ROOT / "src-tauri/Cargo.toml"):
        text = manifest.read_text(encoding="utf-8")
        assert 'license-file = "../LICENSE"' in text
        assert 'license = "MIT"' not in text


def test_package_check_detects_missing_and_altered_notices(tmp_path):
    for path in package_smoke.NOTICE_PATHS:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    package_smoke.check_notices(tmp_path)
    notice = tmp_path / package_smoke.NOTICE_PATHS[-1]
    notice.write_text("altered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="absente ou altérée"):
        package_smoke.check_notices(tmp_path)
    notice.unlink()
    with pytest.raises(RuntimeError, match="absente ou altérée"):
        package_smoke.check_notices(tmp_path)
