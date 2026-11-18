# HookyMod's — sources séparées et projets historiques

Dans cette version source, HookyCreator se concentre sur l'exploration, l'édition de copies d'assets, Player et les exports de modèles. L'atelier HookyMod's, les fiches préparatoires de mobs et l'assemblage d'un dossier de mod ne sont plus accessibles depuis l'interface ou l'API. **Il n'existe pas encore d'application mod maker séparée prête à utiliser.**

La release publique **11.26.2** a été publiée avant ce retrait : ses installateurs contiennent toujours l'ancienne interface et son assembleur. Ce document décrit le code source actuel ; les [notes de cette release](RELEASE_NOTES_11.26.2.md) restent historiques.

## Fichiers existants

- Les projets HookyMod's enregistrés dans `<travail>/hookymods/`, les fiches dans `<travail>/mobs/`, les copies sous `<travail>/assets/` et les dossiers `mods/` existants ne sont ni supprimés ni déplacés par HookyCreator. `<travail>` vaut normalement `<projet>/hookycreator_work/` ou le dossier choisi avec `--work`.
- Une ancienne session ouverte sur `studio`, `create` ou `mobs` revient à l'accueil. Les autres onglets et choix d'import compatibles sont repris ; les contrôles des fonctions retirées ne sont plus affichés. Avant la première nouvelle écriture, la session historique contenant des données mod maker est conservée telle quelle dans `<travail>/sessions/*.pre-modmaker-<empreinte>.json.bak`. Si cette copie échoue, la session d'origine n'est pas remplacée ; cette copie n'est pas chargée automatiquement. Sur un système de fichiers sans liens durs, une erreur pendant la copie peut laisser une sauvegarde incomplète : après avoir protégé la session d'origine, déplace cette copie incomplète hors de `sessions/` avant de réessayer.
- Sauvegarde tes projets et archives `.hooky` avant toute migration ou utilisation d'anciens outils. Ils peuvent contenir des ressources et informations personnelles.

## Code conservé hors application

[`modmaker/`](../modmaker/README.md) conserve le lecteur et la logique des projets de conception, l'outil expérimental d'archives de mod et une copie de référence de l'interface historique. Celle-ci **n'est pas autonome** et ne fait pas partie du backend desktop. L'ancien assembleur est seulement une classe source archivée : **appelé directement, il supprime et recrée `mods/<nom>` sans sauvegarde**. Il ne crée pas d'acteur jouable ni ne met RESTBL à jour.

Le lecteur source d'archives de conception `modmaker.studio` reste utilisable par un développeur ; il vérifie le schéma mais ne convertit pas les blocs en jeu. L'ancien [guide d'utilisation de l'interface 11.26.2](../modmaker/legacy_web/HOOKYMODS-legacy.md) est archivé uniquement comme référence historique et ne décrit pas l'interface actuelle.

## Paquets `.hooky` depuis les sources

L'outil de référence `python -m modmaker.modpackage` peut emballer, inspecter et extraire des fichiers `romfs` **déjà préparés**. L'ancien point d'entrée `python -m hookycreator.modpackage` reste compatible depuis les sources. Aucun de ces outils n'est exposé par HookyCreator ni inclus dans le backend desktop actuel.

La variante projet de conception et la variante paquet de mod ont des identifiants distincts ; ne les confonds pas. Ni l'intégration directe dans Link's Tool Box ou Longshot ni la compatibilité en jeu ne sont validées. Le [contrat `.hooky`](HOOKY_FORMAT.md), aussi disponible [en anglais](HOOKY_FORMAT_EN.md), décrit les formats, leurs limites et un exemple synthétique reproductible.
