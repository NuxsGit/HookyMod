# Moteur Rust de HookyMod

Il s'agit de la **première étape** de la migration, pas encore d'une application
entièrement réécrite. Elle accélère les parties les plus coûteuses sans retirer
les fonctions ni remplacer les formats existants.

## Compiler sur Windows

Il faut Python, Rust et Cargo dans le `PATH`. Ferme HookyCreator avant de
compiler (Windows verrouille une DLL en cours d'utilisation).

1. Dans le dossier du projet, double-clique sur **`Compiler_Rust.bat`**. Ou,
   dans PowerShell :

   ```powershell
   py -3 -m pip install -r requirements.txt
   py -3 tools\build_rust.py
   ```

2. Relance HookyCreator comme d'habitude :

   ```powershell
   py -3 -m hookycreator C:\chemin\vers\romfs --open
   ```

3. Ouvre **Réglages → Moteur & performances**. « Moteur Rust : actif » confirme
   que la DLL a bien été chargée. En cas d'erreur, le motif est indiqué et le
   moteur Python prend le relais ; le reste de l'application reste utilisable.

La bibliothèque doit être compilée pour la **même architecture** que Python.
Le script vérifie son chargement avant de l'installer dans
`hookymod/_native_lib/`. Ce dossier est local et ignoré par Git : il faut donc
recompiler après une nouvelle copie du dépôt ou un changement du code Rust.
La version actuelle utilise l'**ABI 6** : si une ancienne DLL est encore
présente, l'application la refuse et fonctionne en Python jusqu'à recompilation.
Les dépendances Rust (`ruzstd` et `twox-hash` sous licence MIT,
`hmac-sha256` sous licence ISC) sont incluses dans
[`rust/vendor/`](../rust/vendor/README.md) :
**aucune crate ni maturin à télécharger**. Le runtime Python et les dépendances
de `requirements.txt` restent nécessaires à HookyCreator ; l'outil en ligne de
commande Rust décrit ci-dessous fonctionne, lui, sans Python.

Linux/macOS : `python3 tools/build_rust.py`, puis redémarrer l'application.
La compilation du noyau exige Rust **1.87 ou plus récent**. La fenêtre Tauri v2
et son sidecar autonome se construisent séparément, avec des dépendances
supplémentaires : voir [`DESKTOP.md`](DESKTOP.md).

## Périmètre et compatibilité

| Travail | Aujourd'hui |
|---|---|
| Disposition des textures BNTX X1 / Orin : lecture et écriture | Rust, repli Python si nécessaire |
| BC1/BC2/BC3/BC4/BC5 en lecture | Rust **si `texture2ddecoder` est absent** ; sinon son chemin C existant reste prioritaire (BC2 utilise le repli Python) |
| Encodage BC1/BC3/BC4/BC5, pixels non compressés et mips | Rust pour les formats déjà encodables, repli Python ; les octets produits restent identiques |
| BC6 / BC7 / ASTC en lecture | `texture2ddecoder` reste nécessaire |
| Rastérisation 3D, profondeur et réduction anticrénelée | Rust ; projection, éclairage et sélection des faces restent en Python |
| Table de couleurs des albédos pour les aperçus | Rust (jusqu'à 64 millions de pixels et une grille de 256²), repli Python ; décodage BNTX partagé sans détour PNG |
| Tables de formes et d'attributs BFRES 10.x | Lecture structurée en Python quand reconnue ; recherche heuristique Rust possible en repli, avec validation Python |
| Indices et positions BFRES 10.x | Lecture structurée en Python ; accélération Rust sur certains gros tampons du repli heuristique |
| Filtre des triangles BFRES 10.x | Rust pour les gros lots compatibles ; mêmes seuils et mêmes indices que le filtre Python |
| Décompression Zstandard | Rust via `ZstdDicts` lorsque compatible, sinon repli `zstandard` ; certaines lectures directes restent en `zstandard` |
| Compression Zstandard | `zstandard` dans l'application ; sans dictionnaire seulement dans l'outil Rust autonome |
| Lecture et reconstruction SARC | Rust dans l'application pour les entrées compatibles, avec repli Python ; objets, avertissements et archives imbriquées conservés |
| Lecture et reconstruction RESTBL | Rust pour les tables compatibles, avec repli Python ; le dictionnaire Python, les opérations de modification et le CRC restent dans l'application |
| Paquets NUP | Liste, vérification et extraction d'une entrée en Rust ; extraction complète et application restent en Python |
| Serveur, navigation, lecture BFRES complète, Blender, mobs et exports | Python, comme avant |

Le moteur ne modifie pas la géométrie exportée autrement que par les contrôles
BFRES existants. Ses entrées sont vérifiées et les cas non pris en charge
reviennent au chemin Python. La recherche des formes BFRES 10.x conserve les
heuristiques existantes : elle ne constitue ni un parseur BFRES complet ni une
garantie que tous les modèles seront correctement interprétés. Elle accepte
1 Gio d'entrée et 64 Mio de métadonnées au maximum (250 000 formes) ; au-delà,
ou si les règles Python de formats sont modifiées, le chemin Python prend le
relais. Les tampons de conversion géométrique sont bornés ; les réglages mémoire
des aperçus restent appliqués. L'injection OBJ/glTF/GLB en BFRES 10.x n'est
**pas** rendue possible par ce portage : elle reste refusée ; seules certaines
structures BFRES 9.x sont réécrites après contrôle.

## Commandes Rust autonomes (expérimental)

La compilation produit aussi `rust/target/release/hookymod-rs` (`.exe` sous
Windows). Cet exécutable est indépendant de Python. Par exemple, depuis
PowerShell, après avoir adapté les chemins :

```powershell
# Lire directement un SARC .zs avec les dictionnaires de ce dump
.\rust\target\release\hookymod-rs.exe --romfs C:\jeu\romfs sarc list C:\jeu\romfs\Pack\Animal.pack.zs --json

# Décompresser un modèle dans un nouveau fichier, sans modifier le dump
.\rust\target\release\hookymod-rs.exe --romfs C:\jeu\romfs zs decompress C:\jeu\romfs\Model\MonModele.bfres.zs .\MonModele.bfres

# Lire une table de tailles ou créer une copie SARC modifiée
.\rust\target\release\hookymod-rs.exe restbl info C:\jeu\romfs\System\Resource\ResourceSizeTable.rsizetable.zs --json
.\rust\target\release\hookymod-rs.exe --romfs C:\jeu\romfs sarc replace C:\jeu\romfs\Pack\Animal.pack.zs chemin/dans/pack fichier.bin .\modifie.pack

# Explorer un NUP et extraire une seule entrée vers un chemin explicite
.\rust\target\release\hookymod-rs.exe nup info C:\jeu\dump.nup
.\rust\target\release\hookymod-rs.exe nup list C:\jeu\dump.nup --only romfs --json
.\rust\target\release\hookymod-rs.exe nup verify C:\jeu\dump.nup --only romfs --deep
.\rust\target\release\hookymod-rs.exe nup extract C:\jeu\dump.nup romfs/Model/MonModele.bfres.zs .\MonModele.bfres.zs
```

`--dict chemin\ZsDic.pack.zs` charge aussi le pack, ou un dictionnaire isolé.
Les dictionnaires à en-tête zstd utilisent leur id interne ; les dictionnaires
bruts connus sont identifiés par leur nom. Pour une trame qui n'annonce aucun id
mais qui utilise un dictionnaire implicite, `--force-dict <id>` le sélectionne
explicitement (`0` pour un dictionnaire brut de nom inconnu). **Il faut être sûr
d'utiliser le bon dictionnaire** : en l'absence de checksum dans la trame, son
contenu ne peut pas être authentifié. Les trames concaténées et skippables sont
lues, les tailles annoncées et les checksums présents sont vérifiés. Un flux
sans checksum ne permet pas de détecter toutes les corruptions : vérifier sa
provenance et, si possible, une empreinte externe.

Pour les paquets NUP, `info` lit l'en-tête et la TOC, sans charger tout le
paquet ; `list` et `extract` utilisent le premier titre par défaut et acceptent
`--title <index>`. `verify` contrôle tous les titres par défaut : sans `--deep`,
seules les métadonnées sont vérifiées ; avec `--deep`, les entrées sont lues,
décompressées si nécessaire et leurs empreintes SHA-256 présentes sont vérifiées.
Une empreinte absente ne permet pas de contrôler l'intégrité. Les fichiers `.zs`
déjà présents dans le romfs restent compressés à l'extraction. L'extraction vise
un **seul fichier explicite**, jamais un chemin créé automatiquement à partir des
noms contenus dans le paquet. Ce lecteur NUP est testé avec des données
synthétiques ; il n'a pas encore été vérifié sur un paquet réel.

Les commandes `sarc add/replace/remove` et `restbl ensure` produisent une
**sortie non compressée**, distincte de l'original ; elles refusent l'extension
`.zs`. L'extraction d'une entrée SARC copie ses octets tels quels. La commande
`zs compress fichier sortie.zs --no-dict` permet une compression générique sans
dictionnaire, relue après écriture en mémoire ; elle ne préserve ni l'id du
dictionnaire d'origine ni nécessairement la taille dans l'en-tête. Sa
compatibilité avec le jeu n'est pas garantie. **La recompression avec
dictionnaire n'est pas encore portée** : conserver les outils Python pour créer
un mod compressé. Aucune commande Rust n'écrase un fichier existant.

La lecture est bornée à 1 Gio d'entrée et 512 Mio de sortie par défaut ; pour
NUP, ces limites s'appliquent à chaque entrée (la taille du paquet entier n'est
pas limitée à 1 Gio et la TOC est limitée à 64 Mio).
`--max-output <octets>` (avant la commande) peut relever cette dernière limite
jusqu'à 2 Gio sur une architecture compatible. La fenêtre zstd est limitée à
128 Mio, chaque dictionnaire à 16 Mio et leur collection à 64 Mio ; RESTBL
accepte jusqu'à quatre millions d'entrées hachées. Ces bornes ne sont **pas** un
plafond de mémoire total du processus, et peuvent refuser des fichiers valides
plus volumineux. L'outil refuse les trames tronquées ou aux contrôles présents
incorrects ; il ne garantit pas la détection de toutes les corruptions ni la
validité de formats internes qu'il ne lit pas. Dans l'application, si la limite
native est atteinte, le repli `zstandard` conserve ses propres limites. Pour
HookyCreator, les lectures via `ZstdDicts` exigent des frames complètes ; les
autres utilisateurs de ce repli conservent son comportement historique.

Pour SARC dans l'application, la lecture native accepte au plus 1 Gio en
entrée et 128 Mio de métadonnées ; la reconstruction native est limitée à
512 Mio de contenu en entrée et de sortie. Les noms et les entrées sont aussi
bornés. Pour RESTBL, la lecture native accepte au plus 1 Gio et quatre millions
d'entrées hachées, avec 64 Mio de métadonnées ; la reconstruction native est
limitée à 512 Mio. Les doublons conservent leur première position et leur
dernière valeur, comme le dictionnaire Python ; la queue des entrées nommées
est préservée. Ces limites ne constituent pas un plafond de mémoire du
processus : les fichiers plus grands, les types Python inhabituels et les cas
refusés par Rust suivent le chemin Python historique, qui a ses propres
caractéristiques.

## Vérifier et mesurer

```powershell
cargo test --manifest-path rust/Cargo.toml --offline --locked --all-targets
py -3 -m pip install pytest
py -3 -m pytest -q tests/
py -3 tools\benchmark_rust.py
```

La suite compare Rust au repli Python sur des textures synthétiques X1/Orin,
des fichiers tronqués, le décodage et l'encodage BCn, les mips, des aperçus 3D
et des BFRES synthétiques. Les tables de formes BFRES sont également comparées
au Python sur des entrées tronquées ou mutées, les doublons de tampons, les
formats connus et les règles modifiées. Elle compare aussi la lecture et la
reconstruction SARC natives au Python historique, y compris les archives
imbriquées, les mutations, les avertissements, les types inhabituels et les
appels concurrents. Elle compare aussi la lecture/reconstruction RESTBL, les
doublons et les queues opaques. Les tests vérifient les BNTX, SARC et RESTBL
construits, les NUP synthétiques (arborescences, extractions, empreintes et
erreurs), et comparent la décompression/compression Zstandard indépendante
avec `zstandard` (dictionnaires, checksums, trames multiples et erreurs). Les
tests CLI exigent que l'exécutable release ait été compilé auparavant. La CI
`.github/workflows/rust-core.yml` est prévue pour compiler et exécuter les suites
avec et sans Rust sous Linux et Windows. Pour forcer le repli localement :

```powershell
$env:HOOKYMOD_DISABLE_RUST = "1"
py -3 -m pytest -q tests/
Remove-Item Env:HOOKYMOD_DISABLE_RUST
```

`benchmark_rust.py` compare les noyaux sur des données synthétiques, vérifie
leur égalité et affiche les temps sur **ta** machine. Les conversions vers les
objets Python ont un coût : notamment, la reconstruction RESTBL peut être
comparable ou plus lente qu'avant. Pour Zstandard, la référence est la
bibliothèque C du module Python : le décodeur Rust peut aussi être plus lent et
ne doit pas être présenté comme une accélération systématique.
Pour mesurer également
la lecture d'un vrai modèle, sans rien modifier dans ton dump :

```powershell
py -3 tools\benchmark_model.py C:\chemin\vers\romfs Model\NomDuModele.bfres.zs
```

Ce second test crée un dossier de travail temporaire, mesure séparément le scan,
l'ouverture avec un cache HookyCreator vierge, puis la réouverture. Les temps
comprennent la lecture, les textures associées et l'aperçu côté serveur, mais
**pas le navigateur** ; le cache du système d'exploitation n'est pas vidé.
L'objectif de trois secondes doit aussi être vérifié dans l'interface réelle.

## Suite du portage

Porter et tester progressivement le parsing BFRES/BNTX, les exports, la
recompression avec dictionnaire et les autres opérations sur les conteneurs,
puis le serveur et les outils de création de mods. Conserver les vérifications
de parité et les refus explicites à chaque étape,
notamment pour les formats qui ne peuvent pas encore être reconstruits sans
risquer d'inventer de la géométrie.
