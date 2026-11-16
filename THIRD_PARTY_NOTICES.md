# Third-party notices

HookyMod contains or uses third-party components under their own licenses.
The project-specific [HookyMod Noncommercial License](LICENSE) does not
replace or limit those separate licenses. In particular:

- The vendored Rust crates `ruzstd`, `twox-hash` and `hmac-sha256` retain
  their MIT or ISC terms and copyright notices. Their versions, origins and
  local changes are documented in [rust/vendor/README.md](rust/vendor/README.md),
  with the full notices in [ruzstd/LICENSE](rust/vendor/ruzstd/LICENSE),
  [twox-hash/LICENSE](rust/vendor/twox-hash/LICENSE), and
  [hmac-sha256/LICENSE](rust/vendor/hmac-sha256/LICENSE).
- The FSR 1 shader ports retain the MIT notices of their respective authors,
  included in [LICENSE-FSR1.txt](hookycreator/web/shaders/LICENSE-FSR1.txt).
- The NupExplorer reference and its MIT notice are documented in
  [docs/PROVENANCE.md](docs/PROVENANCE.md) and
  [NUP_EXPLORER_MIT.txt](docs/licenses/NUP_EXPLORER_MIT.txt).
- Other libraries, including Python, Tauri and their transitive dependencies,
  have their own copyright notices and license terms. The Python requirements
  are not fully pinned; actual dependencies differ by platform and build.

This file is an index of the notices identified in the source tree, **not a
complete inventory of every component in a standalone installer**. Before
publicly distributing a binary, the distributor must inventory the exact
Python wheels, native libraries, Rust crates and platform dependencies
actually shipped, include their required license texts and notices, and meet
any applicable source-code or other redistribution obligations. In particular,
native libraries bundled in wheels and components brought in by PyInstaller
and Tauri need platform-specific review. See [docs/PROVENANCE.md](docs/PROVENANCE.md).
