"""DMG cleanup tolerates a briefly busy volume but never hides failure."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
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
    mount = tmp_path / "volume"
    errors = [detach_error(mount, 'hdiutil: couldn\'t unmount "disk2" - Resource busy')
              for _ in range(busy_count)]
    with patch.object(package_smoke.subprocess, "run", side_effect=[*errors, None]) as run, \
         patch.object(package_smoke.time, "sleep") as sleep:
        package_smoke.detach_dmg(mount)
    assert run.call_count == busy_count + 1
    for invocation in run.call_args_list:
        assert invocation.args[0] == ["hdiutil", "detach", str(mount)]
        assert invocation.kwargs["check"] is True
        assert "-force" not in invocation.args[0]
    assert sleep.call_args_list == [call(2 * (i + 1)) for i in range(busy_count)]


def test_detach_busy_failure_remains_a_failure(tmp_path):
    mount = tmp_path / "volume"
    error = detach_error(mount, "Resource busy")
    with patch.object(package_smoke.subprocess, "run", side_effect=error) as run, \
         patch.object(package_smoke.time, "sleep") as sleep:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            package_smoke.detach_dmg(mount)
    assert exc.value is error
    assert run.call_count == 4
    assert sleep.call_args_list == [call(2), call(4), call(6)]


def test_detach_other_errors_are_not_retried(tmp_path):
    mount = tmp_path / "volume"
    error = detach_error(mount, "not a disk image")
    with patch.object(package_smoke.subprocess, "run", side_effect=error) as run, \
         patch.object(package_smoke.time, "sleep") as sleep:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            package_smoke.detach_dmg(mount)
    assert exc.value is error
    assert run.call_count == 1
    sleep.assert_not_called()
