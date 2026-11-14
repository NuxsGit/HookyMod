# Sources tierces Rust incluses localement

Ces sources Rust sont incluses pour permettre une compilation sans réseau.
Elles ne contiennent aucun fichier du jeu.

* `ruzstd` **0.9.1**, tag `v0.9.1`, commit
  `ed658571b404784e9b2bfda0de630a7c35eff72d` :
  <https://github.com/KillingSpark/zstd-rs/tree/v0.9.1/ruzstd> ; licence MIT,
  copyright Moritz Borcherding, voir `ruzstd/LICENSE`.
* `twox-hash` **2.1.2**, tag `v2.1.2`, commit
  `bc5bb80b4857707e0372d2386157b1d31e4441d3` :
  <https://github.com/shepmaster/twox-hash/tree/v2.1.2> ; licence MIT,
  copyright Jake Goulding, voir `twox-hash/LICENSE`. XXH64 sert à vérifier la
  somme de contrôle facultative des trames zstd.
* `hmac-sha256` **1.1.15**, tag `1.1.15`, commit
  `3f2ad8a97d4d0e46a6eaf7d3323f25aef6d095bd` :
  <https://github.com/jedisct1/rust-hmac-sha256/tree/1.1.15> ; licence ISC,
  copyright Frank Denis, voir `hmac-sha256/LICENSE`. SHA-256 sert à vérifier
  l'intégrité des en-têtes et entrées NUP quand leurs empreintes sont fournies.

Les fichiers README et les sources proviennent des tags amont. Dans le README
`ruzstd`, deux espaces de fin de ligne ont été retirés ; les autres corrections
locales de `ruzstd` concernent le code :

* les deux bits réservés des modes de séquence sont vérifiés (RFC 8878
  §3.1.1.3.2.1). Sans ce contrôle, certaines trames corrompues étaient
  acceptées, y compris une trame avec checksum valide ;
* des contrôles de taille sont effectués **avant** l'allocation et l'écriture
  des blocs, littéraux, séquences et tables d'entropie. Ils sont adaptés des
  propositions amont [#129](https://github.com/KillingSpark/zstd-rs/pull/129)
  (commits `dd9ac2ce9b8b5dfc400e8627ce22d4b7d1e8b11e`,
  `18ab951025feaff6729877dc73ef5cf655e68bce`) et
  [#130](https://github.com/KillingSpark/zstd-rs/pull/130)
  (commit `e3bdc835577466d451c1518216da2985272c2fec`), sous la même licence
  MIT. Ces propositions n'étaient pas encore fusionnées lors de l'intégration.

Les manifests `Cargo.toml` sont réduits aux fonctionnalités utilisées (`std`,
`hash`, `xxhash64`) et emploient des dépendances locales. Les manifests d'origine
sont conservés sous `Cargo.toml.upstream`. Les fonctionnalités amont qui exigent
d'autres crates ne sont pas activées ici.
