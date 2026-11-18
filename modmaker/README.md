# Mod-maker : code historique séparé

Ce dossier conserve des sources expérimentales retirées de l'application HookyCreator. Il ne constitue pas une application autonome et n'est pas inclus dans le backend desktop.

- `studio.py` : logique des projets de conception et lecteur/écrivain des archives `.hooky` de conception non jouables.
- `modpackage.py` : outil source expérimental pour emballer, inspecter et extraire des fichiers `romfs` déjà préparés : `python -m modmaker.modpackage --help`. L'ancien nom `python -m hookycreator.modpackage` reste un alias source.
- `legacy_project.py` : anciennes fiches de mobs et ancien assembleur, non appelés par HookyCreator. **Attention : `build_mod` supprime et recrée `mods/<nom>` sans sauvegarde.**
- `legacy_web/` : interface, traductions, smoke et guide historiques archivés comme référence ; ils ne sont pas autonomes et ne sont pas chargés par HookyCreator.

Les dossiers utilisateurs déjà enregistrés ne sont pas effacés lors de l'ouverture de HookyCreator. Voir [les notes de séparation](../docs/HOOKYMODS.md) et le [contrat expérimental `.hooky`](../docs/HOOKY_FORMAT.md). Les archives ne génèrent pas de contenu jouable et aucune compatibilité de lanceur ou en jeu n'a été validée.
