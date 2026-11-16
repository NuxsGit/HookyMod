# Ouvrir et diagnostiquer des ressources

HookyMod lit des ressources compatibles fournies par l'utilisateur : dossiers extraits, certaines archives SARC, fichiers `.zs`, BFRES et BNTX. Un suffixe reconnu ne garantit pas que la version ou le contenu du fichier soit entièrement pris en charge. Ne distribue pas de fichiers ou de clés que tu n'as pas le droit de partager.

## Dictionnaires et compression

Beaucoup de fichiers `.zs` utilisent un dictionnaire zstd. L'application recherche notamment `Pack/ZsDic.pack.zs` dans le projet et ses parents. Utilise les dictionnaires correspondant à tes propres fichiers : s'ils manquent, l'ouverture ou la recompression peut être refusée. Une frame tronquée est rejetée plutôt que traitée comme une ressource complète.

```bash
python -m hookymod.cli doctor /chemin/vers/romfs
python -m hookymod.cli dicts /chemin/vers/romfs
python -m hookymod.cli scan /chemin/vers/romfs --json -o rapport.json
python -m hookymod.cli list /chemin/vers/archive.pack.zs --recursive --romfs /chemin/vers/romfs
```

Le scan est un inventaire de candidats ; consulte les avertissements, notamment s'il est limité ou rencontre des entrées illisibles. Les formats inconnus ne deviennent pas exploitables parce qu'ils sont listés.

## Textures et modèles

Les textures BNTX sont souvent distinctes des modèles BFRES. Dans HookyCreator, les références de matériaux reconnues permettent de rechercher des images dans l'inventaire ; une référence absente, ambiguë ou non décodable reste signalée. Certaines associations par nom sont heuristiques et doivent être vérifiées.

Les BFRES 10.x reconnus peuvent être décodés à partir des tables de formes, indices, matériaux et attributs du LOD 0. Un repli heuristique existe pour certaines autres dispositions ; il peut omettre ou mélanger des éléments et le signale. Squelettes, pose, animations, morphs, shaders et autres LOD ne sont pas reconstruits. L'écriture 10.x est refusée ; l'injection est limitée à certaines structures 9.x. Voir [HOOKYCREATOR.md](HOOKYCREATOR.md).

```bash
python -m hookymod.cli textures texture.bntx -o images/
python -m hookymod.cli view modele.bfres -o apercu.png
python -m hookymod.cli export-model modele.bfres -o modele_export
```

La CLI de modèle recherche des textures voisines par nom : seul un albédo unique pour un matériau unique est relié, de façon heuristique. Les autres cartes candidates restent à brancher manuellement. Les noms de sortie ambigus et les conteneurs BNTX multi-images sont ignorés et signalés ; les PNG voisins sont limités à 64 Mio. Pour un OBJ source, elle conserve les indices UV et normales par coin mais ne lit pas les bibliothèques MTL ni les textures référencées ; les polygones sont triangulés en éventail avec un avertissement. Les maillages 10.x préconstruits refusent une demande de LOD autre que 0. Choisir un dossier d'export dédié : la CLI peut remplacer ses fichiers homonymes. Le mode individuel de HookyCreator propose une résolution par matériau plus détaillée et fournit un rapport. L'explorateur Tk n'associe pas automatiquement les textures lors de son export glTF/OBJ.

## Autres diagnostics

- Une interface Tk absente peut nécessiter l'installation du paquet système `python3-tk` sous Debian/Ubuntu ; la CLI et l'atelier navigateur peuvent être utilisés sans elle.
- Les textures BC6/BC7/ASTC ont besoin de `texture2ddecoder` pour les chemins de lecture concernés. L'encodage ASTC/BC7 n'est pas fourni.
- Une ressource modifiée dont la taille change peut nécessiter une adaptation de la table RESTBL. L'assemblage de HookyCreator ne la met pas automatiquement à jour.
- Les archives, animations, paramètres et formats non reconnus peuvent rester illisibles. Une exportation ou une relecture valide ne garantit pas le résultat en jeu.

## Références et attributions

Ces projets externes sont cités comme sources ou outils distincts, pas comme dépendances intégrées ni comme garantie de compatibilité :

- [OctoSquiddy/Switch-2-Toolbox](https://github.com/OctoSquiddy/Switch-2-Toolbox), structures BFRES/BNTX et textures externes ; projet sous GPL-3.0. Voir aussi [la discussion sur les dictionnaires](https://github.com/KillzXGaming/Switch-Toolbox/issues/757).
- [Kabosune/oot-remake-modding](https://github.com/Kabosune/oot-remake-modding), notes sur les fichiers, dictionnaires et table des tailles.
- [CosmicDreamsOfCode/BfresToCast](https://github.com/CosmicDreamsOfCode/BfresToCast), autre convertisseur de modèles.
- Hookshot de NotRexed, notamment ses notes sur NUP et les tables de tailles ([adresse GitHub historique](https://github.com/NotRexed/Hookshot)). [Longshot](https://git.magicbean.dev/MagicBeanDev/Longshot) est un fork distinct maintenu par Team MagicBean ; la compatibilité avec HookyMod n'a pas été vérifiée.
- [zortos293/Lichii](https://github.com/zortos293/Lichii), projet indépendant d'émulation.
- `KillzXGaming/tegra_swizzle`, formules publiques de disposition des pixels X1/Orin ; la famille d'outils TotkToolkit propose également des références sur des formats apparentés.

Les licences et attributions des composants effectivement utilisés sont récapitulées dans [PROVENANCE.md](PROVENANCE.md) et dans les répertoires des dépendances.
