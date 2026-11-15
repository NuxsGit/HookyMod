# HookyCreator — fenêtre Tauri v2

HookyCreator peut être construit comme une application à fenêtre native. Tauri
fournit la fenêtre, le choix de dossier et les téléchargements ; le moteur
existant reste un **sidecar Python autonome**, avec sa bibliothèque Rust et ses
décodeurs. Ce n'est pas une réécriture complète en Rust. Un installateur construit
correctement ne demande pas à l'utilisateur final d'installer Python ou Rust.
Blender reste nécessaire pour produire automatiquement un vrai `.blend`.

## Utilisation

1. À la première ouverture, choisis le dossier de ton jeu extrait (par exemple
   `romfs` avec `Pack/ZsDic.pack.zs`), ou un dossier de fichiers extraits. Il
   doit être accessible en écriture : le travail est créé dans
   `<projet>/hookycreator_work/`. Aux ouvertures suivantes, le dernier projet
   est rouvert automatiquement ; s'il n'existe plus, le sélecteur revient.
2. Utilise **Projet → Ouvrir un projet…** pour changer de dossier ; le projet
   courant reste ouvert si le suivant ne peut pas démarrer. L'interface du projet
   courant est enregistrée avant le changement. Un scan volumineux peut prendre
   du temps ; **Projet → Annuler l'ouverture** permet de l'annuler.
3. L'export d'un modèle indique le chemin absolu du dossier et démarre un ZIP.
   Dans la fenêtre native, les ZIP et sons sont enregistrés avec des noms uniques
   dans le dossier système **Téléchargements/HookyCreator/** (à défaut sous
   `~/Downloads/HookyCreator/`). L'application affiche le chemin après le
   transfert. Si nécessaire, utilise le lien de secours de l'export. Le mode
   navigateur utilise les réglages de téléchargement habituels du navigateur.
4. **Exporter en .blend** exige Blender installé ; indique son chemin dans
   Réglages s'il n'est pas détecté. Sinon le ZIP contient un glTF et un script,
   mais pas de nouveau `.blend`.

La vue, les onglets, les filtres, la sélection, l'inspecteur et les brouillons
sont sauvegardés par projet. Les réglages et les fichiers modifiés restent
séparés. Une fermeture normale attend la confirmation de sauvegarde ; si elle
échoue, l'application demande avant de quitter. Une interruption brutale reste
sans garantie. Détails, limites et mesures : [`PERFORMANCE.md`](PERFORMANCE.md).

Les formats non vérifiés ou non pris en charge ne sont pas montrés comme
utilisables. Les sons sont expérimentaux et en lecture seule : voir
[`HOOKYCREATOR.md`](HOOKYCREATOR.md) pour les formats exacts et les limites.
L'assemblage d'un mod peut remplacer sans sauvegarde `mods/<nom>` ; l'interface
demande confirmation et ne rend pas un mob automatiquement jouable.

## Construction Windows / Linux

Construire **sur le système et l'architecture visés** : PyInstaller ne
cross-compile pas le sidecar. Il faut Python 3.11 64 bits avec sa bibliothèque
partagée, les outils C/C++ de la plateforme, Rust/Cargo 1.97 ou plus récent,
Node.js et npm. Sous Windows, installer aussi les outils C++ de Visual Studio et
le runtime WebView2. Sous Linux, Tauri demande GTK3, WebKitGTK 4.1 et les
bibliothèques système associées. Exemple Debian/Ubuntu :

```bash
sudo apt-get install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev \
  libxdo-dev librsvg2-dev patchelf gstreamer1.0-plugins-base gstreamer1.0-plugins-good
```

Depuis la racine du dépôt :

```bash
python -m pip install -r requirements-desktop-build.txt
npm ci
python tools/build_desktop_backend.py
npm run tauri -- build -- --locked
```

Sous Windows, `py -3` peut remplacer `python`. Le script compile d'abord le noyau
Rust existant puis le sidecar avec PyInstaller et le place dans
`src-tauri/binaries/` avec le suffixe de la plateforme. La CLI Tauri produit les
paquets dans `src-tauri/target/release/bundle/` (par exemple NSIS sous Windows,
`.deb` sous Linux). La CI cible Windows x64 et Ubuntu 24.04 x64 ; le paquet Linux
n'est pas garanti sur les autres distributions ou versions de glibc. Ces builds
de test ne sont pas signés et Windows peut afficher un avertissement. Ces sorties
et les données de jeu restent hors Git. Ne pas empaqueter ni distribuer de dump
du jeu. Les bibliothèques et crates de Tauri nécessitent leur propre téléchargement ;
le fichier `src-tauri/Cargo.lock` fige leurs versions, mais seul le noyau `rust/`
dispose de crates vendorizées.

Pour un exécutable non empaqueté de développement :

```bash
npm run tauri -- build --debug --no-bundle
```

Le dossier `target/debug/` doit conserver à côté de l'exécutable la copie du
sidecar créée par Tauri. Reconstruis le sidecar après une modification Python ou
Rust. Une version macOS peut nécessiter les outils Apple et n'a pas été validée
par la CI décrite ici.

## Fonctionnement local et vérifications

Le sidecar n'écoute que sur `127.0.0.1`, sur un port aléatoire. Un jeton propre
à la session est transmis par tube à Tauri, puis échangé contre un cookie
`HttpOnly`/`SameSite=Strict` ; les routes sensibles vérifient également l'origine
et la méthode HTTP. Ce n'est pas une isolation contre un autre programme ayant
les mêmes droits sur la machine. Le sidecar s'arrête à la fermeture de la
fenêtre ou si son tube parent est fermé. La page chargée ne reçoit pas de
permission Tauri pour ouvrir arbitrairement des dossiers ou exécuter des commandes.

Le workflow `.github/workflows/desktop.yml` construit le sidecar et la fenêtre
sur Linux et Windows, puis exécute un parcours WebDriver avec des données
synthétiques (session, fichiers masqués, export ZIP, sélection, son, fermeture
et reprise du projet). Elle extrait le `.deb` ou installe silencieusement le NSIS, vérifie la présence des
exécutables et reteste le backend livré. Les installateurs de test sont conservés
comme artefacts de CI lorsque le workflow réussit. Le dialogue de choix lui-même
n'est pas automatisé : le mode debug
accepte une racine de fixture par variable d'environnement pour ce test, option
absente des builds release. Un autre test ouvre le `.blend` avec un vrai Blender
sur Linux.

Pour lancer ces vérifications localement après construction :

```bash
python tools/smoke_desktop_backend.py src-tauri/binaries/hookycreator-backend-<target-triple>
cargo test --locked --manifest-path src-tauri/Cargo.toml
# Après installation de tauri-driver et du WebDriver natif, sous un écran ou Xvfb :
python tools/smoke_desktop_webview.py
python -m pytest -q tests/test_real_blender.py
```

Sous Windows, ajouter `.exe` au nom du sidecar. Le test Blender est ignoré si
Blender n'est pas installé ; le WebDriver exige une version de WebKitWebDriver ou
Microsoft Edge WebDriver compatible avec la webview du système.
