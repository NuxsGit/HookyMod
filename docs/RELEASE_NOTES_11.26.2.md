# HookyMod 11.26.2

Cette version publique rassemble les évolutions de HookyCreator et de HookyMod's. Certaines fonctions restent **expérimentales** ; conserve des sauvegardes et vérifie les résultats avant de les utiliser ou de les partager.

## Nouveautés

- **Player** : aperçu temporaire d'un personnage avec jusqu'à cinq emplacements de tenues ou d'objets, rattachement manuel à un os, réglages locaux et lecture de clips compatibles. La caméra peut suivre une racine unique. La composition reste dans la session ouverte, mais n'est ni sauvegardée après rechargement ni exportée.
- **Ensembles** : suggestions de modèles et de tenues associées à une animation à partir des noms ; choix manuel possible. Ce sont des indices, pas une preuve de compatibilité ni une liste exhaustive.
- **FSR 1 réglable** pour l'aperçu WebGL2, avec qualité, netteté et limites de résolution et de fréquence d'animation. Ce n'est pas FSR 4 ; cela ne réduit pas le nombre de triangles et ne modifie ni PNG ni exports. Le gain dépend du matériel et peut être négatif.
- **HookyMod's** : atelier de conception de projets `.hooky` non jouables. L'outil distinct de paquet de mod reste expérimental et utilisable depuis les sources ; l'intégration aux lanceurs et la compatibilité en jeu ne sont pas validées.
- Exports de modèle et d'animation améliorés : GLB autonome avec un clip, ou GLB avec plusieurs clips détectés et rapport des omissions ; les exports OBJ restent statiques.

## Corrections

- Après une perte de contexte WebGL, les commandes Player sont désactivées et **Réessayer** recharge la composition sélectionnée.
- Le serveur navigateur écoute désormais sur `127.0.0.1` par défaut. Il refuse les requêtes inter-origines et les actions d'écriture en GET ; celles-ci nécessitent un POST JSON. **Changement pour les scripts utilisant l'ancienne API.** Une écoute non locale configurée avec `--host` reste sans authentification : ne pas l'exposer à un réseau non fiable.
- Versions affichées par les outils harmonisées, dimensions CLI mal formées refusées et indication Blender corrigée pour l'export GLB seul.

## Installateurs et limites

- Linux x86-64 : paquet `.deb` construit sur Ubuntu 24.04 ; les autres distributions et les anciennes versions de glibc ne sont pas validées.
- Windows x86-64 : installateur NSIS ; WebView2 est nécessaire.
- macOS : DMG distincts Apple Silicon et Intel. La fenêtre macOS n'a pas été testée interactivement dans la CI.

Les installateurs **ne sont pas signés** et les DMG **ne sont pas notariés** ; SmartScreen ou Gatekeeper peuvent les avertir ou les bloquer. Vérifie les téléchargements avec `SHA256SUMS.txt`.

Les tests automatisés utilisent uniquement des données synthétiques. Les modèles, équipements et animations réels, le rendu sur GPU physique et la compatibilité en jeu n'ont pas été validés. Les animations FSKA Euler et l'association des os par nom restent partielles ; les variantes non prises en charge peuvent être refusées. Les personnages peuvent nécessiter des ajustements de proportions, d'attaches, de matériaux ou de masques. L'assemblage d'un mod supprime et recrée `mods/<nom>` sans sauvegarde.

Aucun jeu, clé, dictionnaire ou asset utilisateur n'est fourni intentionnellement. Utilise uniquement des fichiers obtenus légalement et vérifie tes droits avant de partager les sorties. Les droits du logo conservé n'ont pas été établis et l'inventaire des licences et obligations des dépendances embarquées n'est pas complet : consulte `LICENSE`, `THIRD_PARTY_NOTICES.md` et `docs/PROVENANCE.md` avant toute redistribution.
