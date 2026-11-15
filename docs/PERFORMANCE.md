# Reprise et performances de HookyCreator

## Reprise automatique

L'interface enregistre au fil des modifications la vue et les onglets, le
contenu des champs de recherche et des formulaires (brouillons), la sélection,
la position de défilement, l'inspecteur et les options d'affichage. Les
réglages restent dans `settings.json` et les fichiers édités restent dans le
dossier de travail : l'état de navigation ne les remplace pas.

Chaque projet possède un instantané distinct dans
`<travail>/sessions/<empreinte-du-projet>.json`. Le travail est
`<projet>/hookycreator_work/` par défaut, ou le dossier indiqué par `--work`
en mode navigateur. La fenêtre native conserve le chemin du dernier projet
dans le dossier de configuration utilisateur de l'application, sans y enregistrer
de jeton ; elle le rouvre automatiquement s'il existe. Sinon le sélecteur de
dossier revient. Pour le navigateur, il faut relancer le serveur sur le même
projet et avec le même dossier `--work` éventuel ; le port peut changer.

Les écritures sont atomiques. En cas de sauvegardes arrivant dans le désordre
pour une même fenêtre, la plus récente est conservée. Si deux fenêtres ou deux
serveurs écrivent dans le même travail, la dernière écriture reçue l'emporte :
ce n'est pas une fusion collaborative. Les chemins d'assets enregistrés ne sont
rouverts que s'ils figurent encore dans l'inventaire ; un fichier de session
absent, tronqué, invalide ou d'une version inconnue est ignoré. Le scan incomplet
peut donc empêcher certains anciens onglets ou sélections d'être retrouvés.

La fermeture normale de la fenêtre native et le changement de projet attendent
la confirmation des sauvegardes de l'interface et des réglages. En cas d'échec
ou de délai dépassé, un changement de projet est annulé et la fermeture demande
confirmation. Les sauvegardes restent **best-effort** en cas de crash, arrêt
forcé, panne électrique ou fermeture brutale du navigateur. Les fichiers choisis
dans les boîtes d'import, les tâches d'export en cours, la lecture audio, le
plein écran et les positions/taille de fenêtre ne sont pas restaurés. La session
est limitée à 64 onglets, 5 000 éléments sélectionnés et 4 Mio ; les saisies
sont limitées à 2 048 caractères par champ. Une erreur d'écriture est affichée.
Ne pas considérer cette fonctionnalité comme une sauvegarde de sécurité du dump
ou du dossier de travail.

## Optimisations

- Les cartes metallic/roughness sont assemblées par canaux, sans boucle Python
  par pixel pour les tailles courantes ; le décodage PNG sans filtre évite aussi
  une passe inutile. Les résultats sont vérifiés pixel par pixel dans les tests.
- Les PNG et les `.blend` ne sont pas recompressés dans le ZIP. Le reste du ZIP
  utilise une compression rapide. L'export est toujours préparé dans un dossier
  isolé et le ZIP n'inclut pas les anciennes sorties de l'utilisateur.
- Un cache mémoire **borné à 64 Mio** réutilise les exports glTF/OBJ complets
  répétés tant que le modèle, les textures et l'inventaire n'ont pas changé. Les
  dates, taille et identité fournies par le système de fichiers participent à la
  clé ; une modification externe conservant toutes ces métadonnées pourrait ne
  pas être détectée. Les exports incomplets et les vrais `.blend` ne sont pas
  mis dans ce cache. Vider le cache ou rescanner l'invalide.
- Au démarrage, la première réponse d'inventaire ne parcourt plus les caches de
  vignettes pour produire leurs statistiques ; ces chiffres arrivent en arrière-plan.
  Les grandes réponses JSON sont compressées rapidement sur la boucle locale.

## Mesurer chez soi

Depuis la racine du dépôt, après installation de `requirements.txt` :

```bash
python tools/benchmark_workflow.py /chemin/vers/romfs Model/MonModele.bfres.zs
# Mesurer également l'appel réel à Blender (s'il est détecté) :
python tools/benchmark_workflow.py /chemin/vers/romfs Model/MonModele.bfres.zs --blend
# En utilisant un sidecar autonome déjà construit :
python tools/benchmark_workflow.py /chemin/vers/romfs Model/MonModele.bfres.zs --backend /chemin/vers/hookycreator-backend
```

Le script démarre le backend natif en local, mesure son lancement et sa réponse
d'état, puis le POST de préparation et le téléchargement complet du ZIP. Il
vérifie ensuite le ZIP et les références glTF **hors chronométrage**. Le travail
et le cache applicatif sont temporaires ; le cache du système d'exploitation
n'est pas vidé. Le premier export de chaque modèle n'est pas forcément totalement
froid : les modèles d'une même commande partagent le cache de textures. La fenêtre
Tauri, le rendu de l'interface et la fidélité en jeu ne sont pas mesurés. Avec
`--blend`, vérifier que la méthode affichée est `blender` : `script` signifie que
Blender est absent ou a échoué, donc qu'aucun nouveau `.blend` n'a été produit.

## Exemple de mesures

Dans un conteneur Linux x86-64 (2 vCPU Xeon 2,6 GHz, Python 3.11, sans noyau
Rust, `zstandard` et `texture2ddecoder` installés), quatre BFRES réels ont été
exportés en glTF/OBJ et ZIP avec cache applicatif temporaire. Les quatre textures
RGBA8 1024² disponibles pour cet essai sont **synthétiques** et peuvent être
appariées heuristiquement à plusieurs modèles ; ce n'est pas une validation des
textures du jeu. Le tableau inclut la préparation et le téléchargement HTTP :

| Modèle | Faces décodées | Cartes | Premier export | Répétition |
|---|---:|---:|---:|---:|
| Chat | 7 292 | 5 | 0,414 s | 0,029 s |
| Vache | 5 437 | 5 | 0,210 s | 0,018 s |
| Epona adulte | 113 432 | 5 | 2,444 s | 0,099 s |
| Wolfos | 118 676 | 0 | 2,649 s | 0,102 s |

Les ZIP ont été relus et les références glTF vérifiées ; les BFRES ont toutefois
émis des avertissements, donc le décodage et le rendu complets ne sont pas
garantis. Sur un inventaire distinct de 66 700 fichiers **vides synthétiques**,
le lancement du processus Python jusqu'à l'état HTTP décodé a pris 0,960 s. Ce
chiffre exclut la fenêtre Tauri et la validation des assets. Les caches OS
étaient chauds pour ces essais.

Ces mesures atteignent les objectifs côté backend pour **ces échantillons**,
pas une garantie de moins de 3 s pour tous les modèles ou de moins de 5 s pour
l'application native complète. La taille du dump, les textures réelles, la
vitesse du disque et de l'ordinateur, le système, les caches et Blender lui-même
peuvent changer sensiblement les temps. L'appel et l'interface Blender ne sont
pas inclus dans le tableau.
