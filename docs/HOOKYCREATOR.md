# HookyCreator — l'atelier de contenu

Une application locale (navigateur) pour **ajouter du contenu** au jeu : des
textures retouchées, des variantes de mobs, des modèles passés par Blender, et
un dossier de mod prêt à charger. Distincte de l'explorateur (`hookymod/app.py`,
sorte de Nintendo-Toolbox) : ici on ne regarde pas, on **fabrique**.

```bash
python -m hookycreator <dossier du projet>          # http://127.0.0.1:8765/
python -m hookycreator dump/romfs --open            # ouvre le navigateur
python -m hookycreator demo --port 9000             # autre port
```

Le dossier du projet peut être un `romfs` complet (avec `Pack/ZsDic.pack.zs`)
ou un simple dossier contenant trois-quatre assets extraits : les dictionnaires
zstd sont cherchés dans le projet et ses parents.

## Ce que fait chaque onglet

| Onglet | Actions | État |
|---|---|---|
| **Textures** | aperçu décodé, infos (format, taille, mips, Orin), remplacer par une image (PNG/JPG), exporter en PNG | ✅ formats encodables (BC1/BC3/BC4/BC5, non compressés). ASTC/BC7 : refusé explicitement |
| **Modèles** | aperçu 3D rendu côté serveur, planche de rotations, export glTF + OBJ (+ textures voisines) pour Blender | ✅ BFRES v9 et **v10** |
| **Mobs** | fiche de mob : modèle + textures copiés sous un nouveau nom, liste de ce qui reste à faire côté jeu | ✅ préparation des fichiers |
| **Mod** | assemble `mods/<nom>/romfs/…` (chemins d'origine) + `hookycreator.json` | ✅ |
| **Tout exploiter** | parcourt aussi l'intérieur des packs (`.pack.zs`, SARC) et exporte **toutes** les textures (PNG) et tous les modèles (glTF + OBJ) avec un `rapport.json` | ✅ |

## Tout exploiter (lots)

Deux façons, mêmes résultats :

```bash
python -m hookycreator export-all dump/romfs -o export_all     # en ligne de commande
# ou, dans l'interface : Accueil ▸ « Tout exploiter » ▸ Lancer l'export complet
```

* les **packs** (`.pack.zs`, `.sarc`, imbriqués jusqu'à 3 niveaux) sont ouverts :
  une texture qui vit dans une archive est trouvée et exportée comme les autres ;
* classement par **contenu** (magic `BNTX`/`FRES`) et, à défaut, par nom — les
  fichiers d'animation (`.anim.bfres`) sont reconnus comme tels ;
* chaque élément obtient un statut : `ok`, `skip` (avec la raison : format non
  décodable, animation pas encore lue…) ou `error`. Le tout est écrit dans
  `rapport.json` à côté des fichiers ;
* progression affichée (barre + libellé) sans bloquer l'interface ; l'export
  tourne en tâche de fond côté serveur.

## Règles tenues

* **Rien n'est écrasé** : toute modification va dans `hookycreator_work/`
  (assets/, mobs/, export/), l'original du projet reste intact.
* **Rien de faux** : si une texture ne peut pas être réencodée (ASTC), l'import
  est refusé avec la raison ; si un modèle n'est pas décodable, l'aperçu
  explique pourquoi au lieu d'afficher une géométrie inventée.
* **Aucun code Nintendo** : tout passe par les lecteurs/écrivains de HookyMod,
  eux-mêmes écrits à partir des fichiers.

## Ce qui reste à faire (dans l'ordre)

1. **Paramètre d'acteur (BYML)** : écrire le fichier d'acteur du mob (copie avec
   `$parent`, nom de modèle, textures). Cela demande un lecteur/écrivain BYML —
   il n'existe pas encore dans HookyMod, et c'est le vrai verrou pour qu'un mob
   apparaisse en jeu.
2. **RESTBL** : déclarer les tailles des nouveaux fichiers dans la table du mod.
3. **ASTC / BC7 en écriture** : pour retoucher les albédos du jeu sans changer
   leur format.
4. **Squelette et animations** (`FSKL`) : pour animer les mobs créés.
5. **Peinture intégrée** : un canevas dans l'onglet Textures, plutôt qu'un
   aller-retour par un éditeur externe.

## Architecture

```
hookycreator/
  project.py   projet, scan, textures, modèles, mobs, export de mod
  server.py    API JSON + fichiers statiques (http.server, zéro dépendance)
  pngread.py   lecture d'images (Pillow si présent, sinon décodeur PNG maison)
  web/         interface (HTML/CSS/JS, sans build)
```
