# Modder le remake Switch 2 (Hookshot) avec HookyMod

Contexte : *The Legend of Zelda: Ocarina of Time* (remake Switch 2) tourne sur PC via
**Hookshot** ([NotRexed/Hookshot](https://github.com/NotRexed/Hookshot)), une
recompilation native. Ses fichiers de jeu ne sont pas un `romfs` posé sur le disque : ils
arrivent dans un **paquet `.nup`**, ce qui rend le modding « illisible » au premier abord.

Bonne nouvelle : à l'intérieur, le pipeline d'assets est celui de Nintendo
(`SARC` + zstd avec dictionnaire + `BFRES` + `BNTX`). Il suffit donc de savoir lire
l'emballage `.nup` — c'est ce que fait `hookymod.nup`.

## 1. Ce que Hookshot accepte

| Entrée | Détail |
| --- | --- |
| fichier `.nup` | dump Switch 2 déchiffré ; Hookshot extrait lui-même `romfs` + `exefs` |
| dossiers `romfs` + `exefs` | dump déjà extrait (la plupart des outils de dump savent le faire) |

Pour modder sans reconstruire le jeu, il suffit donc d'un dossier `romfs` accessible :

```bash
# extraire le .nup en romfs/ + exefs/ (sans toucher au fichier d'origine)
python -m hookymod.cli nup "/chemin/game.nup" --extract "C:/Games/Hookshot_dump" --verify
```

Dans l'interface : **Fichier → Ouvrir un paquet .nup (Switch 2)…**, choisir le `.nup`,
puis le dossier d'extraction : le romfs extrait est ouvert directement dans l'explorateur.

## 2. Où vont les mods

Hookshot charge les mods depuis un dossier `mods/` à côté de `hookshot.exe`
(ou la variable d'environnement `HOS_MODS`), au format LayeredFS des émulateurs :

```
mods/
  MonMod/                 ← préfixe « - » ou «_» = mod désactivé
    romfs/
      Model/Link.bfres    ← remplace le fichier du jeu au même chemin
      Textures/....
  contents/<title id>/AutreMod/romfs/...   ← disposition imbriquée acceptée aussi
```

Règles importantes :

* **seuls les mods RomFS sont chargés** ; les patchs `exefs` (`.pchtxt`, `.ips`) sont
  ignorés, le code du jeu étant recompilé à l'avance (il faudrait recompiler) ;
* quand deux mods touchent le même fichier, celui qui trie en dernier gagne ;
* un fichier de mod **plus gros** que celui du jeu n'est pas chargé, sauf si la table des
  tailles de ressources l'autorise (voir §3).

L'export de HookyMod (**Fichier → Exporter en mod…**) écrit exactement
`<dossier choisi>/romfs/<chemin romfs>` : pointe-le sur `mods/MonMod/` et c'est prêt.

## 3. Le vrai blocage : la table des tailles (RESTBL)

Le jeu réserve pour chaque fichier une taille maximale
(`romfs/System/Resource/ResourceSizeTable.*.rsizetable[.zs]`). Une texture HD ou un modèle
plus lourd dépasse cette réservation → le fichier est **silencieusement ignoré**. C'est la
cause n°1 des mods « qui ne font rien », et Hookshot demande donc au mod de fournir la
table avec ses entrées agrandies.

HookyMod le fait pour toi :

* à l'export d'un mod, il compare la taille de chaque fichier écrit avec la réservation du
  jeu et propose d'**agrandir la table** ;
* la table agrandie est écrite dans le mod
  (`romfs/System/Resource/ResourceSizeTable.*`), recompressée avec le dictionnaire
  d'origine si le jeu la stocke en `.zs` ;
* en ligne de commande :

```bash
# que réserve le jeu pour ce fichier ?
python -m hookymod.cli restbl "/romfs" --get "Model/Link.bfres"

# agrandir (créer / relever l'entrée) et écrire la table recompressée
python -m hookymod.cli restbl "/romfs" --ensure "Model/Link.bfres=8388608" \
    --output nouveau/ResourceSizeTable.Product.100.rsizetable.zs --romfs "/romfs"
```

La clé est le CRC-32 du chemin relatif au romfs, suffixe `.zs` retiré
(`Model/Link.bfres.zs` → `Model/Link.bfres`). Les entrées sont triées par CRC après
modification, comme le jeu l'exige pour sa recherche dichotomique.

## 4. Déroulé complet

```bash
# 1. vérifier l'emballage et extraire
python -m hookymod.cli nup game.nup --verify --deep --extract dump --only romfs

# 2. identifier les formats présents (BNTX ? BFRES ? SARC ?)
python -m hookymod.cli scan dump/romfs --hexdump 32

# 3. explorer une archive de textures
python -m hookymod.cli list dump/romfs/Pack/*.pack -r
python -m hookymod.cli textures dump/romfs/Pack/Textures.pack.zs -e "..." -o png/

# 4. remplacer une entrée (recompression automatique avec le bon dictionnaire)
python -m hookymod.cli replace dump/romfs/Pack/Model.pack.zs \
    -e "Model/Link.bfres" -i mon_link.bfres -o out/Model.pack.zs --romfs dump/romfs

# 5. préparer le mod ; l'export GUI propose d'agrandir la table de tailles
#    (ou : restbl --ensure … comme au §3)
```

## 5. Ce qui reste hors de portée

* **patchs de code (`exefs`)** : Hookshot recompile le jeu ; patcher `main` demanderait un
  rebuild complet. Ce n'est pas un format, c'est une limite du portage.
* **fichier de jeu chiffré** : un `.nup` chiffré ou un `.nsp`/`.xci` Switch 2 doit d'abord
  être déchiffré par les outils de dump ; HookyMod ne contient aucune clé.
* **`.zs` dont le dictionnaire manque** : sans `Pack/ZsDic.pack.zs`, impossible de
  décompresser ; l'outil l'indique explicitement au lieu d'échouer silencieusement.
* **formats internes inconnus** : lance `scan` puis `carve` ; le rapport dit s'il s'agit
  bien de SARC/zstd/BNTX/BFRES ou d'autre chose.

## 6. Licences

Hookshot est sous **GPL-3.0** ; HookyMod est sous **MIT** et n'embarque aucun code de
Hookshot : seuls les formats de fichiers (faits observables) ont servi de référence. Aucun
fichier du jeu n'est distribué.
