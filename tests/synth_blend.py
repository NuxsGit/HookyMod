"""Fabrique de fichiers ``.blend`` minimalistes, pour tester le lecteur.

Aucun Blender n'est disponible (ni souhaitable) en test : on écrit donc un
fichier conforme — en-tête, blocs, ADN (SDNA) — qui contient de vrais maillages.
Deux dispositions sont produites :

* ``modern=False`` : ``mvert`` / ``mpoly`` / ``mloop`` (Blender 2.5 → 3.6) ;
* ``modern=True``  : attributs ``CustomData`` + ``face_offset_indices`` (4.1+).

Ce qui est vérifié par les tests, c'est que :mod:`hookymod.blendread` lit ces
fichiers **sans rien savoir de leur disposition à l'avance**, uniquement via
l'ADN qu'ils embarquent.
"""

from __future__ import annotations

import struct

PTR = 4                      # taille de pointeur des fichiers produits


class _Dna:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.types: list[str] = []
        self.lens: list[int] = []
        self.entries: list[tuple[int, list[tuple[int, int]]]] = []

    # -- registres
    def name_id(self, name: str) -> int:
        if name not in self.names:
            self.names.append(name)
        return self.names.index(name)

    def type_id(self, name: str, size: int = 0) -> int:
        if name not in self.types:
            self.types.append(name)
            self.lens.append(size)
        return self.types.index(name)

    def add_struct(self, name: str, fields: list[tuple[str, str]]) -> int:
        pairs: list[tuple[int, int]] = []
        size = 0
        for type_name, field_name in fields:
            type_index = self.type_id(type_name)
            elem = PTR if type_name.startswith("*") else self.lens[type_index]
            dims = 1
            if "[" in field_name:
                body = field_name[field_name.index("["):]
                while body.startswith("["):
                    end = body.index("]")
                    dims *= int(body[1:end])
                    body = body[end + 1:]
            size += elem * dims
            pairs.append((type_index, self.name_id(field_name)))
        index = len(self.entries)
        self.entries.append((self.type_id(name, size), pairs))
        return index

    # -- sérialisation
    def payload(self) -> bytes:
        def pad(blob: bytes) -> bytes:
            return blob + bytes((-len(blob)) % 4)

        out = b"SDNA"
        out += b"NAME" + struct.pack("<i", len(self.names))
        out += b"".join(name.encode("latin-1") + b"\x00" for name in self.names)
        out = pad(out)
        out += b"TYPE" + struct.pack("<i", len(self.types))
        out += b"".join(name.encode("latin-1") + b"\x00" for name in self.types)
        out = pad(out)
        out += b"TLEN" + b"".join(struct.pack("<h", size) for size in self.lens)
        out = pad(out)
        out += b"STRC" + struct.pack("<i", len(self.entries))
        for type_index, pairs in self.entries:
            out += struct.pack("<hh", type_index, len(pairs))
            for field_type, field_name in pairs:
                out += struct.pack("<hh", field_type, field_name)
        return out


def _cstring(text: str, size: int) -> bytes:
    raw = text.encode("latin-1")[:size - 1]
    return raw + bytes(size - len(raw))


def _pack_matrix(matrix) -> bytes:
    return struct.pack("<16f", *matrix)


IDENTITY = (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0)


def _triangulate(faces):
    out = []
    for face in faces:
        for i in range(1, len(face) - 1):
            out.append((face[0], face[i], face[i + 1]))
    return out


def make_blend(objects, *, modern: bool = False, materials=("M_Corps",),
               version: str = "300", compress: bool = False) -> bytes:
    """Écrit un .blend contenant ``objects``.

    ``objects`` : listes de dicts ``{name, positions, faces, matrix?, material?}``.
    """
    dna = _Dna()
    dna.type_id("char", 1)
    dna.type_id("float", 4)
    dna.type_id("int", 4)
    for pointer in ("*MVert", "*MPoly", "*MLoop", "*Mesh", "*Material", "*int",
                    "*void", "*CustomDataLayer"):
        dna.type_id(pointer, PTR)
    dna.add_struct("ID", [("char", "name[66]")])
    dna.add_struct("Material", [("ID", "id")])
    if modern:
        dna.add_struct("CustomDataLayer", [("int", "type"), ("*void", "data"),
                                           ("char", "name[64]")])
        dna.add_struct("CustomData", [("*CustomDataLayer", "layers"),
                                      ("int", "totlayer")])
        dna.add_struct("Mesh", [
            ("ID", "id"), ("CustomData", "vdata"), ("CustomData", "ldata"),
            ("int", "totvert"), ("int", "totloop"), ("int", "faces_num"),
            ("*int", "face_offset_indices"),
        ])
    else:
        dna.add_struct("MVert", [("float", "co[3]")])
        dna.add_struct("MPoly", [("int", "loopstart"), ("int", "totloop")])
        dna.add_struct("MLoop", [("int", "v")])
        dna.add_struct("Mesh", [
            ("ID", "id"), ("int", "totvert"), ("int", "totpoly"),
            ("int", "totloop"), ("*MVert", "mvert"), ("*MPoly", "mpoly"),
            ("*MLoop", "mloop"),
        ])
    dna.add_struct("Object", [
        ("ID", "id"), ("int", "type"), ("*Mesh", "data"),
        ("float", "object_to_world[4][4]"), ("int", "totcol"),
        ("*Material", "mat[4]"),
    ])
    def struct_index(name: str) -> int:
        """Index dans la liste des structures (c'est ce que porte un bhead)."""
        for position, (type_index, _fields) in enumerate(dna.entries):
            if dna.types[type_index] == name:
                return position
        raise AssertionError(f"structure absente : {name}")

    mesh_struct = struct_index("Mesh")
    material_struct = struct_index("Material")
    object_struct = struct_index("Object")

    blocks: list[list] = []          # [code, payload(addr_map), sdna, count, addr]
    cursor = [0x1000]

    def alloc(size: int) -> int:
        address = cursor[0]
        cursor[0] += max(16, (size + 15) // 16 * 16)
        return address

    material_payloads = []
    material_addresses = []
    for name in materials:
        payload = _cstring(name, 66)
        address = alloc(len(payload))
        material_addresses.append(address)
        material_payloads.append((payload, address))

    mesh_entries = []
    for obj in objects:
        positions = list(obj.get("positions") or [])
        faces = [tuple(face) for face in (obj.get("faces") or [])]
        if modern:
            verts = b"".join(struct.pack("<3f", *point) for point in positions)
            face_offsets = [0]
            for face in faces:
                face_offsets.append(face_offsets[-1] + len(face))
            corners = [index for face in faces for index in face]
            corner_bytes = b"".join(struct.pack("<i", value) for value in corners)
            offset_bytes = b"".join(struct.pack("<i", value) for value in face_offsets)
            vert_addr, corner_addr = alloc(len(verts)), alloc(len(corner_bytes))
            offsets_addr = alloc(len(offset_bytes))
            layer_size = 4 + PTR + 64
            vert_layers_addr = alloc(layer_size)
            loop_layers_addr = alloc(layer_size)
            vert_layers = (struct.pack("<i", 3) + struct.pack("<I", vert_addr)
                           + _cstring("position", 64))
            loop_layers = (struct.pack("<i", 1) + struct.pack("<I", corner_addr)
                           + _cstring("corner_vert", 64))
            mesh_payload = (
                _cstring(obj.get("name", "Mesh"), 66)
                + struct.pack("<ii", vert_layers_addr, 1)      # vdata
                + struct.pack("<ii", loop_layers_addr, 1)      # ldata
                + struct.pack("<iii", len(positions), len(corners), len(faces))
                + struct.pack("<I", offsets_addr)
            )
            mesh_addr = alloc(len(mesh_payload))
            mesh_entries.append((mesh_payload, mesh_addr))
            blocks += [
                [b"DATA", verts, -1, 1, vert_addr],
                [b"DATA", corner_bytes, -1, 1, corner_addr],
                [b"DATA", offset_bytes, -1, 1, offsets_addr],
                [b"DATA", vert_layers, -1, 1, vert_layers_addr],
                [b"DATA", loop_layers, -1, 1, loop_layers_addr],
            ]
        else:
            verts = b"".join(struct.pack("<3f", *point) for point in positions)
            loops = [index for face in faces for index in face]
            loop_bytes = b"".join(struct.pack("<i", value) for value in loops)
            start = 0
            poly_bytes = b""
            for face in faces:
                poly_bytes += struct.pack("<ii", start, len(face))
                start += len(face)
            vert_addr, poly_addr, loop_addr = (alloc(len(verts)), alloc(len(poly_bytes)),
                                               alloc(len(loop_bytes)))
            mesh_payload = (
                _cstring(obj.get("name", "Mesh"), 66)
                + struct.pack("<iii", len(positions), len(faces), len(loops))
                + struct.pack("<III", vert_addr, poly_addr, loop_addr)
            )
            mesh_addr = alloc(len(mesh_payload))
            mesh_entries.append((mesh_payload, mesh_addr))
            blocks += [
                [b"DATA", verts, -1, 1, vert_addr],
                [b"DATA", poly_bytes, -1, 1, poly_addr],
                [b"DATA", loop_bytes, -1, 1, loop_addr],
            ]
        blocks.append([b"ME\x00\x00", mesh_payload, mesh_struct, 1, mesh_addr])

    for payload, address in material_payloads:
        blocks.append([b"MA\x00\x00", payload, material_struct, 1, address])

    for index, obj in enumerate(objects):
        mesh_addr = mesh_entries[index][1]
        chosen = obj.get("material")
        mat_address = 0
        if chosen and chosen in materials:
            mat_address = material_addresses[materials.index(chosen)]
        payload = (
            _cstring(obj.get("name", "Objet"), 66)
            + struct.pack("<i", 1 if obj.get("positions") else 0)   # type
            + struct.pack("<I", mesh_addr if obj.get("positions") else 0)
            + _pack_matrix(obj.get("matrix") or IDENTITY)
            + struct.pack("<i", 1 if mat_address else 0)
            + struct.pack("<4I", mat_address, 0, 0, 0)
        )
        addr = alloc(len(payload))
        blocks.append([b"OB\x00\x00", payload, object_struct, 1, addr])

    blocks.append([b"DNA1", dna.payload(), -1, 1, alloc(0)])
    blocks.append([b"ENDB", b"", 0, 0, 0])

    out = b"BLENDER" + (b"-" if PTR == 8 else b"_") + b"v" + version.encode("latin-1")
    for code, payload, sdna_index, count, addr in blocks:
        out += code + struct.pack("<i", len(payload))
        out += struct.pack("<I", addr)
        out += struct.pack("<i", sdna_index) + struct.pack("<i", count)
        out += payload
    if compress:
        import zstandard
        out = zstandard.ZstdCompressor().compress(out)
    return out


def cube(size: float = 1.0) -> dict:
    half = size / 2
    positions = [(-half, -half, -half), (half, -half, -half), (half, half, -half),
                 (-half, half, -half), (-half, -half, half), (half, -half, half),
                 (half, half, half), (-half, half, half)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)]
    return {"name": "Cube", "positions": positions, "faces": faces}
