# Traducteur BFRES 10.x — ce qui est mesuré, ce qui est vérifié

Ce dossier documente **comment HookyMod apprend** le format des modèles du
remake. Il n'y a pas de spécification publique du BFRES 10.x : tout ce qui suit
a été mesuré sur des fichiers réels, puis verrouillé par des tests.

Corpus de mesure (fourni par l'utilisateur, jamais versionné) :

| fichier | taille décompressée | formes |
|---|---|---|
| `Animal_Butterfly.Animal_Butterfly.bfres.zs` | 131 792 o | 1 |
| `Enemy_TorchSlug.Enemy_TorchSlug.bfres.zs` | 476 208 o | 5 |
| `Enemy_Wolfos.Enemy_Wolfos.bfres.zs` | 10 872 096 o | 8 |

## 1. En-tête

| offset | type | valeur observée |
|---|---|---|
| `0x00` | 4 o | `FRES` puis 4 octets de bourrage |
| `0x08` | u32 | version, `0x000A0400` = 10.4 |
| `0x0C` | u16 | BOM `0xFFFE` (petit-boutiste) |
| `0x28` | u64 | offset du bloc `FMDL` |
| `0x170` | u64 | **base des tampons GPU** (0x4000, 0xA000, 0x15000…) |

Les tailles des tampons sont *relatives à cette base* : la base change d'un
modèle à l'autre, elle ne peut donc pas être codée en dur.

## 2. `FMDL` et `FVTX`

`FMDL` : nom `+0x08`, `FVTX` `+0x20`, matériaux `+0x38` (tous en u64).

`FVTX` (un bloc **par forme**) :

| offset | type | contenu |
|---|---|---|
| `+0x04` | u32 | nombre de tampons |
| `+0x08` | u64 | table d'attributs |
| `+0x10` | u64 | table des LOD |
| `+0x18` | u64 | fin des données |
| `+0x48` | u32 | tampon de sommets, **relatif à la base** |
| `+0x4C` | u32 | octet bas = nombre d'attributs, octet haut = index de forme |

## 3. Table d'attributs

Entrées de 16 octets : `{référence de chaîne u32, 0, format u32, offset u32}`.
Les chaînes sont **préfixées par leur longueur** (`03 00 "_p0"`), d'où la
référence « chaîne − 2 ».

Mesuré :

| nom | format | sens | octets |
|---|---|---|---|
| `_p0` | `0x1805` | position `3 × f32` | 12 |
| `_p0` | `0x1505` | position `3 × f16` | 6 |
| `_n0`, `_t0` | `0x0E02` | normale / tangente compactées | 4 |
| `_u0` | `0x1201`, `0x1202` | UV `2 × u16` | 4 |
| `_i0` | `0x0B03`, `0x0903`, `0x0203` | indices de peaux | 4 |
| `_w0` | `0x0B01`, `0x0901` | poids de peaux | 1–2 |
| `_c0` | — | couleur | 4 |

La taille de chaque attribut se déduit de l'offset du **suivant** : le fichier
donne sa propre disposition, aucune table externe n'est nécessaire. Le pas
d'enregistrement est la fin du dernier attribut, arrondie à 4 octets
(24 pour le papillon, 32 pour les ennemis, 28 pour les formes sans peau).

## 4. Comptage des sommets

Les tampons de sommets sont rangés **bout à bout**. Le nombre de sommets d'une
forme est donc `(tampon suivant − tampon) / pas`, ce qui se vérifie :

```
Wolfos : 0x1C2600 − 0x163C00 = 0x5EA00 = 387 584 = 12 112 × 32
```

Quand l'écart est anormal (plusieurs Mo), le nombre est ramené à
`max(index) + 1` : on ne lit que ce qui est réellement référencé.

## 5. Indices

Le tampon d'indices commence à la base des tampons et finit au premier tampon de
sommets. Il est découpé en blocs de `0x1000` octets ; un sous-maillage commence
sur une frontière de bloc, éventuellement après du remplissage (d'où le
balayage par pas de 2 octets). Un sous-maillage fait au plus 682 triangles
(`682 × 6 = 4092 < 4096`).

Chaque bloc est attribué à la forme dont les positions donnent la meilleure
**cohérence** (rapport arête moyenne / distance moyenne au hasard) ; au-delà de
0,35 le bloc est refusé et signalé. C'est ce qui empêche d'exporter du bruit.

## 6. Ce qui est vérifié par les tests

* `tests/test_bfres_v10.py::DataDrivenTests` fabrique un BFRES 10.4 **conforme
  au format mesuré** (en-tête, `FMDL`, `FVTX`, vraie table d'attributs, tampons
  rangés comme dans le jeu) et exige que la lecture retrouve la géométrie sans
  aucun offset codé en dur ;
* un tampon de sommets rempli de `NaN` doit être **refusé** ;
* les détecteurs historiques (pas d'enregistrement, découpage, cohérence)
  restent testés : ce sont les replis quand la table est illisible.

## 7. Limites connues (assumées, signalées dans le rapport)

1. La table `FSHP` (plages d'index exactes par sous-maillage et par LOD) n'est
   pas encore lue : l'attribution des blocs se fait par validation géométrique,
   et le rapport indique combien de blocs ont été ignorés.
2. Plusieurs LOD d'une même forme peuvent être inclus : la géométrie reste
   juste mais plus dense que nécessaire, et le rapport le dit
   (« sous-maillage(s) de LOD inférieur inclus »).
3. Les formats d'attributs inconnus ne sont pas devinés : l'attribut est ignoré
   et le rapport liste les attributs réellement lus.

## 8. Méthode de travail (à réutiliser pour les prochaines familles)

1. décompresser (`ZsDic.pack.zs` fournit les dictionnaires zstd) ;
2. dumper l'en-tête et chercher les signatures (`FMDL`, `FVTX`, `FSHP`,
   `FSKL`, `_STR`) ;
3. valider une hypothèse **sur deux modèles différents** avant de coder ;
4. chercher un invariant arithmétique (écart de tampons / pas, nombre de
   triangles par bloc) : c'est ce qui distingue une vraie structure d'une
   coïncidence ;
5. écrire le test synthétique d'abord, puis brancher le lecteur.

## État des contrôles visuels (11/11)

| modèle | formes | sous-maillages | faces | verdict |
| --- | --- | --- | --- | --- |
| papillon (référence) | 1 | 6 blocs de LOD | ≥ 682 | conforme |
| `Enemy_TorchSlug` | 5 | 15 | 4 393 | **conforme** : limace + coquille + pédoncule oculaire |
| `Enemy_Wolfos` | 8 | 152 | 63 607 | **incomplet** : morceaux justes mais espacés, surface trouée |

Trois garde-fous ont été ajoutés après avoir vu le rendu Wolfos partir en
« spaghettis » (arêtes traversant tout le modèle) :

1. `_decode_positions` s'arrête au premier sommet aberrant — l'écart entre
   tampons donnait 274 441 sommets pour une forme qui en compte 944 ;
2. un bloc d'indices est refusé si ses arêtes médianes dépassent 15 % de la
   diagonale de la forme ;
3. les triangles dont une arête traverse le modèle sont retirés à l'unité, et
   un bloc qui en perd la moitié est rejeté.

Reste à faire sur Wolfos : les morceaux acceptés sont à la bonne échelle mais
la surface n'est pas continue. Hypothèses à trancher à la prochaine passe —
soit des blocs d'indices valides encore refusés, soit des formes en espace
local qu'il faut replacer avec le squelette (`FSKL`, lu mais pas encore
exploité).
