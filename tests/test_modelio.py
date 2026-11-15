"""Import standard : index OBJ distincts, glTF/GLB, transformations et refus sûrs."""
from __future__ import annotations

import base64
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hookymod import blender, modelio
from hookymod.mesh import MeshData
from hookymod.util import HookyError

OBJ = b"""\
o Plan
v 0 0 0
v 1 0 0
v 0 1 0
v 1 1 0
vt 0 0
vt 1 0
vt 0 1
vt 1 1
vt .5 .5
vn 0 0 1
usemtl Surface
f 1/1/1 2/2/1 3/3/1
f -3/5/1 -1/4/1 -2/3/1
"""


def fixture():
    # Les positions et normales entrelacées testent byteStride et byteOffset.
    vertices = b"".join(struct.pack("<6f", *p, 0, 0, 1) for p in
                        [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)])
    uv = struct.pack("<6H", 0, 0, 65535, 0, 0, 65535)
    indices = struct.pack("<3H", 0, 1, 2)
    data = vertices + uv + indices
    doc = {"asset": {"version": "2.0"}, "scene": 0,
           "scenes": [{"nodes": [0]}],
           "nodes": [{"name": "Transforme", "children": [1], "translation": [2, 3, 4]},
                     {"mesh": 0, "scale": [-2, 3, 1]}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                                         "indices": 3, "material": 0}]}],
           "materials": [{"name": "Surface"}],
           "buffers": [{"uri": "geometrie.bin", "byteLength": len(data)}],
           "bufferViews": [{"buffer": 0, "byteLength": len(vertices), "byteStride": 24},
                           {"buffer": 0, "byteOffset": len(vertices), "byteLength": len(uv)},
                           {"buffer": 0, "byteOffset": len(vertices)+len(uv), "byteLength": len(indices)}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                         {"bufferView": 0, "byteOffset": 12, "componentType": 5126, "count": 3, "type": "VEC3"},
                         {"bufferView": 1, "componentType": 5123, "normalized": True, "count": 3, "type": "VEC2"},
                         {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"}]}
    return doc, data


def glb(doc, data):
    doc = json.loads(json.dumps(doc))
    del doc["buffers"][0]["uri"]
    encoded = json.dumps(doc).encode()
    encoded += b" " * (-len(encoded) % 4)
    data += b"\0" * (-len(data) % 4)
    body = struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    body += struct.pack("<II", len(data), 0x004E4942) + data
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


class ObjTests(unittest.TestCase):
    def test_separate_indices_negative_indices_uv_seam_and_material(self):
        result = modelio.read_model("plan.obj", OBJ)
        mesh = result.meshes[0]
        self.assertEqual(mesh.material, "Surface")
        self.assertEqual(len(mesh.positions), 5)  # même sommet, UV différentes
        self.assertEqual(mesh.faces, [(0, 1, 2), (3, 4, 2)])
        self.assertEqual(mesh.uvs, [(0., 1.), (1., 1.), (0., 0.), (.5, .5), (1., 0.)])
        self.assertEqual(mesh.normals, [(0., 0., 1.)] * 5)

    def test_invalid_indices_nonfinite_and_incomplete_uv_are_refused(self):
        for raw in (b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9\n",
                    OBJ.replace(b"v 1 0 0", b"v nan 0 0"),
                    OBJ.replace(b"3/3/1", b"3//1", 1)):
            with self.subTest(raw=raw[:20]), self.assertRaises(HookyError):
                modelio.read_model("input.obj", raw)

    def test_quads_are_triangulated_with_warning(self):
        result = modelio.read_model("input.obj", b"v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n")
        self.assertEqual(result.meshes[0].faces, [(0, 1, 2), (0, 2, 3)])
        self.assertTrue(result.warnings)


class GltfTests(unittest.TestCase):
    def test_external_buffer_transforms_normals_uv_and_mirror_winding(self):
        doc, data = fixture()
        result = modelio.read_model("mesh.gltf", json.dumps(doc).encode(), {"geometrie.bin": data})
        mesh = result.meshes[0]
        self.assertEqual(mesh.positions, [(2., 3., 4.), (0., 3., 4.), (2., 6., 4.)])
        self.assertEqual(mesh.faces, [(0, 2, 1)])
        self.assertEqual(mesh.uvs, [(0., 0.), (1., 0.), (0., 1.)])
        self.assertEqual(mesh.normals, [(0., 0., 1.)] * 3)
        self.assertEqual(mesh.material, "Surface")

    def test_glb_and_data_uri(self):
        doc, data = fixture()
        expected = modelio.read_model("mesh.glb", glb(doc, data)).meshes[0]
        doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(data).decode()
        actual = modelio.read_model("mesh.gltf", json.dumps(doc).encode()).meshes[0]
        self.assertEqual(actual.positions, expected.positions)
        self.assertEqual(actual.faces, expected.faces)
        self.assertEqual(actual.uvs, expected.uvs)

    def test_uri_safety_sparse_bounds_skin_and_cycles(self):
        original, data = fixture()
        variants = []
        def variant():
            item = json.loads(json.dumps(original))
            variants.append(item)
            return item
        variant()["buffers"][0]["uri"] = "../../secret.bin"
        variant()["buffers"][0]["uri"] = "https://example.org/a.bin"
        variant()["buffers"][0]["uri"] = "%2e%2e/secret.bin"
        variant()["accessors"][0]["sparse"] = {"count": 1}
        variant()["accessors"][0]["count"] = 1000
        variant()["nodes"][1]["skin"] = 0
        variant()["nodes"][1]["children"] = [0]
        variant()["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}]
        variant()["extensionsRequired"] = ["KHR_draco_mesh_compression"]
        for item in variants:
            with self.subTest(item=item), self.assertRaises(HookyError):
                modelio.read_model("x.gltf", json.dumps(item).encode(), {"geometrie.bin": data})
        with self.assertRaisesRegex(HookyError, "manquant"):
            modelio.read_model("x.gltf", json.dumps(original).encode())
        with self.assertRaises(HookyError):
            modelio.read_model("x.glb", glb(original, data)[:-1])

    def test_gltf_degenerate_triangles_are_rejected(self):
        doc, data = fixture()
        repeated = data[:-6] + struct.pack("<3H", 0, 0, 2)
        aligned = bytearray(data)
        struct.pack_into("<3f", aligned, 24, 1., 0., 0.)
        struct.pack_into("<3f", aligned, 48, 2., 0., 0.)
        for raw in (repeated, bytes(aligned)):
            with self.subTest(raw=raw), self.assertRaisesRegex(HookyError, "dégénér"):
                modelio.read_model("mesh.gltf", json.dumps(doc).encode(), {"geometrie.bin": raw})

    def test_buffer_total_and_attribute_types_are_checked_before_decoding(self):
        with patch.object(modelio, "MAX_BYTES", 10):
            doc = {"buffers": [{"uri": "one.bin", "byteLength": 6},
                               {"uri": "one.bin", "byteLength": 6}]}
            with self.assertRaisesRegex(HookyError, "total"):
                modelio._buffers(doc, None, {"one.bin": b"123456"})
        doc, data = fixture()
        for idx, component, normalized in ((0, 5123, False), (1, 5123, True), (2, 5123, False)):
            variant = json.loads(json.dumps(doc))
            variant["accessors"][idx]["componentType"] = component
            variant["accessors"][idx]["normalized"] = normalized
            with self.subTest(accessor=idx), self.assertRaises(HookyError):
                modelio.read_model("mesh.gltf", json.dumps(variant).encode(), {"geometrie.bin": data})
        too_many = json.loads(json.dumps(doc))
        too_many["meshes"][0]["primitives"] *= 2
        with patch.object(modelio, "MAX_VERTICES", 5), self.assertRaisesRegex(HookyError, "trop grande"):
            modelio.read_model("mesh.gltf", json.dumps(too_many).encode(), {"geometrie.bin": data})

    def test_own_export_reimports_all_triangles_and_uv(self):
        source = MeshData(name="m", positions=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                          uvs=[(0, 0), (.5, 1), (1, 0)], normals=[(0, 0, 1)] * 3,
                          faces=[(0, 1, 2)], material="m")
        with tempfile.TemporaryDirectory() as tmp:
            files = blender.export_bundle([source], Path(tmp), "test")
            result = modelio.read_model("test.gltf", files["gltf"].read_bytes(),
                                        {"test.bin": files["bin"].read_bytes()})
            self.assertEqual(result.meshes[0].positions, source.positions)
            self.assertEqual(result.meshes[0].uvs, source.uvs)
            self.assertEqual(result.meshes[0].faces, source.faces)
            result_obj = modelio.read_model("test.obj", files["obj"].read_bytes())
            self.assertEqual(result_obj.meshes[0].uvs, source.uvs)


if __name__ == "__main__":
    unittest.main()
