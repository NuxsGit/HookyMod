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

## Modèles : ce qui est traduit aujourd'hui

La lecture des BFRES 10.x est **pilotée par les données** : le module
`hookymod/bfres_v10.py` lit la table d'attributs de sommets du fichier (nom,
format, offset, pas) au lieu de supposer une disposition. Conséquence :

* le papillon (`_p0` en `3 x f16`, pas 24 o) **et** les ennemis (`_p0` en
  `3 x f32`, pas 32 o) sont décodés par le même code ;
* les positions, les UV, les normales et les couleurs sont prises dans le bon
  tampon, avec des garde-fous (cohérence géométrique, indices bornés, UV
  corrélées aux aires 3D) ;
* ce qui n'est pas reconnu est **refusé et expliqué** dans `warnings` : blocs
  d'indices ignorés, LOD inférieurs inclus, attributs non décodés.

Détail des mesures et méthode : `docs/trainIA/README.md`.

## Budget mémoire et culling

Un aperçu 3D coûte de la mémoire même sans carte graphique : chaque sommet et
chaque face est copié dans une structure de rendu, et l'image garde un tampon
de couleurs plus un tampon de profondeur. Sur un modèle de plusieurs centaines
de milliers de faces, cela suffit à faire tomber la machine.

L'onglet **Réglages** fixe donc un **budget**, en gigaoctets :

* **6 Go par défaut**, réglable de 1 à **25 Go** ;
* anticrénelage ×1 à ×4, plafond de faces facultatif ;
* culling des faces arrière et culling hors écran, activables séparément ;
* dégradation automatique activable — sinon l'aperçu est refusé net.

La dégradation suit un ordre fixe et **le dit** : d'abord l'anticrénelage
redescend, ensuite le nombre de faces dessinées est limité au prorata, et si
même cela ne rentre pas, l'aperçu est refusé avec les chiffres exacts (« il
faudrait 8,2 Go pour un budget de 6 Go »). Le panneau du modèle affiche
l'estimation en direct, la part du budget utilisée et les décisions prises.

Côté code : `hookycreator/settings.py` (bornes et persistance dans
`work/settings.json`), `hookycreator/vram.py` (estimation, plan, découpe des
maillages), `hookymod/render.py` (`frustum=` pour l'élagage hors écran).
L'API expose `/api/settings` (GET/POST) et `/api/model/plan`.

## Une fenêtre d'atelier, pas un site

L'interface reprend les codes d'un logiciel de bureau : barre de titre avec
menus déroulants (Fichier, Édition, Modèle, Texture, Blender, Affichage, Aide),
rail d'activités à gauche, **onglets de documents** (un modèle, une texture, un
`.blend` ouvert restent ouverts), inspecteur docké à droite, **journal** en bas
(Ctrl+J) et barre d'état (projet, compteurs, budget, heure).

| raccourci | effet |
|---|---|
| Ctrl+K | palette de commandes (vues, actions, modèles, textures, `.blend`) |
| Ctrl+J | journal |
| Ctrl+1 … Ctrl+6 | accueil, modèles, textures, mobs, blender, réglages |
| Ctrl+F | filtrer la vue courante |
| F5 | rafraîchir le projet |
| Échap | fermer menu, palette et inspecteur |

L'accent (menthe, azur, ambre, framboise) et la densité (confort, compact) se
règlent dans **Réglages → Interface** et sont mémorisés avec le reste.

## Traduire un `.blend` (sans Blender)

Onglet **Blender** : on dépose un `.blend`, l'atelier le lit lui-même.

1. le fichier est décompressé si besoin (Blender 3.0+ enregistre en zstd) ;
2. son **ADN** (SDNA) est lu pour interpréter ses blocs — aucune disposition de
   structure n'est codée en dur ;
3. les objets, leur matrice et leur maillage sont extraits, quelle que soit la
   génération : `mvert`/`mpoly`/`mloop` (2.5 → 3.6), attributs `CustomData`
   (3.5+), `face_offset_indices` (4.1+) ;
4. les faces sont triangulées, les coordonnées ramenées de l'axe Z (Blender) à
   l'axe Y (jeu), et la géométrie peut être mise à l'échelle du modèle cible ;
5. un aperçu PNG s'affiche **avant** l'injection, et un second après.

**Où ça s'arrête, et pourquoi.** L'injection réécrit la géométrie *dans* le
BFRES cible en gardant sa disposition (attributs, strides, matériaux). Cela
demande de connaître la table des sous-maillages du fichier. Un BFRES 7/8 se
réécrit ; un **BFRES 10.x se lit mais ne se réécrit pas encore** : sa table de
sous-maillages n'est pas décodée, donc on ne peut pas promettre que le jeu
affichera le bon nombre de triangles. Dans ce cas l'atelier refuse avec ce
message et propose l'export Blender — il ne produit jamais un fichier faux.

Formats acceptés pour le `.blend` : Blender 2.5 à 4.x, compressé ou non.
Rien n'est écrit dans le dump : le résultat part dans le dossier de travail et
sera repris par l'assemblage du mod.

## Rapidité : cache disque et préchauffage

Décoder un BFRES coûte près d'une seconde (analyse des tampons, cohérence des
indices) : redessiner une grille de vingt-cinq modèles à chaque ouverture prenait
**22 secondes**. Trois choses ont changé :

* **cache disque des vignettes** — la clé contient le chemin, la taille, la date
  du fichier, la taille demandée et les réglages de rendu : modifier un modèle
  ou changer l'anticrénelage invalide la vignette tout seul. Servir les mêmes
  vingt-cinq tuiles prend désormais **3 ms** ;
* **préchauffage** — au démarrage, deux fils remplissent le cache en tâche de
  fond (le décodage reste mono-thread : c'est du Python pur) ; la barre d'état
  affiche `vignettes 12/25`. Ce qui est déjà en cache est sauté ;
* **ETag** — le navigateur reçoit un `ETag` par image et redemande `304` : une
  vignette déjà vue ne retélécharge aucun octet. L'URL porte `&v=<date>`, donc
  une modification du fichier change l'URL et l'image.

Les grilles sont **paginées** (48 tuiles, puis « afficher plus ») et chargées en
`loading="lazy"` : une grille de mille modèles n'ouvre pas mille requêtes.

Réglages → **Mémoire & cache** : mémoire du processus, taille du cache, nombre de
maillages gardés en RAM (LRU borné à la moitié du budget), bouton « vider le
cache », bouton « rescanner ». Un bug de concurrence a été corrigé au passage :
deux vignettes qui se rendaient ensemble se volaient les dictionnaires zstd
(« aucun dictionnaire trouvé » aléatoire).

## Sélection multiple et export groupé

Clic = ouvrir, **Ctrl+clic** = ajouter/retirer, **Maj+clic** = plage,
**Ctrl+A** = tout. Une barre flottante indique la sélection et propose
« Exporter en ZIP » : le serveur écrit un seul fichier (`modèles/…/OBJ+glTF`,
`textures/…/PNG`, `LISEZMOI.txt`) au lieu de N téléchargements.

## Les trois pastilles

Elles font quelque chose : 🔴 quitte l'atelier (arrêt du serveur après
confirmation), 🟡 réduit l'interface (rail masqué), 🟢 passe en plein écran réel
(API Fullscreen). Le titre des boutons suit l'état, et un refus du navigateur est
signalé plutôt qu'ignoré.
