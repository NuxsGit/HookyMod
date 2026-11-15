# HookyMod · HookyCreator

HookyMod est une boîte à outils locale d'inspection et de modification de ressources. HookyCreator est son atelier visuel : il explore des modèles et textures fournis par l'utilisateur, prépare des exports standards et assemble des copies modifiées. L'explorateur historique Python/Tk et la ligne de commande restent disponibles.

**Apporte tes propres fichiers obtenus légalement.** Aucun dump ni asset propriétaire n'est nécessaire à la suite de tests. La lecture des formats est partielle et un fichier exporté ou réinjecté n'est pas une garantie de fidélité ni de fonctionnement en jeu.

## Démarrage rapide

Python 3.10 ou plus récent est requis. Installer les dépendances depuis le dépôt :

```bash
python -m pip install -r requirements.txt
python -m hookycreator /chemin/vers/romfs --host 127.0.0.1 --open
```

L'atelier s'ouvre à `http://127.0.0.1:8765/`. `--host 127.0.0.1` limite l'écoute à la machine locale ; n'expose pas le serveur navigateur à un réseau non fiable. Le projet peut aussi être un dossier de ressources extraites. Pour changer l'emplacement des copies et caches, ajouter `--work /chemin/de/travail`.

Pour l'explorateur historique, installer Tkinter puis lancer `python AssetViewer.py`. La fenêtre native Tauri avec sidecar autonome est décrite dans [docs/DESKTOP.md](docs/DESKTOP.md).

## Modèles : export et retour

1. Dans **Modification → Modèles**, ouvre une fiche puis **Exporter en glTF / OBJ**. L'export contient un glTF 2.0 (`.gltf` + `.bin`), un OBJ et son MTL, les PNG décodables disponibles, une notice et un rapport. Le panneau indique le dossier absolu et propose un ZIP qui ne contient que les fichiers de cet export. Relancer l'export remplace les fichiers de même nom dans le dossier associé : sauvegarde tes modifications ailleurs.
2. Les références de textures explicites reconnues dans les matériaux sont résolues par matériau. Si elles manquent, l'association par nom est seulement heuristique et signalée ; les doublons et références absentes ne sont pas raccordés arbitrairement. `TEXTURES.md` et `RAPPORT_MODELE.txt` détaillent les limites. Squelette, pose, animations, morphs, shaders et autres LOD ne sont pas reconstruits. Vérifie aussi les UV et le rendu dans ton logiciel 3D.
3. Après édition, utilise **Importer un modèle** : sélectionne un OBJ triangulé, un GLB ou un glTF 2.0 avec ses éventuels `.bin`, analyse-le et choisis explicitement une cible compatible. L'import porte sur la **géométrie seulement** : les matériaux et textures de la cible sont conservés, pas ceux de la source.
4. L'injection réécrit une copie dans `hookycreator_work/assets/`, jamais le fichier du projet d'origine. Une seule forme et un seul LOD sont actuellement acceptés dans certaines structures BFRES 9.x ; les tampons sont de capacité fixe. Les cibles 10.x, les squelettes, morphs, bornes à réécrire, attributs non gérés et autres structures non vérifiées sont refusés. Il est normal de ne trouver aucune cible compatible. Les contrôles et la relecture ne prouvent pas la sûreté en jeu.

Les UV et normales fournies doivent pouvoir être conservées par la cible. Les normales absentes ne sont pas inventées dans l'export ; un aperçu peut néanmoins calculer ses propres normales de face. Les polygones OBJ doivent être triangulés avant injection. Les extensions glTF obligatoires, les tampons comprimés, les déformations, les accessors sparse et les accès réseau ne sont pas pris en charge.

Voir le [guide de l'atelier](docs/HOOKYCREATOR.md) pour le détail des limites, des cartes PNG et des opérations de lots.

## Autres fonctions

- Textures BNTX : aperçu, export PNG, remplacement dans une copie pour les formats encodables. ASTC/BC7 ne sont pas encodables ; certains formats de lecture nécessitent `texture2ddecoder`.
- Archives SARC et `.zs` : inspection, extraction et reconstruction, avec recherche des dictionnaires zstd fournis par l'utilisateur. Des données incomplètes ou un dictionnaire manquant sont signalés.
- Sons : conversion expérimentale en lecture seule de certains BWAV Opus mono vers Ogg Opus.
- Session : navigation, filtres, sélection, inspecteur et brouillons sont enregistrés par projet. Les copies modifiées et les réglages sont distincts de la session. Une fermeture brutale ne garantit pas la dernière sauvegarde ; voir [performances et reprise](docs/PERFORMANCE.md).
- Création de mod : assemblage des copies modifiées et fiches préparatoires. **Assembler supprime et recrée `mods/<nom>` sans sauvegarde** ; la fiche ne crée pas d'acteur jouable et l'atelier ne met pas automatiquement à jour RESTBL.

## Ligne de commande

```bash
# Diagnostic et inspection
python -m hookymod.cli doctor /chemin/vers/romfs
python -m hookymod.cli scan /chemin/vers/romfs
python -m hookymod.cli list archive.pack.zs --recursive

# Export standard ; --formats gltf ou --formats obj pour un seul format
python -m hookymod.cli export-model modele.bfres -o modele_export
python -m hookymod.cli export-model modele.bfres -o modele_export --no-textures

# Projet éditable PNG / OBJ et reconstruction prudente
python -m hookymod.cli translate unpack --romfs /chemin/vers/romfs -p projet
python -m hookymod.cli translate status -p projet
python -m hookymod.cli translate pack -p projet -o mods --mod-name MonMod

# Export par lots, avec rapport d'erreurs et de limites
python -m hookycreator export-all /chemin/vers/romfs -o export_all
```

La CLI `export-model` recherche les textures voisines par nom et ne relie qu'un albédo unique à un matériau unique ; l'association reste heuristique. Les BNTX multi-images et les noms de sortie ambigus sont ignorés et signalés. Pour un OBJ en entrée, elle conserve les indices UV et normales par coin, mais ne lit pas les bibliothèques MTL ni les textures de l'OBJ source. Pour les BFRES 10.x préconstruits, une demande de LOD autre que 0 est refusée. Choisis un dossier de sortie dédié : la CLI peut remplacer ses fichiers homonymes. Dans l'explorateur Tk, l'export glTF/OBJ ne relie pas automatiquement les textures. Le traducteur n'écrit que les géométries compatibles et rapporte les refus. Les noms de commandes et options disponibles peuvent être vérifiés avec `--help`.

Le scan est limité par défaut à 200 000 fichiers reconnus. Un scan incomplet ou des entrées illisibles sont signalés ; `--scan-limit 0` retire ce plafond, au prix d'une consommation mémoire potentiellement supérieure.

## Tests et construction

```bash
python -m pip install -r requirements.txt pytest pytest-subtests pillow
python -m pytest -q
# Tests d'interface JavaScript, sans dépendance npm supplémentaire
for f in tests/*_test.js; do node "$f" || break; done
```

Les tests de formats utilisent des fixtures synthétiques. L'interopérabilité avec Blender est testée en option quand il est installé, uniquement comme consommateur de glTF/OBJ. Des tests natifs supplémentaires et les prérequis Windows/Linux sont décrits dans [docs/DESKTOP.md](docs/DESKTOP.md). Le noyau Rust accélère certains traitements, mais ne remplace pas encore l'application Python ; voir [docs/RUST.md](docs/RUST.md).

## Documentation et provenance

- [Atelier, formats et limites](docs/HOOKYCREATOR.md)
- [Fenêtre native et construction](docs/DESKTOP.md)
- [Reprise et performances](docs/PERFORMANCE.md)
- [Inspection des ressources et dictionnaires](docs/OUVRIR_LES_ASSETS.md)
- [Provenance, références et dépendances tierces](docs/PROVENANCE.md)

Le dépôt ne fournit ni jeu, ni clés, ni ressources propriétaires. Il appartient à chaque utilisateur de vérifier ses droits et les licences applicables. Les composants tiers conservent leurs attributions et leurs licences respectives.

## Soutenir le projet

Adresse Solana : `y8eeEqNTo7pANwRVvwsrNDu9gpPTFYW5FrP2oMUtfhZ`.
