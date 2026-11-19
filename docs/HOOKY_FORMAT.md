# `.hooky` — contrat d'échange pour projets et mods

Ce document est destiné aux créateurs de mods et aux développeurs de lanceurs. **`.hooky` est le format de HookyMod's**, avec pour objectif de transporter aussi bien des modifications que de nouveaux contenus pour Longshot sur PC. Les identifiants et les versions ci-dessous servent à distinguer les usages : l'extension seule ne suffit pas. Voir aussi la [présentation en anglais pour les développeurs de lanceurs](HOOKY_FORMAT_EN.md).

> **Statut : expérimental, à discuter avec les développeurs des lanceurs avant stabilisation.** Le code source de l'atelier historique et une implémentation de référence de paquet sont conservés dans `modmaker/`, hors de l'application HookyCreator. L'interface historique n'est pas autonome. Les installateurs publics 11.26.2 contiennent encore l'atelier de conception ; la version source le retire. L'outil de paquet peut emballer et vérifier des fichiers déjà préparés, mais ne les génère pas depuis les blocs visuels. Aucun chargement direct `.hooky` par Link's Tool Box ou le lanceur officiel Longshot, ni compatibilité en jeu, n'a été validé ici.

| Identifiant dans `manifest.json` | Version | Usage actuel | Produit par |
| --- | --- | --- | --- |
| `org.hookymod.workspace` | 1 | Projet de conception avec éléments et blocs ; **non jouable** | Ancien atelier HookyMod's (11.26.2) ; code source `modmaker/studio.py` |
| `org.hookymod.mod` | 1 | Proposition d'un paquet contenant des fichiers `romfs` déjà préparés | Outil de référence `modmaker/modpackage.py`, depuis les sources uniquement |

Chaque variante est un ZIP dont le manifeste se trouve à la racine. Lire et vérifier `format`, `version` et `kind` avant de choisir un traitement ; refuser une variante ou une version inconnue, sans supposer qu'il s'agit d'un mod. Le lecteur historique de projet accepte uniquement la première variante ; HookyCreator ne propose plus cet import. Une archive de mod ne devient pas automatiquement un projet éditable, et un projet ne doit pas être installé comme mod.

La spécification et le code de référence de ce dépôt sont soumis à la [licence du projet](../LICENSE), qui limite notamment leur utilisation et distribution commerciales. Les conditions d'une éventuelle collaboration et d'une distribution tierce doivent être vérifiées avec les titulaires de droits. Aucun droit sur Longshot, ses ressources ou les marques tierces n'est accordé.

## 1. Projet de conception existant : `org.hookymod.workspace` v1

### Archive et manifeste

Le lecteur source historique accepte **exactement** deux entrées nommées à la racine :

```text
manifest.json
workspace.json
```

`manifest.json` est un objet JSON UTF-8 contenant exactement ces champs :

```json
{
  "format": "org.hookymod.workspace",
  "version": 1,
  "kind": "design-workspace",
  "playable": false,
  "workspace": "workspace.json",
  "sha256": "02d7b964e692198bee3bfee28599d1cb00fd9c1cca4bb93b013be3f6dc4e3bdd"
}
```

L'empreinte de cet exemple correspond aux **137 octets UTF-8 suivants, sans retour à la ligne final** dans `workspace.json` :

```json
{"author":"","description":"","elements":[],"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"Exemple","revision":0,"schema":1,"target":""}
```

L'ordre et les espaces du JSON n'ont pas de signification sémantique, mais l'empreinte porte sur les octets exacts du fichier : recalculer SHA-256 après toute modification, même d'espacement. `playable` doit être le booléen `false`. Les clés inconnues, répétées ou manquantes sont refusées. Les constantes non JSON telles que `NaN` et `Infinity` sont refusées.

### `workspace.json`

L'objet doit contenir exactement les clés suivantes. Les limites de texte sont comptées en caractères Unicode, pas en octets UTF-8 ; les codes U+0000 à U+001F sont refusés sauf tabulation, retour chariot et saut de ligne, et les substituts Unicode isolés sont refusés.

| Champ | Type et contraintes |
| --- | --- |
| `schema` | Entier `1`. |
| `id` | 32 caractères hexadécimaux minuscules, sans tirets. |
| `name` | Chaîne non vide après retrait des espaces périphériques, 96 caractères maximum. |
| `description`, `author`, `target` | Chaînes présentes, éventuellement vides ; respectivement 1 200, 96 et 160 caractères maximum. `target` est une indication libre, non validée comme version ou compatibilité. |
| `revision` | Entier entre `0` et `999999999`. C'est une révision de sauvegarde, pas une version de mod. |
| `elements` | Liste ordonnée de 0 à 100 éléments. |

Chaque élément contient exactement `id`, `kind`, `name`, `description`, `asset` et `blocks` :

- `id` : 32 caractères hexadécimaux minuscules, unique parmi les éléments du projet.
- `kind` : `item`, `scene`, `dungeon` ou `building`. `name` et `description` suivent les mêmes limites que pour le projet ; le nom doit être non vide.
- `asset` : `null` ou objet `{ "name": "...", "data": "..." }`. `data` est du base64 standard strict des octets du fichier. Le nom est un nom simple non caché, sans séparateur, de 128 caractères maximum et dont l'extension, insensible à la casse, est `.png`, `.obj` ou `.glb`. Le lecteur source effectue seulement un contrôle de l'en-tête PNG/GLB ou de l'encodage UTF-8 de l'OBJ, pas une conversion ni une validation complète du modèle. Une pièce jointe fait de 1 octet à 2 Mio ; leur total est limité à 6 Mio.
- `blocks` : liste ordonnée de 0 à 100 objets contenant exactement `{ "type": "...", "value": "..." }`. La valeur est une chaîne d'au plus 256 caractères, éventuellement vide.

Types de blocs reconnus : événements `when_enter`, `when_interact`, `when_use` ; conditions `if_has_item`, `if_flag` ; actions `show_message`, `give_item`, `open_door`, `play_scene`, `wait`. Ce sont actuellement des **données de conception**, pas une interface de programmation du jeu. Leur interprétation locale est décrite dans le [guide historique archivé](../modmaker/legacy_web/HOOKYMODS-legacy.md#simuler-une-procédure-hors-jeu) et ne vaut pas spécification d'un moteur de jeu.

Le lecteur actuel borne l'archive à 12 Mio, le manifeste à 8 192 octets et `workspace.json` à 10 Mio. Il refuse notamment les fichiers supplémentaires, doublons ZIP, liens symboliques, entrées chiffrées, méthodes de compression autres que STORED et DEFLATE, JSON invalide, champs inconnus et empreintes incorrectes. Il lit en mémoire et n'extrait pas l'archive. L'import crée une copie avec un nouvel `id` de projet et une révision `0` ; les éléments restent ceux de la copie. Un contrôle de conception réussi n'est pas un contrôle de compatibilité du jeu. Le lecteur actuel n'est pas une garantie générale contre toutes les archives malveillantes.

## 2. Proposition de paquet de mod : `org.hookymod.mod` v1

Cette variante est **implémentée expérimentalement par l'outil de référence**, mais n'est pas encore une norme stabilisée entre lanceurs. Elle transporte des **fichiers complets fournis par le créateur**, sans les convertir. Dans un lanceur adapté à une superposition `romfs`, un chemin correspondant à une ressource existante peut servir à la remplacer ; un chemin nouveau peut fournir une ressource supplémentaire **si le jeu sait la référencer et la charger**. Un fichier supplémentaire ne crée pas à lui seul un objet, un placement, une scène ou un comportement.

Aucune opération de suppression, de patch binaire partiel, de fusion structurée, de dépendance ou de script d'installation n'est définie dans cette version. Les conflits et l'ordre entre mods relèvent du lanceur et doivent être montrés à l'utilisateur. La présence d'un fichier n'atteste pas qu'il est sûr, légal, adapté à la version du jeu ou jouable.

### Enveloppe

```text
manifest.json
romfs/Example/hello.txt
... autres fichiers romfs/... déclarés ...
```

- Une entrée `manifest.json` et **de 1 à 256 fichiers** `romfs/...`, chacun déclaré exactement une fois ; aucune autre entrée ZIP et aucune entrée de dossier. Les dossiers parents sont implicites.
- ZIP classique monovolume, sans préfixe ni commentaire d'archive, avec répertoire central classique. Les archives nécessitant ZIP64 sont refusées. Les en-têtes locaux, données, éventuels descripteurs classiques et répertoire central doivent être contigus, sans octets non déclarés. Les en-têtes locaux et descripteurs doivent concorder avec le répertoire central. Limites : 36 Mio d'archive, 512 Kio de répertoire central et 512 Kio de manifeste.
- Fichiers ordinaires seulement, sans chiffrement ; méthodes ZIP STORED ou DEFLATE. Les liens et types spéciaux déclarés par les attributs Unix ainsi que les attributs de répertoire DOS sont refusés ; aucune métadonnée ZIP ne doit être utilisée pour créer un lien. Les noms ASCII peuvent utiliser le drapeau ZIP ordinaire ; les noms non ASCII doivent être encodés en UTF-8 et marqués comme tels dans le ZIP.
- Au maximum 16 Mio de données **décompressées par fichier** et 32 Mio de données décompressées pour l'ensemble des fichiers `romfs`. Un fichier vide est autorisé. Les tailles et limites doivent être contrôlées avant et pendant la décompression, et non uniquement à partir des métadonnées ZIP. Un flux DEFLATE doit se terminer exactement et produire la taille déclarée : refuser une archive qui dissimule des octets supplémentaires après un préfixe annoncé valide.
- Les fichiers sont des données non fiables : une empreinte ne prouve ni leur origine ni leur innocuité. Ne jamais lancer un fichier, une commande, un exécutable ou un lien lors de l'import.

Ces limites sont provisoires et conservatrices pour l'implémentation en mémoire ; elles peuvent exclure des mods volumineux. Elles devront être évaluées avec les développeurs de lanceurs et des exemples autorisés avant stabilisation, éventuellement avec une lecture en flux et une évolution versionnée du contrat.

### Manifeste de mod

Toutes les clés montrées ci-dessous sont obligatoires et aucune autre n'est acceptée en v1, y compris dans `target` et dans chaque entrée de `files`. Le JSON est en UTF-8 sans BOM ; les clés répétées et les constantes non JSON sont refusées. Exemple complet pour un unique fichier `romfs/Example/hello.txt` contenant exactement `Hello Hooky!` suivi d'un octet LF (`0a`) :

```json
{
  "format": "org.hookymod.mod",
  "version": 1,
  "kind": "romfs-overlay",
  "id": "org.hookymod.example",
  "name": "Exemple synthétique",
  "mod_version": "0.1.0",
  "author": "",
  "description": "Démonstration du conteneur uniquement, pas un mod jouable.",
  "target": {
    "runtime": "longshot",
    "platform": "pc",
    "runtime_version": "",
    "game_version": ""
  },
  "files": [
    {
      "path": "romfs/Example/hello.txt",
      "size": 13,
      "sha256": "a6bf8d151c47db988d94987901e0e3f6c1dc20e3f00c5d451db96a790e7da041"
    }
  ]
}
```

| Champ | Règle v1 |
| --- | --- |
| `format`, `version`, `kind` | Respectivement `org.hookymod.mod`, entier `1` et `romfs-overlay`. |
| `id` | Identifiant stable choisi par le créateur : 1 à 64 caractères ASCII minuscules, chiffres, `.`, `_` ou `-` ; premier et dernier caractères alphanumériques. Il identifie le mod, pas un chemin de fichier ni une autorisation de remplacer une installation existante. |
| `name`, `mod_version` | Chaînes non vides après retrait des espaces périphériques, maximum 96 et 64 caractères. `mod_version` est une étiquette libre, pas une comparaison SemVer garantie. |
| `author`, `description` | Chaînes présentes, éventuellement vides, maximum 96 et 1 200 caractères. |
| `target` | Objet exact : `runtime: "longshot"`, `platform: "pc"`, `runtime_version` et `game_version`, ces deux derniers étant des chaînes libres de 96 caractères maximum, vides si non précisées. Elles sont informatives, pas une vérification automatique. Aucune version de lanceur n'est imposée par ce contrat. |
| `files` | Liste de 1 à 256 objets exacts `{path, size, sha256}`. `path` correspond exactement au nom ZIP ; `size` est le nombre entier d'octets décompressés ; `sha256` est l'empreinte de ces octets en 64 caractères hexadécimaux minuscules. |

Les chaînes de métadonnées sont limitées en caractères Unicode. Les codes U+0000 à U+001F sont interdits sauf tabulation, retour chariot et saut de ligne ; les substituts Unicode isolés sont interdits. Les valeurs restent des données non fiables : les échapper dans l'interface et ne pas les utiliser comme commandes ou chemins sans validation supplémentaire. L'ordre de `files` n'a pas de signification ; l'exporteur de référence le trie pour rendre ses résultats reproductibles dans le même environnement. Les empreintes détectent une altération mais ne sont pas des signatures : un auteur malveillant peut recalculer les empreintes.

### Chemins de ressources

Un `path` est un nom relatif ZIP en UTF-8 normalisé NFC, avec `/` comme séparateur, commençant exactement par `romfs/`. Le chemin complet fait au plus 512 octets UTF-8, avec au plus 24 composants **y compris `romfs`** ; chaque composant fait au plus 255 octets UTF-8. Les composants vides, `.`, `..`, les composants commençant par `.`, ceux finissant par un espace ASCII ou un point, les caractères de contrôle Unicode et les caractères `< > : " \ | ? *` sont interdits. Les noms de périphériques Windows, y compris avec une extension ou des espaces ASCII avant cette extension, sont interdits : `CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`, `CLOCK$`, `COM1` à `COM9` et `LPT1` à `LPT9` (y compris les chiffres supérieurs `¹`, `²`, `³`).

Les noms complets doivent être uniques ; les collisions après `casefold` Unicode puis normalisation NFC sont refusées **pour chaque préfixe** : `romfs/Dir/a` et `romfs/dir/b` ne peuvent pas coexister. Un fichier ne peut pas être le dossier parent d'un autre. Ces règles sont volontairement conservatrices pour éviter les divergences entre systèmes de fichiers ; elles ne garantissent pas que le jeu reconnaîtra le chemin. Le lecteur de référence utilise les tables Unicode de son interpréteur Python, qui peuvent varier selon sa version ; il faudra fixer et tester une version commune des tables avant stabilisation, notamment pour les caractères récemment attribués.

### Outil de référence et essai reproductible sans jeu

Depuis la racine du dépôt et Python 3.10 ou plus récent, le module utilise la bibliothèque standard et ne nécessite aucune donnée de jeu. L'ancien nom `python -m hookycreator.modpackage` reste un alias source compatible ; aucun de ces modules n'est inclus dans le backend desktop. L'exemple ci-dessous crée uniquement un fichier texte synthétique : **il ne sera pas jouable**.

```bash
python -c "from pathlib import Path; p=Path('exemple-mod/romfs/Example/hello.txt'); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'Hello Hooky!\n')"
python -m modmaker.modpackage pack --romfs exemple-mod/romfs --output exemple.hooky --id org.hookymod.example --name "Exemple synthétique" --mod-version 0.1.0 --description "Démonstration du conteneur uniquement, pas un mod jouable."
python -m modmaker.modpackage inspect exemple.hooky
python -m modmaker.modpackage unpack exemple.hooky --destination exemple-extrait
```

`inspect` vérifie l'ensemble de l'archive et affiche son manifeste sans extraire ; `unpack` revérifie puis crée `exemple-extrait/romfs/...` dans un **nouveau** dossier. Ni l'un ni l'autre n'installe ou ne lance un mod. `pack` et `unpack` refusent de remplacer une destination existante ; le paquet doit être placé hors du `romfs` source. En cas d'erreur d'écriture, un fichier ou dossier partiel peut rester à inspecter et supprimer manuellement. La collecte refuse les liens symboliques et points de réanalyse et parcourt au maximum 8 192 entrées source. Ces commandes supposent un système de fichiers local de confiance, sans modification concurrente des dossiers pendant l'opération ; elles ne constituent pas un bac à sable contre un autre processus local hostile.

Pour un vrai paquet, remplace le dossier synthétique par un dossier `romfs` de **modifications déjà préparées** : vérifie chaque fichier et tes droits, et ne sélectionne pas le jeu entier par erreur. Les octets sont inclus tels quels et peuvent contenir des données personnelles ou protégées. Le format n'intègre pas automatiquement le manifeste local `hookycreator.json` ni son chemin source, mais les fichiers explicitement choisis peuvent eux-mêmes contenir des informations privées.

Pour un prototype Python, l'API de référence renvoie le manifeste vérifié et un dictionnaire `{chemin: octets}` sans extraire ni installer :

```python
from pathlib import Path
from modmaker.modpackage import MAX_ARCHIVE_BYTES, PackageError, read_package

try:
    with Path("exemple.hooky").open("rb") as source:
        archive = source.read(MAX_ARCHIVE_BYTES + 1)
    manifest, files = read_package(archive)
    # Présenter les métadonnées et les chemins à l'utilisateur, puis demander
    # son accord avant toute opération propre au lanceur.
except (PackageError, OSError) as error:
    print("Paquet refusé :", error)
```

Cette API conserve les octets validés en mémoire ; un lanceur peut aussi écrire son propre lecteur borné dans un autre langage. Elle ne contrôle ni le contenu spécifique au jeu ni l'environnement d'installation.

### Piste d'intégration pour un lanceur

Le lanceur peut implémenter ce contrat lui-même ou examiner l'implémentation de référence et ses tests, sous réserve des conditions de licence :

1. Limiter les octets reçus et le nombre d'entrées **avant** de construire toute la liste ZIP ; contrôler l'enveloppe, les chemins, la version et l'exactitude du manifeste. Refuser les projets `org.hookymod.workspace`, versions inconnues et archives non conformes.
2. Décompresser sous limites, vérifier le CRC ZIP, la taille et SHA-256 de **chaque** fichier avant toute installation. Refuser les doublons, parcours de chemin, liens, entrées spéciales et collisions de casse ; ne pas faire confiance à une extraction ZIP générique aveugle. Ne pas exécuter de données, même si elles ont une extension de programme.
3. Présenter l'identifiant, le nom, la cible, les versions déclarées et les chemins ; signaler l'origine non authentifiée et les conflits éventuels. Une empreinte seule ne permet pas d'attribuer une archive à son auteur. Laisser l'utilisateur choisir explicitement une installation de test et confirmer l'opération.
4. Préparer un nouveau dossier temporaire privé et y créer uniquement des fichiers ordinaires `romfs/...`, sans suivre de liens ni utiliser les permissions fournies par le ZIP. Ne jamais écraser silencieusement un mod existant, le jeu, une sauvegarde ou la table de tailles. Prévoir collision, sauvegarde, publication atomique si possible et retour arrière en cas d'échec ; tenir compte des accès concurrents et des particularités du système de fichiers. L'outil de référence extrait dans un nouveau dossier, mais ne réalise pas cette transaction d'installation.
5. Ajouter ensuite ce dossier par le mécanisme propre au lanceur, en respectant ses règles de priorité et de désactivation. Recalculer ou vérifier les tables de tailles avec les mécanismes adaptés au jeu et au lanceur ; une taille SHA/ZIP n'est pas une taille de ressource en jeu. Afficher les erreurs et ne pas démarrer le jeu sans action explicite.

Au commit public [`a49cd941c3f24a27e2143f12e18f1c89f8b86d2f`](https://github.com/losava/Links-Toolbox/tree/a49cd941c3f24a27e2143f12e18f1c89f8b86d2f), Link's Tool Box documente l'import de **dossiers contenant `romfs`**, l'ordre et l'activation des mods, ainsi qu'une reconstruction de table de tailles lors de l'enregistrement. L'adaptation proposée serait d'ajouter une lecture `.hooky` validée en amont de ce flux existant, pas d'inventer une API présente aujourd'hui. Des différences entre versions et installations restent possibles ; aucun essai avec Longshot n'a été réalisé ici.

## 3. Contenu et comportements jouables : travail futur

Le paquet `romfs-overlay` peut transporter les fichiers d'un mod fait par ailleurs, mais il ne définit pas comment les **fabriquer**. La proposition ci-dessous décrit un chemin de développement, **pas des champs actuellement acceptés dans un manifeste v1, ni une API existante de Longshot**.

### Contrats à définir

- **Projet source futur** : donner à chaque objet, scène, donjon ou bâtiment un identifiant stable et éviter les collisions entre créateurs ; décrire les ressources, leurs formats et leurs droits ; représenter les références et les placements de manière typée. L'actuel `workspace` v1 conserve des idées, mais ne fournit ni registre d'acteurs ni placements jouables. Une représentation enrichie nécessitera une version ou un identifiant de format distinct et une migration explicite.
- **Comportements** : lier chaque procédure à une cible et à un événement réellement disponibles ; utiliser des références d'objets, états, portes et scènes vérifiables plutôt que supposer que du texte libre est un identifiant valide. Définir l'ordre, les conditions, les valeurs manquantes, la durée et l'annulation des attentes, les limites d'exécution et les conflits. Par exemple, une interaction conditionnelle « si un état est actif, afficher un message » ne pourra fonctionner que si la cible expose ou compile effectivement ces deux opérations. La trace du simulateur actuel ne les met pas en œuvre en jeu.
- **Adaptateur de compilation** : analyser les capacités de la cible, résoudre les références, convertir les ressources et générer les fichiers et enregistrements nécessaires ; signaler toute fonction, valeur ou ressource non prise en charge comme erreur. Si la cible requiert une interface d'exécution supplémentaire, la spécifier et la vérifier séparément : v1 ne définit ni chargement de code ni permission implicite d'en exécuter. L'adaptateur pourra ensuite emballer les fichiers produits dans `org.hookymod.mod` v1 s'ils respectent ses limites.
- **Lanceur** : contrôler le conteneur, présenter les métadonnées et conflits, installer après confirmation, puis appliquer ses propres règles d'ordre et de table de tailles. Il ne peut pas déduire de la seule présence d'un fichier ajouté que celui-ci est référencé ou fonctionnel.

### Étapes de validation

1. Documenter les formats, identifiants, références, placements et mécanismes de chargement réellement pris en charge par la cible.
2. Implémenter l'adaptateur et les diagnostics ; une ressource ou un bloc non pris en charge doit produire une erreur explicite, jamais une prétention de réussite.
3. Établir une sémantique de comportements et une interface d'exécution réellement disponibles, avec limites, sécurité et gestion des conflits. Le simulateur actuel n'est ni cette interface ni une preuve d'équivalence.
4. Tester séparément remplacement d'une ressource, ajout réellement référencé, activation/désactivation, ordre entre mods, table de tailles, récupération après erreur, puis parcours en jeu sur installations de test.
5. Convenir de l'évolution versionnée du contrat avec les développeurs de Link's Tool Box et, le moment venu, du lanceur officiel Longshot. Ne pas réinterpréter silencieusement une archive v1 existante.

Les exemples de ce dépôt sont synthétiques ; aucun contenu, dictionnaire, clé ni exécutable du jeu n'est fourni. L'implémentation future et les tests en jeu restent à réaliser.
