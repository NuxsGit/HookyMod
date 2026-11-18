"""The standalone app keeps asset/Player components but not maker code."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.check_desktop_bundle import REQUIRED, archive_entries, verify_entries


def test_required_entries_allow_native_windows_separators():
    entries = {name.replace("/", "\\") for name in REQUIRED}
    verify_entries(entries | {"hookymod.viewer", "unrelated/module"})


@pytest.mark.parametrize("name", [
    "modmaker", "modmaker.studio", "modmaker/legacy_web/views.html",
    "hookycreator.modpackage", "hookycreator/studio.py", "hookycreator/web/studio.js",
    "hookymod.app", "hookymod.cli", "hookymod.__main__", "hookymod.translate",
    "hookymod\\translate.py",
])
def test_maker_and_separate_cli_entries_are_rejected(name):
    with pytest.raises(ValueError, match="interdits"):
        verify_entries(set(REQUIRED) | {name})


def test_missing_player_or_server_components_are_rejected():
    for name in ("hookycreator/web/player.js", "hookycreator.server", "hookymod.animation"):
        with pytest.raises(ValueError, match="absents"):
            verify_entries(set(REQUIRED) - {name})


def test_archive_listing_runs_viewer_and_ignores_headings(tmp_path):
    binary = tmp_path / "sidecar"
    binary.write_bytes(b"placeholder, viewer is mocked")
    output = "Contents of 'sidecar' (PKG/CArchive):\n hookycreator/web/index.html\nContents of 'PYZ.pyz' (PYZ):\n hookycreator.server\n"
    result = SimpleNamespace(returncode=0, stdout=output, stderr="")
    with patch("tools.check_desktop_bundle.subprocess.run", return_value=result) as run:
        assert archive_entries(binary) == {"hookycreator/web/index.html", "hookycreator.server"}
    assert run.call_args.args[0][-3:] == ["--recursive", "--brief", str(binary)]


def test_archive_inspection_failure_is_not_ignored(tmp_path):
    binary = tmp_path / "sidecar"
    binary.touch()
    with patch("tools.check_desktop_bundle.subprocess.run", return_value=SimpleNamespace(
            returncode=1, stdout="", stderr="broken archive")), \
            pytest.raises(RuntimeError, match="broken archive"):
        archive_entries(binary)
