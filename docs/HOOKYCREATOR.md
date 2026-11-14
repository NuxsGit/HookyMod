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

## Ouvrir un modèle : mesurer, puis diviser le temps

Un clic sur un modèle prenait **10 à 30 s** sur les personnages complets. Profil
en main, le coût se répartissait ainsi sur une armure de 2,3 Mo :

| étape | avant | après |
|---|---|---|
| fiche du modèle (`model_info`) | 4,97 s (redécodait tout) | **0,65 s** |
| décodage de la géométrie | 4,87 s | **0,39 s** |
| rendu 460 × 345 | 4,17 s | **1,1 s** |
| réouverture (cache disque) | 4,97 s | **0,30 s** |
| export Blender (OBJ + glTF) | 18,5 s | **1,9 s** |

Ce qui a changé :

* **la fiche ne redécode plus** : elle réutilise les maillages déjà décodés (le
  même chemin que l'aperçu) au lieu de relire le BFRES une deuxième fois ;
* **géométrie mise en cache sur disque** (`work/cache/geo/*.geo`, clé = taille +
  date) : rouvrir un modèle coûte une lecture de fichier, pas un décodage ;
* **décodage plus direct** : positions lues via `array` (au lieu d'un appel
  `struct.unpack_from` par sommet), distances calculées sans `math.dist`
  (4,7 millions d'appels en moins), et **seulement les sommets réellement
  utilisés** (2,4 M de sommets décodés pour 165 000 faces auparavant) ;
* **rastériseur en balayage de lignes** : le z avance par addition au lieu de
  trois produits scalaires par pixel, l'éclairage est calculé une fois par face
  (plus de `math.cos` par face), les bornes du nuage de points sont calculées en
  une passe, et la géométrie aplatie est réutilisée d'un aperçu à l'autre ;
* **plafonds réglables** : « Plafond des vignettes » et « Plafond de l'aperçu
  détaillé » (Réglages ▸ Interface). Un modèle dense est échantillonné au lieu
  d'être tronqué, donc la silhouette reste entière.

## Trois défauts corrigés au passage

1. **Une épée exportée en morceaux.** Le découpage des sous-maillages ne
   regardait que des blocs de 0x1000 octets et s'arrêtait au premier triangle
   douteux : un sous-maillage qui commençait au milieu d'un bloc était perdu.
   Le découpage avance maintenant mot par mot et prolonge chaque suite tant
   qu'elle reste valide, où qu'elle commence.
2. **De longues traînées horizontales à l'écran.** L'interpolation des bords
   sortait du triangle sur une arête presque horizontale (extrapolation) : une
   arête de 0,01 unité barrait tout l'écran. Les lignes sont désormais bornées au
   triangle, et les triangles aberrants (arête > 8 × l'arête médiane) sont
   écartés.
3. **Un glTF refusé par Blender** : le `.bin` n'était pas recopié dans le ZIP de
   sélection, et les PNG déjà présents dans le dossier d'export n'étaient pas
   liés (copie sur soi-même). Le `.bin` fait maintenant partie des fichiers de
   l'export, et les albédos sont rattachés aux matériaux même quand le nom du
   matériau ne contient pas celui de la texture (`Mt_Blade` ↔
   `Weapon_Sword_Kokiri_Alb`), avec repli sur l'unique albédo du modèle.

Ce qui est **refusé** reste affiché : la fiche du modèle liste les sous-maillages
ignorés et leurs raisons (« 39 suite(s) d'indices ignorée(s) faute de
validation »), l'export renvoie les mêmes remarques, et l'atelier écrit
explicitement « aucune texture trouvée » plutôt que de produire un glTF muet.

# Vue « Ensembles » : voir un objet en entier

Un modèle du jeu n'est jamais seul. `Animal_Cat.Animal_Cat.bfres` a ses textures
dans un autre dossier et son animation dans `Animal_Cat_Animation.anim.bfres`.
L'onglet **Ensembles** réunit les trois pour chaque modèle :

* la vignette est le **modèle assemblé**, coloré par son albédo (une couleur par
  face, prise au centre des UV) — c'est la vue « en entier » ;
* deux pastilles disent ce qui est là : `1 texture(s)` / `1 anim(s)` ; grisées
  quand il manque quelque chose (`complet` = les deux) ;
* un appariement par nom relie les fichiers sans en décoder aucun, donc la liste
  s'affiche instantanément même sur un dump complet (150 modèles : < 0,5 s).

L'appariement des animations enlève le suffixe que le jeu ajoute
(`_Animation`, `_Player_Animation`) avant de comparer, sinon rien ne
correspondrait : `Animal_Cat_Animation` → `animal_cat`. Les albédos sont classés
par précision du nom (nom du modèle → famille `Weapon_Sword` → préfixe
`Weapon`), et les cartes de normale ou de métallique sont écartées : elles ne
doivent jamais colorer le modèle.

Sans texture, la couleur vient du **matériau** (et non du sous-maillage) : un mob
découpé en dix-huit morceaux qui partagent `Mt_Body` apparaît d'une seule teinte
au lieu d'une mosaïque de confettis. C'était le défaut qui empêchait de lire un
animal « en entier ».

La fiche d'un modèle affiche désormais ses **textures** (bande de vignettes
cliquables) et ses **animations** liées. Les animations restent des fichiers
séparés du jeu : elles ne sont pas encore injectées dans le modèle exporté, et
la fiche le dit.

Réglages ▸ Interface ▸ « colorer les aperçus avec les textures du modèle »
permet de revenir à la géométrie nue (utile pour lire une silhouette).

# Export `.blend`

Un `.blend` est le format interne de Blender. Le produire à la main suppose de
réimplémenter son format (SDNA, blocs, images embarquées) et le moindre écart
donne un fichier que Blender refuse. HookyCreator fait donc ce que fait un
utilisateur : **il pilote Blender**.

1. Le modèle est exporté comme d'habitude (glTF + `.bin` + OBJ + PNG).
2. `creer_blend.py` est écrit à côté.
3. Si Blender est trouvé — chemin réglé dans **Réglages**, sinon `blender` du
   `PATH`, sinon les dossiers d'installation Windows (`C:\Program Files\Blender
   Foundation\Blender 4.x`) — il est lancé en tâche de fond :
   `blender --background --factory-startup --python creer_blend.py -- mon.gltf mon.blend`.
   Le script importe le glTF, **emballe les textures** dans le fichier
   (`pack_all`), puis enregistre le `.blend`.
4. Le fichier est **vérifié** avant d'être annoncé : présent, non vide, plus
   récent que l'export. Un `.blend` périmé est supprimé avant l'essai pour ne
   jamais passer pour un résultat.

Si Blender est absent ou échoue, l'atelier ne prétend rien : il affiche la
commande exacte à lancer, la propose à la copie, et laisse le script utilisable.
Le bouton **Exporter en .blend** de la barre de sélection produit un seul ZIP
(`hookycreator-blender.zip`) avec un dossier par modèle — glTF, `.bin`, PNG,
`creer_blend.py` et, quand Blender est là, le `.blend` — plus un `LIRE-MOI.txt`
qui donne la commande de chaque modèle restant. Le ZIP reste donc utilisable sur
une machine sans Blender.

# Orientation des textures (V) et vérificateur d'UV

## Le bug

Une épée (Biggoron) exportée pour Blender arrivait avec sa texture **à l'envers** :
il fallait retourner l'image dans Paint pour que ça marche. Cause : l'export
retournait V **deux fois** pour le glTF. Chaque format a sa convention, et elles
ne sont pas les mêmes :

| Format | Origine de (0, 0) | Ce que fait l'importeur Blender | Ce qu'il faut écrire |
| --- | --- | --- | --- |
| `.gltf` | coin **haut**-gauche (spec glTF 2.0, §3.8.3) | retourne V vers son repère (bas-gauche) | `v` **tel quel** |
| `.obj` | coin **bas**-gauche (OpenGL) | ne retourne rien | `1 - v` |

Les UV lues dans le BFRES sont déjà dans le repère du jeu (V = 0 en haut de
l'image), comme le glTF. Écrire `1 - v` dans le glTF annulait la conversion de
Blender : texture à l'envers. L'OBJ, lui, avait raison depuis le début — les deux
fichiers ne doivent donc **pas** porter le même V, et c'est documenté dans
`IMPORT_BLENDER.txt` pour éviter qu'on « corrige » l'un avec l'autre.

## Le vérificateur

Ouvrir un modèle ▸ **Vérifier les UV**. L'écran montre l'atlas du modèle avec les
**triangles UV tracés dessus** (translucides, une couleur par matériau) :

* si le contour du maillage épouse les zones utiles de la texture (la lame sur la
  lame, la garde sur la garde), la texture tombe juste ;
* le bouton **V retourné** rejoue le tracé avec `1 - v` : quand les deux se
  ressemblent, c'est en comparant qu'on tranche, pas en devinant.

Sous l'image, les chiffres qui trahissent un vrai défaut :

* **bornes U / V** — un modèle dont toutes les UV sortent de [0, 1] se répète ;
* **hors [0, 1]** — nombre de sommets concernés (tiling voulu sur un tissu,
  suspect ailleurs) ;
* **triangles plats en UV** — aire nulle : la texture y est réduite à un point ;
* **étirement** — 95ᵉ centile de la densité de texels rapporté à la médiane
  (le maximum servait de faux positif à cause d'un unique triangle minuscule).

Un modèle sans coordonnées de texture le dit explicitement au lieu d'afficher un
atlas vide.

## Bugs visuels corrigés au passage

* les textures transparentes se lisent sur un **damier** (elles s'affichaient sur
  un fond noir, indistinguables d'une image noire) ;
* une vignette qui ne se charge pas marque sa tuile « aperçu indisponible » au
  lieu de laisser l'icône « fichier manquant » du navigateur ;
* le rail a maintenant sept entrées : il **défile** sur une fenêtre basse au lieu
  de rogner les dernières ;
* `.badge` était défini deux fois (styles partiellement écrasés) : une seule
  définition, avec les deux variantes `warn` et `off` ;
* la barre d'état compte aussi les **animations**, et un bouton d'export
  sélectionné indique son état (`aria-pressed`) au lieu de rester neutre.

## Toutes les cartes de texture, pas seulement la couleur

L'export d'origine ne sortait que l'**albédo** : dans Blender, l'épée de
Biggoron arrivait avec sa couleur, mais sans normale (surface lisse et fausse),
sans rugosité (tout brillant) et sans métal (tout plastique). La cause était un
filtre sur le nom : `texture_assets_for` ne gardait que `_Alb`, `_Dif`,
`albedo`, `basecolor`.

Désormais un modèle exporte **toutes** ses cartes. Le suffixe du fichier suffit
à connaître le rôle — et c'est toujours le **dernier** jeton qui décide, sinon
le préfixe `Mt_` (pour *material*) serait lu comme du métal :

| Suffixes rencontrés | Rôle | Entrée Principled BSDF |
| --- | --- | --- |
| `_Alb` `_Albedo` `_BC` `_BaseColor` `_Dif` `_Col` | albédo | Base Color (sRGB) |
| `_Nrm` `_Normal` | normale | Normal ← nœud Normal Map (Non-Color) |
| `_Rgh` `_Rough` | rugosité | Roughness (Non-Color) |
| `_Mtl` `_Met` `_Metal` | métal | Metallic (Non-Color) |
| `_AO` `_Occ` | occlusion | facteur AO (Non-Color) |
| `_Emi` `_Emit` | émission | Emission, force 1.0 |
| `_Spc` `_Spec` | spéculaire | pas d'équivalent glTF → à la main |
| `_Msk` `_Mask` `_Op` `_Alpha` | masque / opacité | Alpha, à la main |
| autre | `autre` | **exporté quand même**, signalé à part |

Un suffixe inconnu n'est jamais jeté : une carte qu'on cache est une carte
qu'on croit absente. Elle part dans l'export, classée « autre », et
`TEXTURES.md` dit qu'il faut l'ouvrir pour décider.

### Ce que produit un export

* un PNG par carte, nommé comme dans le jeu (`Mt_..._Alb.png`, `..._Nrm.png`…) ;
* un `*_MetalRough.png` **composé** : **G = rugosité, B = métal** — c'est
  l'emballage que glTF et Unity attendent, et il évite les deux images ;
* `TEXTURES.md` : le tableau fichier → rôle → entrée Blender, plus les pièges ;
* `assembler_shaders.py` : un script `bpy` qui recrée les nœuds tout seul
  (Base Color, Normal Map, Separate Color sur la MR, émission) ;
* le `.gltf` porte `baseColorTexture`, `normalTexture`,
  `metallicRoughnessTexture`, `occlusionTexture` et `emissiveTexture`, avec
  `metallicFactor`/`roughnessFactor` à 1.0 pour que ce soit **l'image** qui
  décide (sinon Blender assombrit malgré la carte) ;
* le `.mtl` de l'OBJ porte `map_Kd` et `norm` (Wavefront n'a rien d'autre).

### Le piège n°1 : l'espace colorimétrique

Une normale lue en sRGB paraît bombée de travers, une rugosité en sRGB donne un
métal trop brillant. Une seule règle : **albédo et émission en sRGB**, tout le
reste (normale, rugosité, métal, AO, masque) en **Non-Color**. Le script le fait
pour vous ; à la main, c'est le menu déroulant au-dessus de l'aperçu de l'image.

Quand un modèle n'a **que** l'albédo, ce n'est pas un bug : le jeu n'a pas plus
de cartes pour lui. Gardez `Roughness ≈ 0.5`, `Metallic = 0`.

### Dans l'interface

* chaque vignette de texture porte une **pastille de rôle** (`Alb`, `Nrm`,
  `Rgh`, `Mtl`, `AO`, `Emi`, `Spc`, `Msk`, `?`) — le nom complet est tronqué
  dans la grille, la pastille non ;
* le menu **« Toutes les cartes »** de l'écran Textures ne montre qu'un rôle ;
* la fiche d'un modèle liste **toutes** ses cartes, groupées par rôle ;
* la **coche verte** sur une vignette est le bouton de sélection (Ctrl+clic
  pour en prendre plusieurs) : ce n'est pas un état de texture.

Tests : `tests/test_texture_maps.py` (20 cas) — classification par suffixe,
`Mt_` non métallique, composition de la MR, export multi-cartes, câblage glTF
et MTL.

## Le site était devenu lent : ce qui coûtait, et ce que ça coûte maintenant

Trois causes, mesurées avant de toucher au code. Aucune n'était « le navigateur
est lent » : c'étaient des parcours complets de la liste des fichiers, répétés
pour chaque modèle, et un état de projet renvoyé en clair à chaque chargement.

| Ce qui se passait | Avant | Après |
| --- | --- | --- |
| Ouvrir la vue **Ensembles** (600 modèles, 3 600 fichiers) | **4,27 s** | **12 ms** |
| La même chose, deuxième affichage | 4,27 s | 0 ms (mémorisé) |
| Chercher les cartes de 600 modèles | 3,0 s | 5 ms |
| `GET /api/state` (même projet) | 615 Ko, 20 ms | **21 Ko** compressé, 5 ms |
| `GET /api/state` (12 000 assets) | 2,1 Mo | **71 Ko**, 16 ms |
| Scan de 18 000 fichiers | — | **178 ms** |
| Afficher une vignette de texture (1024²) | 18,5 Ko | **1,2 Ko** (256 px) |
| Recherche : une frappe | 1 rendu de grille | 1 rendu après 120 ms de calme |

### 1. Chaque modèle relisait tout le projet

`texture_assets_for`, `texture_maps_for` et `animations_for` parcouraient la
liste **complète** des assets à chaque appel : le coût était
`modèles × fichiers`. À 600 modèles et 3 600 fichiers, 4 secondes pour afficher
une liste ; sur un vrai dump, la vue ne s'affichait plus.

Désormais `scan()` construit une fois pour toutes :

* `by_kind` — les assets déjà triés par famille (plus de `by_kind()` linéaire) ;
* un index **nom → assets** pour les textures, dont la clé est le nom sans son
  espace de noms ni son suffixe de rôle (`Mt_Weapon_Sword_Biggoron_Alb` →
  `weapon_sword_biggoron`) ;
* un index équivalent pour les animations (`Animal_Cat_Animation` →
  `animal_cat`), avec recherche par intervalle trié (`bisect`) pour les
  variantes préfixées.

Le classement par précision ne change pas (nom exact, puis famille, puis
préfixe) — il est simplement indexé, et **paresseux** : un modèle qui trouve ses
textures au rang 0 ne paie pas les deux rangs suivants.

### 2. L'état du projet était reconstruit à chaque demande

`state()` re-sérialisait tous les assets, relisait le dossier de géométrie pour
ses statistiques, et reparcourait les fichiers modifiés. Trois correctifs :

* le JSON est sérialisé **une fois par version** du projet (`state_payload`) ;
* les statistiques de cache et la liste des fichiers modifiés sont mémorisées
  quelques secondes (`bump()` invalide après chaque écriture) ;
* les réponses JSON partent en **gzip** quand le navigateur l'accepte : 2,1 Mo
  d'état deviennent 71 Ko, soit un rapport de 30.

Les listes de la vue (textures, modèles, ensembles) passent par le même cache
`cached_json`, invalidé au scan, à l'import et au changement de réglages.

### 3. Les vignettes servaient la taille pleine

Une texture de 1024² dans une tuile de 160 px, c'est 20 fois trop de pixels à
transmettre **et** à décoder côté navigateur. `GET /api/texture/png` accepte
`max=` : les tuiles demandent 320 px (192 px dans la fiche du modèle), le mip le
plus proche sert quand il existe, sinon l'image est réduite par pas entier — une
copie de pixels, pas un rééchantillonnage coûteux.

La vignette pleine taille reste disponible (éditeur de texture, export).

### Ce qui a aussi été fait, plus discrètement

* `scan()` utilise `os.scandir` : un appel système par entrée au lieu de trois
  (`Path`, `is_file`, `stat`), et le tri se fait une fois à la fin ;
* les fichiers statiques (`app.js`, `style.css`) portent un **ETag** : un
  rechargement coûte un 304 de quelques octets, pas 78 Ko ;
* `find_blender()` ne relit plus le `PATH` et « Program Files » à chaque
  affichage de la vue Ensembles (mémo de 30 s) ;
* taper dans la recherche attend 120 ms de calme avant de reconstruire la grille.

### Comment vérifier soi-même

`tests/test_performance.py` (17 cas) mesure ce qui doit le rester : liste des
ensembles, recherche des cartes de chaque modèle, mémoïsation de l'état, gzip
négocié par-dessus HTTP, ETag des fichiers statiques, vignettes réduites. Les
bornes sont larges (facteur ~10) : le but est de détecter un retour au coût
`modèles × fichiers`, pas de chronométrer la machine.
