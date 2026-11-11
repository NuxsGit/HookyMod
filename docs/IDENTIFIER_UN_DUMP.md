# Identifier un dump inconnu (et me transmettre le résultat)

Ce guide sert quand on ne sait pas encore **dans quels formats** le jeu stocke ses
assets : remake PC sur Unreal/Unity, portage Switch (SARC + zstd + BFRES/BNTX),
version 3DS (ZAR + CMB + CTEX), moteur maison, etc.

L'outil embarque un scanner de formats : aucun parseur n'est nécessaire pour
l'utiliser, et il ne modifie rien.

## 1. Scanner le dump

```bash
# rapport lisible, avec un extrait hexadécimal des en-têtes
python -m hookymod.cli scan "/chemin/vers/le/jeu" --hexdump 32 --strings 6

# version machine (JSON), plus facile à envoyer
python -m hookymod.cli scan "/chemin/vers/le/jeu" --json -o rapport.json
```

Depuis l'interface graphique : menu **Outils → Scanner un dossier (formats)…**, puis
**Copier le rapport** ou **Enregistrer…** (JSON).

Le scanner affiche :

* le **nombre de fichiers**, la taille totale et l'histogramme des extensions ;
* les **magics reconnus** (SARC, RARC, U8, Yaz0, zstd, BNTX, BFRES, BNSH, BYML, AAMP,
  CMB, CTEX, BCH, `.uasset`, `.pak`, IoStore, UnityFS, `.pck` Godot, ZIP, DDS, Ogg…),
  avec des détails utiles (dictionnaire zstd et taille décompressée par exemple) ;
* les **échantillons** par extension (nom, taille, magic, en-tête, chaînes internes) ;
* le **moteur probable**, avec la justification.

Options utiles :

| Option | Effet |
| --- | --- |
| `--samples N` | nombre de fichiers analysés par extension (défaut 3) |
| `--hexdump N` | affiche les N premiers octets des échantillons |
| `--strings N` | affiche N chaînes ASCII (noms internes, chemins, noms de matériaux) |
| `--depth N` | profondeur maximale d'exploration (0 = tout) |
| `--limit N` | nombre maximum de fichiers lus (défaut 200 000) |
| `--json` | sortie JSON |
| `-o FICHIER` | écrit le rapport dans un fichier |

Sans `--hexdump`, le rapport ne contient **aucun octet du jeu** : seulement des noms de
fichiers, des tailles et des formats identifiés. Avec `--hexdump`, ce sont des en-têtes de
quelques dizaines d'octets, ce qui reste nécessaire pour reconnaître un format inconnu.

## 1 bis. Fichier au format inconnu mais qui contient d'autres choses

Un conteneur maison met souvent ses blocs après un en-tête : le scanner teste aussi les
structures connues **à n'importe quel offset** et affiche les blocs trouvés
(`contient : modèle BFRES @ 0x40`). En ligne de commande :

```bash
python -m hookymod.cli carve "chemin/vers/fichier" --bytes 4194304 --limit 20
```

## 2. Ce qui est utile de transmettre

1. Le rapport `scan --json` (ou le texte complet).
2. La **liste du dossier racine du jeu** (à côté de `romfs`, s'il existe) : présence de
   `*.exe`, `*.pak`, `*.utoc`/`*.ucas`, `Data/`, `Engine/`, `Game/`, `*.pck`, `UnityPlayer.dll`…
3. Pour **2 ou 3 fichiers représentatifs** (le plus gros conteneur, une texture probable,
   un modèle probable) : les 256 premiers octets en hexadécimal **et** les chaînes ASCII,
   par exemple :

   ```bash
   python -m hookymod.cli scan "chemin/vers/fichier" --hexdump 256 --strings 12
   ```
4. Le nom du **loader / mod loader** utilisé par le jeu (ex. Hookshot) et, si possible, sa
   documentation ou son dépôt : c'est lui qui impose la structure `romfs/` attendue.
5. Si le jeu est un **remake PC**, le nom du moteur s'il est visible (Unreal, Unity, Godot,
   moteur maison) — le rapport le devine déjà dans la plupart des cas.

## 3. Ce qu'il ne faut **pas** transmettre

* Des assets complets (modèles, textures, sons, archives entières) : ce sont des contenus
  protégés, et le dépôt est volontairement **source only** (voir la note légale du README).
* Des captures d'écran de contenu de jeu : inutiles pour le format.

Des en-têtes de 32 à 256 octets, des tailles et des noms de fichiers suffisent pour écrire
un parseur.

## 4. Ce que le scanner ne peut pas faire

* Il n'**extrait pas** les assets d'un format qu'il ne connaît pas : il dit seulement
  « c'est du X, voici la structure des premiers octets ».
* L'identification par extension seule (`.bin`, `.dat`, `.data`) est une **hypothèse** :
  elle est marquée `confidence: extension` dans le JSON.
* Il ne lit pas les archives **chiffrées** (certains `.pak` Unreal, certains conteneurs
  propriétaires) : dans ce cas l'en-tête est aléatoire et le rapport indique
  « format inconnu » avec l'entropie élevée.

## 5. Après l'identification

Selon le résultat :

* **SARC / zstd / BFRES / BNTX** → déjà pris en charge (exploration, extraction,
  remplacement, textures PNG, modèles OBJ/3D, patchs, export de mod).
* **ZAR / CMB / CTEX (3DS)** → parseurs à ajouter ; la structure est proche de ce qui existe
  déjà (archives + modèles + textures), c'est le prochain chantier naturel.
* **Unreal (`.uasset` / `.pak` / IoStore)** → changer de stratégie : on passe par les
  conteneurs Unreal (extraction des `.uasset`, puis des formats internes), pas par un romfs
  Nintendo. Il faudra un parseur `FIoStoreToc` / `FPakFile` en lecture seule, puis les
  objets `UObject` exportés.
* **Unity (`.assets` / bundles)** → lecture des SerializedFiles (arborescence
  `TypeTree`), puis des `Texture2D` / `Mesh` sérialisés.
