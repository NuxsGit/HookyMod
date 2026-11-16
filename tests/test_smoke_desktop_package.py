"""DMG cleanup tolerates a briefly busy volume but never hides failure."""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import smoke_desktop_package as package_smoke  # noqa: E402


def detach_error(mount, message):
    return subprocess.CalledProcessError(
        1, ["hdiutil", "detach", str(mount)], stderr=message
    )


@pytest.mark.parametrize("busy_count", [1, 2, 3])
def test_detach_retries_only_briefly_busy_volumes(tmp_path, busy_count):
    mount, image = tmp_path / "volume", tmp_path / "app.dmg"
    errors = [detach_error(mount, 'hdiutil: couldn\'t unmount "disk2s1" - Resource busy')
              for _ in range(busy_count)]
    with patch.object(package_smoke.subprocess, "run", side_effect=[*errors, None]) as run, \
         patch.object(package_smoke, "_dmg_attached", return_value=True) as attached, \
         patch.object(package_smoke.time, "sleep") as sleep:
        package_smoke.detach_dmg(mount, image)
    assert run.call_count == busy_count + 1
    assert run.call_args_list[0].args[0] == ["hdiutil", "detach", str(mount)]
    for invocation in run.call_args_list[1:]:
        assert invocation.args[0] == ["hdiutil", "detach", "/dev/disk2"]
        assert invocation.kwargs["check"] is True
        assert "-force" not in invocation.args[0]
    assert attached.call_args_list == [call("/dev/disk2", image)] * busy_count
    assert sleep.call_args_list == [call(2 * (i + 1)) for i in range(busy_count)]


def test_detach_busy_failure_remains_a_failure(tmp_path):
    mount = tmp_path / "volume"
    error = detach_error(mount, "Resource busy")
    with patch.object(package_smoke.subprocess, "run", side_effect=error) as run, \
         patch.object(package_smoke.time, "sleep") as sleep:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            package_smoke.detach_dmg(mount, tmp_path / "app.dmg")
    assert exc.value is error
    assert run.call_count == 4
    assert sleep.call_args_list == [call(2), call(4), call(6)]


def test_detach_other_errors_are_not_retried(tmp_path):
    mount = tmp_path / "volume"
    error = detach_error(mount, "not a disk image")
    with patch.object(package_smoke.subprocess, "run", side_effect=error) as run, \
         patch.object(package_smoke.time, "sleep") as sleep:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            package_smoke.detach_dmg(mount, tmp_path / "app.dmg")
    assert exc.value is error
    assert run.call_count == 1
    sleep.assert_not_called()


def test_detach_accepts_a_missing_device_only_after_verified_ejection(tmp_path):
    mount, image = tmp_path / "volume", tmp_path / "app.dmg"
    busy = detach_error(mount, 'hdiutil: couldn\'t eject "disk2" - Resource busy')
    missing = detach_error(mount, "hdiutil: detach failed - No such file or directory")
    with patch.object(package_smoke.subprocess, "run", side_effect=[busy, missing]) as run, \
         patch.object(package_smoke, "_dmg_attached", side_effect=[True, False]) as attached, \
         patch.object(package_smoke.time, "sleep"):
        package_smoke.detach_dmg(mount, image)
    assert run.call_args_list[1].args[0] == ["hdiutil", "detach", "/dev/disk2"]
    assert attached.call_args_list == [call("/dev/disk2", image)] * 2
    # A missing mount/device is not success if the original image is still in
    # hdiutil's inventory, or if no device was identified from a busy error.
    with patch.object(package_smoke.subprocess, "run", side_effect=[busy, missing]), \
         patch.object(package_smoke, "_dmg_attached", return_value=True), \
         patch.object(package_smoke.time, "sleep"):
        with pytest.raises(subprocess.CalledProcessError):
            package_smoke.detach_dmg(mount, image)
    with patch.object(package_smoke.subprocess, "run", side_effect=missing):
        with pytest.raises(subprocess.CalledProcessError):
            package_smoke.detach_dmg(mount, image)


def test_busy_then_ejected_image_is_verified_against_hdiutil_inventory(tmp_path):
    mount, image = tmp_path / "volume", tmp_path / "app.dmg"
    busy = detach_error(mount, 'hdiutil: couldn\'t eject "disk2" - Resource busy')
    missing = detach_error(mount, "hdiutil: detach failed - No such file or directory")
    attached = {"images": [{"image-path": str(image),
                            "system-entities": [{"dev-entry": "/dev/disk2"}]}]}
    replies = [busy, SimpleNamespace(stdout=plistlib.dumps(attached)), missing,
               SimpleNamespace(stdout=plistlib.dumps({"images": []}))]
    with patch.object(package_smoke.subprocess, "run", side_effect=replies) as run, \
         patch.object(package_smoke.time, "sleep"):
        package_smoke.detach_dmg(mount, image)
    assert [entry.args[0] for entry in run.call_args_list] == [
        ["hdiutil", "detach", str(mount)], ["hdiutil", "info", "-plist"],
        ["hdiutil", "detach", "/dev/disk2"], ["hdiutil", "info", "-plist"]]


def test_dmg_inventory_requires_the_original_image_and_well_formed_plist(tmp_path):
    image, other = tmp_path / "app.dmg", tmp_path / "other.dmg"
    data = {"images": [{"image-path": str(image),
                        "system-entities": [{"dev-entry": "/dev/disk2"},
                                            {"dev-entry": "/dev/disk2s1", "mount-point": str(tmp_path)}]}]}
    with patch.object(package_smoke.subprocess, "run", return_value=SimpleNamespace(stdout=plistlib.dumps(data))) as run:
        assert package_smoke._dmg_attached("/dev/disk2", image)
        assert not package_smoke._dmg_attached("/dev/disk3", other)
        with pytest.raises(RuntimeError, match="autre image"):
            package_smoke._dmg_attached("/dev/disk2", other)
        with pytest.raises(RuntimeError, match="périphérique inattendu"):
            package_smoke._dmg_attached("/dev/disk3", image)
    assert run.call_args.args[0] == ["hdiutil", "info", "-plist"]
    # The whole-disk entry can disappear before its partition is ejected.
    data["images"][0]["system-entities"] = [{"dev-entry": "/dev/disk2s1"}]
    with patch.object(package_smoke.subprocess, "run", return_value=SimpleNamespace(stdout=plistlib.dumps(data))):
        assert package_smoke._dmg_attached("/dev/disk2", image)
    for invalid in (b"bad plist", plistlib.dumps({}), plistlib.dumps({"images": ["invalid"]}),
                    plistlib.dumps({"images": [{"system-entities": ["invalid"]}]})):
        with patch.object(package_smoke.subprocess, "run", return_value=SimpleNamespace(stdout=invalid)):
            with pytest.raises(RuntimeError, match="Impossible de vérifier"):
                package_smoke._dmg_attached("/dev/disk2", image)
