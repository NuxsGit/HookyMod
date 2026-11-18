"""Keep the published synthetic examples consistent with both .hooky readers."""
from __future__ import annotations

import io
import json
import os
import re
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path

from hookycreator import modpackage, studio


ROOT = Path(__file__).resolve().parent.parent


def _blocks(section: str) -> list[str]:
    return re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)


def _zip(entries):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return result.getvalue()


def test_documented_workspace_example_is_a_real_non_playable_project():
    text = (ROOT / "docs" / "HOOKY_FORMAT.md").read_text(encoding="utf-8")
    section = text.split("### Archive et manifeste", 1)[1].split("### `workspace.json`", 1)[0]
    manifest, workspace = _blocks(section)
    archive = _zip([("manifest.json", manifest.encode("utf-8")), ("workspace.json", workspace.encode("utf-8"))])
    parsed = studio.read_archive(archive)
    assert parsed == json.loads(workspace)
    assert studio.check_workspace(parsed)["playable"] is False


def test_documented_mod_manifest_matches_actual_reference_output():
    text = (ROOT / "docs" / "HOOKY_FORMAT.md").read_text(encoding="utf-8")
    section = text.split("### Manifeste de mod", 1)[1].split("### Chemins de ressources", 1)[0]
    (french_raw,) = _blocks(section)
    english = (ROOT / "docs" / "HOOKY_FORMAT_EN.md").read_text(encoding="utf-8")
    (english_raw,) = _blocks(english)
    for raw in (french_raw, english_raw):
        expected = json.loads(raw)
        target = expected["target"]
        metadata = {key: expected[key] for key in ("id", "name", "mod_version", "author", "description")}
        metadata.update({key: target[key] for key in ("runtime_version", "game_version")})
        archive = modpackage.make_package(metadata, [("romfs/Example/hello.txt", b"Hello Hooky!\n")])
        actual, files = modpackage.read_package(archive)
        assert actual == expected
        assert files == {"romfs/Example/hello.txt": b"Hello Hooky!\n"}
        # An independent ZIP writer need not use the reference writer's JSON
        # ordering or whitespace conventions.
        independent = _zip([("manifest.json", raw.encode("utf-8")), ("romfs/Example/hello.txt", b"Hello Hooky!\n")])
        assert modpackage.read_package(independent) == (expected, files)


def test_documented_cli_examples_are_reproducible(tmp_path):
    env = dict(os.environ, PYTHONUTF8="1")
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    for filename in ("HOOKY_FORMAT.md", "HOOKY_FORMAT_EN.md"):
        text = (ROOT / "docs" / filename).read_text(encoding="utf-8")
        (commands,) = re.findall(r"```bash\n(.*?)\n```", text, re.DOTALL)
        if filename.endswith("_EN.md"):
            (manifest,) = _blocks(text)
        else:
            section = text.split("### Manifeste de mod", 1)[1].split("### Chemins de ressources", 1)[0]
            (manifest,) = _blocks(section)
        work = tmp_path / filename
        work.mkdir()
        results = []
        for command in commands.splitlines():
            argv = shlex.split(command)
            assert argv[0] == "python"
            result = subprocess.run([sys.executable, *argv[1:]], cwd=work, env=env,
                                    text=True, encoding="utf-8", capture_output=True, check=False)
            assert result.returncode == 0, result.stderr
            results.append(result)
        assert json.loads(results[2].stdout) == json.loads(manifest)
        assert (work / "exemple-extrait" / "romfs" / "Example" / "hello.txt").read_bytes() == b"Hello Hooky!\n"
