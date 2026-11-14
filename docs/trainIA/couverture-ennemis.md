# Couverture de la famille « ennemis » (11 ennemis, 15 fichiers)

Rapport produit par :

```bash
python -m tools.coverage /tmp/dumps -o previews/ennemis
```

`/tmp/dumps` contenait les 14 BFRES de modèle de `Model/` plus une coquille
d'animation. Les PNG de contrôle restent hors dépôt (`previews/`), puisqu'ils
sont rendus à partir d'assets du jeu ; ce document ne contient que des mesures.

## Ce que les aperçus ont montré

| modèle | verdict visuel |
| --- | --- |
| `Enemy_Stalfos` | squelette complet : crâne, cage thoracique, bras, jambes, épée — **juste** |
| `Enemy_Tektite_Blue` / `_Red` | corps + quatre longues pattes + œil — **juste** |
| `Enemy_Tentacle_Red` | tentacule dressé avec sa ventouse — **juste** |
| `Enemy_TrickyDoor` | porte avec sa gueule — **plausible** |
| `Enemy_TorchSlug` | limace, coquille, pédoncule oculaire — **juste** |
| `Enemy_Wolfos` | morceaux à la bonne échelle mais espacés — **incomplet** |
| `Enemy_Spike` / `_fracture`, `Stalchild` / `_Big`, `Stinger`, `Tailpasaran` | géométrie produite, à contrôler à l'œil |

## Mesures

# Couverture

15 fichier(s) : **14 traduit(s)**, 0 refusé(s), 0 en erreur, 1 animation(s).

| fichier | état | sous-maillages | faces | sommets | taille | avertissements |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `Enemy_Spike.Enemy_Spike.bfres.zs` | ok | 21 | 8648 | 58842 | [0.495, 0.518, 0.504] | 37 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 20 sous-maillage(s) de LOD inférieur inclus (pic d'indices 2801). ; UV décodées (2 x u16). |
| `Enemy_Spike.Enemy_Spike_fracture.bfres.zs` | ok | 50 | 18879 | 344400 | [0.552, 0.528, 0.507] | 24 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 39 sous-maillage(s) de LOD inférieur inclus (pic d'indices 6887). ; UV décodées (2 x u16). |
| `Enemy_Spike.anim.bfres.zs` | animation | 0 | 0 | 0 | — | BFRES d'animation : pas de maillage à traduire |
| `Enemy_Stalchild.Enemy_Stalchild.bfres.zs` | ok | 29 | 11732 | 135433 | [2.555, 1.376, 0.618] | 11 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 24 sous-maillage(s) de LOD inférieur inclus (pic d'indices 5058). ; forme 3 : 1 sous-maillage(s) |
| `Enemy_Stalchild_Big.Enemy_Stalchild_Big.bfres.zs` | ok | 26 | 13238 | 92886 | [4.088, 2.201, 0.989] | 17 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 12 sous-maillage(s) de LOD inférieur inclus (pic d'indices 5058). ; forme 1 : 7 sous-maillage(s) |
| `Enemy_Stalfos.Enemy_Stalfos.bfres.zs` | ok | 52 | 18051 | 219036 | [2.132, 2.229, 0.587] | 18 bloc(s) d'indices ignoré(s) faute de validation. ; forme 2 : 25 sous-maillage(s) de LOD inférieur inclus (pic d'indices 5415). ; forme 0 : 7 sous-maillage(s) |
| `Enemy_Stinger.Enemy_Stinger.bfres.zs` | ok | 67 | 30829 | 595781 | [1.168, 0.6, 1.442] | 38 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 47 sous-maillage(s) de LOD inférieur inclus (pic d'indices 10097). ; forme 3 : 4 sous-maillage(s |
| `Enemy_Tailpasaran.Enemy_Tailpasaran.bfres.zs` | ok | 57 | 25251 | 911088 | [0.659, 0.592, 0.79] | 67 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 49 sous-maillage(s) de LOD inférieur inclus (pic d'indices 15983). ; UV décodées (2 x f16). |
| `Enemy_Tektite_Blue.Enemy_Tektite_Blue.bfres.zs` | ok | 134 | 60704 | 1625151 | [2.009, 0.697, 1.505] | 52 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 41 sous-maillage(s) de LOD inférieur inclus (pic d'indices 13375). ; forme 1 : 75 sous-maillage( |
| `Enemy_Tektite_Red.Enemy_Tektite_Red.bfres.zs` | ok | 129 | 59182 | 1454202 | [1.763, 0.525, 1.214] | 45 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 78 sous-maillage(s) de LOD inférieur inclus (pic d'indices 11653). ; forme 1 : 29 sous-maillage( |
| `Enemy_Tentacle_Red.Enemy_Tentacle_Red.bfres.zs` | ok | 257 | 120537 | 5572531 | [4.792, 23.614, 4.888] | 123 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 230 sous-maillage(s) de LOD inférieur inclus (pic d'indices 21682). ; UV décodées (2 x u16). |
| `Enemy_TorchSlug.Enemy_TorchSlug.bfres.zs` | ok | 15 | 4393 | 37050 | [0.757, 0.531, 1.279] | 9 bloc(s) d'indices ignoré(s) faute de validation. ; forme 1 : 11 sous-maillage(s) de LOD inférieur inclus (pic d'indices 2952). ; UV décodées (2 x u16). |
| `Enemy_TrickyDoor.Enemy_TrickyDoor.bfres.zs` | ok | 27 | 9472 | 301040 | [1.652, 2.699, 0.142] | 30 bloc(s) d'indices ignoré(s) faute de validation. ; forme 2 : 22 sous-maillage(s) de LOD inférieur inclus (pic d'indices 11959). ; forme 1 : 1 sous-maillage(s |
| `Enemy_TrickyDoor.Enemy_TrickyDoor_fracture.bfres.zs` | ok | 27 | 12922 | 297432 | [1.662, 2.604, 0.208] | 10 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 25 sous-maillage(s) de LOD inférieur inclus (pic d'indices 11015). ; UV décodées (2 x u16). |
| `Enemy_Wolfos.Enemy_Wolfos.bfres.zs` | ok | 152 | 63607 | 1388962 | [3.878, 1.831, 1.273] | 183 bloc(s) d'indices ignoré(s) faute de validation. ; forme 0 : 45 sous-maillage(s) de LOD inférieur inclus (pic d'indices 12001). ; forme 1 : 71 sous-maillage |
