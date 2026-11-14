"""Export ``.blend`` : on écrit un vrai fichier Blender, ou on dit pourquoi.

Un ``.blend`` est le format interne de Blender : le produire à la main
supposerait de réimplémenter son format (SDNA, blocs de données, images
embarquées) et le moindre écart donnerait un fichier que Blender refuse —
exactement ce qu'on ne veut pas livrer. La seule façon honnête d'obtenir un
``.blend`` correct est donc de **piloter Blender lui-même** :

* si Blender est installé (chemin réglé dans les paramètres, dans le ``PATH``,
  ou dans les dossiers d'installation habituels sous Windows), on lance
  ``blender --background --python script.py -- entree.gltf sortie.blend`` ;
  l'import glTF ouvre la géométrie, les matériaux et les textures, puis Blender
  enregistre le ``.blend`` (textures incluses dans le fichier) ;
* sinon, on écrit le script ``creer_blend.py`` à côté de l'export et on donne la
  commande exacte à lancer. Le dossier reste utilisable, mais **on ne prétend
  pas** avoir produit un ``.blend``.

Aucun fichier n'est déclaré réussi s'il n'existe pas : la fonction vérifie la
sortie et la taille du fichier avant de rendre la main.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

#: nom du script déposé à côté de l'export
SCRIPT_NAME = "creer_blend.py"
#: délai maximal laissé à Blender (un gros modèle peut prendre une minute)
TIMEOUT = 300

IMPORT_SCRIPT = '''\
"""Crée {blend} depuis {gltf} — script généré par HookyCreator.

Deux façons de s'en servir :

  blender --background --factory-startup --python creer_blend.py
  # ou, si le chemin de Blender est renseigné dans HookyCreator,
  # le bouton « Exporter en .blend » s'en occupe.

Le script importe le glTF (géométrie + matériaux + textures), emballe les images
dans le fichier pour qu'il soit autonome, puis enregistre le .blend.
"""

import sys
import os

import bpy

# arguments après « -- » (Blender peut ajouter les siens avant)
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
here = os.path.dirname(os.path.abspath(__file__))
gltf = argv[0] if argv else os.path.join(here, r"{gltf_name}")
blend = argv[1] if len(argv) > 1 else os.path.join(here, r"{blend_name}")

bpy.ops.wm.read_factory_settings(use_empty=True)
result = bpy.ops.import_scene.gltf(filepath=gltf)
if "FINISHED" not in result:
    raise SystemExit("import glTF refusé : %s" % (result,))

# les PNG du dossier deviennent des images internes du .blend
try:
    bpy.ops.file.pack_all()
except Exception as error:      # pas de texture : ce n'est pas une erreur
    print("emballage des textures ignoré :", error)

bpy.ops.wm.save_as_mainfile(filepath=blend)
print("HOOKY_OK", blend)
'''


def _windows_candidates() -> list[Path]:
    found: list[Path] = []
    for root in (Path(r"C:\Program Files\Blender Foundation"),
                 Path(r"C:\Program Files (x86)\Blender Foundation")):
        if not root.is_dir():
            continue
        try:
            for folder in sorted(root.iterdir(), reverse=True):
                for name in ("blender.exe", "blender-launcher.exe"):
                    candidate = folder / name
                    if candidate.is_file():
                        found.append(candidate)
        except OSError:
            continue
    return found


def find_blender(configured: str = "") -> Path | None:
    """Exécutable Blender : réglage explicite, ``PATH``, puis dossiers usuels."""
    configured = (configured or "").strip().strip('"')
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate
        # un dossier a été donné : on cherche l'exécutable dedans
        if candidate.is_dir():
            for name in ("blender.exe", "blender", "blender-launcher.exe"):
                inside = candidate / name
                if inside.is_file():
                    return inside
        # nom nu (« blender ») : on laisse « which » trancher
    for name in ("blender", "blender.exe", "blender-launcher.exe"):
        located = shutil.which(name)
        if located:
            return Path(located)
    for candidate in _windows_candidates():
        return candidate
    # installation « portable » sous le dossier personnel (Linux/macOS)
    for name in ("/usr/bin/blender", "/snap/bin/blender",
                 "/Applications/Blender.app/Contents/MacOS/Blender"):
        if Path(name).is_file():
            return Path(name)
    return None


def write_script(folder, gltf_name: str, blend_name: str) -> Path:
    """Écrit ``creer_blend.py`` dans le dossier d'export et rend son chemin."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SCRIPT_NAME
    path.write_text(IMPORT_SCRIPT.format(blend=blend_name, gltf=gltf_name,
                                         gltf_name=gltf_name, blend_name=blend_name),
                    encoding="utf-8")
    return path


def run_script(blender, script, gltf, blend, timeout: int = TIMEOUT) -> dict:
    """Lance Blender en tâche de fond ; rend ``{ok, output, error}``."""
    command = [str(blender), "--background", "--factory-startup",
               "--python", str(script), "--", str(gltf), str(blend)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "command": command,
                "error": f"Blender n'a pas terminé en {timeout} s."}
    except OSError as error:
        return {"ok": False, "output": "", "command": command,
                "error": f"Blender n'a pas pu démarrer : {error}"}
    output = ((completed.stdout or "") + (completed.stderr or ""))[-4000:]
    if completed.returncode != 0:
        return {"ok": False, "output": output, "command": command,
                "error": f"Blender a quitté avec le code {completed.returncode}."}
    if not Path(blend).is_file() or Path(blend).stat().st_size == 0:
        return {"ok": False, "output": output, "command": command,
                "error": "Blender a répondu sans écrire le .blend."}
    return {"ok": True, "output": output, "command": command, "error": ""}


def make_blend(folder, name: str, blender=None, timeout: int = TIMEOUT) -> dict:
    """Produit ``<name>.blend`` si Blender est disponible, sinon le script.

    :param folder: dossier contenant déjà ``<name>.gltf`` (et ses PNG).
    :param blender: exécutable à utiliser (``None`` = chercher tout seul).
    :return: ``{method, blend, script, message, output, command}``.
    """
    folder = Path(folder)
    gltf = folder / f"{name}.gltf"
    blend = folder / f"{name}.blend"
    script = write_script(folder, gltf.name, blend.name)
    executable = Path(blender) if blender else find_blender()

    if not gltf.is_file():
        return {"method": "aucun", "blend": None, "script": str(script),
                "command": None, "output": "",
                "message": f"{gltf.name} absent : exporte d'abord le modèle."}
    if blend.is_file():
        try:
            blend.unlink()          # on ne veut pas valider un ancien fichier
        except OSError:
            pass
    if executable is None:
        command = (f'blender --background --factory-startup --python "{script}" '
                   f'-- "{gltf}" "{blend}"')
        return {"method": "script", "blend": None, "script": str(script),
                "command": command, "output": "",
                "message": ("Blender introuvable : lance la commande affichée, ou "
                            "indique le chemin de blender.exe dans Réglages.")}

    result = run_script(executable, script, gltf, blend, timeout)
    if not result["ok"]:
        command = " ".join(f'"{part}"' if " " in str(part) else str(part)
                           for part in result["command"])
        return {"method": "script", "blend": None, "script": str(script),
                "command": command, "output": result["output"],
                "message": f"{result['error']} Le script reste utilisable : {command}"}
    return {"method": "blender", "blend": str(blend), "script": str(script),
            "command": " ".join(str(part) for part in result["command"]),
            "output": result["output"],
            "message": f"{blend.name} écrit par Blender "
                       f"({blend.stat().st_size // 1024} Ko)."}


def platform_note() -> str:
    """Petite phrase d'aide selon le système (affichée dans l'atelier)."""
    if sys.platform.startswith("win"):
        return ("Sous Windows, le chemin habituel est "
                r"C:\Program Files\Blender Foundation\Blender 4.x\blender.exe")
    if sys.platform == "darwin":
        return "Sous macOS : /Applications/Blender.app/Contents/MacOS/Blender"
    return "Sous Linux : installe « blender » (paquet de ta distribution)."
