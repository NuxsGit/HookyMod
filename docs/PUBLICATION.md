# Préparation d'une publication

Cette liste est une aide à la vérification, pas un avis juridique ni une garantie
contre une réclamation. Avant de rendre les sources ou des binaires publics :

1. Confirmer les droits sur les contributions, les ressources et le logo fourni,
   ainsi que l'accord des titulaires concernés pour la [licence du projet](../LICENSE).
   Les anciens manifests Rust déclaraient MIT : les droits déjà accordés ne
   disparaissent pas. Les composants tiers gardent leurs propres licences.
2. Vérifier les fichiers, secrets, dépendances, notices et licences ; retirer tout
   dump, clé, contenu de jeu, donnée personnelle ou autre fichier non autorisé.
   Les tests utilisent des fixtures synthétiques. Un scan automatique ne garantit
   ni l'origine des données, ni l'absence de tout secret ou contenu tiers.
3. Pour une nouvelle publication à historique vierge, partir d'une archive source
   vérifiée **sans dossier `.git`**, puis initialiser un dépôt Git séparé. Ne pas
   créer un fork ni importer l'ancien historique. Réexaminer les réglages et les
   contenus propres au nouveau dépôt avant d'en modifier la visibilité. Réécrire
   seulement la branche principale de l'ancien dépôt ne nettoie pas ses autres
   branches, références de pull requests, artefacts, journaux, clones ou caches.
4. Refaire les tests et les builds sur l'instantané retenu, notamment Windows.
   Avant diffusion d'un exécutable, inventorier les dépendances réellement
   embarquées et satisfaire leurs obligations spécifiques : les notices actuellement
   incluses ne constituent pas un audit complet. Voir [PROVENANCE.md](PROVENANCE.md)
   et [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Archive source locale

`python tools/create_source_snapshot.py` produit
`release-preparation/HookyMod-source.zip` à partir des fichiers suivis et du
contenu actuel de l'arbre de travail, sans historique Git ni fichiers ignorés.
Les nouveaux fichiers non suivis doivent être ajoutés explicitement avec
`--include-untracked chemin/du/fichier` (une option par fichier) ou être suivis
par Git avant l'export. Les autres fichiers non suivis sont exclus. L'outil
refuse notamment les liens symboliques, certaines extensions de ressources de
jeu, certains noms de fichiers de secrets et quelques signatures binaires.
Ces contrôles sont incomplets : **inspecter l'archive avant toute publication**.

Après vérification et accord des ayants droit, créer un dépôt GitHub **nouveau
et vide**, décompresser l'archive dans un dossier neuf, y initialiser Git puis
pousser son premier commit. L'ancien dépôt peut rester privé et intact. Ne
jamais importer des branches, pull requests, journaux ou artefacts de l'ancien
dépôt sans les avoir audités séparément.

## Appliquer une mise à jour sans fusionner l'ancien historique

Si une branche principale a été recréée sans parent commun avec une ancienne
branche, une fusion de cette dernière peut rattacher l'ancien historique.
Un correctif de fichiers produit avec `git diff --binary` peut être appliqué
à un checkout propre de la nouvelle base après contrôle avec `git apply --check`.
Vérifier les changements, puis créer un nouveau commit sur la base propre : le
correctif transporte des modifications de fichiers, pas les anciens commits.
Cela ne nettoie **pas** les autres branches, références de pull requests ou
artefacts du dépôt GitHub ; ils nécessitent toujours une vérification séparée.
