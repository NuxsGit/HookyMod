# Reprise et performances de HookyCreator

## Reprise automatique

L'interface enregistre au fil des modifications la vue et les onglets, le
contenu des champs de recherche et des formulaires (brouillons), la sélection,
la position de défilement, l'inspecteur et les options d'affichage. Les
réglages restent dans `settings.json` et les fichiers édités restent dans le
dossier de travail : l'état de navigation ne les remplace pas.

Chaque projet possède un instantané distinct dans
`<travail>/sessions/<empreinte-du-projet>.json`. Le travail est
`<projet>/hookycreator_work/` par défaut, ou le dossier indiqué par `--work`
en mode navigateur. La fenêtre native conserve le chemin du dernier projet
dans le dossier de configuration utilisateur de l'application, sans y enregistrer
de jeton ; elle le rouvre automatiquement s'il existe. Sinon le sélecteur de
dossier revient. Pour le navigateur, il faut relancer le serveur sur le même
projet et avec le même dossier `--work` éventuel ; le port peut changer.

Les écritures sont atomiques. En cas de sauvegardes arrivant dans le désordre
pour une même fenêtre, la plus récente est conservée. Si deux fenêtres ou deux
serveurs écrivent dans le même travail, la dernière écriture reçue l'emporte :
ce n'est pas une fusion collaborative. Les chemins d'assets enregistrés ne sont
rouverts que s'ils figurent encore dans l'inventaire ; un fichier de session
absent, tronqué, invalide ou d'une version inconnue est ignoré. Le scan incomplet
peut donc empêcher certains anciens onglets ou sélections d'être retrouvés.

La fermeture normale de la fenêtre native et le changement de projet attendent
la confirmation des sauvegardes de l'interface et des réglages. En cas d'échec
ou de délai dépassé, un changement de projet est annulé et la fermeture demande
confirmation. Les sauvegardes restent **best-effort** en cas de crash, arrêt
forcé, panne électrique ou fermeture brutale du navigateur. Les fichiers choisis
dans les boîtes d'import, les tâches d'export en cours, la lecture audio, le
plein écran et les positions/taille de fenêtre ne sont pas restaurés. La vue
Player peut être rouverte, mais sa composition temporaire n'est pas enregistrée
après rechargement ou fermeture ; elle est seulement conservée entre les vues
pendant la session ouverte. La session est limitée à 64 onglets, 5 000 éléments sélectionnés et 4 Mio ; les saisies
sont limitées à 2 048 caractères par champ. Une erreur d'écriture est affichée.
Ne pas considérer cette fonctionnalité comme une sauvegarde de sécurité du dump
ou du dossier de travail.

## Optimisations

- Les cartes metallic/roughness sont assemblées par canaux, sans boucle Python
  par pixel pour les tailles courantes ; le décodage PNG sans filtre évite aussi
  une passe inutile. Les résultats sont vérifiés pixel par pixel dans les tests.
- Les PNG ne sont pas recompressés dans le ZIP ; le reste utilise une compression
  rapide. L'export individuel est préparé dans un dossier isolé et le ZIP
  n'inclut pas les anciennes sorties de l'utilisateur.
- Un cache mémoire **borné à 64 Mio** peut réutiliser les exports glTF/OBJ
  répétés tant que le modèle, les textures et l'inventaire n'ont pas changé. Les
  dates, taille et identité fournies par le système de fichiers participent à la
  clé ; une modification externe conservant toutes ces métadonnées pourrait ne
  pas être détectée. Une carte candidate illisible empêche sa mise en cache ;
  l'absence de candidats peut être mémorisée jusqu'au prochain scan. Vider le
  cache ou rescanner l'invalide.
- Au démarrage, la première réponse d'inventaire ne parcourt plus les caches de
  vignettes pour produire leurs statistiques ; ces chiffres arrivent en arrière-plan.
  Les grandes réponses JSON sont compressées rapidement sur la boucle locale.
- La vue WebGL2 interactive envoie la géométrie décodée complète à la carte
  graphique. Elle est chargée à l'ouverture de la fiche et libérée à sa fermeture ;
  elle est bornée séparément du budget des PNG CPU. Les vignettes sans réduction
  de faces peuvent être plus lentes qu'auparavant. Le FSR 1 expérimental réduit la
  résolution de rendu, pas le nombre de triangles ni le temps de décodage BFRES.
  Ses niveaux (77/67/59/50 % par dimension), la netteté, le MSAA, la densité, la
  taille du canvas et le plafond de rafraîchissement des animations sont réglables.
  Aucun gain de performance n'est garanti : compare avec le mode natif.
- Player cumule les limites de géométrie (128 Mio, 2 millions de sommets,
  4 millions de triangles), de skins (64 Mio) et de textures (16) pour toutes
  ses couches, y compris les ressources temporaires d'un remplacement. La vue
  libère ses ressources GPU quand on la quitte et recharge la composition en y
  revenant. Ce n'est pas le budget mémoire du rendu PNG.

Sur trois modèles réels fournis séparément (2 148, 39 679 et 43 160 triangles),
les indices LOD 0 des exports glTF/OBJ ont été comparés aux maillages décodés.
Les pièces rigides reconnues ont été replacées pour deux modèles sans changer
ces indices. Un parcours Chromium Linux en WebGL2 **émulé par SwiftShader** a
exercé la rotation, le zoom, le fil de fer, les deux passes FSR et les replis ;
une fixture synthétique a aussi vérifié des albédos par matériau et l'orientation
V. Cela ne mesure pas une carte physique ou la fenêtre Tauri, ne vérifie pas de
textures réelles et ne prouve pas la fidélité en jeu.

## Smoke test WebGL synthétique

Sur Linux x86-64 avec Node.js ≥ 22.17 et les bibliothèques système Chromium
(notamment `libnss3` et `libnspr4`), les dépendances isolées et verrouillées dans
`tools/webgl/` permettent d'exercer la géométrie complète, les albédos, les UV,
les passes EASU/RCAS, les quatre niveaux FSR, les limites de résolution et de
mémoire, le fil de fer, les skins indépendants, les attaches et les replis. Deux
autres parcours démarrent le véritable backend sur des projets temporaires
synthétiques : Player, recherche de modèles par animation, tenue animée visible,
suivi de racine, changements rapides, rescans, restauration entre vues et
conservation des originaux ; puis langues et thèmes, animation, lecture et
téléchargements audio/vidéo et remplacements :

```bash
python -m pip install -r requirements.txt
npm ci --prefix tools/webgl --ignore-scripts --no-audit --no-fund
CHROMIUM=$(node --input-type=module -e "import c from './tools/webgl/node_modules/@sparticuz/chromium/build/index.js'; console.log(await c.executablePath())")
HOOKY_CHROMIUM="$CHROMIUM" HOOKY_PLAYWRIGHT="$PWD/tools/webgl/node_modules/playwright-core" node tools/smoke_webgl.js
HOOKY_CHROMIUM="$CHROMIUM" HOOKY_PLAYWRIGHT="$PWD/tools/webgl/node_modules/playwright-core" node tools/smoke_player.js
HOOKY_CHROMIUM="$CHROMIUM" HOOKY_PLAYWRIGHT="$PWD/tools/webgl/node_modules/playwright-core" node tools/smoke_web_media.js
# Facultatif : refaire le parcours média avec un sidecar Linux déjà construit.
HOOKY_DESKTOP_BACKEND="$PWD/src-tauri/binaries/hookycreator-backend-x86_64-unknown-linux-gnu" \
  HOOKY_CHROMIUM="$CHROMIUM" HOOKY_PLAYWRIGHT="$PWD/tools/webgl/node_modules/playwright-core" node tools/smoke_web_media.js
```

Le parcours avec sidecar teste aussi la connexion par jeton, le cookie HttpOnly
et la politique de sécurité HTTP ; il utilise toujours Chromium, pas une fenêtre
Tauri. Ces tests utilisent SwiftShader, pas une carte physique. Avec un autre navigateur
Chromium compatible et `playwright-core`, `HOOKY_CHROMIUM` et `HOOKY_PLAYWRIGHT`
peuvent désigner leurs chemins respectifs ; `HOOKY_PYTHON` peut pointer vers
l'interpréteur de l'environnement virtuel. Player et ses compositions n'ont pas
été validés avec des assets du jeu. Le parcours Tauri natif est distinct et décrit
dans [DESKTOP.md](DESKTOP.md).

## Mesurer chez soi

Depuis la racine du dépôt, après installation de `requirements.txt` :

```bash
python tools/benchmark_workflow.py /chemin/vers/romfs Model/MonModele.bfres.zs
# En utilisant un sidecar autonome déjà construit :
python tools/benchmark_workflow.py /chemin/vers/romfs Model/MonModele.bfres.zs --backend /chemin/vers/hookycreator-backend
```

Le script démarre le backend natif en local, mesure son lancement et sa réponse
d'état, puis le POST de préparation et le téléchargement complet du ZIP. Il
vérifie ensuite le ZIP et les références glTF **hors chronométrage**. Le travail
et le cache applicatif sont temporaires ; le cache du système d'exploitation
n'est pas vidé. Le premier export de chaque modèle n'est pas forcément totalement
froid : les modèles d'une même commande partagent le cache de textures. La fenêtre
Tauri, le rendu de l'interface et la fidélité en jeu ne sont pas mesurés. Le
rapport liste aussi les remarques et le nombre de cartes réellement exportées.

## Exemple de mesures

Dans un conteneur Linux x86-64 (2 vCPU Xeon 2,6 GHz, Python 3.11, sans noyau
Rust, avec `zstandard` et `texture2ddecoder`), quatre BFRES réels ont été exportés
avec le lecteur structuré LOD 0, dans un travail temporaire. Le tableau inclut la
préparation et le téléchargement HTTP des ZIP ; les caches du système étaient
chauds. Les quatre BNTX disponibles dans le dossier étaient **synthétiques** et
ne correspondaient pas aux références des matériaux : aucune carte n'a donc été
reliée ou exportée dans cet essai.

| Échantillon | Triangles LOD 0 | Cartes | Premier export | Répétition |
|---|---:|---:|---:|---:|
| Modèle A | 2 148 | 0 | 0,031 s | 0,006 s |
| Modèle B | 14 070 | 0 | 0,146 s | 0,014 s |
| Modèle C | 39 679 | 0 | 0,469 s | 0,020 s |
| Modèle D | 43 160 | 0 | 0,502 s | 0,022 s |

Les ZIP et les références glTF ont été relus ; les remarques signalent notamment
l'absence de textures, de squelette et d'autres LOD. Ce n'est **pas** une
validation de rendu, de textures réelles ou de fonctionnement en jeu. Le processus
Python de cet essai a répondu à l'état HTTP en 0,119 s pour neuf assets. Sur un
inventaire distinct de 66 700 fichiers **vides synthétiques**, une mesure a donné
1,153 s jusqu'à l'état HTTP décodé, hors fenêtre Tauri et validation des assets.

Un autre passage sur les quatre mêmes modèles avec un sidecar Linux PyInstaller
**expérimental** et le noyau Rust ABI 6 a mesuré 0,572 s jusqu'à l'état HTTP,
puis 0,037 / 0,159 / 0,616 / 0,687 s pour les premiers exports ; les répétitions
ont pris 0,006 / 0,012 / 0,020 / 0,021 s. Un passage précédent avait mesuré
1,649 s jusqu'à l'état HTTP. Ce binaire de test, non distribué, utilisait une
bibliothèque CPython compilée localement et une toolchain Rust 1.88 de provenance
non vérifiée ; il ne représente pas un installateur officiel. Aucun de ces essais
ne mesure l'ouverture ou l'affichage d'une fenêtre Tauri.

Ce sont des résultats locaux sur ces seuls échantillons, pas une garantie de
moins de 3 s pour tous les modèles ni de moins de 5 s pour l'application native
complète. Taille des fichiers, textures réelles, disque, processeur, système et
caches peuvent changer les temps. Le chargement dans un logiciel 3D n'est pas
inclus.
