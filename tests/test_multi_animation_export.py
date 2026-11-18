"""Export groupé dans un GLB autonome ; fixtures synthétiques uniquement."""
from __future__ import annotations

import concurrent.futures
import copy
import itertools
import json
import os
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from hookycreator import project as project_mod
from hookycreator.project import Project, ProjectError
from hookycreator.server import HookyCreatorServer
from hookymod import blender, gltf_animation
from tests import synth
from tests.synth_animation import (
    fska_fixture,
    fska_two_clips_with_jump,
    named_rigid_model,
)
from tests.test_animated_export import accessor, glb_parts, simple_binding


def make_project(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "Rigid.bfres").write_bytes(named_rigid_model())
    project = Project(root, tmp_path / "work")
    return project


def download_path(project, result):
    params = urllib.parse.parse_qs(urllib.parse.urlparse(result["download_url"]).query)
    assert params["kind"] == ["glb"]
    return project.export_download(params["id"][0], "glb")


def with_name(name: str, *, data=None):
    encoded = name.encode("utf-8")
    assert len(encoded) <= 12
    data = bytearray(data or fska_fixture())
    struct.pack_into("<H", data, 0x280, len(encoded))
    data[0x282:0x282 + len(encoded)] = encoded
    return bytes(data)


def independent_bindings():
    """Three clips affect different bone/channel pairs on the same skin."""
    mesh, skin, move = simple_binding(bones=2)
    move["name"] = "Move"
    curve = move["bones"][0]["curves"][0]
    curve["values"], curve["slopes"] = [0., 3.], [.3, .3]
    spin = copy.deepcopy(move)
    spin["name"] = "Spin"
    curve = spin["bones"][0]["curves"][0]
    curve["target"], curve["values"], curve["slopes"] = "rotation.z", [0., 1.], [.1, .1]
    bounce = copy.deepcopy(move)
    bounce["name"], bounce["bones"][0]["name"] = "Bounce", "Bone1"
    curve = bounce["bones"][0]["curves"][0]
    curve["target"], curve["values"], curve["slopes"] = "translation.y", [0., 2.], [.2, .2]
    return mesh, skin, [gltf_animation.bind(skin, clip) for clip in (move, spin, bounce)]


def with_missing_track():
    data = bytearray(fska_fixture())
    track = bytes(data[0x300:0x338])
    base = bytes(data[0x360:0x388])
    data[0x580:0x5a8] = base
    data[0x5b0:0x5d8] = base
    struct.pack_into("<Q", data, 0x310, 0x580)
    data[0x338:0x370] = track
    struct.pack_into("<QQQ", data, 0x338, 0x2b0, 0x400, 0x5b0)
    struct.pack_into("<H", data, 0x2b0, 5)
    data[0x2b2:0x2b7] = b"Ghost"
    struct.pack_into("<i", data, 0x144, 4)
    struct.pack_into("<H", data, 0x14c, 2)
    return bytes(data)


def test_one_glb_shared_skin_each_clip_once_embedded_textures_and_snapshots(tmp_path):
    project = make_project(tmp_path)
    two = fska_two_clips_with_jump()
    (project.root / "Rigid.anim.bfres").write_bytes(two)
    (project.root / "Rigid_Copy.anim.bfres").write_bytes(two)
    (project.root / "Outside.anim.bfres").write_bytes(with_name("Dash"))
    # Real per-material references in the synthetic model; the direct GLB must
    # contain the associated images without needing any neighboring PNG.
    for name in ("Tex_A_Alb", "Tex_B_Alb"):
        (project.root / f"{name}.bntx").write_bytes(
            synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8)))
    project.scan()
    source_before = {p.name: p.read_bytes() for p in project.root.iterdir()}
    static = project.export_model("Rigid.bfres", textures=False, archive=True)
    static_glb = Path(static["absolute_folder"]) / "Rigid.glb"
    old_static = static_glb.read_bytes()
    result = project.export_model("Rigid.bfres", all_animations=True, archive=True,
                                  animation_rel="./Outside.anim.bfres")
    group = result["animations"]
    assert group["source_files"] == ["Outside.anim.bfres", "Rigid.anim.bfres", "Rigid_Copy.anim.bfres"]
    assert group["source_clips"] == 5 and len(group["exported"]) == 3
    assert len(group["duplicates"]) == 2 and not group["skipped"] and not group["partial"]
    assert result["download_name"] == "Rigid-animations.glb"
    assert set(result["files"]) == {"Rigid-animations.glb", "RAPPORT_ANIMATIONS.txt"}
    standalone = download_path(project, result).read_bytes()
    doc, blob = glb_parts(standalone)
    assert [clip["name"] for clip in doc["animations"]] == ["Dash", "Walk", "Run"]
    assert len(doc["skins"]) == 1 and len(doc["skins"][0]["joints"]) == 2
    joints = set(doc["skins"][0]["joints"])
    assert {img["name"] for img in doc["images"]} == {"Tex_A_Alb.png", "Tex_B_Alb.png"}
    assert all("uri" not in img and img["mimeType"] == "image/png" for img in doc["images"])
    assert static_glb.read_bytes() == old_static  # separate output does not overwrite the ZIP export
    for clip in doc["animations"]:
        assert all(c["target"]["node"] in joints for c in clip["channels"])
        assert clip["extras"]["sourceFile"] in group["source_files"]
        for sampler in clip["samplers"]:
            times = [t[0] for t in accessor(doc, blob, sampler["input"])]
            assert all(a < b for a, b in itertools.pairwise(times))
            assert times[-1] == pytest.approx(10 / 30)  # no repeated loop
    for name, expected in (("Walk", 4.), ("Run", -4.)):
        clip = next(c for c in doc["animations"] if c["name"] == name)
        channel = next(c for c in clip["channels"] if c["target"]["path"] == "translation")
        sampler = clip["samplers"][channel["sampler"]]
        assert accessor(doc, blob, sampler["output"])[-1][0] == pytest.approx(expected)
    report = (Path(result["absolute_folder"]) / "RAPPORT_ANIMATIONS.txt").read_text(encoding="utf-8")
    assert "Occurrences identiques regroupées" in report and "saut" in report
    assert source_before == {p.name: p.read_bytes() for p in project.root.iterdir()}
    # A later export must not mutate the already issued direct download.
    (project.root / "Outside.anim.bfres").write_bytes(with_name("Jump"))
    again = project.export_model("Rigid.bfres", all_animations=True, archive=True,
                                 animation_rel="Outside.anim.bfres")
    assert result["download_url"] != again["download_url"]
    assert download_path(project, result).read_bytes() == standalone
    assert [a["name"] for a in glb_parts(download_path(project, again).read_bytes())[0]["animations"]] == ["Jump", "Walk", "Run"]


def test_all_candidates_including_after_the_32_shown_in_the_preview(tmp_path):
    project = make_project(tmp_path)
    for index in range(35):
        (project.root / f"Rigid_{index:02}.anim.bfres").write_bytes(with_name(f"C{index:03}"))
    project.scan()
    info = project.model_info("Rigid.bfres")
    assert info["animation_count"] == 35 and len(info["animations"]) == 32
    result = project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    doc, _ = glb_parts(download_path(project, result).read_bytes())
    assert len(result["animations"]["source_files"]) == 35
    assert [clip["name"] for clip in doc["animations"]] == [f"C{i:03}" for i in range(35)]
    assert not result["animations"]["partial"]


def test_distinct_homonyms_are_not_deduplicated_and_exact_copies_are_reported(tmp_path):
    project = make_project(tmp_path)
    original = fska_fixture()
    different = bytearray(original)
    struct.pack_into("<f", different, 0x528, 9.)
    (project.root / "Rigid_A.anim.bfres").write_bytes(original)
    (project.root / "Rigid_B.anim.bfres").write_bytes(bytes(different))
    (project.root / "Rigid_C.anim.bfres").write_bytes(original)
    project.scan()
    result = project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    group = result["animations"]
    assert [item["export_name"] for item in group["exported"]] == ["Walk", "Walk (2)"]
    assert [(item["path"], item["same_as"]["path"]) for item in group["duplicates"]] == [
        ("Rigid_C.anim.bfres", "Rigid_A.anim.bfres")]
    doc, blob = glb_parts(download_path(project, result).read_bytes())
    assert [a["name"] for a in doc["animations"]] == ["Walk", "Walk (2)"]
    values = []
    for clip in doc["animations"]:
        channel = next(c for c in clip["channels"] if c["target"]["path"] == "translation")
        values.append(accessor(doc, blob, clip["samplers"][channel["sampler"]]["output"])[-1][0])
    assert values == pytest.approx([2., 9.])


def test_partial_export_reports_every_incompatible_clip_file_and_missing_track(tmp_path):
    project = make_project(tmp_path)
    two = bytearray(fska_two_clips_with_jump())
    struct.pack_into("<I", two, 0x154, 0x3004)  # Run has an unknown rotation mode
    (project.root / "Rigid_A.anim.bfres").write_bytes(two)
    (project.root / "Rigid_B.anim.bfres").write_bytes(b"broken BFRES")
    missing = bytearray(fska_fixture())
    missing[0x292:0x296] = b"Nope"  # no animated bone corresponds to the skin
    (project.root / "Rigid_C.anim.bfres").write_bytes(bytes(missing))
    (project.root / "Rigid_D.anim.bfres").write_bytes(with_missing_track())
    project.scan()
    result = project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    group = result["animations"]
    assert group["partial"] and group["source_clips"] == 4
    assert [(item["path"], item["index"]) for item in group["skipped"]] == [
        ("Rigid_A.anim.bfres", 1), ("Rigid_B.anim.bfres", None), ("Rigid_C.anim.bfres", 0)]
    assert "rotation" in group["skipped"][0]["reason"]
    assert "Aucun os animé" in group["skipped"][2]["reason"]
    assert group["exported"][1]["missing"] == ["Ghost"]
    assert any("Export partiel" in warning for warning in result["warnings"])
    report = (Path(result["absolute_folder"]) / "RAPPORT_ANIMATIONS.txt").read_text(encoding="utf-8")
    for item in group["skipped"]:
        assert item["path"] in report and item["reason"] in report
    assert "Ghost" in report and "PARTIEL" in report
    doc, _ = glb_parts(download_path(project, result).read_bytes())
    assert [item["name"] for item in doc["animations"]] == ["Walk", "Walk (2)"]


def test_no_compatible_clips_refuses_without_overwriting_previous_export(tmp_path):
    project = make_project(tmp_path)
    path = project.root / "Rigid.anim.bfres"
    path.write_bytes(fska_two_clips_with_jump())
    project.scan()
    previous = project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    folder = Path(previous["absolute_folder"])
    before = {name: (folder / name).read_bytes() for name in previous["files"]}
    data = bytearray(fska_two_clips_with_jump())
    struct.pack_into("<I", data, 0x104, 0x3004)
    struct.pack_into("<I", data, 0x154, 0x3004)
    path.write_bytes(data)
    with pytest.raises(ProjectError, match="Aucun clip compatible") as error:
        project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    assert "Walk (index 0)" in str(error.value) and "Run (index 1)" in str(error.value)
    assert before == {name: (folder / name).read_bytes() for name in previous["files"]}
    assert download_path(project, previous).read_bytes() == before["Rigid-animations.glb"]


def test_sampling_budget_is_shared_between_clips_and_reports_the_failing_clip(tmp_path, monkeypatch):
    mesh, skin, first = simple_binding()
    first["rel"], first["index"] = "one.anim.bfres", 0
    second = copy.deepcopy(first)
    second.update(name="Second", rel="two.anim.bfres", index=1)
    first_binding = gltf_animation.bind(skin, first)
    second_binding = gltf_animation.bind(skin, second)
    # Each simple clip uses two samples; three would be enough for either clip
    # alone but must not be silently reset for every clip in a group.
    monkeypatch.setattr(gltf_animation, "MAX_SAMPLES", 3)
    target = tmp_path / "many.glb"
    target.write_bytes(b"previous")
    with pytest.raises(gltf_animation.ExportAnimationError, match="Second.*two.anim.bfres.*échantillons|Second.*images-clés"):
        blender.export_bundle([mesh], tmp_path, "many", formats=("glb",),
                              animations=iter((first_binding, second_binding)))
    assert target.read_bytes() == b"previous"
    monkeypatch.setattr(gltf_animation, "MAX_SAMPLES", 1_000_000)
    monkeypatch.setattr(gltf_animation, "MAX_CHANNELS", 1)
    with pytest.raises(gltf_animation.ExportAnimationError, match="Second.*Trop de canaux"):
        blender.export_bundle([mesh], tmp_path, "many", formats=("glb",),
                              animations=iter((first_binding, second_binding)))
    assert target.read_bytes() == b"previous"


def test_grouped_actions_restore_unkeyed_channels_and_obey_shared_budgets(tmp_path, monkeypatch):
    mesh, _skin, bindings = independent_bindings()
    target = tmp_path / "independent.glb"
    blender.export_bundle([mesh], tmp_path, "independent", formats=("glb",), animations=bindings)
    doc, blob = glb_parts(target.read_bytes())
    nodes = {node["name"]: i for i, node in enumerate(doc["nodes"])}
    originals = {"Move": (nodes["Bone0"], "translation"), "Spin": (nodes["Bone0"], "rotation"),
                 "Bounce": (nodes["Bone1"], "translation")}
    union = set(originals.values())
    original_samples = 0
    constants = {}
    for item in doc["animations"]:
        channels = {(channel["target"]["node"], channel["target"]["path"]): channel
                    for channel in item["channels"]}
        assert set(channels) == union
        for key, channel in channels.items():
            sampler = item["samplers"][channel["sampler"]]
            if key == originals[item["name"]]:
                original_samples += doc["accessors"][sampler["input"]]["count"]
            else:
                assert accessor(doc, blob, sampler["input"]) == [(0.,)]
                assert accessor(doc, blob, sampler["output"])[0] == pytest.approx(doc["nodes"][key[0]][key[1]])
                if key in constants:
                    assert constants[key] == (sampler["input"], sampler["output"])
                constants[key] = (sampler["input"], sampler["output"])
    # Both added channels and samples count against the global limits. A
    # rejected conversion must leave the previously published GLB untouched.
    before = target.read_bytes()
    for setting, limit in (("MAX_CHANNELS", 5), ("MAX_SAMPLES", original_samples)):
        with monkeypatch.context() as patcher:
            patcher.setattr(gltf_animation, setting, limit)
            with pytest.raises(gltf_animation.ExportAnimationError, match="isoler les poses"):
                blender.export_bundle([mesh], tmp_path, "independent", formats=("glb",), animations=bindings)
        assert target.read_bytes() == before


def test_bounds_and_source_mutations_refuse_without_publishing_partial_glb(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    first = project.root / "Rigid_A.anim.bfres"
    second = project.root / "Rigid_B.anim.bfres"
    first.write_bytes(fska_two_clips_with_jump())
    second.write_bytes(with_name("Dash"))
    project.scan()
    result = project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    target = Path(result["absolute_folder"]) / "Rigid-animations.glb"
    before = target.read_bytes()
    for setting, limit in (("MAX_ANIMATION_EXPORT_FILES", 1), ("MAX_ANIMATION_EXPORT_CLIPS", 1),
                           ("MAX_ANIMATION_EXPORT_BYTES", 2), ("MAX_ANIMATION_EXPORT_KEYS", 1),
                           ("MAX_ANIMATION_EXPORT_TRACKS", 1), ("MAX_ANIMATION_EXPORT_METADATA", 1)):
        with monkeypatch.context() as patcher:
            patcher.setattr(project_mod, setting, limit)
            with pytest.raises(ProjectError, match="Trop"):
                project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
        assert target.read_bytes() == before
    export = blender.export_bundle
    def mutate(*args, **kwargs):
        written = export(*args, **kwargs)
        replacement = second.with_name("replacement.tmp")
        replacement.write_bytes(with_name("Jump"))
        os.replace(replacement, second)
        return written
    with patch.object(blender, "export_bundle", side_effect=mutate), pytest.raises(ProjectError, match="a changé"):
        project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    assert target.read_bytes() == before
    # A file that changes before its turn is decoded must also be detected.
    read_clip = project_mod.animation_mod.read_clip
    changed = False
    def mutate_before_second(*args, **kwargs):
        nonlocal changed
        clip = read_clip(*args, **kwargs)
        if not changed:
            changed = True
            replacement = second.with_name("replacement.tmp")
            replacement.write_bytes(with_name("Other"))
            os.replace(replacement, second)  # identity changes even on coarse-timestamp filesystems
        return clip
    with patch.object(project_mod.animation_mod, "read_clip", side_effect=mutate_before_second), \
            pytest.raises(ProjectError, match="Rigid_B.*a changé"):
        project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    assert target.read_bytes() == before


def test_incomplete_scan_and_concurrent_downloads_are_explicit_and_isolated(tmp_path):
    project = make_project(tmp_path)
    (project.root / "Rigid.anim.bfres").write_bytes(fska_two_clips_with_jump())
    project.scan()
    project.scan_truncated = True
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(project.export_model, "Rigid.bfres", False,
                                   all_animations=True, archive=True) for _ in range(3)]
        results = [f.result(timeout=30) for f in futures]
    assert len({r["download_url"] for r in results}) == 3
    for result in results:
        assert result["animations"]["partial"] and result["animations"]["scan_incomplete"]
        assert any("Inventaire incomplet" in warning for warning in result["warnings"])
        assert [a["name"] for a in glb_parts(download_path(project, result).read_bytes())[0]["animations"]] == ["Walk", "Run"]


def test_api_and_direct_http_download_use_glb_mime_and_reject_confusion(tmp_path):
    project = make_project(tmp_path)
    (project.root / "Rigid.anim.bfres").write_bytes(fska_two_clips_with_jump())
    project.scan()
    server = HookyCreatorServer(project.root, project.work, host="127.0.0.1", port=0).start()
    try:
        for invalid in (
            {"all_animations": "true"},
            {"all_animations": True, "animation": {"path": "Rigid.anim.bfres", "index": 0}},
            {"animation_file": "Rigid.anim.bfres"},
            {"all_animations": True, "animation_file": "../escape.anim.bfres"},
            {"all_animations": True, "animation_file": "Rigid.bfres"},
        ):
            with pytest.raises((ProjectError, KeyError)):
                server.api("POST", "/api/model/export", {}, {"path": "Rigid.bfres", **invalid})
        origin = f"http://127.0.0.1:{server.port}"
        req = urllib.request.Request(origin + "/api/model/export", method="POST",
            data=json.dumps({"path": "Rigid.bfres", "textures": False, "all_animations": True}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.load(response)
        with urllib.request.urlopen(origin + result["download_url"], timeout=30) as response:
            assert response.headers.get_content_type() == "model/gltf-binary"
            assert "hookycreator-animations.glb" in response.headers["Content-Disposition"]
            assert response.headers["Cache-Control"] == "no-store"
            doc, _ = glb_parts(response.read())
        assert [clip["name"] for clip in doc["animations"]] == ["Walk", "Run"]
        token = urllib.parse.parse_qs(urllib.parse.urlparse(result["download_url"]).query)["id"][0]
        for suffix in ("", "&kind=selection", "&kind=../../exe", "&kind=glb&kind=selection",
                       "&kind=&kind=glb", f"&id={token}&kind=glb"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(origin + f"/api/export/download?id={token}" + suffix, timeout=5)
            assert error.value.code == 404
        with pytest.raises(ProjectError):
            server.project.export_download(token, "../../exe")
    finally:
        server.stop()


def test_expired_glb_and_zip_share_cache_limit_without_touching_other_files(tmp_path):
    project = make_project(tmp_path)
    (project.root / "Rigid.anim.bfres").write_bytes(fska_fixture())
    project.scan()
    folder = project.work / "cache" / "downloads"
    folder.mkdir()
    old_zip = folder / ("a" * 32 + ".zip")
    old_glb = folder / ("b" * 32 + ".glb")
    unrelated = folder / "user.glb"
    for item in (old_zip, old_glb, unrelated):
        item.write_bytes(b"keep?")
        os.utime(item, (time.time() - 8 * 86400,) * 2)
    result = project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    assert not old_zip.exists() and not old_glb.exists()
    assert unrelated.read_bytes() == b"keep?" and download_path(project, result).is_file()


@pytest.mark.skipif(os.environ.get("HOOKY_TEST_GLTF_VALIDATOR") != "1",
                    reason="Khronos glTF validator is an optional test dependency")
def test_khronos_validates_multi_clip_glb_and_gltf(tmp_path):
    project = make_project(tmp_path)
    (project.root / "Rigid.anim.bfres").write_bytes(fska_two_clips_with_jump())
    for name in ("Tex_A_Alb", "Tex_B_Alb"):
        (project.root / f"{name}.bntx").write_bytes(synth.make_bntx(4, 4, synth.rgba8_pixels(4, 4)))
    project.scan()
    result = project.export_model("Rigid.bfres", all_animations=True, archive=True)
    mesh, skin, first = simple_binding()
    second = copy.deepcopy(first)
    second["name"] = "Different"
    second["bones"][0]["curves"][0]["values"][1] = -1.
    written = blender.export_bundle([mesh], tmp_path, "many", formats=("glb", "gltf"),
                                    animations=iter((gltf_animation.bind(skin, first),
                                                     gltf_animation.bind(skin, second))))
    glb_doc, _ = glb_parts(written["glb"].read_bytes())
    assert glb_doc["animations"] == json.loads(written["gltf"].read_text(encoding="utf-8"))["animations"]
    mesh, _, bindings = independent_bindings()
    isolated = blender.export_bundle([mesh], tmp_path, "independent", formats=("glb",), animations=bindings)
    script = Path(__file__).resolve().parents[1] / "tools" / "validate_gltf.js"
    files = [download_path(project, result), written["glb"], written["gltf"], isolated["glb"]]
    run = subprocess.run(["node", str(script), *(str(file) for file in files)],
                         capture_output=True, text=True, check=False, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    reports = json.loads(run.stdout)
    assert len(reports) == len(files) and all(r["numErrors"] == 0 for r in reports)


def test_rejected_clips_still_consume_the_aggregate_decode_budget(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    bad = bytearray(fska_fixture())
    struct.pack_into("<I", bad, 0x444, 0x10)  # second curve repeats the first target
    (project.root / "Rigid_A.anim.bfres").write_bytes(bad)
    (project.root / "Rigid_B.anim.bfres").write_bytes(fska_fixture())
    project.scan()
    monkeypatch.setattr(project_mod, "MAX_ANIMATION_EXPORT_KEYS", 4)
    with pytest.raises(ProjectError, match="Trop d'images-clés source"):
        project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
    assert not list((project.work / "export").rglob("*-animations.glb"))
