# Provenance, références et licences tierces

## Ressources utilisateur

HookyMod est destiné à fonctionner sur des fichiers fournis par l'utilisateur. Les dumps, modèles, textures, sons, dictionnaires et autres données propriétaires utilisés pour des essais locaux doivent rester hors du dépôt ; `.gitignore` couvre leurs principales extensions, mais n'enlève pas un fichier déjà suivi par Git. Les tests automatisés fabriquent leurs propres fixtures synthétiques. Aucun fichier de jeu n'est nécessaire pour les exécuter.

Les essais sur quelques fichiers réels ont été effectués à partir de données externes fournies séparément, sans les intégrer au dépôt. Ils ne constituent ni une validation générale des formats ni un audit juridique des droits sur ces données. Chaque utilisateur doit vérifier ses propres droits.

## Code et références de formats

Les lecteurs et écrivains du projet ont été développés pour HookyMod. Aucun SDK officiel ni code décompilé n'est intentionnellement inclus. Les structures publiques suivantes ont notamment été consultées pour la compréhension et la comparaison des formats :

- [OctoSquiddy/Switch-2-Toolbox](https://github.com/OctoSquiddy/Switch-2-Toolbox), notamment les structures BFRES FMDL, FSHP, Mesh, SubMesh et FMAT. Ce projet est sous GPL-3.0 ; ses structures ont servi de référence, aucun de ses fichiers source n'est incorporé ici.
- Hookshot de NotRexed, dont les notes sur les paquets NUP et les tables de tailles ont été consultées ; son [adresse GitHub historique](https://github.com/NotRexed/Hookshot) peut ne plus être accessible. [Longshot](https://git.magicbean.dev/MagicBeanDev/Longshot), maintenu par Team MagicBean, est un fork distinct de Hookshot sous GPL-3.0-or-later. HookyMod n'incorpore le code d'aucun de ces projets ; la compatibilité de ses mods avec Longshot n'a pas été vérifiée.
- [Spécification BWAV](https://gota7.github.io/Citric-Composer/specs/binaryWav.html), [RFC Opus](https://www.rfc-editor.org/rfc/rfc6716), [RFC Ogg Opus](https://www.rfc-editor.org/rfc/rfc7845) et comportement documenté de [vgmstream](https://github.com/vgmstream/vgmstream/blob/master/src/meta/bwav.c) pour la sentinelle de boucle. Le lecteur et le remultiplexeur audio de ce dépôt sont propres au projet ; aucun code vgmstream n'y est inclus.
- [lunixxx37/NupExplorer](https://github.com/lunixxx37/NupExplorer), commit `6d6d356e4c108dfb754ce4747f378d0d8a582b56` (MIT, Copyright 2026 lunixxx37), a servi de référence pour les dispositions FSKA 10.x, les courbes Hermite, le squelette et les poids ; l'implémentation des courbes a été comparée aux sorties de son lecteur. La notice MIT complète est conservée dans [`licenses/NUP_EXPLORER_MIT.txt`](licenses/NUP_EXPLORER_MIT.txt). Ces comparaisons ne valident pas le rendu en jeu.
- [BfAudioSharp](https://github.com/BladesawStudios/BfAudioSharp/blob/main/src/BfAudioSharp/Bwav.cs) décrit le CRC sur les données des canaux sans padding et l'alignement BWAV. Sa licence est AGPL-3.0 ; seul le format décrit a été consulté, aucun code source de ce projet n'est incorporé ici.
- Les passes FSR 1 WebGL2 EASU et RCAS incluses sous [`hookycreator/web/shaders/`](../hookycreator/web/shaders/) proviennent de [skateddu/sharpview-fsr-video-upscaler](https://github.com/skateddu/sharpview-fsr-video-upscaler/tree/e5e7b56755d123c076ce1c6eb0605721f0027c48), commit `e5e7b56755d123c076ce1c6eb0605721f0027c48` (MIT, Andrea Cadeddu), port GLSL ES 3.0 du code AMD FidelityFX Super Resolution 1 (MIT, AMD). Les deux notices intégrales sont conservées dans [`LICENSE-FSR1.txt`](../hookycreator/web/shaders/LICENSE-FSR1.txt).
- Les autres références précédemment documentées, dont les outils d'inspection, sont listées dans [OUVRIR_LES_ASSETS.md](OUVRIR_LES_ASSETS.md). Les noms et liens des projets tiers sont conservés pour l'attribution.

Ces descriptions de provenance ne remplacent pas un audit complet de la chaîne de dépendances ou des licences.

## Dépendances et licence du projet

Les dépendances Python génériques comprennent notamment `zstandard`, `texture2ddecoder`, `soundfile` (avec libsndfile, NumPy et CFFI) et `Pillow` ; la construction desktop utilise Tauri et PyInstaller. Les versions et dépendances sont définies dans les fichiers de requirements, de verrouillage npm et Cargo. Les crates vendorizées du noyau Rust, leurs auteurs et leurs licences MIT ou ISC sont documentés dans [rust/vendor/README.md](../rust/vendor/README.md) et leurs fichiers de licence restent présents. Tauri, ses dépendances et les autres bibliothèques conservent leurs propres licences.

Le code, la documentation et les ressources propres au projet que leurs ayants droit proposent sous ces conditions sont couverts par la [licence non commerciale HookyMod](../LICENSE). Elle interdit la vente et l'utilisation ou la distribution commerciale, y compris des versions modifiées, et permet les dons volontaires sans contrepartie. C'est une licence **source disponible**, non une licence open source au sens de l'OSI. Les deux manifests du projet pointent vers ce fichier ; les licences des composants tiers ne sont pas modifiées. L'index des notices connues est dans [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Les anciens manifests ont déclaré MIT : les droits éventuellement déjà accordés sous cette licence ne sont pas annulés par ce nouveau fichier. Avant publication, les titulaires des droits doivent confirmer qu'ils peuvent appliquer les nouvelles conditions aux contributions et aux ressources concernées, y compris au logo fourni. La provenance de l'ensemble du code et des ressources reste à examiner.

Les exécutables autonomes peuvent aussi incorporer le runtime Python, les dépendances transitives de Tauri, les bibliothèques natives des wheels et des éléments de PyInstaller. L'index ne constitue pas un inventaire complet de leurs licences et obligations ; chaque paquet binaire doit être audité pour sa plateforme avant diffusion.
