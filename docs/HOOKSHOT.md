# Paquets, traduction et dossiers de mod

Cette page décrit les opérations locales de HookyMod sur des fichiers fournis par l'utilisateur. [Longshot](https://git.magicbean.dev/MagicBeanDev/Longshot) est un fork distinct de Hookshot, maintenu par Team MagicBean ; HookyMod n'est affilié à aucun des deux et son intégration à Longshot n'a pas été testée. Les règles d'un chargeur externe peuvent évoluer : consulte sa documentation et vérifie la structure attendue avant d'installer un mod. L'assemblage de fichiers ne garantit pas leur fonctionnement.

## Extraire un paquet NUP

```bash
python -m hookymod.cli nup /chemin/paquet.nup --verify --deep --extract /chemin/dump --only romfs
```

Choisis un **dossier de destination vide**. Une extraction peut remplacer des fichiers existants et laisser une sortie partielle après une erreur ; elle refuse les chemins hors cible et le remplacement du paquet source. `--verify --deep` vérifie les empreintes présentes, mais une empreinte absente ne prouve pas l'intégrité. La lecture NUP a été testée sur des paquets synthétiques, pas sur un paquet réel. Aucun déchiffrement ni aucune clé ne sont fournis. Le lecteur traite uniquement les paquets déjà lisibles fournis par l’utilisateur ; une extraction complète peut également contenir un exécutable. Ne partage ces données que si tu en as le droit.

## Traducteur PNG / OBJ

```bash
python -m hookymod.cli translate unpack --romfs /chemin/dump/romfs -p mon_projet
python -m hookymod.cli translate status -p mon_projet
# Modifier les PNG et les OBJ triangulés dans mon_projet/pc/.
python -m hookymod.cli translate pack -p mon_projet -o mods --mod-name MonMod
```

`pc/` contient les fichiers éditables, `origin/` les originaux servant de gabarits et `manifest.json` leurs correspondances. `pack` ne reconstruit par défaut que les fichiers modifiés ; `--all` recopie aussi les originaux inchangés. Les fichiers reconstruits et les mods assemblés peuvent conserver des données ou contenus dérivés des fichiers d'entrée : vérifie leur contenu et tes droits avant tout partage. Lis les erreurs du rapport : les géométries incompatibles ne sont pas injectées. Les copies d'origine ne sont pas modifiées.

L'injection de géométrie est volontairement limitée à certaines structures BFRES 9.x, à une forme et un LOD, avec des tampons de capacité fixe et des attributs réécrivables. Les BFRES 10.x, squelettes, morphs, bornes à réécrire et autres structures non vérifiées sont refusés. Les matériaux et textures du fichier cible restent en place. Voir [HOOKYCREATOR.md](HOOKYCREATOR.md) pour les limites et l'import glTF/GLB de l'atelier.

PNG → BNTX conserve le format et la disposition de la cible lorsque l'encodeur les prend en charge. BC7 et ASTC ne sont pas encodables. Une texture, un modèle ou une table reconstruits nécessitent toujours une validation dans l'environnement cible.

## Table des tailles et assemblage

Certains environnements utilisent une table RESTBL pour réserver la taille des ressources. Un fichier plus gros que la réservation peut nécessiter une entrée mise à jour :

```bash
python -m hookymod.cli restbl /chemin/dump/romfs --get Model/MonModele.bfres
python -m hookymod.cli restbl /chemin/dump/romfs --ensure Model/MonModele.bfres=8388608 --output nouvelle.rsizetable
```

Le traducteur tente de mettre à jour la table lors de `pack`, sauf avec `--no-table` ; consulte son rapport. L'assemblage de mod dans HookyCreator ne met pas RESTBL à jour automatiquement. Il supprime et recrée `mods/<nom>` sans sauvegarde ; l'interface demande confirmation. Les fiches de mobs ne créent pas d'acteurs jouables.

## Attribution

Hookshot de NotRexed est un projet distinct distribué sous GPL-3.0 ; ses notes publiques sur les paquets et tables ont été consultées comme références d'interopérabilité. Son [adresse GitHub historique](https://github.com/NotRexed/Hookshot) peut ne plus être accessible. Aucun code de Hookshot ni de Longshot n'a été intentionnellement repris durant ce travail ; la provenance de l'ensemble du code reste à vérifier. Les autres sources et licences tierces sont indiquées dans [PROVENANCE.md](PROVENANCE.md).
