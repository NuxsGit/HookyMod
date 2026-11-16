"""Exports GLB/glTF animés, OBJ/MTL et refus : uniquement des fixtures synthétiques."""
from __future__ import annotations

import bisect
import copy
import io
import json
import math
import os
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

import pytest

from hookycreator import model_skin
from hookycreator.project import Project, ProjectError
from hookycreator.server import HookyCreatorServer
from hookymod import animation, blender, gltf_animation, util
from hookymod.mesh import MeshData
from tests.synth_animation import fska_fixture, fska_two_clips_with_jump, fska_with_jump, named_rigid_model


def glb_parts(data):
    magic, version, total = struct.unpack_from("<4sII", data)
    assert (magic, version, total) == (b"glTF", 2, len(data))
    length, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON" and length % 4 == 0
    doc = json.loads(data[20:20 + length])
    offset = 20 + length
    size, kind = struct.unpack_from("<I4s", data, offset)
    assert kind == b"BIN\0" and size % 4 == 0 and offset + 8 + size == len(data)
    return doc, data[offset + 8:]


def accessor(doc, blob, index):
    a = doc["accessors"][index]
    v = doc["bufferViews"][a["bufferView"]]
    fmt = {5126: "f", 5123: "H", 5125: "I"}[a["componentType"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
    offset = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    size = struct.calcsize("<" + fmt * width)
    return [struct.unpack_from("<" + fmt * width, blob, offset + i * size) for i in range(a["count"])]


def sample_channel(doc, blob, channel, seconds):
    sampler = doc["animations"][0]["samplers"][channel["sampler"]]
    times = [x[0] for x in accessor(doc, blob, sampler["input"])]
    values = accessor(doc, blob, sampler["output"])
    spline = sampler["interpolation"] == "CUBICSPLINE"
    def value(index):
        return values[3 * index + 1] if spline else values[index]
    if len(times) == 1 or seconds <= times[0]:
        return value(0)
    if seconds >= times[-1]:
        return value(-1 if not spline else len(times) - 1)
    i = bisect.bisect_right(times, seconds) - 1
    dt = times[i + 1] - times[i]
    u = (seconds - times[i]) / dt
    if spline:
        left, right = value(i), value(i+1)
        outgoing, incoming = values[3*i+2], values[3*(i+1)]
        return tuple((2*u**3-3*u*u+1)*left[j] + (u**3-2*u*u+u)*dt*outgoing[j]
                     + (-2*u**3+3*u*u)*right[j] + (u**3-u*u)*dt*incoming[j]
                     for j in range(len(left)))
    if channel["target"]["path"] == "rotation":
        return gltf_animation._slerp(value(i), value(i+1), u)
    return tuple(a + (b-a)*u for a, b in zip(value(i), value(i+1)))


def multiply(a, b):
    return [sum(a[4*r+k] * b[4*k+c] for k in range(3)) + (a[4*r+3] if c == 3 else 0)
            for r in range(3) for c in range(4)]


def local_matrix(translation, rotation, scale):
    x, y, z, w = rotation
    r = (1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
         2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
         2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))
    return [r[0]*scale[0], r[1]*scale[1], r[2]*scale[2], translation[0],
            r[3]*scale[0], r[4]*scale[1], r[5]*scale[2], translation[1],
            r[6]*scale[0], r[7]*scale[1], r[8]*scale[2], translation[2]]


def gltf_palette(doc, blob, seconds):
    nodes = copy.deepcopy(doc["nodes"])
    for channel in doc["animations"][0]["channels"]:
        target = channel["target"]
        nodes[target["node"]][target["path"]] = sample_channel(doc, blob, channel, seconds)
    parents = {child: i for i, node in enumerate(nodes) for child in node.get("children", [])}
    worlds = {}
    def world(i):
        if i not in worlds:
            node = nodes[i]
            local = local_matrix(node.get("translation", (0, 0, 0)), node.get("rotation", (0, 0, 0, 1)),
                                 node.get("scale", (1, 1, 1)))
            worlds[i] = multiply(world(parents[i]), local) if i in parents else local
        return worlds[i]
    skin = doc["skins"][0]
    inverses = accessor(doc, blob, skin["inverseBindMatrices"])
    result = []
    for node, m in zip(skin["joints"], inverses):
        inverse = [m[0], m[4], m[8], m[12], m[1], m[5], m[9], m[13], m[2], m[6], m[10], m[14]]
        result.append(multiply(world(node), inverse))
    return result


def project_with_animation(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "Rigid.bfres").write_bytes(named_rigid_model())
    (root / "Walk.anim.bfres").write_bytes(fska_fixture())
    project = Project(root, tmp_path / "work")
    project.scan()
    return project


def read_archive(project, result):
    token = result["download_url"].split("=", 1)[1]
    return zipfile.ZipFile(project.export_download(token))


def test_end_to_end_animated_export_and_cache_isolation(tmp_path):
    project = project_with_animation(tmp_path)
    source_before = {p.name: p.read_bytes() for p in project.root.iterdir()}
    with patch.object(blender, "export_bundle", wraps=blender.export_bundle) as build:
        static = project.export_model("Rigid.bfres", textures=False, archive=True)
        animated = project.export_model("Rigid.bfres", textures=False, archive=True,
                                        animation_rel="Walk.anim.bfres", animation_index=0)
        again = project.export_model("Rigid.bfres", textures=False, archive=True)
        assert build.call_count == 2  # static cache, jamais réutilisé pour le clip
    assert animated["animation"] == {"path": "Walk.anim.bfres", "index": 0, "name": "Walk",
                                      "frames": 10, "fps": 30, "matched": 1, "missing": 0}
    with read_archive(project, animated) as archive:
        names = set(archive.namelist())
        assert {f"Rigid/Rigid.{ext}" for ext in ("glb", "gltf", "bin", "obj", "mtl")} <= names
        assert archive.testzip() is None
        doc, binary = glb_parts(archive.read("Rigid/Rigid.glb"))
        text_doc = json.loads(archive.read("Rigid/Rigid.gltf"))
        assert doc["animations"] == text_doc["animations"]
        assert doc["skins"] == text_doc["skins"]
        assert "uri" not in doc["buffers"][0]
        assert doc["animations"][0]["name"] == "Walk"
        assert len(doc["skins"][0]["joints"]) == 2
        assert all("JOINTS_0" in m["primitives"][0]["attributes"] for m in doc["meshes"])
        assert b"mtllib Rigid.mtl" in archive.read("Rigid/Rigid.obj")
        assert b"newmtl " in archive.read("Rigid/Rigid.mtl")
        assert "OBJ/MTL restent statiques" in archive.read("Rigid/RAPPORT_MODELE.txt").decode()
        assert "OBJ/MTL ne stockent ni squelette ni animation" in archive.read("Rigid/IMPORT_BLENDER.txt").decode()
    with read_archive(project, static) as archive:
        doc, _ = glb_parts(archive.read("Rigid/Rigid.glb"))
        assert "skins" not in doc and "animations" not in doc
    with read_archive(project, again) as archive:
        doc, _ = glb_parts(archive.read("Rigid/Rigid.glb"))
        assert "animations" not in doc
    assert source_before == {p.name: p.read_bytes() for p in project.root.iterdir()}


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed to compare the WebGL preview")
def test_glb_joint_matrices_match_actual_webgl_preview_at_fractional_frames(tmp_path):
    project = project_with_animation(tmp_path)
    result = project.export_model("Rigid.bfres", textures=False,
                                  animation_rel="Walk.anim.bfres", animation_index=0)
    doc, blob = glb_parts((Path(result["absolute_folder"]) / "Rigid.glb").read_bytes())
    meshes = project.model_meshes("Rigid.bfres")
    clip = project.animation_clip("Walk.anim.bfres", 0)
    packet = project.model_skin("Rigid.bfres")
    import base64
    payload = {"packet": base64.b64encode(packet).decode(), "clip": clip,
               "model": {"meshes": [{"name": m.name, "positions": [0] * (len(m.positions)*3)} for m in meshes]},
               "frames": [0, .37, 1, 2.5, 5, 7.73, 10]}
    js = """
const h = require('./hookycreator/web/model_animation.js');
let raw = '';
process.stdin.on('data', x => raw += x);
process.stdin.on('end', () => {
  const p=JSON.parse(raw), bytes=Buffer.from(p.packet,'base64');
  const skin=h.parseSkin(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),p.model);
  const binding=h.bind(skin,p.clip);
  console.log(JSON.stringify(p.frames.map(t => Array.from(h.palette(binding,t)))));
});
"""
    completed = subprocess.run([shutil.which("node"), "-e", js], input=json.dumps(payload),
                               text=True, capture_output=True, check=True)
    reference = json.loads(completed.stdout)
    for frame, preview in zip(payload["frames"], reference):
        matrices = gltf_palette(doc, blob, frame / clip["fps"])
        for i, matrix in enumerate(matrices):
            assert matrix == pytest.approx(preview[i*24:i*24+12], abs=0.0015)


def simple_binding(*, weights=None, bones=1, clip=None):
    mesh = MeshData(name="Shape", positions=[(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)],
                    faces=[(0, 1, 2)], uvs=[(0., 0.), (1., 0.), (0., 1.)], material="Surface")
    skeleton = [{"name": f"Bone{i}", "parent": -1, "scale": [1., 1., 1.],
                 "rotation": [0., 0., 0., 1.], "translation": [0., 0., 0.], "ssc": False,
                 "inverse": [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0.]} for i in range(bones)]
    if weights is None:
        weights = [255] + [0]*7
    indices = list(range(min(bones, 8))) + [0] * (8-min(bones, 8))
    skin = {"bones": skeleton, "meshes": [{"name": mesh.name, "vertices": 3,
             "joints": struct.pack("<8H", *indices)*3, "weights": bytes(weights)*3}]}
    if clip is None:
        clip = {"name": "Test", "fps": 30, "frames": 10, "loop": False,
                "bones": [{"name": "Bone0", "base": {}, "ssc": False,
                           "curves": [{"target": "translation.x", "interpolation": "cubic",
                                       "frames": [0., 10.], "values": [0., 1.], "slopes": [.1, .1]}]}]}
    return mesh, skin, clip


def test_eight_influences_are_retained_normalized_and_duplicate_joints_merged(tmp_path):
    mesh, skin, clip = simple_binding(weights=[11, 19, 23, 31, 37, 41, 43, 47], bones=8)
    binding = gltf_animation.bind(skin, clip)
    written = blender.export_bundle([mesh], tmp_path, "eight", formats=("glb",), animation=binding)
    doc, blob = glb_parts(written["glb"].read_bytes())
    attributes = doc["meshes"][0]["primitives"][0]["attributes"]
    joints = accessor(doc, blob, attributes["JOINTS_0"])[0] + accessor(doc, blob, attributes["JOINTS_1"])[0]
    weights = accessor(doc, blob, attributes["WEIGHTS_0"])[0] + accessor(doc, blob, attributes["WEIGHTS_1"])[0]
    assert joints == tuple(range(8))
    assert sum(weights) == pytest.approx(1.)
    assert weights == pytest.approx([x / 252 for x in [11, 19, 23, 31, 37, 41, 43, 47]])
    # Doubler un indice doit additionner les influences, non produire deux joints identiques.
    data = skin["meshes"][0]
    data["joints"] = struct.pack("<8H", 0, 0, 2, 3, 4, 5, 6, 7)*3
    written = blender.export_bundle([mesh], tmp_path, "duplicate", formats=("glb",),
                                    animation=gltf_animation.bind(skin, clip))
    doc, blob = glb_parts(written["glb"].read_bytes())
    attributes = doc["meshes"][0]["primitives"][0]["attributes"]
    joints = accessor(doc, blob, attributes["JOINTS_0"])[0] + accessor(doc, blob, attributes["JOINTS_1"])[0]
    weights = accessor(doc, blob, attributes["WEIGHTS_0"])[0] + accessor(doc, blob, attributes["WEIGHTS_1"])[0]
    assert len([j for j, w in zip(joints, weights) if w and j == 0]) == 1
    assert weights[0] == pytest.approx((11+19) / 252)
    assert sum(weights) == pytest.approx(1.)


def test_fska_duplicate_frame_is_exported_once_and_jump_is_reported(tmp_path):
    project = project_with_animation(tmp_path)
    (project.root / "Walk.anim.bfres").write_bytes(fska_with_jump())
    source = (project.root / "Walk.anim.bfres").read_bytes()
    clip = animation.read_clip(source, 0)
    assert clip["bones"][0]["curves"][0]["frames"] == [0., 5., 5., 10.]
    result = project.export_model("Rigid.bfres", textures=False, archive=True,
                                  animation_rel="Walk.anim.bfres", animation_index=0)
    assert (project.root / "Walk.anim.bfres").read_bytes() == source
    assert any("saut" in note.lower() for note in result["warnings"])
    with read_archive(project, result) as archive:
        doc, blob = glb_parts(archive.read("Rigid/Rigid.glb"))
        assert len(doc["animations"]) == 1 and doc["animations"][0]["name"] == "Walk"
        assert "saut" in archive.read("Rigid/RAPPORT_MODELE.txt").decode().lower()
        for sampler in doc["animations"][0]["samplers"]:
            times = [item[0] for item in accessor(doc, blob, sampler["input"])]
            assert all(a < b for a, b in zip(times, times[1:]))
        translation = next(c for c in doc["animations"][0]["channels"]
                           if c["target"]["path"] == "translation")
        curve = clip["bones"][0]["curves"][0]
        for frame in (0., 1., 4., 4.9, 5., 5.1, 7., 10.):
            actual = sample_channel(doc, blob, translation, frame / clip["fps"])[0]
            assert actual == pytest.approx(animation.evaluate(curve, frame), abs=.002)


def test_each_selected_clip_is_exported_once_and_downloads_remain_distinct(tmp_path):
    project = project_with_animation(tmp_path)
    (project.root / "Walk.anim.bfres").write_bytes(fska_two_clips_with_jump())
    assert [item["name"] for item in project.animation_list("Walk.anim.bfres")["clips"]] == ["Walk", "Run"]
    results = [project.export_model("Rigid.bfres", textures=False, archive=True,
                                    animation_rel="Walk.anim.bfres", animation_index=index)
               for index in (0, 1)]
    assert results[0]["download_url"] != results[1]["download_url"]
    for result, name in zip(results, ("Walk", "Run")):
        with read_archive(project, result) as archive:
            doc, blob = glb_parts(archive.read("Rigid/Rigid.glb"))
            assert [item["name"] for item in doc["animations"]] == [name]
            assert doc["animations"][0]["extras"]["sourceLoop"] is True
            # Le cycle source n'est pas recopié plusieurs fois, même s'il boucle.
            for sampler in doc["animations"][0]["samplers"]:
                times = accessor(doc, blob, sampler["input"])
                assert times[-1][0] == pytest.approx(10 / 30)


def test_repeated_negative_zero_is_a_valid_starting_instant(tmp_path):
    mesh, skin, clip = simple_binding()
    curve = clip["bones"][0]["curves"][0]
    curve.update(frames=[-0.0, -0.0, 10.], values=[0., 1., 2.], slopes=[.1, .1, .1])
    written = blender.export_bundle([mesh], tmp_path, "negative-zero", formats=("glb",),
                                    animation=gltf_animation.bind(skin, clip))
    doc, blob = glb_parts(written["glb"].read_bytes())
    channel = next(c for c in doc["animations"][0]["channels"] if c["target"]["path"] == "translation")
    assert sample_channel(doc, blob, channel, 0.)[0] == pytest.approx(0.)
    assert sample_channel(doc, blob, channel, .1 / 30)[0] == pytest.approx(
        animation.evaluate(curve, .1), abs=.002)


@pytest.mark.parametrize("path", ["translation.x", "rotation.z"])
def test_duplicate_keys_at_start_middle_and_end_keep_each_side(tmp_path, path):
    mesh, skin, clip = simple_binding()
    curve = {"target": path, "interpolation": "cubic",
             "frames": [0., 0., 3., 5., 5., 8., 10., 10.],
             "values": [0., .4, .7, .9, 1.5, 1.8, 2., 2.3],
             "slopes": [.1] * 8}
    clip["bones"][0]["curves"] = [curve]
    written = blender.export_bundle([mesh], tmp_path, "jump", formats=("glb",),
                                    animation=gltf_animation.bind(skin, clip))
    doc, blob = glb_parts(written["glb"].read_bytes())
    assert len(doc["animations"]) == 1
    kind = path.split(".")[0]
    channel = next(c for c in doc["animations"][0]["channels"] if c["target"]["path"] == kind)
    for frame in (0., .1, 1., 2.5, 4.9, 5., 5.1, 7., 9.9, 10.):
        actual = sample_channel(doc, blob, channel, frame / 30)
        value = animation.evaluate(curve, frame)
        if kind == "translation":
            assert actual[0] == pytest.approx(value, abs=.002)
        else:
            assert gltf_animation._angle(actual, gltf_animation._quat((0., 0., value))) < .002


def test_distinct_curves_can_jump_on_opposite_sides_of_the_same_frame(tmp_path):
    mesh, skin, clip = simple_binding()
    curves = [
        {"target": "translation.x", "interpolation": "cubic", "frames": [0., 5., 5., 10.],
         "values": [0., 1., 3., 4.], "slopes": [.2, .2, .2, .2]},
        {"target": "translation.y", "interpolation": "cubic", "frames": [5., 5., 10.],
         "values": [2., 4., 5.], "slopes": [.2, .2, .2]},
    ]
    clip["bones"][0]["curves"] = curves
    written = blender.export_bundle([mesh], tmp_path, "mixed", formats=("glb",),
                                    animation=gltf_animation.bind(skin, clip))
    doc, blob = glb_parts(written["glb"].read_bytes())
    channel = next(c for c in doc["animations"][0]["channels"] if c["target"]["path"] == "translation")
    for frame in (0., 4.9, 5., 5.1, 10.):
        actual = sample_channel(doc, blob, channel, frame / 30)
        assert actual[:2] == pytest.approx([animation.evaluate(c, frame) for c in curves], abs=.002)


def test_jump_keys_too_close_for_gltf_fail_without_overwriting_output(tmp_path):
    mesh, skin, clip = simple_binding()
    curve = clip["bones"][0]["curves"][0]
    curve.update(frames=[0., 1., 1., 1. + 1e-8, 10.],
                 values=[0., 1., 2., 3., 4.], slopes=[0.] * 5)
    target = tmp_path / "too-close.glb"
    target.write_bytes(b"previous export")
    with pytest.raises(gltf_animation.ExportAnimationError, match="trop proches"):
        blender.export_bundle([mesh], tmp_path, "too-close", formats=("glb",),
                              animation=gltf_animation.bind(skin, clip))
    assert target.read_bytes() == b"previous export"


def test_equal_duplicate_keys_preserve_their_two_slopes(tmp_path):
    mesh, skin, clip = simple_binding()
    curve = {"target": "translation.x", "interpolation": "cubic",
             "frames": [0., 5., 5., 10.], "values": [0., 1., 1., 4.],
             "slopes": [.2, .2, .6, .6]}
    clip["bones"][0]["curves"] = [curve]
    written = blender.export_bundle([mesh], tmp_path, "slope", formats=("glb",),
                                    animation=gltf_animation.bind(skin, clip))
    doc, blob = glb_parts(written["glb"].read_bytes())
    channel = next(c for c in doc["animations"][0]["channels"] if c["target"]["path"] == "translation")
    for frame in (0., 1., 4.9, 5., 5.1, 7., 10.):
        assert sample_channel(doc, blob, channel, frame / 30)[0] == pytest.approx(
            animation.evaluate(curve, frame), abs=2e-5)


def test_curve_sampling_approximates_hermite_and_multiturn_euler_rotations(tmp_path):
    mesh, skin, clip = simple_binding()
    curves = [
        {"target": "translation.x", "interpolation": "cubic", "frames": [2., 8.],
         "values": [1., 3.], "slopes": [.6, -.4]},
        {"target": "translation.y", "interpolation": "cubic", "frames": [0., 3., 7., 10.],
         "values": [0., 1., -1., .5], "slopes": [.3, -.1, .5, 0.]},
        {"target": "rotation.z", "interpolation": "cubic", "frames": [0., 10.],
         "values": [0., 12*math.pi], "slopes": [1.2*math.pi, 1.2*math.pi]},
        {"target": "rotation.y", "interpolation": "cubic", "frames": [0., 5., 10.],
         "values": [0., .7, -.4], "slopes": [.2, -.1, .1]},
    ]
    clip["bones"][0]["curves"] = curves
    written = blender.export_bundle([mesh], tmp_path, "curves", formats=("glb",),
                                    animation=gltf_animation.bind(skin, clip))
    doc, blob = glb_parts(written["glb"].read_bytes())
    channels = {c["target"]["path"]: c for c in doc["animations"][0]["channels"]}
    assert doc["animations"][0]["samplers"][channels["translation"]["sampler"]]["interpolation"] == "LINEAR"
    assert doc["animations"][0]["samplers"][channels["rotation"]["sampler"]]["interpolation"] == "LINEAR"
    for k in range(201):
        frame = k / 20
        xyz = sample_channel(doc, blob, channels["translation"], frame / 30)
        assert xyz[:2] == pytest.approx([animation.evaluate(c, frame) for c in curves[:2]], abs=2e-5)
        actual = sample_channel(doc, blob, channels["rotation"], frame / 30)
        expected = gltf_animation._quat([0., animation.evaluate(curves[3], frame), animation.evaluate(curves[2], frame)])
        assert gltf_animation._angle(actual, expected) < .0025


def test_animated_export_refuses_incompatible_variants_and_keeps_previous_output(tmp_path):
    project = project_with_animation(tmp_path)
    static = project.export_model("Rigid.bfres", textures=False)
    target = Path(static["absolute_folder"]) / "Rigid.glb"
    original = target.read_bytes()
    with pytest.raises(Exception, match="hors limites"):
        project.export_model("Rigid.bfres", textures=False, animation_rel="Walk.anim.bfres", animation_index=4)
    assert target.read_bytes() == original
    with pytest.raises(ProjectError):
        project.export_model("Rigid.bfres", animation_rel="Walk.anim.bfres", animation_index=None)
    with pytest.raises(ProjectError):
        project.export_model("Rigid.bfres", animation_rel="Walk.anim.bfres", animation_index=True)
    mesh, skin, clip = simple_binding(bones=2)
    bad = copy.deepcopy(clip)
    bad["bones"][0]["curves"][0]["frames"] = [10., 0.]
    with pytest.raises(gltf_animation.ExportAnimationError, match="non triées"):
        gltf_animation.bind(skin, bad)
    bad = copy.deepcopy(clip)
    bad["bones"][0]["curves"][0]["frames"] = [0., float("nan")]
    with pytest.raises(gltf_animation.ExportAnimationError, match="non finie"):
        gltf_animation.bind(skin, bad)
    bad = copy.deepcopy(clip)
    bad["bones"][0]["curves"][0]["slopes"] = [0.]
    with pytest.raises(gltf_animation.ExportAnimationError, match="incomplètes"):
        gltf_animation.bind(skin, bad)
    bad = copy.deepcopy(clip)
    bad["bones"][0]["curves"][0]["target"] = "rotation.w"
    with pytest.raises(gltf_animation.ExportAnimationError, match="rotation"):
        gltf_animation.bind(skin, bad)
    bad = copy.deepcopy(clip)
    bad["bones"][0]["name"] = "Missing"
    with pytest.raises(gltf_animation.ExportAnimationError, match="Aucun os animé"):
        gltf_animation.bind(skin, bad)
    skin["bones"][1]["parent"] = 0
    skin["bones"][1]["ssc"] = True
    clip["bones"][0]["curves"].append({"target": "scale.x", "interpolation": "cubic",
                                      "frames": [0., 10.], "values": [1., 2.], "slopes": [0., 0.]})
    with pytest.raises(gltf_animation.ExportAnimationError, match="Compensation d'échelle"):
        gltf_animation.bind(skin, clip)


def test_api_requires_explicit_valid_selection_and_rejects_path_escape(tmp_path):
    project = project_with_animation(tmp_path)
    server = HookyCreatorServer(project.root, tmp_path / "other-work", host="127.0.0.1", port=0)
    for selected in ({}, {"path": None, "index": None}, {"path": "Walk.anim.bfres", "index": True},
                     {"path": "Walk.anim.bfres", "index": "0"}, {"path": "x", "index": 0, "extra": 1}):
        with pytest.raises(ProjectError):
            server.api("POST", "/api/model/export", {}, {"path": "Rigid.bfres", "animation": selected})
    with pytest.raises(Exception):
        server.api("POST", "/api/model/export", {}, {"path": "Rigid.bfres",
                   "animation": {"path": "../Walk.anim.bfres", "index": 0}})
    payload, mime = server.api("POST", "/api/model/export", {}, {"path": "Rigid.bfres", "textures": False,
                               "animation": {"path": "Walk.anim.bfres", "index": 0}})
    assert mime == "application/json"
    assert json.loads(payload)["animation"]["name"] == "Walk"


def test_glb_embeds_material_images_and_external_uris_are_encoded(tmp_path):
    mesh, _, _ = simple_binding()
    image = tmp_path / "albédo #1.png"
    png = util.png_bytes(1, 1, bytes((200, 30, 10, 255)))
    image.write_bytes(png)
    written = blender.export_bundle([mesh], tmp_path, "modèle", formats=("glb", "gltf"),
                                    textures={"Surface": str(image)})
    doc, blob = glb_parts(written["glb"].read_bytes())
    assert doc["images"][0]["mimeType"] == "image/png" and "uri" not in doc["images"][0]
    view = doc["bufferViews"][doc["images"][0]["bufferView"]]
    assert view["byteOffset"] % 4 == 0
    assert blob[view["byteOffset"]:view["byteOffset"]+view["byteLength"]] == png
    external = json.loads(written["gltf"].read_text())
    assert unquote(external["images"][0]["uri"]) == image.name
    assert unquote(external["buffers"][0]["uri"]) == "modèle.bin"
    assert "%" in external["images"][0]["uri"]


def test_obj_shared_material_names_and_mtl_references_are_consistent(tmp_path):
    one, _, _ = simple_binding()
    two = copy.deepcopy(one); two.name = "Second"; two.material = "Surface"
    three = copy.deepcopy(one); three.name = "Second"; three.material = "Surface #A\nnewmtl fake"
    four = copy.deepcopy(one); four.name = "Fourth"; four.material = "Surface__A_newmtl_fake"
    written = blender.export_bundle([one, two, three, four], tmp_path, "model", formats=("obj",),
                                    material_maps={"Surface": {"albedo": "good.png"},
                                                   three.material: {"albedo": "other.png"}})
    assert written["mtl"].name == "model.mtl"
    obj = written["obj"].read_text().splitlines()
    mtl = written["mtl"].read_text().splitlines()
    uses = [line[7:] for line in obj if line.startswith("usemtl ")]
    declared = [line[7:] for line in mtl if line.startswith("newmtl ")]
    groups = [line[2:] for line in obj if line.startswith("g ")]
    assert uses[0] == uses[1] and len(declared) == 3 and set(uses) == set(declared)
    assert len(set(groups)) == 4 and len(set(declared)) == 3
    assert "map_Kd good.png" in mtl and "map_Kd other.png" in mtl
    assert "newmtl fake" not in mtl


def test_no_uv_means_no_invalid_gltf_texture_binding(tmp_path):
    mesh, _, _ = simple_binding()
    mesh.uvs = None
    image = tmp_path / "a.png"
    image.write_bytes(util.png_bytes(1, 1, bytes((1, 2, 3, 255))))
    written = blender.export_bundle([mesh], tmp_path, "no_uv", textures={"Surface": str(image)},
                                    formats=("gltf", "glb"), warnings=[])
    doc, _ = glb_parts(written["glb"].read_bytes())
    assert "baseColorTexture" not in doc["materials"][0]["pbrMetallicRoughness"]
    assert blender.NO_UV_WARNING in written["report"].read_text()


def test_sampling_respects_total_budget_even_for_static_components(tmp_path, monkeypatch):
    mesh, skin, clip = simple_binding()
    clip["frames"] = 0
    clip["bones"][0]["base"]["scale"] = [1, 1, 1]
    monkeypatch.setattr(gltf_animation, "MAX_SAMPLES", 1)
    with pytest.raises(gltf_animation.ExportAnimationError, match="échantillons|images-clés"):
        blender.export_bundle([mesh], tmp_path, "limited", formats=("glb",),
                              animation=gltf_animation.bind(skin, clip))
    assert not (tmp_path / "limited.glb").exists()


@pytest.mark.skipif(os.environ.get("HOOKY_TEST_GLTF_VALIDATOR") != "1",
                    reason="Khronos glTF validator is an optional test dependency")
def test_khronos_validator_accepts_static_animated_and_eight_weight_exports(tmp_path):
    project = project_with_animation(tmp_path)
    result = project.export_model("Rigid.bfres", textures=False,
                                  animation_rel="Walk.anim.bfres", animation_index=0)
    folder = Path(result["absolute_folder"])
    mesh, skin, clip = simple_binding(weights=[11, 19, 23, 31, 37, 41, 43, 47], bones=8)
    rig = blender.export_bundle([mesh], tmp_path, "eight", formats=("glb",),
                                animation=gltf_animation.bind(skin, clip))
    image = tmp_path / "albédo #1.png"
    image.write_bytes(util.png_bytes(1, 1, bytes((20, 30, 40, 255))))
    textured = blender.export_bundle([mesh], tmp_path, "modèle", formats=("gltf", "glb"),
                                     textures={"Surface": str(image)})
    jump_clip = copy.deepcopy(clip)
    jump_clip["bones"][0]["curves"][0].update(
        frames=[0., 5., 5., 10.], values=[0., 1., 3., 4.], slopes=[.2] * 4)
    jump = blender.export_bundle([mesh], tmp_path, "jump", formats=("glb", "gltf"),
                                 animation=gltf_animation.bind(skin, jump_clip))
    files = [folder / "Rigid.glb", folder / "Rigid.gltf", rig["glb"], textured["glb"], textured["gltf"],
             jump["glb"], jump["gltf"]]
    script = Path(__file__).resolve().parents[1] / "tools" / "validate_gltf.js"
    run = subprocess.run(["node", str(script), *(str(f) for f in files)],
                         capture_output=True, text=True, check=False, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    reports = json.loads(run.stdout)
    assert len(reports) == len(files)
    assert all(report["numErrors"] == 0 for report in reports)


def test_model_or_animation_changed_during_preparation_refuses_to_publish(tmp_path):
    project = project_with_animation(tmp_path)
    previous = project.export_model("Rigid.bfres", textures=False)
    target = Path(previous["absolute_folder"]) / "Rigid.glb"
    original = target.read_bytes()
    export = blender.export_bundle
    def mutate(*args, **kwargs):
        result = export(*args, **kwargs)
        source = project.root / "Walk.anim.bfres"
        data = bytearray(source.read_bytes()); data[-1] ^= 1
        replacement = source.with_suffix(".new")
        replacement.write_bytes(data)
        os.replace(replacement, source)
        return result
    with patch.object(blender, "export_bundle", side_effect=mutate):
        with pytest.raises(ProjectError, match="a changé"):
            project.export_model("Rigid.bfres", textures=False,
                                 animation_rel="Walk.anim.bfres", animation_index=0)
    assert target.read_bytes() == original
