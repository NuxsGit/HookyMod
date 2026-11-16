"""Données synthétiques pour les smoke tests du backend et de la webview.

Ces fichiers sont construits à la volée et ne contiennent aucun asset du jeu.
"""
from pathlib import Path

import synth
from synth_audio import make_opus_bwav
from synth_animation import fska_fixture
from synth_video import make_webm


def make_desktop_fixture(root: Path) -> Path:
    model = root / "Model"
    model.mkdir(parents=True)
    (model / "Heros.bfres").write_bytes(synth.make_bfres())
    (model / "Heros.anim.bfres").write_bytes(fska_fixture())
    (model / "Heros_Alb.bntx").write_bytes(
        synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8)))
    (model / "Heros_Nrm.bntx").write_bytes(
        synth.make_bntx(8, 8, synth.rgba8_pixels(8, 8, seed=3)))
    (model / "Broken.bfres").write_bytes(b"FRES" + b"\0" * 20)
    (model / "Broken_Alb.bntx").write_bytes(b"BNTX" + b"\0" * 20)
    (model / "Silence.bwav").write_bytes(make_opus_bwav())
    (model / "Broken.bwav").write_bytes(b"BWAV" + b"\0" * 20)
    (model / "Synthetic.webm").write_bytes(make_webm())
    return root
