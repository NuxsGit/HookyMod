"""Player-like skeletal variants constructed entirely from synthetic data.

These fixtures deliberately exercise different features; they are not copies
of game assets and do not establish compatibility with a real Player model.
"""
from __future__ import annotations

import bisect
import copy
import json
import math
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from hookycreator import model_skin
from hookymod import animation, bfres_v10, bfres_v10_skeleton, blender, gltf_animation
from tests.synth_animation import (
    fska_fixture,
    fska_mixed_quaternion_fixture,
    named_rigid_model,
    player_variant,
)
from tests.test_animated_export import (
    accessor,
    glb_parts,
    gltf_palette,
    sample_channel,
    simple_binding,
)


def node_result(packet, meshes, clip=None, frames=(0,)):
    if not shutil.which("node"):
        pytest.skip("Node is needed to compare the WebGL preview")
    payload = {"packet": packet.hex(), "model": {"meshes": [
        {"name": m.name, "positions": [v for p in m.positions for v in p]} for m in meshes]},
        "clip": clip, "frames": frames}
    script = r"""
const fs = require('fs'); const h = require('./hookycreator/web/model_animation.js');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const b = Buffer.from(p.packet, 'hex');
const skin = h.parseSkin(b.buffer.slice(b.byteOffset, b.byteOffset+b.byteLength), p.model);
const binding = p.clip ? h.bind(skin, p.clip) : {skin, tracks: []};
console.log(JSON.stringify(p.frames.map(frame => {
  const pose = h.pose(binding, frame);
  return {palette: Array.from(pose.palette), worlds: pose.worlds};
})));
"""
    result = subprocess.run([shutil.which("node"), "-e", script], input=json.dumps(payload),
                            cwd=Path(__file__).resolve().parents[1],
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_scaled_mixed_rotations_compensation_pose_skin_and_normals():
    data = player_variant()
    sk = bfres_v10_skeleton.read(data, 0x1500)
    root = (0., -3., 0., 5., 2., 0., 0., 0., 0., 0., 4., 0.)
    child = (-1., 0., 0., -1., 0., -1., 0., 0., 0., 0., 1., 0.)
    assert sk.matrices[0] == pytest.approx(root, abs=1e-6)
    assert sk.matrices[1] == pytest.approx(child, abs=1e-6)
    meshes, report = bfres_v10.parse(data)
    assert report.structured
    assert meshes[0].positions[0] == pytest.approx((-1., 0., .125), abs=1e-6)
    assert meshes[0].positions[1] == pytest.approx((-2., 0., .125), abs=1e-6)
    assert meshes[0].normals[0] == pytest.approx((-2**-.5, -2**-.5, 0), abs=1e-6)
    # The other shape has vertices assigned separately to root and child.
    assert meshes[1].positions[0] == pytest.approx((-4., 2., .5), abs=1e-6)
    assert meshes[1].normals[0] == pytest.approx((0., 1., 0.), abs=1e-6)
    packet = model_skin.encode(data, meshes, structured=report.structured)
    header = model_skin.decode(packet, meshes)
    assert [b["rotation_mode"] for b in header["bones"]] == ["quaternion", "euler"]
    assert header["bones"][1]["ssc"] is True
    pose, = node_result(packet, meshes)
    identity = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0.)
    for index, expected in enumerate((root, child)):
        assert pose["worlds"][index] == pytest.approx(expected, abs=1e-6)
        assert pose["palette"][index * 24:index * 24 + 12] == pytest.approx(identity, abs=1e-6)


def test_zero_bone_mode_with_euler_header_is_only_inferred_when_unambiguous():
    data = bytearray(named_rigid_model())
    child = 0x1580 + 0x58
    struct.pack_into("<I", data, child + 0x2c, 0)  # no explicit Euler bit
    sk = bfres_v10_skeleton.read(bytes(data), 0x1500)
    assert sk.rotation_modes[1] == "euler"
    assert sk.matrices[1] == pytest.approx((0., -1., 0., 5., 1., 0., 0., 2., 0., 0., 1., 0.), abs=1e-6)
    # A small rotation and W=1 might instead be a slightly nonunit
    # quaternion. Without evidence, reject rather than silently choose one.
    struct.pack_into("<f", data, child + 0x44, .01)
    with pytest.raises(bfres_v10_skeleton.SkeletonError, match="rotation ambigu"):
        bfres_v10_skeleton.read(bytes(data), 0x1500)
    # A normalized quaternion with W != 1 cannot be the documented Euler
    # representation (which uses W=1), even with an Euler skeleton header.
    struct.pack_into("<4f", data, child + 0x3c, 0., 0., math.sin(math.pi / 4), math.cos(math.pi / 4))
    sk = bfres_v10_skeleton.read(bytes(data), 0x1500)
    assert sk.rotation_modes[1] == "quaternion"
    assert sk.matrices[1] == pytest.approx((0., -1., 0., 5., 1., 0., 0., 2., 0., 0., 1., 0.), abs=1e-6)
    struct.pack_into("<4f", data, child + 0x3c, 0., 0., 0., .9)
    with pytest.raises(bfres_v10_skeleton.SkeletonError, match="quaternion non unitaire"):
        bfres_v10_skeleton.read(bytes(data), 0x1500)
    # The common identity has identical Euler/quaternion behavior.
    struct.pack_into("<4f", data, child + 0x3c, 0., 0., 0., 1.)
    assert bfres_v10_skeleton.read(bytes(data), 0x1500).rotation_modes[1] == "euler"


@pytest.mark.parametrize("mode", [0, 0x100, 0x200])
def test_standard_and_maya_scale_headers_are_exposed(mode):
    model = bytearray(named_rigid_model())
    struct.pack_into("<I", model, 0x1504, 0x1000 | mode)
    assert len(bfres_v10_skeleton.read(bytes(model), 0x1500).matrices) == 2
    source = bytearray(fska_fixture())
    struct.pack_into("<I", source, 0x104, 0x1004 | mode)
    header = animation.list_clips(bytes(source))[0]
    assert header["scale_mode"] == mode and header["supported"]
    assert animation.read_clip(bytes(source), 0)["scale_mode"] == mode


def test_softimage_scale_modes_are_rejected_for_skeleton_and_clip():
    model = bytearray(named_rigid_model())
    struct.pack_into("<I", model, 0x1504, 0x1300)
    with pytest.raises(bfres_v10_skeleton.SkeletonError, match="Softimage"):
        bfres_v10_skeleton.read(bytes(model), 0x1500)
    clip = bytearray(fska_fixture())
    struct.pack_into("<I", clip, 0x104, 0x1304)
    header = animation.list_clips(bytes(clip))[0]
    assert header["scale_mode"] == 0x300 and not header["supported"]
    with pytest.raises(animation.AnimationError, match="Softimage"):
        animation.read_clip(bytes(clip), 0)
    _mesh, skin, parsed = simple_binding()
    parsed["scale_mode"] = 0x300
    with pytest.raises(gltf_animation.ExportAnimationError, match="Softimage"):
        gltf_animation.bind(skin, parsed)


def test_scaled_skeleton_still_checks_stored_inverse_bind_matrices():
    data = bytearray(player_variant())
    struct.pack_into("<HH", data, 0x153a, 2, 2)
    struct.pack_into("<Q", data, 0x1520, 0x1740)
    struct.pack_into("<4H", data, 0x1700, 0, 1, 0, 1)
    struct.pack_into("<hh", data, 0x1580 + 0x24, 0, 2)
    struct.pack_into("<hh", data, 0x1580 + 0x58 + 0x24, 1, 3)
    struct.pack_into("<12f", data, 0x1740, 0., .5, 0., 0., -1/3, 0., 0., 5/3, 0., 0., .25, 0.)
    struct.pack_into("<12f", data, 0x1770, -1., 0., 0., -1., 0., -1., 0., 0., 0., 0., 1., 0.)
    bfres_v10_skeleton.read(bytes(data), 0x1500)
    struct.pack_into("<f", data, 0x1770, -.5)
    with pytest.raises(bfres_v10_skeleton.SkeletonError, match="incompatibles"):
        bfres_v10_skeleton.read(bytes(data), 0x1500)


def test_nonuniform_normals_use_inverse_transpose():
    data = bytearray(player_variant(compensate=False))
    # Attach the first shape to the scaled root, with a diagonal normal.
    struct.pack_into("<H", data, 0x400 + 0x54, 0)
    meshes, _ = bfres_v10.parse(bytes(data))
    normal = meshes[0].normals[0]
    length = math.sqrt(.5**2 + (1/3)**2)
    assert normal == pytest.approx((-(1/3)/length, .5/length, 0), abs=1e-6)
    assert math.sqrt(sum(x*x for x in normal)) == pytest.approx(1.)


def test_distinct_unsupported_bone_diagnostics():
    for label, patch, pattern in (
        ("billboard", lambda d: struct.pack_into("<I", d, 0x1580 + 0x2c, 0x11000), "billboard"),
        ("rotation", lambda d: struct.pack_into("<I", d, 0x1580 + 0x2c, 0x3000), "rotation"),
        ("singular", lambda d: struct.pack_into("<f", d, 0x1580 + 0x30, 0.), "échelle|singulière"),
    ):
        data = bytearray(named_rigid_model()); patch(data)
        meshes, report = bfres_v10.parse(bytes(data))
        with pytest.raises(model_skin.SkinError, match=pattern) as error:
            model_skin.encode(bytes(data), meshes, structured=report.structured)
        assert any(pattern.split("|")[0] in w.lower() for w in report.warnings), label
        for unrelated in {"billboard", "rotation", "échelle"} - {label if label != "singular" else "échelle"}:
            assert unrelated not in str(error.value).lower(), (label, str(error.value))


def test_scaled_skeleton_export_matches_webgl_at_fractional_frames(tmp_path):
    data = player_variant(compensate=False)
    meshes, report = bfres_v10.parse(data)
    packet = model_skin.encode(data, meshes, structured=report.structured)
    skin = model_skin.decode(packet, meshes)
    clip = animation.read_clip(fska_fixture(), 0)
    binding = gltf_animation.bind(skin, clip)
    written = blender.export_bundle(meshes, tmp_path, "scaled", formats=("glb",), animation=binding)
    doc, blob = glb_parts(written["glb"].read_bytes())
    frames = [i / 10 for i in range(101)]
    poses = node_result(packet, meshes, clip, frames)
    for frame, pose in zip(frames, poses):
        actual = gltf_palette(doc, blob, frame / clip["fps"])
        for index, matrix in enumerate(actual):
            # Euler-to-quaternion sampling is approximate and nonuniform scale
            # magnifies angular error. A 0.01-frame sweep measured a maximum
            # matrix-component difference of 1.02e-4 for this fixture.
            assert matrix == pytest.approx(pose["palette"][24 * index:24 * index + 12], abs=2e-4, rel=0)


def test_mixed_fska_quaternion_and_euler_curves():
    raw = fska_mixed_quaternion_fixture()
    assert animation.list_clips(raw)[0]["supported"]
    clip = animation.read_clip(raw, 0)
    root, child = clip["bones"]
    assert (root["rotation_mode"], child["rotation_mode"]) == ("quaternion", "euler")
    quat = root["curves"][1]
    assert quat["target"] == "rotation" and quat["interpolation"] == "slerp"
    assert animation.evaluate_quaternion(quat, 5) == pytest.approx(
        (0., 0., math.sin(math.pi / 8), math.cos(math.pi / 8)), abs=1e-6)
    assert child["curves"][0]["target"] == "rotation.z"


def test_invalid_quaternion_clips_are_rejected():
    for label, patch in (
        ("unknown clip mode", lambda d: struct.pack_into("<I", d, 0x104, 0x3004)),
        ("Euler clip with quaternion", lambda d: struct.pack_into("<I", d, 0x104, 0x1004)),
        ("quantized quaternion", lambda d: struct.pack_into("<H", d, 0x440, 0x0086)),
        ("wrong target", lambda d: struct.pack_into("<I", d, 0x444, 0x24)),
        ("zero quaternion", lambda d: struct.pack_into("<4f", d, 0x900, 0., 0., 0., 0.)),
        ("nonfinite quaternion", lambda d: struct.pack_into("<f", d, 0x900, math.nan)),
        ("unsorted keys", lambda d: d.__setitem__(slice(0x540, 0x542), b"\x0a\x00")),
        ("quaternion bone with scalar rotation", lambda d: struct.pack_into("<H", d, 0x440, 0x0002)),
    ):
        bad = bytearray(fska_mixed_quaternion_fixture()); patch(bad)
        try:
            animation.read_clip(bytes(bad), 0)
        except animation.AnimationError:
            continue
        pytest.fail(f"Malformed {label} clip was accepted")


def test_mixed_quaternion_clip_export_matches_webgl(tmp_path):
    data = player_variant(compensate=False)
    meshes, report = bfres_v10.parse(data)
    packet = model_skin.encode(data, meshes, structured=report.structured)
    skin = model_skin.decode(packet, meshes)
    clip = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
    binding = gltf_animation.bind(skin, clip)
    written = blender.export_bundle(meshes, tmp_path, "mixed", formats=("glb",), animation=binding)
    doc, blob = glb_parts(written["glb"].read_bytes())
    frames = [i / 10 for i in range(101)]
    poses = node_result(packet, meshes, clip, frames)
    for frame, pose in zip(frames, poses):
        for index, matrix in enumerate(gltf_palette(doc, blob, frame / clip["fps"])):
            assert matrix == pytest.approx(pose["palette"][24 * index:24 * index + 12], abs=2e-6, rel=0)


def test_quaternion_sampling_also_bounds_linear_component_importers(tmp_path):
    # Blender 4.2 interpolates quaternion F-curves component-wise. Using only
    # the source endpoints would give a visibly wrong angular speed even though
    # the GLB is valid and a spec-compliant SLERP consumer would look correct.
    data = player_variant(compensate=False)
    meshes, report = bfres_v10.parse(data)
    packet = model_skin.encode(data, meshes, structured=report.structured)
    skin = model_skin.decode(packet, meshes)
    clip = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
    path = blender.export_bundle(meshes, tmp_path, "nlerp", formats=("glb",),
                                 animation=gltf_animation.bind(skin, clip))["glb"]
    doc, blob = glb_parts(path.read_bytes())
    channel = next(c for c in doc["animations"][0]["channels"]
                   if c["target"]["path"] == "rotation" and doc["nodes"][c["target"]["node"]]["name"] == "Root")
    sampler = doc["animations"][0]["samplers"][channel["sampler"]]
    times = [v[0] for v in accessor(doc, blob, sampler["input"])]
    values = accessor(doc, blob, sampler["output"])
    assert len(times) > 2  # the original quaternion curve contains just two keys
    curve = clip["bones"][0]["curves"][1]
    for frame in (i / 100 for i in range(1001)):
        seconds = frame / clip["fps"]
        index = max(0, min(len(times) - 2, bisect.bisect_right(times, seconds) - 1))
        u = max(0., min(1., (seconds - times[index]) / (times[index + 1] - times[index])))
        q = [a + u*(b-a) for a, b in zip(values[index], values[index + 1])]
        length = math.hypot(*q)
        q = [v / length for v in q]
        assert gltf_animation._angle(q, animation.evaluate_quaternion(curve, frame)) < .00012


def test_fast_euler_rotation_also_bounds_linear_component_importers(tmp_path):
    mesh, skin, clip = simple_binding()
    curve = {"target": "rotation.z", "interpolation": "cubic", "frames": [0., 10.],
             "values": [0., 5 * math.pi], "slopes": [math.pi / 2] * 2}
    clip["bones"][0]["curves"] = [curve]
    path = blender.export_bundle([mesh], tmp_path, "fast-euler", formats=("glb",),
                                 animation=gltf_animation.bind(skin, clip))["glb"]
    doc, blob = glb_parts(path.read_bytes())
    channel = next(c for c in doc["animations"][0]["channels"] if c["target"]["path"] == "rotation")
    sampler = doc["animations"][0]["samplers"][channel["sampler"]]
    times = [value[0] for value in accessor(doc, blob, sampler["input"])]
    values = accessor(doc, blob, sampler["output"])
    for frame in (i / 100 for i in range(1001)):
        seconds = frame / clip["fps"]
        index = max(0, min(len(times) - 2, bisect.bisect_right(times, seconds) - 1))
        u = max(0., min(1., (seconds - times[index]) / (times[index + 1] - times[index])))
        q = [a + u * (b - a) for a, b in zip(values[index], values[index + 1])]
        q = [value / math.hypot(*q) for value in q]
        expected = gltf_animation._quat((0., 0., animation.evaluate(curve, frame)))
        assert gltf_animation._angle(q, expected) < .00012
        slerp = sample_channel(doc, blob, channel, seconds)
        length = math.hypot(*slerp)
        assert gltf_animation._angle([value / length for value in slerp], expected) < .00012


def test_quaternion_jump_is_preserved_as_a_short_gltf_transition(tmp_path):
    raw = bytearray(fska_mixed_quaternion_fixture())
    struct.pack_into("<H", raw, 0x440, 0x80)  # float frames; float quaternion keys
    struct.pack_into("<H", raw, 0x442, 4)
    struct.pack_into("<Q", raw, 0x430, 0x940)
    struct.pack_into("<4f", raw, 0x940, 0., 5., 5., 10.)
    for i, angle in enumerate((0., .3, 1.5, 2.)):
        struct.pack_into("<4f", raw, 0x900 + i * 16, 0., 0., math.sin(angle/2), math.cos(angle/2))
    clip = animation.read_clip(bytes(raw), 0)
    data = player_variant(compensate=False)
    meshes, report = bfres_v10.parse(data)
    packet = model_skin.encode(data, meshes, structured=report.structured)
    binding = gltf_animation.bind(model_skin.decode(packet, meshes), clip)
    assert binding["repeated_keys"] == 1
    written = blender.export_bundle(meshes, tmp_path, "quaternion-jump", formats=("glb",), animation=binding)
    doc, blob = glb_parts(written["glb"].read_bytes())
    channel = next(c for c in doc["animations"][0]["channels"]
                   if c["target"]["path"] == "rotation" and doc["nodes"][c["target"]["node"]]["name"] == "Root")
    curve = clip["bones"][0]["curves"][1]
    for frame in (0., 1., 4.9, 5., 5.1, 7.5, 10.):
        actual = sample_channel(doc, blob, channel, frame / clip["fps"])
        assert gltf_animation._angle(actual, animation.evaluate_quaternion(curve, frame)) < .002


def test_uniform_compensated_scale_is_exported_and_matches_preview(tmp_path):
    data = bytearray(player_variant())
    struct.pack_into("<3f", data, 0x1580 + 0x30, 2., 2., 2.)
    meshes, report = bfres_v10.parse(bytes(data))
    packet = model_skin.encode(bytes(data), meshes, structured=report.structured)
    skin = model_skin.decode(packet, meshes)
    # Root's animation base changes its scale from the rest value 2 to 1.
    # Child has no scale track, so the exporter must add compensation anyway.
    clip = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
    written = blender.export_bundle(meshes, tmp_path, "uniform-ssc", formats=("glb",),
                                    animation=gltf_animation.bind(skin, clip))
    doc, blob = glb_parts(written["glb"].read_bytes())
    helper = next(node for node in doc["nodes"] if node.get("name", "").startswith("__HookySSC_1_"))
    helper_index = doc["nodes"].index(helper)
    assert any(c["target"] == {"node": helper_index, "path": "scale"}
               for c in doc["animations"][0]["channels"])
    frames = [i / 10 for i in range(101)]
    poses = node_result(packet, meshes, clip, frames)
    for frame, pose in zip(frames, poses):
        for index, matrix in enumerate(gltf_palette(doc, blob, frame / clip["fps"])):
            assert matrix == pytest.approx(pose["palette"][24 * index:24 * index + 12], abs=2e-6, rel=0)


def test_export_refuses_variable_ssc_parent_even_when_uniform_at_endpoints():
    data = bytearray(player_variant())
    struct.pack_into("<3f", data, 0x1580 + 0x30, 2., 2., 2.)
    meshes, report = bfres_v10.parse(bytes(data))
    skin = model_skin.decode(model_skin.encode(bytes(data), meshes, structured=report.structured), meshes)
    clip = animation.read_clip(fska_fixture(), 0)
    clip["bones"][0]["curves"].append({"target": "scale.x", "interpolation": "cubic",
                                       "frames": [0., 10.], "values": [1., 1.], "slopes": [1., -1.]})
    with pytest.raises(gltf_animation.ExportAnimationError, match="constante et non nulle"):
        gltf_animation.bind(skin, clip)


def test_constant_nonuniform_ssc_export_matches_preview_with_animated_child_translation(tmp_path):
    data = player_variant()
    meshes, report = bfres_v10.parse(data)
    packet = model_skin.encode(data, meshes, structured=report.structured)
    skin = model_skin.decode(packet, meshes)
    clip = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
    clip["bones"][0]["base"]["scale"] = [2., 3., 4.]
    clip["bones"][0]["curves"].append({"target": "scale.x", "interpolation": "cubic",
                                        "frames": [0., 10.], "values": [2., 2.], "slopes": [0., 0.]})
    clip["bones"][1]["curves"].append({"target": "translation.y", "interpolation": "cubic",
                                        "frames": [0., 10.], "values": [2., 3.], "slopes": [.1, .1]})
    binding = gltf_animation.bind(skin, clip)
    written = blender.export_bundle(meshes, tmp_path, "nonuniform-ssc", formats=("glb",), animation=binding)
    doc, blob = glb_parts(written["glb"].read_bytes())
    joint = next(index for index, node in enumerate(doc["nodes"]) if node.get("name") == "Child")
    helper = next(index for index, node in enumerate(doc["nodes"]) if node.get("name", "").startswith("__HookySSC_1_"))
    assert doc["nodes"][helper]["children"] == [joint]
    assert helper not in doc["skins"][0]["joints"]
    assert any(c["target"] == {"node": helper, "path": "translation"}
               for c in doc["animations"][0]["channels"])
    frames = [i / 10 for i in range(101)]
    for frame, pose in zip(frames, node_result(packet, meshes, clip, frames)):
        for index, matrix in enumerate(gltf_palette(doc, blob, frame / clip["fps"])):
            assert matrix == pytest.approx(pose["palette"][24 * index:24 * index + 12], abs=2e-6, rel=0)


def test_grouped_ssc_actions_restore_helper_scale_and_translation(tmp_path):
    data = player_variant()
    meshes, report = bfres_v10.parse(data)
    packet = model_skin.encode(data, meshes, structured=report.structured)
    skin = model_skin.decode(packet, meshes)
    first = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
    first["name"] = "Small"
    first["bones"][1]["curves"].append({"target": "translation.y", "interpolation": "cubic",
                                         "frames": [0., 10.], "values": [2., 3.], "slopes": [.1, .1]})
    second = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
    second["name"] = "Large"
    second["bones"][0]["base"]["scale"] = [2., 3., 4.]
    bindings = [gltf_animation.bind(skin, clip) for clip in (first, second)]
    path = blender.export_bundle(meshes, tmp_path, "grouped-ssc", formats=("glb",), animations=bindings)["glb"]
    doc, blob = glb_parts(path.read_bytes())
    helper = next(index for index, node in enumerate(doc["nodes"]) if node.get("name", "").startswith("__HookySSC_1_"))
    frames = [i / 10 for i in range(101)]
    for action, clip in zip(doc["animations"], (first, second)):
        assert {"scale", "translation"} <= {c["target"]["path"] for c in action["channels"]
                                            if c["target"]["node"] == helper}
        single = copy.deepcopy(doc)
        single["animations"] = [action]
        for frame, pose in zip(frames, node_result(packet, meshes, clip, frames)):
            for index, matrix in enumerate(gltf_palette(single, blob, frame / clip["fps"])):
                assert matrix == pytest.approx(pose["palette"][24 * index:24 * index + 12], abs=2e-6, rel=0)


def test_clip_only_uniform_ssc_export_matches_preview(tmp_path):
    data = player_variant(compensate=False)
    meshes, report = bfres_v10.parse(data)
    packet = model_skin.encode(data, meshes, structured=report.structured)
    skin = model_skin.decode(packet, meshes)
    clip = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
    clip["bones"][0]["base"]["scale"] = [2., 2., 2.]
    clip["bones"][1]["ssc"] = True
    binding = gltf_animation.bind(skin, clip)
    path = blender.export_bundle(meshes, tmp_path, "clip-ssc", formats=("glb",), animation=binding)["glb"]
    doc, blob = glb_parts(path.read_bytes())
    assert not any(node.get("name", "").startswith("__HookySSC_") for node in doc["nodes"])
    frames = [i / 10 for i in range(101)]
    for frame, pose in zip(frames, node_result(packet, meshes, clip, frames)):
        for index, matrix in enumerate(gltf_palette(doc, blob, frame / clip["fps"])):
            assert matrix == pytest.approx(pose["palette"][24 * index:24 * index + 12], abs=2e-6, rel=0)


def test_clip_only_nonuniform_ssc_is_refused_without_writing_partial_gltf(tmp_path):
    data = player_variant(compensate=False)
    meshes, report = bfres_v10.parse(data)
    skin = model_skin.decode(model_skin.encode(data, meshes, structured=report.structured), meshes)
    clip = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
    clip["bones"][0]["base"]["scale"] = [2., 3., 4.]
    clip["bones"][1]["ssc"] = True
    with pytest.raises(gltf_animation.ExportAnimationError, match="présente seulement dans le clip"):
        gltf_animation.bind(skin, clip)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.environ.get("HOOKY_TEST_GLTF_VALIDATOR") != "1",
                    reason="Khronos glTF validator is an optional test dependency")
def test_khronos_validates_scaled_mixed_and_compensated_exports(tmp_path):
    files = []
    for label, compensate, rest_scale, animated_scale in (
        ("no-ssc", False, (2., 3., 4.), (1., 1., 1.)),
        ("uniform", True, (2., 2., 2.), (1., 1., 1.)),
        ("nonuniform", True, (2., 3., 4.), (2., 3., 4.)),
        ("mirrored", True, (-2., 3., 4.), (-2., 3., 4.)),
    ):
        data = bytearray(player_variant(compensate=compensate))
        struct.pack_into("<3f", data, 0x1580 + 0x30, *rest_scale)
        meshes, report = bfres_v10.parse(bytes(data))
        packet = model_skin.encode(bytes(data), meshes, structured=report.structured)
        skin = model_skin.decode(packet, meshes)
        clip = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
        clip["bones"][0]["base"]["scale"] = animated_scale
        written = blender.export_bundle(meshes, tmp_path, label, formats=("glb", "gltf"),
                                        animation=gltf_animation.bind(skin, clip))
        files.extend([written["glb"], written["gltf"]])
        if label == "nonuniform":
            other = copy.deepcopy(clip)
            other["name"] = "Other"
            other["bones"][0]["base"]["scale"] = [1., 1., 1.]
            grouped = blender.export_bundle(meshes, tmp_path, "grouped-ssc", formats=("glb",),
                                             animations=[gltf_animation.bind(skin, item) for item in (clip, other)])
            files.append(grouped["glb"])
    script = Path(__file__).resolve().parents[1] / "tools" / "validate_gltf.js"
    run = subprocess.run(["node", str(script), *(str(f) for f in files)],
                         capture_output=True, text=True, check=False, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    reports = json.loads(run.stdout)
    assert len(reports) == len(files) and all(report["numErrors"] == 0 for report in reports)
