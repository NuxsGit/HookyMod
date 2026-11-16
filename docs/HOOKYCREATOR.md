# HookyCreator — guide de l'atelier

HookyCreator fonctionne dans un navigateur local ou dans une fenêtre native. Il explore des ressources fournies par l'utilisateur, prépare des exports et travaille sur des copies. Aucun résultat n'est garanti fidèle ou fonctionnel en jeu ; vérifie les rapports et teste avec tes propres outils.

## Démarrer et retrouver son projet

```bash
python -m pip install -r requirements.txt
python -m hookycreator /chemin/vers/romfs --host 127.0.0.1 --open
# Autre emplacement pour les copies, caches et sessions :
python -m hookycreator /chemin/vers/romfs --host 127.0.0.1 --work /chemin/vers/travail --open
```

Le projet peut être un dossier de fichiers extraits. Les dictionnaires zstd sont recherchés dans le projet et ses parents. Le serveur navigateur est destiné à un usage local : évite de l'exposer à un réseau non fiable. Pour la fenêtre native, voir [DESKTOP.md](DESKTOP.md).

Par défaut, les modifications sont écrites dans `<projet>/hookycreator_work/assets/`. Les vues, onglets, filtres, sélections et brouillons sont sauvegardés par projet, séparément des fichiers édités et des réglages. En navigateur, relance le même projet avec le même `--work` éventuel ; la fenêtre native rouvre le dernier projet accessible. La reprise ne remplace pas une sauvegarde et une interruption brutale peut perdre le dernier état : [PERFORMANCE.md](PERFORMANCE.md).

Le scan repère jusqu'à 200 000 fichiers reconnus par défaut. S'il est tronqué ou si des entrées sont illisibles, un avertissement persistant signale que les listes et les exports issus du scan peuvent être incomplets. `--scan-limit 0` supprime le plafond ; l'inventaire peut alors demander plus de mémoire. Les vues vérifient progressivement les candidats et masquent ceux qui ne sont pas exploitables ; cette vérification ne démontre pas leur fidélité.

## Les espaces

- **Modification / HookyCreator** : modèles, ensembles, animations, textures, sons, vidéos WebM et import de modèle.
- **HookyMod's** : atelier distinct pour imaginer de nouveaux éléments et des blocs visuels, puis exporter ou importer un projet `.hooky`. Ce prototype de conception ne produit pas de mod jouable et ne lance pas de jeu ; voir [HOOKYMODS.md](HOOKYMODS.md).
- **Création de mod** : parcours historique des copies modifiées, fiches préparatoires et assemblage, indépendant des projets HookyMod's.
- **Réglages** : budget des aperçus, vignettes, thèmes clair/sombre/Aurora, langue FR/EN/RU/ZH, cache et moteur. Les préférences de l'ancien thème sont automatiquement migrées vers Aurora sans modifier les autres réglages. Certains diagnostics du backend et messages de la fenêtre native restent en français.

Les grilles et boutons peuvent être utilisés à la souris ou au clavier. Les flèches déplacent le focus entre tuiles, Entrée ouvre l'élément et Échap ferme les panneaux. `Ctrl+K` ouvre la palette de commandes, `Ctrl+J` le journal et `Ctrl+F` la recherche.

## Exporter un modèle

1. Ouvre **Modification → Modèles**, puis la fiche du modèle. La vue 3D affiche tous les triangles décodés ou signale un refus ; elle ne remplace pas un contrôle dans un logiciel 3D. La fiche affiche les remarques de décodage et les textures candidates ou référencées.
2. Clique sur **Exporter en glTF / OBJ**. Un dossier d'export est préparé et le résultat donne son chemin absolu ainsi qu'un lien de téléchargement ZIP. Le ZIP contient uniquement les fichiers produits par cette opération. Un nouvel export du même modèle **remplace les fichiers de même nom** dans son dossier : sauvegarde ailleurs tes modifications avant de le relancer. Les autres fichiers préexistants restent en place et sont signalés, mais ne sont pas ajoutés au ZIP.
3. Ouvre le `.gltf` avec son `.bin` et ses PNG dans ton logiciel 3D, ou l'OBJ avec son MTL. Lis `RAPPORT_MODELE.txt` pour les limites géométriques et `TEXTURES.md` pour les cartes. Le panneau d'export garde également les remarques visibles après la disparition des notifications.

Un marqueur local `.hookycreator-source.json` associe le dossier à sa source, y compris après un nouveau scan ou la réouverture du projet. Il contient le chemin du projet et ne fait pas partie du ZIP. Un dossier préexistant sans marqueur reconnu est laissé intact et un autre nom est choisi ; si le marqueur est supprimé ou le dossier renommé, un nouveau dossier est également réservé.

Les maillages reconnus sont exportés avec positions, UV et normales lorsque ces attributs sont décodés. Les normales absentes ne sont pas présentées comme des données d'origine ; les logiciels de rendu peuvent en calculer. Les UV internes et glTF utilisent V = 0 en haut ; l'OBJ utilise V = 0 en bas. **Vérifier les UV** trace les triangles sur une texture et permet de comparer les deux orientations, sans promettre un rendu identique au jeu.

### Examiner le modèle en 3D

La fiche lit les modèles reconnus directement dans l'atelier. Dans la vue WebGL2, glisse pour tourner, utilise la molette pour zoomer ou les flèches et `+` / `−` au clavier ; **Recentrer**, **Plein écran**, **Faces plates**, **Fil de fer** et **Texturé** servent à inspecter la géométrie. En plein écran, `Échap` revient à la fiche et `Home` recentre. La planche PNG reste accessible, ainsi que le retour à la vue 3D. La vue affiche le nombre de triangles effectivement transmis ; aucun sous-échantillonnage ne s'active pendant la rotation. Elle ne change ni l'export ni le BFRES. Elle montre le LOD décodé, pas nécessairement tous les LOD ou toutes les structures du fichier : lis les remarques.

Par sécurité, la vue interactive refuse plus de 2 millions de sommets, 4 millions de triangles ou 128 Mio de géométrie, au lieu d'en retirer une partie. Le fil de fer est refusé si ses indices dépassent 64 Mio. Les images sont plafonnées à 1 600 pixels par côté et à une densité d'écran ×2. Si WebGL2 ou la mémoire graphique est indisponible, un message propose le PNG fixe. Ce dernier et les vignettes utilisent toutes les faces décodées ou sont refusés au-delà de 4 millions de faces / du budget. L'anticrénelage PNG peut être réduit si l'option correspondante est activée ; les réglages de culling ne concernent que le PNG. Un gros modèle peut donc prendre plus de temps à afficher. L'API conserve la possibilité d'un plafond explicite pour les anciens appelants, indépendamment de la vue 3D.

Le sélecteur **FSR 1 expérimental** active les passes EASU et RCAS sur une image rendue à environ 67 % de la résolution, sans changer les triangles. Il peut améliorer les contours ou au contraire être plus lent et moins net selon le matériel ; teste aussi la résolution native. Un échec de compilation ou d'allocation revient au mode natif avec une explication. FSR n'est ni DLSS ni une réparation de géométrie : DLSS n'est pas intégré au moteur WebGL. Les albédos non ambigus sont limités à 16 images de 1 024 pixels pour la vue ; matériaux complexes, transparences et shaders du jeu ne sont pas reproduits. Les exports de textures ne sont pas limités par ces réglages d'affichage.

### Matériaux et textures

Les références FMAT reconnues sont résolues par nom de fichier, puis par nom interne dans le conteneur BNTX ; l'index de l'image correspondante est conservé. Les cartes non ambiguës sont reliées au matériau correspondant dans le glTF, et l'OBJ/MTL référence l'albédo et la normale lorsqu'ils sont disponibles. Des noms manquants, doublons, images illisibles ou plusieurs cartes candidates pour le même rôle sont signalés et ne sont pas raccordés arbitrairement. La résolution ne parcourt pas tous les noms internes de conteneurs portant un nom de fichier différent.

Sans références explicites, une recherche par nom fournit des **candidats heuristiques**, pas une preuve. Un modèle avec plusieurs matériaux sans références explicites ne reçoit pas de raccordement automatique. Les rôles sont déduits des suffixes et doivent être vérifiés :

| Rôle | Exemples de suffixes | Utilisation courante |
| --- | --- | --- |
| Albédo | `_Alb`, `_Dif`, `_BaseColor` | Couleur de base, sRGB |
| Normale | `_Nrm`, `_Normal` | Normal Map, données non colorimétriques ; en OBJ/MTL, `map_Bump` peut être interprété comme relief selon le logiciel |
| Rugosité / métal | `_Rgh`, `_Mtl` | Carte glTF combinée : G = rugosité, B = métal |
| Occlusion / émission | `_AO`, `_Emi` | Occlusion / émission |
| Spéculaire, masque ou inconnu | `_Spc`, `_Msk`, autres | Disponible si décodé ; raccordement manuel possible |

Une carte absente peut être réellement absente, non reconnue ou illisible. Le matériau d'origine peut utiliser des canaux ou shaders non reconstruits ; une texture liée n'est donc pas une preuve de rendu fidèle.

### Limites de lecture BFRES

Le lecteur 10.x structuré suit les tables reconnues pour les formes, indices, matériaux et le LOD 0 ; si cette structure n'est pas disponible, certains fichiers peuvent passer par une extraction heuristique explicitement signalée. D'autres fichiers sont refusés. Pour les formes rigides à zéro ou une influence dont le squelette FSKL est reconnu (os à échelle unitaire, sans billboard et sans poids explicites), positions et normales sont replacées avec les transformations de repos ; les matrices inverses présentes servent à contrôler la hiérarchie. Une variante non comprise reste non replacée et est signalée. Le squelette exportable, la pose complète, l'animation, le morphing, les tangentes, les shaders et les autres LOD ne sont pas reproduits. Une géométrie statique peut donc différer de la pose en jeu même quand l'export aboutit. Les tests avec des fixtures synthétiques et quelques modèles réels ne couvrent pas toutes les variantes ni les textures réelles.

## Importer OBJ, glTF ou GLB

1. Ouvre **Importer un modèle**, choisis exactement un fichier `.obj`, `.gltf` ou `.glb` et, pour un glTF à tampons externes, les `.bin` qu'il référence. Les PNG et MTL ne sont pas importés ; les URI réseau et les accès à des fichiers non sélectionnés sont refusés. Limite totale : 64 Mio, jusqu'à 16 fichiers `.bin`.
2. Choisis l'orientation et l'échelle, puis **Analyser et prévisualiser**. Les OBJ doivent être triangulés avant l'injection ; les triangles d'aire nulle sont refusés. Les scènes glTF 2.0, transformations et tampons supportés sont lus de façon bornée ; les extensions obligatoires, accessors sparse, compression, skinning et morphs non pris en charge sont refusés. Les attributs source qui ne peuvent pas être conservés (tangentes, couleurs ou UV secondaires) autorisent l'aperçu, mais bloquent l'injection. Des coutures UV peuvent augmenter le nombre de sommets ; le maximum est de 1 million de sommets et 2 millions de triangles.
3. Choisis explicitement une cible proposée. La liste ne propose que des cibles ayant passé les vérifications connues ; elle peut être vide. L'analyse de la source n'assure pas qu'elle tient dans la cible. L'option d'adaptation applique une échelle uniforme et recentre la géométrie sur les dimensions de la cible.
4. Clique sur **Injecter dans la copie de travail** et lis le rapport. L'outil vérifie la capacité et les attributs, réécrit puis relit les données avant l'écriture atomique de la copie. Les matériaux et textures source ne sont pas transférés : ceux de la cible restent en place.

L'écriture est limitée à certaines structures **BFRES 9.x** : un modèle, une forme et un LOD ; triangles et tampons de capacité fixe ; pas de squelette, morphing, boîtes ou rayons à réécrire, attributs inconnus ou matériaux source multiples. L'écriture BFRES 10.x est refusée. Les UV de la source et de la cible doivent correspondre ; une normale fournie ne peut pas être écrite dans une cible dépourvue de canal normal. Si la source n'a pas de normales mais que la cible en a, elles peuvent être recalculées. Certaines valeurs ne peuvent pas être représentées dans le format cible et sont refusées après vérification.

Le fichier source du projet n'est pas modifié par l'injection. Les changements vont dans la copie de travail ; les modifications successives portent sur la copie courante. Une variation de taille compressée peut nécessiter une mise à jour de RESTBL. Même relu correctement, le résultat doit être testé : l'outil ne garantit pas sa compatibilité en jeu.

## Textures et sons

La fiche d'une texture permet d'inspecter les images d'un BNTX et de choisir leur index. L'aperçu, l'export PNG et le remplacement utilisent cet index ; l'original reste inchangé. L'export PNG va dans `export_textures/<conteneur>/` du dossier de travail, avec l'index dans le nom ; réexporter le même index remplace son PNG. Les dossiers préexistants sans marqueur reconnu sont évités. Les formats encodables comprennent notamment BC1/BC3/BC4/BC5 et plusieurs formats non compressés. ASTC et BC7 ne sont pas encodables. Les formats de lecture BC6/BC7/ASTC nécessitent `texture2ddecoder`.

La vue Sons est expérimentale : elle lit des BWAV complets PCM16 ou DSP-ADPCM mono/stéréo et certains Opus mono 48 kHz. L'export propose WAV, FLAC, MP3 et Ogg Vorbis, ainsi qu'un remultiplexage Ogg Opus sans réencodage pour les Opus reconnus. Les conversions ne conservent pas les marqueurs de boucle. Le remplacement nécessite la même fréquence et le même nombre de canaux, et écrit une copie BWAV PCM16 dans le dossier de travail (même si la source était Opus ou DSP). Les banques BARS, sons préchargés, autres dispositions/codecs, événements et RESTBL ne sont pas modifiés. **La copie de remplacement n'est pas validée en jeu.**

## Prévisualiser des animations

La vue Animations répertorie les BFRES `.anim.bfres` et `.anim.bfres.zs`. Choisis un clip FSKA Euler et un modèle, ou sélectionne une animation liée depuis la fiche modèle. Les os sont associés par nom et la lecture utilise 30 images/s par convention. La vue 3D transporte jusqu'à huit influences de skin ; elle refuse les variantes de squelette ou de courbe non vérifiées. Les courbes peuvent être exportées en JSON. La vue est expérimentale : matériaux, physique, morphs et fidélité en jeu ne sont pas garantis, et l'export glTF/OBJ reste **statique**, sans rig ni animation.

## Vidéos WebM

La vue **Vidéos** repère les fichiers `.webm`. La lecture et le déplacement dans la vidéo dépendent des codecs disponibles dans le navigateur ou la WebView ; le téléchargement fournit le fichier courant. Un remplacement copie les octets du WebM choisi dans le dossier de travail, sans transcodage et sans modifier l'original. La lecture est limitée à 1 Gio et le fichier de remplacement à 32 Mio.

Le contrôle est **limité à l'en-tête EBML, au DocType WebM et aux bornes du Segment** : il ne valide ni les pistes, ni les codecs, ni la durée, ni les dimensions, ni le fonctionnement en jeu. Un fichier accepté peut donc rester illisible ou incompatible. Les données NDVE n'ont pas été identifiées comme conteneur vidéo standard et ne sont ni lues ni modifiées ; audio séparé et RESTBL ne sont pas mis à jour non plus.

## Exporter une sélection ou tout un projet

La sélection multiple fournit un ZIP contenant les modèles et textures choisis ; chaque modèle y conserve son rapport. L'archive est temporaire : télécharge-la rapidement. Le cache d'archives est élagué au mieux (7 jours, 24 archives ou 2 Gio).

```bash
python -m hookycreator export-all /chemin/vers/romfs -o export_all
# Les BFRES auxiliaires peuvent être inclus comme éléments ignorés dans le rapport :
python -m hookycreator export-all /chemin/vers/romfs -o export_all --kinds texture,model,animation,auxiliary
```

Le mode lots parcourt aussi les packs reconnus jusqu'à trois niveaux. Les modèles seuls dans le projet utilisent les mêmes règles de liaison par matériau que l'export individuel. Pour les modèles **dans une archive**, les textures sont exportées séparément sans raccordement automatique ; cette limite figure dans `rapport.json` et le rapport de modèle. Les archives trop profondes ou illisibles peuvent ne pas être découvertes. Les statuts `ok`, `skip` et `error`, les remarques et un éventuel scan incomplet figurent dans `rapport.json` : `ok` signifie qu'un export a été écrit, pas qu'il est complet ou jouable.

Les dossiers de lots portent eux aussi un marqueur local de source ; les dossiers de provenance inconnue ne sont pas réutilisés. Relancer un lot **remplace les fichiers de même nom** dans les dossiers reconnus, ainsi que `rapport.json`, sans supprimer les autres fichiers. Sauvegarde les exports modifiés avant de recommencer et évite de lancer plusieurs processus d'export en parallèle vers une même destination.

## Assemblage et sécurité des données

**Création de mod** assemble les copies sous `mods/<nom>/romfs/`. Construire un mod **supprime et recrée entièrement** `mods/<nom>` s'il existe, sans sauvegarde ; l'interface demande confirmation mais l'API directe ne le fait pas. Sauvegarde ce dossier avant toute opération. Les noms sont nettoyés ; deux noms saisis peuvent donc désigner le même dossier.

Une fiche de mob copie des fichiers et conserve des notes, mais ne crée ni acteur, ni placement, ni entrée RESTBL. L'assemblage ne met pas non plus RESTBL à jour automatiquement pour les ressources modifiées. Inspecte les tailles et teste le mod dans son environnement cible.

Le noyau Rust est facultatif et ne couvre qu'une partie des traitements. Son état et les replis Python sont affichés dans **Réglages → Moteur & performances** ; détails dans [RUST.md](RUST.md).
