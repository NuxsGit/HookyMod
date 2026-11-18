# HookyMod's — atelier de conception

HookyMod's est un espace **distinct de HookyCreator** : il sert à imaginer du nouveau contenu plutôt qu'à examiner ou modifier les ressources existantes. Cette première version est intégrée à la même application et au même serveur ; ce n'est pas encore un exécutable séparé.

> **Prototype de conception, pas encore un générateur de mods jouables.** Les objets, cinématiques, donjons et bâtiments sont des éléments descriptifs. Les blocs constituent une maquette ordonnée que seul un simulateur local limité interprète ; ils ne génèrent pas de logique ni de fichiers pour le jeu. Aucun lanceur n'est intégré ou validé. La vérification porte uniquement sur le schéma du projet, pas sur la compatibilité en jeu. Un outil expérimental séparé peut toutefois emballer des fichiers de mod déjà préparés, comme expliqué plus bas.

## Commencer sans fichiers de jeu

Depuis le code source, installe les dépendances indiquées dans le [guide HookyCreator](HOOKYCREATOR.md), puis ouvre un dossier vide ou un dossier de travail de ton choix :

```bash
mkdir mon-atelier
python -m hookycreator mon-atelier --host 127.0.0.1 --open
```

Dans la fenêtre native, choisis également un dossier local. Ouvre **HookyMod's** depuis l'accueil ou le rail. Il n'est pas nécessaire de posséder ou de fournir des fichiers de jeu pour utiliser cet atelier. Le serveur navigateur est destiné à la machine locale : ne l'expose pas à un réseau non fiable.

## Créer, sauvegarder, partager

1. Donne un nom à un nouvel espace de travail, puis clique sur **Créer un projet**. Le panneau de gauche permet de rouvrir les projets du même dossier de travail. Le nom, la description, l'auteur et la cible envisagée sont de simples informations : la cible n'est pas vérifiée.
2. Ajoute une idée parmi **Objet**, **Cinématique**, **Donjon** ou **Bâtiment** et renseigne son nom et sa description. Tu peux associer un fichier personnel PNG, OBJ ou GLB par élément, ou le retirer. Seuls l'en-tête et les dimensions d'un PNG, l'en-tête d'un GLB 2.0 ou l'encodage UTF-8 d'un OBJ sont contrôlés : ces contrôles ne prouvent pas que le fichier est complet ni compatible avec un jeu. La ressource n'est pas exécutée, rendue ou convertie.
3. Dans la palette, clique sur un bloc ou glisse-le dans la zone de procédure. Les flèches permettent de changer l'ordre au clavier ; le bouton × retire un bloc. Remplis les valeurs souhaitées et utilise **Simuler** pour essayer la procédure hors jeu. **Vérifier** signale notamment une procédure qui ne commence pas par un événement ou un bloc non événementiel sans valeur. Même sans avertissement, la procédure ne produit pas de logique en jeu.
4. Clique sur **Enregistrer**. L'état indique les modifications non enregistrées. **Exporter .hooky** enregistre d'abord le projet, puis propose l'archive à télécharger ; le lien reste disponible pour réessayer. L'import d'un `.hooky` crée toujours un nouveau projet et ne remplace pas celui d'origine. **Dupliquer** crée aussi une copie indépendante, y compris à partir d'un brouillon.

Les projets sont des fichiers JSON sous `<dossier de travail>/hookymods/`, avec des noms internes aléatoires. Par défaut, le dossier de travail est `<dossier choisi>/hookycreator_work/` ; l'option `--work` permet de le placer ailleurs. Les modifications de HookyCreator et l'assemblage historique dans `mods/` sont séparés : ni l'un ni l'autre n'est ajouté automatiquement au projet `.hooky`. Les projets illisibles sont signalés dans la bibliothèque sans être remplacés.

Enregistre régulièrement. La fenêtre native tente de sauvegarder un brouillon avant sa fermeture ou le changement de dossier et avertit si elle n'y arrive pas. Le navigateur avertit avant de quitter avec des changements non enregistrés ; une interruption brutale peut néanmoins les perdre. Après un redémarrage, rouvre le projet dans la bibliothèque. Passer à un autre projet ou **Recharger** demande confirmation avant d'abandonner un brouillon ; la suppression d'un projet est définitive après confirmation.

Une révision empêche un client périmé de remplacer silencieusement une sauvegarde plus récente **dans un même processus serveur**. En cas de conflit, utilise **Dupliquer** pour conserver ton brouillon sous une nouvelle identité, ou **Recharger** pour abandonner le brouillon et reprendre la dernière sauvegarde. N'utilise pas plusieurs processus serveurs simultanément sur le même dossier de travail : il n'y a pas de verrouillage interprocessus.

Limites actuelles : 100 projets par dossier de travail, 100 éléments par projet, 100 blocs par élément, 2 Mio par fichier joint et 6 Mio de fichiers joints par projet. Le JSON du projet est limité à 10 Mio et l'archive `.hooky` importée ou exportée à 12 Mio. Une interruption ou un disque plein peut empêcher la sauvegarde ; garde des copies de tes archives importantes.

## Simuler une procédure hors jeu

Pour l'élément sélectionné, choisis un événement et, si nécessaire, indique des noms d'objets et d'états initiaux, un par ligne. Clique sur **Simuler** : une trace indique les événements, conditions et actions retenus ou ignorés. Les valeurs initiales et le résultat sont temporaires ; la simulation ne change pas le projet enregistré, ne modifie aucun fichier et ne lance aucun programme.

Les règles du simulateur sont volontairement simples et ne décrivent pas nécessairement celles d'un jeu :

- Chaque bloc d'événement commence un groupe. Les groupes correspondant à l'événement choisi sont parcourus une fois, dans l'ordre ; les autres sont ignorés. La précision facultative d'un événement n'est pas interprétée.
- Une condition vérifie le nom d'objet dans l'inventaire simulé ou l'état dans la liste initiale. Plusieurs conditions se cumulent avec **ET** et filtrent tous les blocs suivants jusqu'au prochain événement. Il n'y a ni « sinon », ni boucles, ni branches imbriquées. Une condition active sans valeur est signalée et bloque la suite du groupe.
- **Donner un objet** ajoute un nom unique à l'inventaire simulé, qui peut ensuite être testé par une condition. **Ouvrir une porte** et **Jouer une scène** consignent des noms ; **Afficher un message** et **Attendre** figurent seulement dans la trace. L'attente ne temporise pas et aucun de ces blocs n'agit sur un jeu.
- Les espaces au début et à la fin sont ignorés ; les comparaisons tiennent compte des majuscules. Les actions actives sans valeur sont signalées et ignorées. Chaque liste initiale accepte jusqu'à 100 valeurs de 256 caractères au maximum ; les doublons sont regroupés.

Par exemple, avec les blocs « Quand on interagit », « Si un état est actif : nuit », « Afficher un message : Bonjour », le message apparaît dans la trace seulement si `nuit` figure parmi les états actifs. Cette simulation ne valide ni le fonctionnement en jeu ni la compatibilité avec un chargeur de mods.

## Variante de projet `.hooky` v1

Le format `.hooky` appartient à HookyMod's ; une variante expérimentale pour transporter des fichiers de mod est décrite plus bas. Aujourd'hui, le **projet de conception exporté par l'atelier** est un ZIP contenant exactement deux fichiers à sa racine. Il n'y a pas encore d'association système pour l'ouvrir par double-clic : utilise **Importer .hooky** dans l'atelier.

- `manifest.json` : identifiant de format `org.hookymod.workspace`, version `1`, type `design-workspace`, indicateur `playable: false`, nom `workspace.json` et empreinte SHA-256 de ses octets ;
- `workspace.json` : schéma `1`, identifiant du projet, nom, description, auteur, cible, révision et liste ordonnée des éléments. Chaque élément conserve son type, son nom, sa description, ses blocs ordonnés et éventuellement le nom et les octets de sa ressource en base64.

L'empreinte détecte certaines altérations accidentelles ; elle n'authentifie pas l'auteur et n'est pas une signature. L'import refuse les versions ou champs inconnus, les entrées ZIP inattendues ou en double, les liens symboliques, données malformées et tailles excessives. Les fichiers ne sont pas extraits sur le disque et aucun contenu de l'archive n'est exécuté. Les vérifications d'en-tête ne constituent pas une analyse complète des ressources jointes.

Le texte et les ressources que tu ajoutes sont inclus tels quels, y compris d'éventuelles informations personnelles ou données protégées que tu aurais saisies ou jointes. Aucun fichier de jeu ni chemin local n'est ajouté automatiquement ; vérifie néanmoins le contenu et tes droits avant de partager l'archive. L'extension `.hooky` et le dossier `hookymods/` sont ignorés par Git par précaution, mais `.gitignore` n'enlève pas les fichiers déjà suivis et ne remplace pas une vérification manuelle.

## Variante expérimentale pour des fichiers de mod

Depuis le code source, `python -m hookycreator.modpackage` propose `pack`, `inspect` et `unpack` pour emballer des **fichiers `romfs` déjà créés**, vérifier leur intégrité et les extraire dans un nouveau dossier. Il ne transforme pas les blocs ni les ressources de l'atelier et ne vérifie pas leur compatibilité avec le jeu. La fenêtre de l'atelier n'exporte ni n'importe cette variante, et les installateurs bêta publiés ne l'incluent pas.

Le [contrat `.hooky` pour les développeurs de lanceurs](HOOKY_FORMAT.md), aussi disponible [en anglais](HOOKY_FORMAT_EN.md), détaille les deux identifiants, le manifeste, les chemins, les limites, des commandes reproductibles avec un exemple synthétique et une proposition d'intégration sûre. Le contrat de paquet est expérimental et reste à discuter avec les développeurs des lanceurs ; aucun import direct ni résultat en jeu n'est validé. Le fait d'emballer des fichiers ne les rend pas jouables.

## Longshot et Link's Tool Box : état de l'intégration

La cible souhaitée est **Longshot sur PC**, avec [losava/Links-Toolbox](https://github.com/losava/Links-Toolbox), puis le lanceur officiel Longshot. Aucune version particulière de ces lanceurs n'est imposée par le format proposé. Le README et les mécanismes de chargement de Link's Tool Box ont été consultés, mais aucun essai dans Longshot ou Link's Tool Box n'a été réalisé avec un paquet créé ici.

Link's Tool Box décrit des mods sous forme de **dossiers contenant `romfs`**. Il peut copier un dossier de mod, activer et réordonner les mods, reconstruire une table de tailles et lancer Longshot par son bouton **Save and play**. Il ne documente pas de prise en charge de `.hooky` ni d'interface permettant à HookyMod's de charger directement un tel projet. Un dossier qui contient `romfs` n'est pas pour autant un mod valide : ses fichiers, chemins et comportements doivent correspondre à la version du jeu ciblée.

**L'atelier ne convertit pas encore les projets `.hooky` en mods Longshot, n'installe rien dans le jeu et ne lance pas Link's Tool Box.** L'assemblage historique de HookyCreator produit lui aussi un dossier `romfs`, mais il collecte seulement des copies modifiées et fiches préparatoires ; il ne transforme pas les éléments ni les blocs HookyMod's en contenu jouable. Construire cet assemblage supprime et recrée `mods/<nom>` sans sauvegarde : protège le dossier avant tout essai. Son manifeste local peut contenir le chemin source ; vérifie-le avant de partager.

### Essai manuel d'un mod déjà compatible

Ces indications concernent uniquement un mod Longshot **déjà créé et vérifié séparément**, pas un projet `.hooky` de conception. Un dossier extrait d'un paquet de mod expérimental contenant de tels fichiers présente la forme `romfs` attendue, mais ce parcours n'a pas été testé ici :

1. Ferme Longshot et sauvegarde tes mods et sauvegardes ; travaille sur une installation de test si possible. Link's Tool Box peut copier et renommer des dossiers de mods et reconstruire la table de tailles.
2. Dans Link's Tool Box, vérifie dans **Folders** que le dossier du jeu et le dossier de mods correspondent à la version de Longshot visée, puis applique les réglages et rouvre l'outil si nécessaire.
3. Dans **Mods**, utilise **Add mods...** pour choisir le dossier du mod contenant `romfs`. Lis les confirmations de copie ou de remplacement, vérifie l'ordre et les éventuels conflits, puis utilise **Save** ou **Save and play**. Ces opérations modifient l'installation sélectionnée ; elles ne constituent pas une validation par HookyMod's.

Pour transformer les blocs en contenu jouable, il faudra documenter les mécanismes réellement pris en charge, préparer des exemples **synthétiques**, développer un adaptateur produisant les ressources, placements et comportements attendus, puis tester dans l'environnement cible. L'adaptation des lanceurs au paquet proposé est un travail distinct. Une éventuelle action de lancement devra être explicite : une archive importée ne doit jamais lancer une commande ni choisir un exécutable à sa place. Aucun code de Link's Tool Box ni de Longshot n'est intégré par cet atelier.

## Documents utiles dans Google Drive

Si tu souhaites préparer la suite, tu peux placer dans le dossier `Hooky` des sous-dossiers tels que `Documentation`, `Exemples_synthetiques` et `Compatibilite` contenant :

- des liens vers la documentation publique des formats et du chargeur, ou des notes que tu es autorisé à partager ;
- une arborescence **texte** du dossier de mod attendu et un exemple minimal créé spécialement pour les tests, sans données de jeu ;
- des captures de l'interface et des messages d'erreur après avoir masqué les chemins personnels et informations privées ;
- tes propres modèles et textures simples, si tu souhaites autoriser leur utilisation comme fixtures de test, avec les conditions d'utilisation indiquées.

Ne fournis pas de clés, de jeu complet, d'exécutable du jeu ni d'autres données dont tu ne peux pas autoriser le partage. Les exemples actuels des tests sont synthétiques et ne nécessitent aucune de ces données.

## Inspiration et indépendance

Le parcours d'espaces de travail et d'édition visuelle s'inspire de l'idée générale d'outils comme MCreator. L'éditeur de blocs présent ici est une implémentation propre et limitée ; il ne contient ni MCreator ni Blockly et ne reprend pas leur code. Leurs licences et marques restent celles de leurs titulaires. Toute future dépendance devra faire l'objet d'une vérification et des notices appropriées.
