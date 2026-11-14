#!/usr/bin/env python3
"""Compare Rust et le repli Python sur des données synthétiques, sans jeu.

Exécuter depuis la racine : ``python tools/benchmark_rust.py``. Le temps du
rendu n'inclut ni la lecture du BFRES ni la recherche de textures sur disque.
"""
from __future__ import annotations

import random
import statistics
import struct
import sys
import time
from array import array
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hookymod import bfres_v10, bntx, bntx_encode, native, render, restbl, sarc, zs  # noqa: E402
from hookymod.mesh import MeshData  # noqa: E402


def timed(fn, repeats=3):
    samples = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), result


def show(title, python_ms, rust_ms):
    ratio = python_ms / rust_ms if rust_ms else 0
    print(f"{title:<39} Python {python_ms:8.1f} ms  Rust {rust_ms:8.1f} ms"
          f"  ×{ratio:.1f}")


def main():
    if not native.available():
        print(native.status()["reason"], file=sys.stderr)
        return 1
    rng = random.Random(10)
    if zs.zstd_available():
        # La référence ici est zstandard (bibliothèque C), pas du Python pur.
        samples = [(f"modele {i:04d} geometrie texture ".encode() + rng.randbytes(64)) * 4
                   for i in range(400)]
        trained = zs.zstd.train_dictionary(4096, samples, dict_id=17)
        content = b"".join(samples) * 6
        frame = zs.zstd.ZstdCompressor(dict_data=trained, write_checksum=True).compress(content)
        dictionary = zs.ZstdDicts()
        dictionary.add("synthetique.zsdic", trained.as_bytes())
        assert native.zstd_decompress(frame, trained.as_bytes(), 17,
                                      force_dictionary=True) == content
        with patch.object(native, "available", return_value=False):
            c_ms, reference = timed(lambda: dictionary.decompress(frame), 5)
        rust_ms, result = timed(lambda: dictionary.decompress(frame), 5)
        assert result == reference == content, "décompression zstd différente"
        ratio = c_ms / rust_ms if rust_ms else 0
        print(f"{'Zstandard avec dictionnaire':<39} zstandard C {c_ms:8.1f} ms"
              f"  Rust {rust_ms:8.1f} ms  ×{ratio:.1f}")
    else:
        print("Zstandard : module Python absent, comparaison ignorée.")

    entries = [sarc.SarcEntry(f"dossier/ressource_{i:04d}.bin", rng.randbytes(128), alignment=8)
               for i in range(500)]
    source = sarc.SarcArchive(entries)
    with patch.object(native, "available", return_value=False):
        raw = source.build()
        py_read_ms, reference = timed(lambda: sarc.SarcArchive.parse(raw), 5)
        py_write_ms, rebuilt = timed(reference.build, 5)
    # Vérifier explicitement que les appels Rust réussissent : un repli
    # invisible ne doit pas être mesuré comme s'il s'agissait du natif.
    assert native.sarc_parse(raw) is not None, "lecture SARC Rust refusée"
    assert native.sarc_build(reference.entries, reference.endian, reference.hash_mult,
                             reference.version) == rebuilt, "reconstruction SARC Rust différente"
    rust_read_ms, parsed = timed(lambda: sarc.SarcArchive.parse(raw), 5)
    rust_write_ms, native_rebuilt = timed(reference.build, 5)
    assert parsed == reference and native_rebuilt == rebuilt, "SARC différent"
    show("Lecture SARC (500 entrées)", py_read_ms, rust_read_ms)
    show("Reconstruction SARC (500 entrées)", py_write_ms, rust_write_ms)

    sizes = {key: rng.randrange(1 << 20) for key in range(50_000)}
    table = restbl.ResourceSizeTable(version=3, name_length=64, sizes=sizes, tail=b"queue\0")
    with patch.object(native, "available", return_value=False):
        raw = table.build()
        py_read_ms, reference = timed(lambda: restbl.ResourceSizeTable.parse(raw), 5)
        py_write_ms, rebuilt = timed(reference.build, 5)
    assert native.restbl_parse(raw) is not None, "lecture RESTBL Rust refusée"
    assert native.restbl_build(reference.sizes, reference.version, reference.name_length,
                               reference.named_count, reference.tail) == rebuilt, \
        "reconstruction RESTBL Rust différente"
    rust_read_ms, parsed = timed(lambda: restbl.ResourceSizeTable.parse(raw), 5)
    rust_write_ms, native_rebuilt = timed(reference.build, 5)
    assert parsed == reference and native_rebuilt == rebuilt, "RESTBL différente"
    show("Lecture RESTBL (50 000 entrées)", py_read_ms, rust_read_ms)
    show("Reconstruction RESTBL (50 000 entrées)", py_write_ms, rust_write_ms)

    # Recherche et tables FVTX synthétiques, sans décodage géométrique. Les
    # tampons sont espacés pour exercer aussi le calcul des nombres de sommets.
    bfres = bytearray(4 * 1024 * 1024)
    for at, name in ((0x140, b"_p0"), (0x150, b"_u0")):
        struct.pack_into("<H", bfres, at, len(name))
        bfres[at + 2:at + 2 + len(name)] = name
    for at, name, fmt, offset in ((0x100, 0x140, 0x1805, 0),
                                  (0x110, 0x150, 0x1201, 12)):
        struct.pack_into("<IIII", bfres, at, name, 0, fmt, offset)
    base = 0x4000
    for index in range(64):
        at = 0x200 + index * 0x50
        bfres[at:at + 4] = b"FVTX"
        struct.pack_into("<Q", bfres, at + 0x08, 0x100)
        struct.pack_into("<Q", bfres, at + 0x18, len(bfres))
        struct.pack_into("<II", bfres, at + 0x48, 0x1000 + index * 0x4000, 2)
    bfres = bytes(bfres)
    shapes = lambda: bfres_v10.find_shapes(bfres, base)
    with patch.object(native, "available", return_value=False):
        py_ms, py = timed(shapes, 5)
    assert native.bfres_find_shapes(bfres, base) is not None, "tables BFRES Rust refusées"
    rust_ms, fast = timed(shapes, 5)
    assert py == fast and len(py) == 64, "tables BFRES différentes"
    show("Tables BFRES (64 formes, 4 Mio)", py_ms, rust_ms)

    size = 1024
    packed_size = bntx.surface_size(size, size, 4, 4, 16, 0, 4)
    packed = rng.randbytes(packed_size)
    args = (size, size, 4, 4, 16, 0, 4, packed)
    # Échauffement de la table d'offsets du repli Python.
    with patch.object(native, "available", return_value=False):
        bntx.deswizzle(*args)
        py_ms, py = timed(lambda: bntx.deswizzle(*args))
    rust_ms, fast = timed(lambda: bntx.deswizzle(*args))
    assert py == fast, "désentrelacement différent"
    show("Désentrelacement BC3 1024² (cache chaud)", py_ms, rust_ms)

    blocks = rng.randbytes(size // 4 * (size // 4) * 16)
    # BC3 Rust est utilisé seulement quand texture2ddecoder est absent.
    with patch.object(bntx, "_t2d", None):
        with patch.object(native, "available", return_value=False):
            py_ms, py = timed(lambda: bntx._decode_bcn("BC3", blocks, size, size, 4, 4), 1)
        rust_ms, fast = timed(lambda: bntx._decode_bcn("BC3", blocks, size, size, 4, 4))
    assert py == fast, "décodage BC3 différent"
    show("Décodage BC3 1024² (sans décodeur C)", py_ms, rust_ms)

    pixels = rng.randbytes(512 * 512 * 4)
    bc3_code = bntx_encode.NAME_TO_CODE["BC3"]
    encode = lambda: bntx_encode.encode_pixels(pixels, 512, 512, bc3_code)
    with patch.object(native, "available", return_value=False):
        py_ms, py = timed(encode, 1)
    rust_ms, fast = timed(encode)
    assert py == fast, "encodage BC3 différent"
    show("Encodage BC3 512²", py_ms, rust_ms)

    n = 160
    points = [(float(x), float(y), float((x * y) % 3) * 0.1)
              for y in range(n) for x in range(n)]
    triangles = []
    for y in range(n - 1):
        for x in range(n - 1):
            i = y * n + x
            triangles.extend(((i, i + 1, i + n), (i + 1, i + n + 1, i + n)))
    words = array("H")
    for triangle in triangles:
        words.extend(triangle)
    segment = lambda: bfres_v10._segment_indices(words, [len(points)])
    with patch.object(native, "available", return_value=False):
        py_ms, py = timed(segment)
    rust_ms, fast = timed(segment)
    assert py == fast, "indices BFRES différents"
    show("Segmentation BFRES (~50 000 triangles)", py_ms, rust_ms)

    positions = struct.pack("<3f", 1.0, 2.0, -0.0) * 100_000
    decode = lambda: bfres_v10._decode_positions(positions, 0, 100_000, 12, 0, 0x1805)
    with patch.object(native, "available", return_value=False):
        py_ms, py = timed(decode)
    rust_ms, fast = timed(decode)
    assert py == fast, "positions BFRES différentes"
    show("Positions BFRES (100 000 sommets)", py_ms, rust_ms)

    filter_faces = lambda: bfres_v10._drop_oversized(points, triangles, 226.0)
    with patch.object(native, "available", return_value=False):
        py_ms, py = timed(filter_faces)
    rust_ms, fast = timed(filter_faces)
    assert py == fast, "géométrie filtrée différente"
    show("Contrôle BFRES (~50 000 triangles)", py_ms, rust_ms)

    vertices = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
    faces = [(4, 5, 6), (4, 6, 7), (1, 0, 3), (1, 3, 2),
             (0, 4, 7), (0, 7, 3), (5, 1, 2), (5, 2, 6),
             (3, 7, 6), (3, 6, 2), (0, 1, 5), (0, 5, 4)]
    model = MeshData(positions=vertices, faces=faces)
    preview = lambda: render.render_rgba([model], 640, 480, supersample=2)
    with patch.object(native, "available", return_value=False):
        py_ms, py = timed(preview, 1)
    rust_ms, fast = timed(preview)
    assert py == fast, "rendu différent"
    show("Aperçu synthétique 640×480, AA ×2", py_ms, rust_ms)
    print("Sorties comparées à la référence : octets, pixels ou géométrie. "
          "Durées hors chargement du modèle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
