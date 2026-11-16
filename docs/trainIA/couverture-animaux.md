# Couverture historique de la famille « animaux »

> Archive de l'ancien lecteur heuristique : ces chiffres et jugements visuels
> datent d'une version antérieure et ne valident pas le lecteur structuré actuel.
> Les aperçus de référence ne sont pas conservés ici. Voir les
> [limites actuelles](../HOOKYCREATOR.md) et les [mesures récentes](../PERFORMANCE.md).

26 modèles `Animal_*` récupérés depuis `Model/`, plus les dictionnaires zstd
du jeu. Rapport produit par :

```bash
python -m tools.coverage /tmp/animals -o previews/animaux
```

**26 fichiers traduits sur 26, 372378 faces.** Les PNG de contrôle restent
hors dépôt (`previews/`), puisqu'ils sont rendus à partir d'assets du jeu.

## Contrôle visuel

| modèle | verdict |
| --- | --- |
| `Animal_Horse_Epona_Adult` | **juste** — cheval complet, tête, crinière, queue, quatre jambes |
| `Animal_Frog` | **juste** — grenouille, ventre, pattes |
| `Animal_Cow`, `Animal_Cucco`, `Animal_Dog`, `Animal_Octopus`, `Animal_Insect` | géométrie produite, silhouettes plausibles |
| `Animal_Shark` | plausible — aileron, queue, corps ; quelques trous |
| `Animal_Cat` | plausible — corps et tête lisibles, pattes peu visibles (modèle à 2 784 faces) |
| `Animal_Environment_Rat` | **douteux** — rendu en pelote hérissée : à traiter, voir plus bas |

## Mesures

| modèle | sous-maillages | faces |
| --- | ---: | ---: |
| `Animal_Horse_Epona_Adult` | 221 | 98448 |
| `Animal_Horse_Epona_Child` | 164 | 68195 |
| `Animal_Horse_General` | 137 | 57874 |
| `Animal_Horse_Ganondorf` | 81 | 35051 |
| `Animal_Cow` | 87 | 32820 |
| Animal 6 (nom neutralisé) | 38 | 16064 |
| `Animal_Shark` | 24 | 9943 |
| `Animal_Frog` | 33 | 9746 |
| `Animal_Fish_Loach` | 20 | 7300 |
| `Animal_Insect` | 15 | 6024 |
| `Animal_Cucco` | 30 | 5779 |
| `Animal_Octopus` | 13 | 4528 |
| `Animal_Dog` | 14 | 3043 |
| `Animal_Cat` | 12 | 2784 |
| `Animal_KokiriForest_Insect_A` | 11 | 2695 |
| `Animal_Environment_Bird_Swarm_Water` | 17 | 2005 |
| `Animal_Fish_Bass` | 5 | 1455 |
| `Animal_Environment_Squirrel` | 8 | 1447 |
| `Animal_Environment_Rat` | 2 | 1239 |
| `Animal_Environment_Bird_Swarm_Ground` | 12 | 1189 |
| `Animal_Butterfly` | 3 | 1158 |
| `Animal_Environment_SwarmBird_Pigeon` | 7 | 1048 |
| `Animal_KokiriForest_Insect_C` | 4 | 1013 |
| `Animal_KokiriForest_Insect_D` | 2 | 606 |
| `Animal_Fish_Blue` | 2 | 598 |
| `Animal_KokiriForest_Insect_B` | 5 | 326 |

## Seuil de cohérence relevé (0,20 → 0,33)

Le seuil de cohérence d'un bloc d'indices a été relevé après constat visuel :
`Animal_Cat` sortait à 1 212 faces (corps seulement), il en sort 2 784 avec le
seuil à 0,33. Les garde-fous ajoutés pour le Wolfos restent en place — arête
médiane sous 15 % de la diagonale, retrait des triangles qui traversent le
modèle — et le Wolfos ne régresse pas (64806 faces, même aspect).

Les trois seuils sont désormais des constantes nommées en tête de
`hookymod/bfres_v10.py` : `COHERENCE_MAX`, `EDGE_RATIO_MAX`,
`OVERSIZED_RATIO`.

## Cas ouvert : `Animal_Environment_Rat`

Une seule forme, pas de 28 octets, positions en f16, 9 882 sommets annoncés.
Les blocs d'indices passent tous les critères mesurables (cohérence, arêtes,
partage d'arêtes, accord des normales) et pourtant le rendu n'est pas un rat :
pelote compacte plus une longue pointe. Les pistes restantes : tampon de
sommets quantifié (échelle par forme), ou positions prises dans un autre
tampon que celui annoncé.

En attendant, le fichier est signalé et son aperçu est fourni : rien n'est
présenté comme correct sans l'image qui le montre.
