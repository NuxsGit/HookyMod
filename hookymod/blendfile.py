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

import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: nom du script déposé à côté de l'export
SCRIPT_NAME = "creer_blend.py"
#: délai maximal laissé à Blender (un gros modèle peut prendre une minute)
TIMEOUT = 300

IMPORT_SCRIPT = '''\
"""Crée un .blend depuis le glTF voisin — script généré par HookyCreator.

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
gltf = argv[0] if argv else os.path.join(here, {gltf_literal})
blend = argv[1] if len(argv) > 1 else os.path.join(here, {blend_literal})

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


#: script déposé avec les textures : il branche chaque carte sur la bonne entrée
#: du Principled BSDF, y compris quand on a importé l'OBJ (qui ne porte que la
#: couleur de base). Écrit en ``bpy`` pur, sans dépendance.
SHADER_SCRIPT = '''"""Branche les textures du dossier sur les matériaux — script HookyCreator.

À lancer **après** avoir importé le modèle (OBJ ou glTF) dans Blender :

  blender --background --python assembler_shaders.py -- mon_modele.blend
  # ou dans Blender : onglet Scripting ▸ Open ▸ ce fichier ▸ Run Script

Il reconnaît les cartes par leur suffixe (_Alb, _Nrm, _Rgh, _Mtl, _AO, _Emi),
charge les images avec le bon espace colorimétrique et relie chaque entrée du
Principled BSDF. Rien n'est inventé : une carte absente reste absente, et le
script dit à la fin ce qu'il n'a pas trouvé.
"""

import glob
import os
import re
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))

# suffixe (dernier jeton du nom) → rôle
ROLES = {
    "alb": "albedo", "albedo": "albedo", "bc": "albedo", "dif": "albedo",
    "basecolor": "albedo", "col": "albedo", "color": "albedo",
    "nrm": "normal", "normal": "normal", "norm": "normal",
    "rgh": "roughness", "rough": "roughness", "roughness": "roughness",
    "mtl": "metallic", "met": "metallic", "metal": "metallic",
    "ao": "occlusion", "occ": "occlusion", "occlusion": "occlusion",
    "emi": "emissive", "emit": "emissive", "emissive": "emissive",
}
# rôles qui sont des données, pas des couleurs (donc Non-Color)
DATA_ROLES = {"normal", "roughness", "metallic", "occlusion"}


def find_maps(folder):
    """{rôle: chemin} d'après le suffixe des PNG du dossier."""
    found = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.png"))):
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        token = [part for part in re.split(r"[_.]", stem) if part]
        role = ROLES.get(token[-1]) if token else None
        if role and role not in found:
            found[role] = path
    return found


def load_image(path, data):
    image = bpy.data.images.load(path, check_existing=True)
    try:
        image.colorspace_settings.name = "Non-Color" if data else "sRGB"
    except Exception as error:                     # ancienne version de Blender
        print("espace colorimétrique non réglé pour", path, error)
    return image


def principled(material):
    material.use_nodes = True
    tree = material.node_tree
    node = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if node is None:
        node = tree.nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None) \
            or tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(node.outputs["BSDF"], output.inputs["Surface"])
    return tree, node


def main():
    maps = find_maps(HERE)
    print("cartes trouvées :", {role: os.path.basename(path) for role, path in maps.items()})
    if not maps:
        print("aucune carte reconnue : rien à brancher")
        return

    images = {role: load_image(path, role in DATA_ROLES) for role, path in maps.items()}
    materials = [m for m in bpy.data.materials if m.users]
    for material in materials:
        tree, node = principled(material)

        def connect(role, socket, via=None):
            if role not in images or socket not in node.inputs:
                return False
            source = node
            tex = tree.nodes.new("ShaderNodeTexImage")
            tex.image = images[role]
            tex.location = (node.location.x - 400, node.location.y - 200)
            if via == "normal":
                bump = tree.nodes.new("ShaderNodeNormalMap")
                bump.location = (node.location.x - 200, node.location.y - 300)
                tree.links.new(tex.outputs["Color"], bump.inputs["Color"])
                tree.links.new(bump.outputs["Normal"], node.inputs["Normal"])
            else:
                tree.links.new(tex.outputs["Color"], node.inputs[socket])
            return True

        connect("albedo", "Base Color")
        connect("roughness", "Roughness")
        connect("metallic", "Metallic")
        connect("normal", "Normal", via="normal")
        # Blender 4.x : « Emission Color » ; 3.x : « Emission »
        emission = "Emission Color" if "Emission Color" in node.inputs else "Emission"
        if connect("emissive", emission) and "Emission Strength" in node.inputs:
            node.inputs["Emission Strength"].default_value = 1.0

    print("matériaux traités :", len(materials))
    missing = [role for role in ("albedo", "normal", "roughness", "metallic")
               if role not in maps]
    if missing:
        print("cartes absentes (ce n'est pas une erreur) :", ", ".join(missing))
    print("Fichier ▸ Enregistrer pour garder les branchements.")


if __name__ == "__main__":
    main()
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


#: dernier résultat de :func:`find_blender` (date, dossier) : chercher Blender
#: relit le PATH et les dossiers « Program Files » à chaque appel, et l'atelier
#: demande cette information à chaque affichage de la vue Ensembles
_BLENDER_CACHE: dict[str, tuple[float, Path | None]] = {}
BLENDER_CACHE_SECONDS = 30.0


def find_blender(configured: str = "") -> Path | None:
    """Exécutable Blender : réglage explicite, ``PATH``, puis dossiers usuels."""
    configured = (configured or "").strip().strip('"')
    cached = _BLENDER_CACHE.get(configured)
    if cached and time.monotonic() - cached[0] < BLENDER_CACHE_SECONDS:
        return cached[1]
    found = _find_blender(configured)
    _BLENDER_CACHE[configured] = (time.monotonic(), found)
    return found


def _find_blender(configured: str) -> Path | None:
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
    # Les noms viennent du projet et peuvent contenir des guillemets, retours à
    # la ligne ou antislashs : seuls des littéraux Python échappés sont insérés.
    path.write_text(IMPORT_SCRIPT.format(gltf_literal=repr(gltf_name),
                                         blend_literal=repr(blend_name)), encoding="utf-8")
    return path


def write_shader_script(folder, name: str = "") -> Path:
    """Dépose ``assembler_shaders.py`` à côté des textures et rend son chemin."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "assembler_shaders.py"
    path.write_text(SHADER_SCRIPT, encoding="utf-8")
    return path


def _blender_command(blender, script, gltf, blend) -> list[str]:
    return [str(blender), "--background", "--factory-startup",
            "--python", str(script), "--", str(gltf), str(blend)]


def _display_command(command: list[str], *, windows: bool | None = None) -> str:
    """Recette à copier dans un shell sans interpréter les noms du projet.

    Sous Windows la recette vise PowerShell : une chaîne simple est littérale,
    et deux apostrophes successives représentent une apostrophe. L'appel réel
    utilise toujours ``subprocess`` avec une liste d'arguments, sans shell.
    """
    if windows is None:
        windows = sys.platform.startswith("win")
    if windows:
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in command)
    return shlex.join(command)


def run_script(blender, script, gltf, blend, timeout: int = TIMEOUT) -> dict:
    """Lance Blender en tâche de fond ; rend ``{ok, output, error}``."""
    command = _blender_command(blender, script, gltf, blend)
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
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
    try:
        written = Path(blend).is_file() and Path(blend).stat().st_size > 0
    except OSError:
        written = False
    if not written:
        return {"ok": False, "output": output, "command": command,
                "error": "Blender a répondu sans écrire un .blend lisible et non vide."}
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
    # La commande affichée est une recette réutilisable vers le fichier final,
    # pas le chemin temporaire utilisé lors de cet essai.
    command = _display_command(_blender_command(executable or "blender", script, gltf, blend))
    if executable is None:
        return {"method": "script", "blend": None, "script": str(script),
                "command": command, "output": "",
                "message": ("Blender introuvable : lance la commande affichée, ou "
                            "indique le chemin de blender.exe dans Réglages. "
                            "Aucun nouveau .blend n'a été créé.")}

    # Le fichier temporaire vide est dans le même dossier que le résultat :
    # les éventuelles références relatives Blender gardent ainsi leur base.
    # Il est fermé avant le lancement (indispensable sous Windows). Un ancien
    # fichier final ne sert jamais de preuve de réussite et n'est remplacé
    # qu'après validation du nouveau fichier.
    output, error, cleanup_warning = "", "", ""
    staged = None
    installed, size = False, 0
    try:
        with tempfile.NamedTemporaryFile(prefix=".hookymod-blend-", suffix=".blend",
                                         dir=folder, delete=False) as temporary:
            staged = Path(temporary.name)
        result = run_script(executable, script, gltf, staged, timeout)
        output = result["output"]
        if not result["ok"]:
            error = result["error"]
        else:
            try:
                size = staged.stat().st_size
                if size == 0:
                    raise OSError("le fichier temporaire est devenu vide")
                staged.replace(blend)
                installed = True
            except OSError as exc:
                error = f"Impossible d'installer le nouveau .blend : {exc}."
    except OSError as exc:
        error = f"Impossible de préparer l'export temporaire : {exc}."
    finally:
        if staged is not None:
            # Blender peut créer une sauvegarde .blend1 du fichier temporaire.
            for path in (staged, Path(str(staged) + "1")):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    cleanup_warning = f"Nettoyage temporaire incomplet : {exc}."
    if installed:
        message = f"{blend.name} écrit par Blender ({size // 1024} Ko)."
        return {"method": "blender", "blend": str(blend), "script": str(script),
                "command": command, "output": output,
                "message": f"{message} {cleanup_warning}".strip()}
    return {"method": "script", "blend": None, "script": str(script),
            "command": command, "output": output,
            "message": (f"{error} Aucun nouveau .blend confirmé ; un ancien fichier "
                        f"éventuel est conservé. {cleanup_warning} Le script reste "
                        f"utilisable : {command}")}


def platform_note() -> str:
    """Petite phrase d'aide selon le système (affichée dans l'atelier)."""
    if sys.platform.startswith("win"):
        return ("Sous Windows, copie la commande dans PowerShell. Le chemin habituel est "
                r"C:\Program Files\Blender Foundation\Blender 4.x\blender.exe")
    if sys.platform == "darwin":
        return "Sous macOS : /Applications/Blender.app/Contents/MacOS/Blender"
    return "Sous Linux : installe « blender » (paquet de ta distribution)."
