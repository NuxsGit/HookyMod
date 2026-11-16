# Identifier des ressources inconnues

Le scanner inspecte un fichier ou un dossier sans le modifier. Les signatures et extensions permettent de suggérer une famille de formats, **pas de garantir** que les fichiers sont lisibles ou que le moteur a été identifié correctement.

## Scanner un dossier

```bash
# Rapport général, sans hexdump ni chaînes extraites
python -m hookymod.cli scan /chemin/vers/ressources --json -o rapport.json

# Diagnostic plus détaillé — peut révéler des fragments et noms internes
python -m hookymod.cli scan /chemin/vers/ressources --hexdump 32 --strings 6
```

Dans l'explorateur Tk : **Outils → Scanner un dossier (formats)…**. Le rapport contient les quantités, tailles, extensions, signatures reconnues, échantillons et une famille probable avec justification. Les diagnostics par extension sont des hypothèses ; les limites de scan et les entrées illisibles doivent être examinées avant de conclure.

Options utiles : `--samples N` contrôle le nombre d'échantillons par extension, `--depth N` la profondeur (0 pour tout), `--limit N` le nombre maximal de fichiers (200 000 par défaut), `--json` la sortie machine et `-o` le fichier de rapport. `--hexdump` et `--strings` ajoutent respectivement des octets et chaînes provenant des fichiers analysés. Même sans ces options, les chemins et noms peuvent être sensibles : vérifie le rapport avant de le partager et ne transmets pas de ressources complètes sans en avoir le droit.

Pour chercher des signatures à l'intérieur d'un fichier :

```bash
python -m hookymod.cli carve /chemin/vers/fichier --bytes 4194304 --limit 20
```

## Interpréter le résultat

- **NUP / NSO2** : un lecteur NUP expérimental existe ; il a été testé sur des paquets synthétiques, pas sur un paquet réel. Aucun déchiffrement ni clé n'est fourni. Voir [HOOKSHOT.md](HOOKSHOT.md).
- **SARC / zstd / BFRES / BNTX** : plusieurs variantes sont prises en charge, avec des limites importantes. Un dictionnaire zstd manquant ou une structure inconnue peut empêcher l'ouverture ; l'injection de modèle est bien plus restreinte que la lecture. Voir [OUVRIR_LES_ASSETS.md](OUVRIR_LES_ASSETS.md) et [HOOKYCREATOR.md](HOOKYCREATOR.md).
- **ZAR / CMB / CTEX**, **Unreal**, **Unity** ou **Godot** : l'inventaire peut reconnaître certaines signatures, mais cela ne signifie pas que leurs archives, modèles ou textures sont pris en charge par les éditeurs de HookyMod.

Les fichiers chiffrés ou malformés peuvent ne pas être reconnus. L'identification d'un magic enfoui ne prouve pas qu'il constitue une structure complète ; la lecture et toute réécriture doivent être vérifiées séparément.
