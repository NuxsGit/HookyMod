"""Assets réels du remake (Switch 2) : BNTX 0.4.1.1 « Ounc » et dictionnaires.

Les fichiers sont fournis par l'utilisateur à la racine du dépôt (papillon) et ne
sont pas versionnés par les tests : chaque test est ignoré s'ils sont absents,
pour que la suite reste verte sur une machine sans dump.

Ce que ces tests verrouillent (mesuré sur les fichiers réels, build 1.1.1) :

* version BNTX ``0.4.1.1`` (``0x00040101``), conteneur ``Ounc``, pas de ``NX  `` ;
* adresses **32 bits** dans la BTI pour 0.4.x : nom à ``+80``, table des mips à
  ``+96`` (les adresses 64 bits de 0.3.x sont à ``+76``/``+92``) ;
* textures du jeu : *Alb* ASTC 8×5 sRGB, *Nrm* ASTC 5×5, *AO*/*Rgh* BC4 256²,
  9 mips, disposition Orin ;
* les textures ne demandent **aucun dictionnaire** (dict_id 0), les modèles oui
  (dict_id 1 = ``zs.zsdic``), et l'identifiant se déduit du **nom** du fichier.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from hookymod import bntx, bfres, util, zs  # noqa: E402

TEXTURES = {
    "AO": ("BC4_UNORM", 256, 256),
    "Alb": ("ASTC_8x5_SRGB", 384, 384),
    "Nrm": ("ASTC_5x5_UNORM", 384, 384),
    "Rgh": ("BC4_UNORM", 256, 256),
}
MODEL = ROOT / "Animal_Butterfly.Animal_Butterfly.bfres.zs"
DICTS = ROOT / "ZsDic.pack.zs"


def _texture_path(name: str) -> Path:
    return ROOT / f"Mt_Body_Animal_Butterfly_{name}.bntx.zs"


def _have_all_textures() -> bool:
    return zs.zstd_available() and all(_texture_path(n).is_file() for n in TEXTURES)


def _load_texture(name: str) -> bntx.BntxFile:
    import zstandard
    raw = _texture_path(name).read_bytes()
    data = zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 28)
    return bntx.BntxFile.parse(data)


def _mean_neighbour_delta(rgba: bytes, width: int, height: int) -> float:
    """Écart moyen entre pixels voisins : une texture correcte est cohérente."""
    stride = width * 4
    total = 0
    pairs = 0
    for y in range(0, height, 4):
        row = y * stride
        for x in range(0, width - 4, 4):
            total += abs(rgba[row + x] - rgba[row + x + 4])
            pairs += 1
    return total / max(1, pairs)


@unittest.skipUnless(_have_all_textures(), "assets du papillon absents du dépôt")
class RealTextureTests(unittest.TestCase):
    def test_version_and_container_are_switch2(self):
        for name in TEXTURES:
            file = _load_texture(name)
            self.assertEqual(file.version, 0x00040101, name)
            self.assertEqual(file.version_label, "0.4.1.1")
            self.assertTrue(file.orin, name)
            self.assertEqual(len(file.textures), 1, name)

    def test_names_come_from_the_string_table(self):
        for name in TEXTURES:
            file = _load_texture(name)
            self.assertEqual(file.textures[0].name, f"Mt_Body_Animal_Butterfly_{name}")

    def test_formats_and_dimensions(self):
        for name, (fmt, width, height) in TEXTURES.items():
            texture = _load_texture(name).textures[0]
            self.assertEqual(texture.format_name, fmt, name)
            self.assertEqual((texture.width, texture.height), (width, height), name)
            self.assertEqual(texture.mip_count, 9, name)

    def test_mip_chain_is_contiguous(self):
        texture = _load_texture("AO").textures[0]
        self.assertEqual(texture.mip_offsets[0], 0)
        self.assertEqual(texture.mip_offsets[1], 32768)   # BC4 256×256
        self.assertEqual(texture.mip_offsets[2], 40960)   # + 128×128

    def test_bc4_texture_decodes_coherently(self):
        for name in ("AO", "Rgh"):
            width, height, rgba = _load_texture(name).textures[0].decode(0)
            self.assertEqual((width, height), (256, 256))
            self.assertEqual(len(rgba), 256 * 256 * 4)
            self.assertLess(_mean_neighbour_delta(rgba, width, height), 20.0, name)

    @unittest.skipUnless(bntx.mod_supports_astc(), "texture2ddecoder requis")
    def test_astc_textures_decode_coherently(self):
        for name in ("Alb", "Nrm"):
            width, height, rgba = _load_texture(name).textures[0].decode(0)
            self.assertEqual((width, height), (384, 384))
            self.assertLess(_mean_neighbour_delta(rgba, width, height), 25.0, name)

    def test_cli_lists_textures_without_dictionary(self):
        import contextlib
        import io
        from hookymod import cli
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["textures", str(_texture_path("Alb")), "--list"])
        self.assertEqual(code, 0)
        output = buffer.getvalue()
        self.assertIn("0.4.1.1", output)
        self.assertIn("ASTC_8x5_SRGB", output)
        self.assertIn("Mt_Body_Animal_Butterfly_Alb", output)


class DictionaryIdTests(unittest.TestCase):
    def test_ids_follow_the_game_names(self):
        self.assertEqual(zs.dict_name_id("zs.zsdic"), 1)
        self.assertEqual(zs.dict_name_id("bkprb.zsdic"), 2)
        self.assertEqual(zs.dict_name_id("bcett.byml.zsdic"), 3)
        self.assertEqual(zs.dict_name_id("pack.zsdic"), 4)
        self.assertEqual(zs.dict_name_id("mon_dico.zsdic"), 0)

    @unittest.skipUnless(zs.zstd_available(), "module zstandard requis")
    def test_store_indexes_raw_dictionaries_by_name(self):
        """Les ``.zsdic`` du jeu sont du contenu brut : zstd leur donne l'ID 0."""
        raw = b"alpha beta gamma delta " * 200          # contenu brut, sans en-tête
        store = zs.ZstdDicts()
        self.assertTrue(store.add("zs.zsdic", raw))
        self.assertIn(1, store.by_id)
        self.assertIsNotNone(store._dict_for(1))

    @unittest.skipUnless(zs.zstd_available(), "module zstandard requis")
    def test_unknown_names_do_not_get_an_id(self):
        import synth
        store = zs.ZstdDicts()
        self.assertTrue(store.add("autre.zsdic", synth.train_dict(
            [b"autre %d" % i * 40 for i in range(64)], 512)))
        self.assertNotIn(2, store.by_id)


@unittest.skipUnless(MODEL.is_file() and DICTS.is_file() and zs.zstd_available(),
                     "modèle ou dictionnaires absents")
class RealModelTests(unittest.TestCase):
    def test_game_dictionaries_give_the_four_expected_ids(self):
        store = zs.ZstdDicts()
        self.assertEqual(store.load_file(DICTS), 4)
        self.assertEqual(sorted(store.by_id), [1, 2, 3, 4])

    def test_model_needs_a_dictionary_and_then_decompresses(self):
        store = zs.ZstdDicts()
        empty = zs.ZstdDicts()
        with self.assertRaises(zs.DictMissing):
            empty.decompress(MODEL.read_bytes())
        store.load_file(DICTS)
        data = store.decompress(MODEL.read_bytes())
        self.assertEqual(data[:4], b"FRES")
        self.assertEqual(len(data), 131792)

    def test_v10_signature_is_recognised(self):
        store = zs.ZstdDicts()
        store.load_file(DICTS)
        data = store.decompress(MODEL.read_bytes())
        self.assertEqual(int.from_bytes(data[8:12], "little") >> 16, 10)
        report = util.hexdump(data[:16], base=0)[0]
        self.assertIn("46 52 45 53", report)          # « FRES »
        for magic in (b"FMDL", b"FVTX", b"FMAT", b"FSHP", b"FSKL", b"_STR"):
            self.assertIn(magic, data)

    def test_parser_reports_instead_of_crashing(self):
        """Tant que la v10 n'est pas décodée, on veut un avertissement clair."""
        store = zs.ZstdDicts()
        store.load_file(DICTS)
        file = bfres.BfresFile.parse(store.decompress(MODEL.read_bytes()))
        self.assertEqual(file.version >> 16, 10)
        if not file.models:
            self.assertTrue(file.warnings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
