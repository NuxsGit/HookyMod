"""Interopérabilité optionnelle : un vrai logiciel ouvre les exports standards.

Blender est seulement un consommateur glTF/OBJ dans ce test ; HookyCreator ne
produit ni ne lit de fichier de projet Blender.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from hookycreator.project import Project
from hookymod import bfres_v10, blender
from tests import synth
from tests.synth_animation import (
    fska_fixture,
    fska_mixed_quaternion_fixture,
    fska_two_clips_with_jump,
    named_rigid_model,
    player_variant,
)
from tests.test_bfres_v10 import DataDrivenTests, make_v10
from tests.test_bfres_v10_struct import structured_model
from tests.test_ensembles_blend import _make_project

BLENDER = os.environ.get("HOOKY_TEST_BLENDER") or shutil.which("blender")


@unittest.skipUnless(BLENDER, "Blender non installé ; interopérabilité facultative")
class RealConsumerTests(unittest.TestCase):
    def test_gltf_and_obj_open_with_uvs_and_gltf_material_link(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hooky-formats-consumer-") as tmp:
            project = _make_project(Path(tmp), animations=False)
            positions, uvs, triangles = DataDrivenTests()._grid()
            (project.root / "Model" / "Heros.bfres").write_bytes(make_v10(positions, uvs, triangles))
            project.scan()
            result = project.export_model("Model/Heros.bfres", archive=True)
            folder = Path(result["absolute_folder"])
            token = result["download_url"].split("=", 1)[1]
            with zipfile.ZipFile(project.export_download(token)) as archive:
                self.assertIn("Heros/Heros.gltf", archive.namelist())
                self.assertIn("Heros/Heros.glb", archive.namelist())
                self.assertIn("Heros/Heros.obj", archive.namelist())
                self.assertIn("Heros/Heros.mtl", archive.namelist())
                self.assertIn("Heros/Heros_Alb.png", archive.namelist())
                # Extraire le GLB seul : ses textures doivent voyager avec lui.
                standalone = Path(tmp) / "standalone.glb"
                standalone.write_bytes(archive.read("Heros/Heros.glb"))
                document = json.loads(archive.read("Heros/Heros.gltf"))
                self.assertIn("TEXCOORD_0", document["meshes"][0]["primitives"][0]["attributes"])

            script = Path(tmp) / "inspect_export.py"
            script.write_text('''
import bpy, json, sys
path, kind = sys.argv[sys.argv.index("--") + 1:]
# La scène de démarrage de Blender peut contenir un cube : compter uniquement
# les objets importés, sans dépendre de la configuration de l'utilisateur.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
if kind in ("gltf", "glb"):
    bpy.ops.import_scene.gltf(filepath=path)
elif bpy.app.version >= (4, 0, 0):
    bpy.ops.wm.obj_import(filepath=path)
else:
    bpy.ops.import_scene.obj(filepath=path)
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
links = [link for material in bpy.data.materials if material.use_nodes
         for link in material.node_tree.links
         if link.from_node.type == "TEX_IMAGE" and link.from_node.image
         and link.from_node.image.name.startswith("Heros_Alb")
         and link.to_node.type == "BSDF_PRINCIPLED" and link.to_socket.name == "Base Color"]
print("HOOKY_VERIFY=" + json.dumps({"objects": len(meshes),
      "faces": sum(len(o.data.polygons) for o in meshes),
      "uv_meshes": sum(bool(o.data.uv_layers) for o in meshes), "albedo_links": len(links)}))
''', encoding="utf-8")
            for kind in ("gltf", "glb", "obj"):
                with self.subTest(kind=kind):
                    source = standalone if kind == "glb" else folder / f"Heros.{kind}"
                    completed = subprocess.run(
                        [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                         "--python", str(script), "--", str(source), kind],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
                    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                    line = next((line for line in completed.stdout.splitlines()
                                 if line.startswith("HOOKY_VERIFY=")), "")
                    self.assertTrue(line, completed.stdout + completed.stderr)
                    verified = json.loads(line.partition("=")[2])
                    self.assertGreater(verified["objects"], 0)
                    self.assertEqual(verified["faces"], result["faces"])
                    self.assertGreater(verified["uv_meshes"], 0)
                    if kind in ("gltf", "glb"):
                        self.assertGreater(verified["albedo_links"], 0)

    def test_structured_materials_uv_and_normal_maps_survive_both_importers(self) -> None:
        """Vérifie les données réellement reçues par Blender, pas seulement le JSON/MTL."""
        with tempfile.TemporaryDirectory(prefix="hooky-material-consumer-") as tmp:
            root = Path(tmp) / "source"
            (root / "Model").mkdir(parents=True)
            (root / "Tex").mkdir()
            data = structured_model()
            (root / "Model/Objet.bfres").write_bytes(data)
            for name, color in {
                "Tex_A_Alb": (250, 0, 0, 255), "Tex_A_Nrm": (128, 128, 255, 255),
                "Tex_B_Alb": (0, 220, 0, 255), "Tex_B_Rgh": (160, 160, 160, 255),
            }.items():
                (root / "Tex" / f"{name}.bntx").write_bytes(
                    synth.make_bntx(4, 4, bytes(color) * 16))
            project = Project(root, Path(tmp) / "work")
            project.scan()
            result = project.export_model("Model/Objet.bfres")
            folder = Path(result["absolute_folder"])

            # Dans Blender les coordonnées monde sont Z-haut et les UV ont V=0
            # en bas. Comparer position et UV par coin, non seulement les comptes.
            meshes, _report = bfres_v10.parse(data)
            expected = {}
            for mesh in meshes:
                expected[mesh.material] = [
                    sorted((round(mesh.positions[i][0], 4), round(-mesh.positions[i][2], 4),
                            round(mesh.positions[i][1], 4), round(mesh.uvs[i][0], 4),
                            round(1 - mesh.uvs[i][1], 4)) for i in face)
                    for face in mesh.faces
                ]
            script = Path(tmp) / "inspect_materials.py"
            script.write_text('''
import bpy, json, os, sys
path, kind = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
if kind in ("gltf", "glb"):
    bpy.ops.import_scene.gltf(filepath=path)
elif bpy.app.version >= (4, 0, 0):
    bpy.ops.wm.obj_import(filepath=path)
else:
    bpy.ops.import_scene.obj(filepath=path)
faces, materials = {}, {}
for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
    uv = obj.data.uv_layers.active
    assert uv is not None, "UV absentes"
    for face in obj.data.polygons:
        material = obj.data.materials[face.material_index]
        assert material is not None and material.use_nodes
        points = []
        for loop in face.loop_indices:
            pos = obj.matrix_world @ obj.data.vertices[obj.data.loops[loop].vertex_index].co
            tex = uv.data[loop].uv
            points.append([float(pos.x), float(pos.y), float(pos.z), float(tex.x), float(tex.y)])
        faces.setdefault(material.name, []).append(points)
        links = material.node_tree.links
        def image_name(image):
            return image.name if image.packed_file else os.path.basename(bpy.path.abspath(image.filepath))
        base = []
        normal = []
        for link in links:
            if link.to_node.type != "BSDF_PRINCIPLED":
                continue
            if link.to_socket.name == "Base Color" and link.from_node.type == "TEX_IMAGE" and link.from_node.image:
                base.append(image_name(link.from_node.image))
            if link.to_socket.name == "Normal" and link.from_node.type == "NORMAL_MAP":
                normal.extend(image_name(n.from_node.image)
                              for n in links if n.to_node == link.from_node
                              and n.to_socket.name == "Color" and n.from_node.type == "TEX_IMAGE"
                              and n.from_node.image)
        materials[material.name] = {"base": base, "normal": normal}
print("HOOKY_MATERIALS=" + json.dumps({"faces": faces, "materials": materials}))
''', encoding="utf-8")
            for kind in ("gltf", "glb", "obj"):
                with self.subTest(kind=kind):
                    completed = subprocess.run(
                        [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                         "--python", str(script), "--", str(folder / f"Objet.{kind}"), kind],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
                    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                    line = next((line for line in completed.stdout.splitlines()
                                 if line.startswith("HOOKY_MATERIALS=")), "")
                    self.assertTrue(line, completed.stdout + completed.stderr)
                    verified = json.loads(line.partition("=")[2])
                    self.assertEqual(set(verified["faces"]), set(expected))
                    for name, faces in expected.items():
                        actual = [sorted(tuple(round(x, 4) for x in point) for point in face)
                                  for face in verified["faces"][name]]
                        self.assertEqual(actual, faces, f"{kind} : positions/UV de {name}")
                    self.assertEqual(verified["materials"]["Mat_A"],
                                     {"base": ["Tex_A_Alb.png"], "normal": ["Tex_A_Nrm.png"]})
                    self.assertEqual(verified["materials"]["Mat_B"],
                                     {"base": ["Tex_B_Alb.png"], "normal": []})

    def test_animated_glb_armature_and_vertices_match_source_motion(self) -> None:
        """Ne pas seulement vérifier le JSON : Blender doit déformer les sommets."""
        import math
        with tempfile.TemporaryDirectory(prefix="hooky-animated-consumer-") as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            (root / "Rigid.bfres").write_bytes(named_rigid_model())
            (root / "Walk.anim.bfres").write_bytes(fska_fixture())
            project = Project(root, Path(tmp) / "work")
            project.scan()
            result = project.export_model("Rigid.bfres", textures=False, archive=True,
                                          animation_rel="Walk.anim.bfres", animation_index=0)
            token = result["download_url"].split("=", 1)[1]
            # Aucune ressource voisine ne doit être nécessaire pour le GLB.
            glb = Path(tmp) / "alone.glb"
            with zipfile.ZipFile(project.export_download(token)) as archive:
                glb.write_bytes(archive.read("Rigid/Rigid.glb"))
            script = Path(tmp) / "inspect_animation.py"
            script.write_text('''
import bpy, json, math, sys
path = sys.argv[sys.argv.index("--") + 1]
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=path)
armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
assert len(armatures) == 1
armature = armatures[0]
obj = next(o for o in bpy.data.objects if o.name == "Forme_A" and o.type == "MESH")
assert any(m.type == "ARMATURE" and m.object == armature for m in obj.modifiers)
assert armature.animation_data and armature.animation_data.action
samples = []
fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
for source_frame in (0, 2.5, 5, 6.25, 10):
    frame = source_frame * fps / 30
    bpy.context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    p = evaluated.matrix_world @ mesh.vertices[0].co
    samples.append([float(p.x), float(p.y), float(p.z)])
    evaluated.to_mesh_clear()
print("HOOKY_ANIMATION=" + json.dumps({"bones": sorted(b.name for b in armature.data.bones),
      "action": armature.animation_data.action.name, "samples": samples}))
''', encoding="utf-8")
            completed = subprocess.run(
                [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                 "--python", str(script), "--", str(glb)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            line = next((line for line in completed.stdout.splitlines() if line.startswith("HOOKY_ANIMATION=")), "")
            self.assertTrue(line, completed.stdout + completed.stderr)
            verified = json.loads(line.partition("=")[2])
            self.assertEqual(verified["bones"], ["Child", "Root"])
            self.assertTrue(verified["action"].startswith("Walk"))
            for frame, actual in zip((0, 2.5, 5, 6.25, 10), verified["samples"]):
                # Fixture : translation linéaire X, rotation Z = (frame/10)^2.
                angle = (frame / 10) ** 2
                expected = (frame * .2 - 2 * math.sin(angle), -.125, 2 * math.cos(angle))
                for value, want in zip(actual, expected):
                    self.assertAlmostEqual(value, want, delta=.001)

    def test_scaled_mixed_rotation_and_constant_ssc_in_blender(self) -> None:
        """Check evaluated vertices for scaled and compensated synthetic rigs."""
        import struct

        from hookycreator import model_skin
        from hookymod import animation, gltf_animation

        with tempfile.TemporaryDirectory(prefix="hooky-player-consumer-") as tmp:
            tmp = Path(tmp)
            paths = []
            for label, compensate, rest_scale, animated_scale, move_child in (
                ("no_ssc", False, (2., 3., 4.), (1., 1., 1.), False),
                ("shear", False, (2., 3., 4.), (2., 3., 4.), False),
                ("uniform", True, (2., 2., 2.), (1., 1., 1.), False),
                ("nonuniform", True, (2., 3., 4.), (2., 3., 4.), True),
                ("mirrored", True, (-2., 3., 4.), (-2., 3., 4.), True),
            ):
                data = bytearray(player_variant(compensate=compensate))
                struct.pack_into("<3f", data, 0x1580 + 0x30, *rest_scale)
                # Non-right-angle rest rotation also creates world-space shear
                # for the nonuniform, uncompensated parent scenario.
                struct.pack_into("<f", data, 0x1580 + 0x58 + 0x44, .6)
                meshes, report = bfres_v10.parse(bytes(data))
                packet = model_skin.encode(bytes(data), meshes, structured=report.structured)
                skin = model_skin.decode(packet, meshes)
                clip = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
                clip["bones"][0]["base"]["scale"] = animated_scale
                if move_child:
                    clip["bones"][1]["curves"].append({
                        "target": "translation.y", "interpolation": "cubic", "frames": [0., 10.],
                        "values": [2., 3.], "slopes": [.1, .1]})
                path = blender.export_bundle(meshes, tmp, label, formats=("glb",),
                                             animation=gltf_animation.bind(skin, clip))["glb"]
                paths.append((path, compensate, animated_scale, move_child))

            script = tmp / "inspect_scaled_player.py"
            script.write_text('''
import bpy, json, math, sys
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=sys.argv[sys.argv.index("--") + 1])
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
obj = next(o for o in bpy.data.objects if o.type == "MESH" and o.name == "Forme_A")
assert arm.animation_data and arm.animation_data.action
assert any(m.type == "ARMATURE" and m.object == arm for m in obj.modifiers)
fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
samples = []
for source_frame in (0., 1.25, 5., 7.5, 10.):
    frame = source_frame * fps / 30
    bpy.context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    points = [evaluated.matrix_world @ mesh.vertices[i].co for i in (0, 1, 2)]
    samples.append([[float(v) for v in point] for point in points])
    evaluated.to_mesh_clear()
print("HOOKY_PLAYER=" + json.dumps({"bones": sorted(b.name for b in arm.data.bones), "samples": samples}))
''', encoding="utf-8")
            for path, compensate, parent_scale, move_child in paths:
                with self.subTest(path=path.name):
                    completed = subprocess.run(
                        [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                         "--python", str(script), "--", str(path)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
                    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                    line = next((line for line in completed.stdout.splitlines() if line.startswith("HOOKY_PLAYER=")), "")
                    self.assertTrue(line, completed.stdout + completed.stderr)
                    verified = json.loads(line.partition("=")[2])
                    bones = ["Child", "Root"] + (["__HookySSC_1_Child"] if compensate else [])
                    self.assertEqual(verified["bones"], sorted(bones))
                    for frame, points in zip((0., 1.25, 5., 7.5, 10.), verified["samples"]):
                        # Root: x=0.2f, rotation Z=pi*f/20. The child's
                        # translation inherits the parent scale even with SSC.
                        root, child = math.pi * frame / 20, .05 * frame
                        offset = parent_scale[1] * (2 + (.1 * frame if move_child else 0))
                        sx, sy, sz = (1., 1., 1.) if compensate else parent_scale
                        expected = []
                        for u, v in ((0., 0.), (1., 0.), (0., 1.)):
                            x = sx * (math.cos(child)*u - math.sin(child)*v)
                            y = offset + sy * (math.sin(child)*u + math.cos(child)*v)
                            expected.append((.2*frame + math.cos(root)*x - math.sin(root)*y,
                                             -.125*sz, math.sin(root)*x + math.cos(root)*y))
                        for actual, want in zip(points, expected):
                            for value, coordinate in zip(actual, want):
                                self.assertAlmostEqual(value, coordinate, delta=.002)

    def test_switching_grouped_ssc_actions_restores_helper_channels(self) -> None:
        from hookycreator import model_skin
        from hookymod import animation, gltf_animation

        with tempfile.TemporaryDirectory(prefix="hooky-ssc-switch-") as tmp:
            data = player_variant()
            meshes, report = bfres_v10.parse(data)
            skin = model_skin.decode(model_skin.encode(data, meshes, structured=report.structured), meshes)
            first = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
            first["name"] = "Small"
            first["bones"][1]["curves"].append({
                "target": "translation.y", "interpolation": "cubic", "frames": [0., 10.],
                "values": [2., 3.], "slopes": [.1, .1]})
            second = animation.read_clip(fska_mixed_quaternion_fixture(), 0)
            second["name"] = "Large"
            second["bones"][0]["base"]["scale"] = [2., 3., 4.]
            bindings = [gltf_animation.bind(skin, clip) for clip in (first, second)]
            glb = blender.export_bundle(meshes, tmp, "ssc-actions", formats=("glb",), animations=bindings)["glb"]
            script = Path(tmp) / "switch_ssc.py"
            script.write_text("""
import bpy, json, math, sys
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=sys.argv[sys.argv.index('--') + 1])
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
obj = next(o for o in bpy.data.objects if o.type == 'MESH' and o.name == 'Forme_A')
for track in arm.animation_data.nla_tracks:
    track.mute = True
fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
samples = []
for name in ('Small', 'Large', 'Small', 'Large'):
    arm.animation_data.action = next(a for a in bpy.data.actions if a.name.startswith(name))
    frame = 5 * fps / 30
    bpy.context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    points = [list(evaluated.matrix_world @ mesh.vertices[i].co) for i in (0, 1)]
    evaluated.to_mesh_clear()
    samples.append({'name': name, 'points': points})
print('HOOKY_SSC_SWITCH=' + json.dumps(samples))
""", encoding="utf-8")
            completed = subprocess.run(
                [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                 "--python", str(script), "--", str(glb)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            line = next((line for line in completed.stdout.splitlines() if line.startswith("HOOKY_SSC_SWITCH=")), "")
            self.assertTrue(line, completed.stdout + completed.stderr)
            root, child = math.pi / 4, math.pi / 4 + .25
            for sample in json.loads(line.partition("=")[2]):
                offset = 2.5 if sample["name"] == "Small" else 6.
                x, y = 1. - offset * math.sin(root), offset * math.cos(root)
                expected = ((x, -.125, y), (x + math.cos(child), -.125, y + math.sin(child)))
                for actual, want in zip(sample["points"], expected):
                    for value, coordinate in zip(actual, want):
                        self.assertAlmostEqual(value, coordinate, delta=.002)

    def test_blender_imports_one_glb_with_two_distinct_actions_on_one_skin(self) -> None:
        from urllib.parse import parse_qs, urlparse

        from hookymod import animation
        with tempfile.TemporaryDirectory(prefix="hooky-multi-consumer-") as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            source = fska_two_clips_with_jump()
            (root / "Rigid.bfres").write_bytes(named_rigid_model())
            (root / "Rigid.anim.bfres").write_bytes(source)
            project = Project(root, Path(tmp) / "work")
            project.scan()
            result = project.export_model("Rigid.bfres", textures=False, all_animations=True, archive=True)
            params = parse_qs(urlparse(result["download_url"]).query)
            glb = Path(tmp) / "alone.glb"
            glb.write_bytes(project.export_download(params["id"][0], "glb").read_bytes())
            script = Path(tmp) / "inspect_multi.py"
            script.write_text("""
import bpy, json, math, sys
path = sys.argv[sys.argv.index('--') + 1]
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=path)
armatures = [o for o in bpy.data.objects if o.type == 'ARMATURE']
assert len(armatures) == 1
arm = armatures[0]
obj = next(o for o in bpy.data.objects if o.name == 'Forme_A' and o.type == 'MESH')
assert any(m.type == 'ARMATURE' and m.object == arm for m in obj.modifiers)
assert arm.animation_data and len(bpy.data.actions) == 2
for track in arm.animation_data.nla_tracks:
    track.mute = True
fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
samples = {}
for action in bpy.data.actions:
    arm.animation_data.action = action
    points = []
    for source_frame in (0., 2.5, 4.9, 5.1, 7., 10.):
        frame = source_frame * fps / 30
        bpy.context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        point = evaluated.matrix_world @ mesh.vertices[0].co
        points.append([float(point.x), float(point.y), float(point.z)])
        evaluated.to_mesh_clear()
    samples[action.name] = {'range': list(action.frame_range), 'points': points}
print('HOOKY_MULTI=' + json.dumps({'bones': sorted(b.name for b in arm.data.bones),
                                   'fps': fps, 'samples': samples}))
""", encoding="utf-8")
            completed = subprocess.run(
                [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                 "--python", str(script), "--", str(glb)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            line = next((line for line in completed.stdout.splitlines() if line.startswith("HOOKY_MULTI=")), "")
            self.assertTrue(line, completed.stdout + completed.stderr)
            verified = json.loads(line.partition("=")[2])
            self.assertEqual(verified["bones"], ["Child", "Root"])
            self.assertEqual(len(verified["samples"]), 2)
            for index, name in enumerate(("Walk", "Run")):
                action = next((value for key, value in verified["samples"].items() if key.startswith(name)), None)
                self.assertIsNotNone(action)
                self.assertAlmostEqual(action["range"][0], 0., delta=.001)
                self.assertAlmostEqual(action["range"][1], 10 * verified["fps"] / 30, delta=.001)
                curve = animation.read_clip(source, index)["bones"][0]["curves"][0]
                for frame, point in zip((0., 2.5, 4.9, 5.1, 7., 10.), action["points"]):
                    angle = (frame / 10) ** 2
                    expected = (animation.evaluate(curve, frame) - 2 * math.sin(angle),
                                -.125, 2 * math.cos(angle))
                    for value, want in zip(point, expected):
                        self.assertAlmostEqual(value, want, delta=.002)

    def test_switching_grouped_actions_restores_the_other_bones_and_channels(self) -> None:
        from tests.test_multi_animation_export import independent_bindings
        with tempfile.TemporaryDirectory(prefix="hooky-action-switch-") as tmp:
            mesh, _skin, bindings = independent_bindings()
            glb = blender.export_bundle([mesh], tmp, "independent", formats=("glb",),
                                        animations=bindings)["glb"]
            script = Path(tmp) / "switch_actions.py"
            script.write_text("""
import bpy, json, math, sys
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=sys.argv[sys.argv.index('--') + 1])
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
for track in arm.animation_data.nla_tracks:
    track.mute = True
fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
samples = []
for name in ('Move', 'Spin', 'Bounce', 'Spin', 'Move', 'Bounce'):
    arm.animation_data.action = next(a for a in bpy.data.actions if a.name.startswith(name))
    frame = 10 * fps / 30
    bpy.context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
    b0, b1 = arm.pose.bones['Bone0'], arm.pose.bones['Bone1']
    samples.append({'name': name, 'location0': list(b0.location),
                    'rotation0': list(b0.rotation_quaternion), 'location1': list(b1.location)})
print('HOOKY_SWITCH=' + json.dumps(samples))
""", encoding="utf-8")
            completed = subprocess.run(
                [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                 "--python", str(script), "--", str(glb)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            line = next((line for line in completed.stdout.splitlines() if line.startswith("HOOKY_SWITCH=")), "")
            self.assertTrue(line, completed.stdout + completed.stderr)
            expected = {
                "Move": ([3., 0., 0.], [1., 0., 0., 0.], [0., 0., 0.]),
                "Spin": ([0., 0., 0.], [math.cos(.5), 0., 0., math.sin(.5)], [0., 0., 0.]),
                "Bounce": ([0., 0., 0.], [1., 0., 0., 0.], [0., 2., 0.]),
            }
            for item in json.loads(line.partition("=")[2]):
                for key, values in zip(("location0", "rotation0", "location1"), expected[item["name"]]):
                    for actual, want in zip(item[key], values):
                        self.assertAlmostEqual(actual, want, delta=.001)

    def test_blender_imports_one_clip_with_duplicate_time_jumps(self) -> None:
        from hookymod import animation, gltf_animation
        from tests.test_animated_export import simple_binding
        with tempfile.TemporaryDirectory(prefix="hooky-jump-consumer-") as tmp:
            mesh, skin, clip = simple_binding()
            curve = {"target": "translation.x", "interpolation": "cubic",
                     "frames": [0., 0., 3., 5., 5., 8., 10., 10.],
                     "values": [0., .4, .7, .9, 1.5, 1.8, 2., 2.3],
                     "slopes": [.1] * 8}
            clip["bones"][0]["curves"] = [curve]
            result = blender.export_bundle([mesh], tmp, "jump", formats=("glb",),
                                           animation=gltf_animation.bind(skin, clip))
            script = Path(tmp) / "inspect_jump.py"
            script.write_text('''
import bpy, json, math, sys
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=sys.argv[sys.argv.index("--") + 1])
obj = next(o for o in bpy.data.objects if o.type == "MESH" and o.name == "Shape")
armature = next(o for o in bpy.data.objects if o.type == "ARMATURE")
fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
samples = []
for source_frame in (0, .1, 1, 4.9, 5, 5.1, 9.9, 10):
    frame = source_frame * fps / 30
    bpy.context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    samples.append(float((evaluated.matrix_world @ mesh.vertices[0].co).x))
    evaluated.to_mesh_clear()
print("HOOKY_JUMP=" + json.dumps({"actions": [a.name for a in bpy.data.actions],
    "action": armature.animation_data.action.name, "samples": samples}))
''', encoding="utf-8")
            completed = subprocess.run(
                [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                 "--python", str(script), "--", str(result["glb"])],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            line = next((line for line in completed.stdout.splitlines() if line.startswith("HOOKY_JUMP=")), "")
            self.assertTrue(line, completed.stdout + completed.stderr)
            verified = json.loads(line.partition("=")[2])
            self.assertEqual(len(verified["actions"]), 1)
            self.assertTrue(verified["action"].startswith("Test"))
            for frame, actual in zip((0, .1, 1, 4.9, 5, 5.1, 9.9, 10), verified["samples"]):
                self.assertAlmostEqual(actual, animation.evaluate(curve, frame), delta=.002)

    def test_blender_keeps_eight_influences_in_glb(self) -> None:
        from hookymod import gltf_animation
        from tests.test_animated_export import simple_binding
        with tempfile.TemporaryDirectory(prefix="hooky-eight-influences-") as tmp:
            mesh, skin, clip = simple_binding(weights=[11, 19, 23, 31, 37, 41, 43, 47], bones=8)
            result = blender.export_bundle([mesh], tmp, "eight", formats=("glb",),
                                           animation=gltf_animation.bind(skin, clip))
            script = Path(tmp) / "inspect_weights.py"
            script.write_text('''
import bpy, json, sys
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=sys.argv[sys.argv.index("--") + 1])
obj = next(o for o in bpy.data.objects if o.type == "MESH" and o.name == "Shape")
weights = {obj.vertex_groups[v.group].name: float(v.weight) for v in obj.data.vertices[0].groups}
print("HOOKY_WEIGHTS=" + json.dumps(weights))
''', encoding="utf-8")
            completed = subprocess.run(
                [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                 "--python", str(script), "--", str(result["glb"])],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            line = next((line for line in completed.stdout.splitlines() if line.startswith("HOOKY_WEIGHTS=")), "")
            self.assertTrue(line, completed.stdout + completed.stderr)
            weights = json.loads(line.partition("=")[2])
            self.assertEqual(set(weights), {f"Bone{i}" for i in range(8)})
            for i, value in enumerate((11, 19, 23, 31, 37, 41, 43, 47)):
                self.assertAlmostEqual(weights[f"Bone{i}"], value / 252, delta=.00001)


if __name__ == "__main__":
    unittest.main()
