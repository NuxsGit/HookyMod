"""Import d'un dossier exporté par HookyMod, exécuté **dans Blender**.

Utilisation (aucune installation, aucun add-on) :

    blender --background --python tools/blender_import.py -- \
        /chemin/papillon_blender --save /chemin/papillon.blend

Sans ``--background``, le fichier s'ouvre dans l'interface et le script rend la
main : on peut alors naviguer, éditer, réexporter.

Le script ne modifie pas la géométrie : il importe (glTF si présent, sinon OBJ),
cadre la vue, active l'affichage des textures en couleur et compte ce qui est
arrivé — pour repérer tout de suite un export incomplet.
"""

import sys
from pathlib import Path

import bpy


def _arguments():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    folder, save = None, None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--save" and index + 1 < len(argv):
            save = argv[index + 1]
            index += 2
            continue
        if not item.startswith("-") and folder is None:
            folder = item
        index += 1
    return folder, save


def _import(path: Path):
    if path.suffix.lower() in (".gltf", ".glb"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif path.suffix.lower() == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        raise SystemExit(f"Format non géré : {path.name}")


def main() -> None:
    folder, save = _arguments()
    if not folder:
        raise SystemExit("Usage : blender -b --python tools/blender_import.py -- DOSSIER [--save fichier.blend]")
    folder = Path(folder)
    candidates = list(folder.glob("*.gltf")) + list(folder.glob("*.glb")) + list(folder.glob("*.obj"))
    if not candidates:
        raise SystemExit(f"Aucun modèle dans {folder}")

    # repartir d'une scène vide : pas de cube par défaut dans le rendu
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _import(candidates[0])

    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    triangles = sum(len(obj.data.polygons) for obj in objects)
    materials = {slot.material.name for obj in objects for slot in obj.material_slots if slot.material}
    images = [image.name for image in bpy.data.images if image.source == "FILE"]

    print(f"Blender {bpy.app.version_string}")
    print(f"  modèle importé   : {candidates[0].name}")
    print(f"  objets maillages : {len(objects)}")
    print(f"  polygones        : {triangles:,}")
    print(f"  matériaux        : {len(materials)} {sorted(materials)[:6]}")
    print(f"  textures         : {len(images)} {images[:6]}")

    # vue correcte : solide + couleur de texture, ombrage lissé
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.shading.type = "MATERIAL"
                        space.shading.use_scene_lights = True

    if objects:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        bpy.ops.object.shade_smooth()   # les ailes du papillon sont lisses

    if save:
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(save).with_suffix(".blend")))
        print(f"  enregistré       : {save}")


main()
