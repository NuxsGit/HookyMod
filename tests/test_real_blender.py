"""Interopérabilité optionnelle : un vrai logiciel ouvre les exports standards.

Blender est seulement un consommateur glTF/OBJ dans ce test ; HookyCreator ne
produit ni ne lit de fichier de projet Blender.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from hookycreator.project import Project
from hookymod import bfres_v10
from tests import synth
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
                self.assertIn("Heros/Heros.obj", archive.namelist())
                self.assertIn("Heros/Heros_Alb.png", archive.namelist())
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
if kind == "gltf":
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
            for kind in ("gltf", "obj"):
                with self.subTest(kind=kind):
                    completed = subprocess.run(
                        [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                         "--python", str(script), "--", str(folder / f"Heros.{kind}"), kind],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
                    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                    line = next((line for line in completed.stdout.splitlines()
                                 if line.startswith("HOOKY_VERIFY=")), "")
                    self.assertTrue(line, completed.stdout + completed.stderr)
                    verified = json.loads(line.partition("=")[2])
                    self.assertGreater(verified["objects"], 0)
                    self.assertEqual(verified["faces"], result["faces"])
                    self.assertGreater(verified["uv_meshes"], 0)
                    if kind == "gltf":
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
if kind == "gltf":
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
        base = []
        normal = []
        for link in links:
            if link.to_node.type != "BSDF_PRINCIPLED":
                continue
            if link.to_socket.name == "Base Color" and link.from_node.type == "TEX_IMAGE" and link.from_node.image:
                base.append(os.path.basename(bpy.path.abspath(link.from_node.image.filepath)))
            if link.to_socket.name == "Normal" and link.from_node.type == "NORMAL_MAP":
                normal.extend(os.path.basename(bpy.path.abspath(n.from_node.image.filepath))
                              for n in links if n.to_node == link.from_node
                              and n.to_socket.name == "Color" and n.from_node.type == "TEX_IMAGE"
                              and n.from_node.image)
        materials[material.name] = {"base": base, "normal": normal}
print("HOOKY_MATERIALS=" + json.dumps({"faces": faces, "materials": materials}))
''', encoding="utf-8")
            for kind in ("gltf", "obj"):
                with self.subTest(kind=kind):
                    completed = subprocess.run(
                        [BLENDER, "--background", "--factory-startup", "--python-exit-code", "7",
                         "--python", str(script), "--", str(folder / f"Objet.{kind}"), kind],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
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


if __name__ == "__main__":
    unittest.main()
