"""Diagnostics : environnement d'exécution et anatomie d'un fichier.

Ces fonctions ne dépendent pas de Tk : elles servent à la fois à l'interface
(menu **Affichage → Diagnostic du fichier courant…**, **Aide → Diagnostic de
l'installation…**) et à la ligne de commande (`python -m hookymod.cli doctor`).
C'est le moyen le plus rapide de comprendre pourquoi un asset Switch 2 ne
s'affiche pas : version du conteneur, disposition des pixels, modules présents.
"""

from __future__ import annotations

import platform
import struct
import sys
from pathlib import Path

from . import bfres as bfres_mod
from . import bntx as bntx_mod
from .sarc import SarcArchive
from .util import HookyError, human_size
from .zs import frame_info, is_zstd


def _module_line(name: str, module, hint: str = "") -> str:
    version = getattr(module, "__version__", "") if module is not None else ""
    state = f"oui{' ' + str(version) if version else ''}" if module is not None else "NON"
    line = f"  {name:<22} {state}"
    if module is None and hint:
        line += f"   → {hint}"
    return line


def environment_report(romfs: Path | str | None = None, dicts: int = 0,
                       theme: str = "") -> str:
    """État de l'installation : modules, Tk, dictionnaires, chemins."""
    lines = ["HookyMod — diagnostic de l'installation", ""]
    lines.append(f"  Python                 {platform.python_version()} "
                 f"({platform.system()} {platform.machine()})")
    lines.append(f"  Exécutable             {sys.executable}")
    try:
        import tkinter
        tk_version = f"oui (Tk {tkinter.TkVersion})"
        png = "?"
        try:
            root = tkinter.Tk()
            root.withdraw()
            try:
                # un PNG 1×1 : si Tk l'accepte, les aperçus de textures passent
                tkinter.PhotoImage(
                    data=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                         b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc"
                         b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
                png = "oui"
            except Exception:  # noqa: BLE001
                png = "non (installe Pillow)"
            finally:
                root.destroy()
        except Exception:  # noqa: BLE001 - pas d'affichage (serveur, CI)
            png = "inconnu (pas d'affichage)"
    except Exception:  # noqa: BLE001
        tk_version, png = "NON (python3-tk)", "impossible"
    lines.append(f"  Tkinter                {tk_version}")
    lines.append(f"  Aperçu PNG par Tk      {png}")

    try:
        import zstandard
    except Exception:  # noqa: BLE001
        zstandard = None
    try:
        import texture2ddecoder
    except Exception:  # noqa: BLE001
        texture2ddecoder = None
    try:
        import PIL
    except Exception:  # noqa: BLE001
        PIL = None

    lines.append(_module_line("zstandard (.zs)", zstandard, "pip install zstandard"))
    lines.append(_module_line("texture2ddecoder", texture2ddecoder,
                              "pip install texture2ddecoder (ASTC/BC7/BC6H)"))
    lines.append(_module_line("Pillow (secours PNG)", PIL, "optionnel"))
    lines.append("")
    lines.append("  Formats de textures décodables")
    astc = "oui" if texture2ddecoder is not None else "non (module absent)"
    lines.append(f"  {'ASTC (Switch 2)':<22} {astc}")
    bc7 = "oui" if texture2ddecoder is not None else "non (module absent)"
    lines.append(f"  {'BC7 / BC6H':<22} {bc7}")
    lines.append(f"  {'BC1-BC5, RGBA8…':<22} oui (décodeur intégré)")
    lines.append("")
    lines.append(f"  romfs                  {romfs or 'non défini'}")
    lines.append(f"  dictionnaires zstd     {dicts}")
    if theme:
        lines.append(f"  thème                  {theme}")
    return "\n".join(lines)


def file_report(data: bytes, name: str = "", orin: bool | None = None) -> str:
    """Anatomie d'un fichier : magic, version, structure, et pourquoi ça bloque."""
    name = name or "(sans nom)"
    lines = [f"Diagnostic : {name}",
             f"Taille : {human_size(len(data))} ({len(data):,} octets)",
             f"Magic  : {data[:4]!r} ({data[:4].hex(' ')})"]
    try:
        from .container import detect_kind
        lines.append(f"Type   : {detect_kind(data)}")
    except Exception:  # noqa: BLE001
        pass

    if is_zstd(data):
        try:
            dict_id, content_size = frame_info(data)
            size = human_size(content_size) if content_size else "inconnu"
            lines += [f"zstd   : contenu annoncé {size}"
                      f" · dictionnaire {'id ' + str(dict_id) if dict_id else 'non'}",
                      "Astuce : si le fichier est un .zs de jeu, charge son dictionnaire "
                      "(Pack/ZsDic.pack.zs) avant de l'ouvrir."]
        except HookyError as exc:
            lines.append(f"zstd   : illisible ({exc})")
        return "\n".join(lines)

    if data[:4] == b"BNTX":
        lines.append("")
        try:
            file = bntx_mod.BntxFile.parse(data, name, orin=orin)
        except Exception as exc:  # noqa: BLE001
            lines += [f"⚠ BNTX illisible : {exc}",
                      "Le fichier est bien un BNTX mais sa structure n'est pas reconnue.",
                      "Essaie la disposition « Switch 2 » dans l'onglet Textures."]
            return "\n".join(lines)
        lines.append(file.info())
        return "\n".join(lines)

    if data[:4] == b"FRES":
        lines.append("")
        try:
            file = bfres_mod.BfresFile.parse(data)
        except Exception as exc:  # noqa: BLE001
            lines += [f"⚠ BFRES illisible : {exc}"]
            return "\n".join(lines)
        lines.append(file.info())
        if not any(shape.vtx_buffer for model in file.models for shape in model.shapes):
            lines += ["", "⚠ Aucun tampon de sommets lisible : le modèle ne peut pas "
                          "être affiché (structure BFRES Switch 2 non gérée ?)."]
        return "\n".join(lines)

    if data[:4] == b"SARC":
        try:
            archive = SarcArchive.parse(data)
            lines += ["", f"Archive SARC : {len(archive)} entrée(s)"]
        except Exception as exc:  # noqa: BLE001
            lines.append(f"⚠ SARC illisible : {exc}")
        return "\n".join(lines)

    if len(data) >= 0x40:
        lines.append("")
        lines.append("En-tête (64 premiers octets) :")
        for offset in range(0, 64, 16):
            chunk = data[offset:offset + 16]
            text = " ".join(f"{byte:02x}" for byte in chunk)
            lines.append(f"  0x{offset:04X}  {text}")
        lines.append("")
        lines.append("Format non reconnu : ce n'est ni un BNTX, ni un BFRES, ni un SARC.")
    return "\n".join(lines)


def parse_u32(data: bytes, offset: int, endian: str = "<") -> int | None:
    """Petit utilitaire utilisé par les tests et les rapports."""
    try:
        return struct.unpack_from(endian + "I", data, offset)[0]
    except struct.error:
        return None
