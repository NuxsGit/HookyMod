# `.hooky` launcher integration — experimental developer draft

HookyMod's owns the `.hooky` format. This draft is intended for discussion with launcher developers, beginning with Link's Tool Box and later the official Longshot launcher. It distinguishes what the repository implements from what remains to be built. The [French reference](HOOKY_FORMAT.md) includes the full design-project schema and a more detailed integration plan.

**Status:** the current HookyCreator source removes the design workspace, mob records and mod assembly from the app; the experimental code is retained under `modmaker/` without a standalone interface. The published 11.26.2 installers still contain the previous workspace. A source-only reference tool can package, validate and extract already prepared mod files. Neither component converts visual blocks into game content. Direct `.hooky` import by a launcher and compatibility in Longshot have **not been verified**. The contract below may change before launcher developers agree on a stable version; no particular launcher version has been specified.

The old source command `python -m hookycreator.modpackage` remains a compatibility alias. Neither module is shipped in the current desktop backend.

## Dispatch by manifest, not extension

Both variants use ZIP and have `manifest.json` at the root. Validate `format`, integer `version` and `kind` before handling a file; reject unsupported identifiers, versions and unexpected fields.

| `format` | `version` | `kind` | Meaning |
| --- | --- | --- | --- |
| `org.hookymod.workspace` | `1` | `design-workspace` | Studio project containing `workspace.json` and `playable: false`. It is **not a mod**. The historical studio exported and imported it; its reader is retained in source. |
| `org.hookymod.mod` | `1` | `romfs-overlay` | Proposed mod package carrying complete, already prepared files under `romfs/`. The reference CLI can pack, inspect and unpack it; the historical studio did not import this variant. |

A launcher must not install a workspace as if it were a mod. The full workspace schema and its existing validation are documented in the [French reference](HOOKY_FORMAT.md#1-projet-de-conception-existant--orghookymodworkspace-v1).

## `org.hookymod.mod` v1 wire contract

An archive contains exactly one `manifest.json` plus 1–256 ordinary files whose exact names are declared in the manifest and start with `romfs/`. No explicit directory entries or extra files are allowed, and the format defines no installation script or command hooks. A file may be empty.

The ZIP is a classic single-volume archive with no prefix or archive comment. Local headers, compressed data, optional classic data descriptors and the central directory must be contiguous, with no undeclared bytes; local headers and descriptors must agree with the central directory. Archives requiring ZIP64, encrypted entries, links or special types declared by Unix attributes, and DOS directory attributes are rejected. ZIP metadata must never be used to create a link. Only STORED and DEFLATE are accepted. ASCII ZIP names may omit the UTF-8 flag; non-ASCII ZIP names must be UTF-8 with the flag set. Limits are **36 MiB archive, 512 KiB central directory, 512 KiB manifest, 16 MiB per uncompressed payload and 32 MiB total uncompressed payload**. Count entries and enforce limits before constructing an unbounded ZIP entry list and while decompressing; do not trust declared sizes alone. Each DEFLATE stream must end exactly and produce the declared size; reject hidden extra output even if a valid prefix matches the declared CRC.

These are provisional conservative limits for the in-memory reference implementation and may exclude large mods. They need evaluation with launcher developers and authorized examples before stabilization, potentially with streaming and a versioned contract change.

`manifest.json` is UTF-8 without a BOM. All keys shown below are mandatory; no extra or repeated keys are allowed at any object level. Non-JSON constants such as `NaN` and `Infinity` are invalid. This valid example describes the 13 bytes consisting of `Hello Hooky!` followed by LF, **not an actual playable mod**:

```json
{
  "format": "org.hookymod.mod",
  "version": 1,
  "kind": "romfs-overlay",
  "id": "org.hookymod.example",
  "name": "Synthetic example",
  "mod_version": "0.1.0",
  "author": "",
  "description": "Container demonstration only, not a playable mod.",
  "target": {
    "runtime": "longshot",
    "platform": "pc",
    "runtime_version": "",
    "game_version": ""
  },
  "files": [
    {
      "path": "romfs/Example/hello.txt",
      "size": 13,
      "sha256": "a6bf8d151c47db988d94987901e0e3f6c1dc20e3f00c5d451db96a790e7da041"
    }
  ]
}
```

- `id`: 1–64 ASCII characters, lowercase letters, digits, `.`, `_` and `-`; first and last characters must be letters or digits. It identifies a mod, not permission to overwrite an existing installation.
- `name` and `mod_version`: strings, nonempty after trimming, at most 96 and 64 Unicode characters. `mod_version` is a free-form label, not necessarily SemVer.
- `author` and `description`: strings, possibly empty, at most 96 and 1,200 Unicode characters.
- `target`: exactly `runtime: "longshot"`, `platform: "pc"`, `runtime_version` and `game_version`. Both version fields are strings of at most 96 characters and may be empty. They are informational labels, not automatically checked compatibility constraints.
- All metadata strings reject U+0000–U+001F except tab, carriage return and line feed, and reject lone Unicode surrogates. Treat them as untrusted display text, not shell commands or filesystem paths.
- `files` contains exact objects `{path, size, sha256}`. `size` is an integer byte count after decompression, not a boolean; `sha256` is the 64-character lowercase hexadecimal digest of those exact bytes. The ZIP names and manifest paths must match exactly, with no missing, extra or duplicate files. File order has no meaning.

### Path rules

Paths are relative UTF-8 NFC strings using `/`, starting with exactly `romfs/`. The complete path is at most 512 UTF-8 bytes, at most 24 components **including `romfs`**, and each component at most 255 UTF-8 bytes. Reject empty components, `.`, `..`, components beginning with `.`, components ending in ASCII space or `.`, Unicode control/surrogate characters, and `< > : " \ | ? *`. Reject Windows device names even with an extension or ASCII spaces before that extension: `CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`, `CLOCK$`, `COM1`–`COM9`, `LPT1`–`LPT9`, including superscript digits `¹`, `²` and `³`.

Reject duplicate paths, file/directory collisions and collisions on **any prefix** after Unicode `casefold` followed by NFC normalization; for example `romfs/Dir/a` and `romfs/dir/b` conflict. These conservative rules improve portability but do not prove a game can use a path. The reference reader uses its Python runtime's Unicode tables, which can vary between versions; pin and test a common Unicode version before stabilization, especially for newly assigned characters.

## Intended overlay semantics and limitations

The package carries the author's exact files; it does not copy game files automatically. If a compatible loader overlays a matching existing game path, the packaged file can replace that resource. An additional path can only add a resource if the game has a valid reference and knows how to load it. Packaging alone does not create a game actor, level, placement, cutscene or behavior. Deletions, binary patches, structured merges, dependency resolution and executable installation hooks are not defined by v1.

SHA-256 and ZIP CRC detect corruption, not authorship or safety: an attacker can supply malicious bytes and matching hashes. The format does not include a signature. A launcher must never execute package content, and game-specific payloads themselves must also be treated as untrusted.

## Reference CLI

Run from the repository source tree with Python 3.10+; the module uses the standard library. This reproducible example uses synthetic text only:

```bash
python -c "from pathlib import Path; p=Path('exemple-mod/romfs/Example/hello.txt'); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'Hello Hooky!\n')"
python -m modmaker.modpackage pack --romfs exemple-mod/romfs --output exemple.hooky --id org.hookymod.example --name "Synthetic example" --mod-version 0.1.0 --description "Container demonstration only, not a playable mod."
python -m modmaker.modpackage inspect exemple.hooky
python -m modmaker.modpackage unpack exemple.hooky --destination exemple-extrait
```

`inspect` validates all bytes without extraction; `unpack` revalidates then creates a **new** destination containing `romfs/`. Neither installs or launches anything. Existing destinations are never replaced, and the output archive must be outside the source `romfs`. Failed writes can leave a partial file or folder to inspect and remove manually. Collection rejects links and Windows reparse points and scans at most 8,192 source entries. These commands assume a trusted local filesystem with no hostile concurrent process; they are not a local-process sandbox.

Only select prepared mod files you have the right to share, not an entire game installation. Data explicitly selected by the author is included unchanged and may contain private or protected information.

For a Python prototype, the reference API validates the complete archive in memory and returns its manifest and a `{path: bytes}` dictionary without extracting or installing:

```python
from pathlib import Path
from modmaker.modpackage import MAX_ARCHIVE_BYTES, PackageError, read_package

try:
    with Path("exemple.hooky").open("rb") as source:
        archive = source.read(MAX_ARCHIVE_BYTES + 1)
    manifest, files = read_package(archive)
    # Present untrusted metadata and paths, then ask for confirmation before
    # any launcher-specific installation action.
except (PackageError, OSError) as error:
    print("Package rejected:", error)
```

A launcher may implement its own bounded reader in another language. This reference API does not validate game-specific content or install it.

## Suggested launcher flow — not yet implemented or validated

1. Bound archive bytes and central-directory parsing; dispatch by `format`/`version`/`kind`. Reject unknown versions, malformed JSON, duplicates, unsafe paths, links, unexpected entries and oversized input.
2. Read every entry under limits and verify ZIP CRC, uncompressed size and SHA-256 **before installation**. Never blindly call a general-purpose ZIP extraction function.
3. Present the metadata, unverified origin, file list and conflicts. Let the user explicitly select and confirm a test installation; no version field proves compatibility.
4. Create ordinary files in a new private staging folder without following links or applying archive permissions. Avoid silent overwrites; handle collisions, backups, concurrency, atomic publication where feasible and rollback. The reference CLI only extracts to a new folder; it does not implement a transactional installer.
5. Pass the resulting folder containing `romfs` to the launcher's own mod management. Respect its activation, ordering, conflict and resource-size-table mechanisms; do not automatically launch the game.

At public Link's Tool Box commit [`a49cd941c3f24a27e2143f12e18f1c89f8b86d2f`](https://github.com/losava/Links-Toolbox/tree/a49cd941c3f24a27e2143f12e18f1c89f8b86d2f), the inspected code describes importing folders containing `romfs`, mod ordering/activation and rebuilding a size table when saving. A future `.hooky` reader could feed a validated folder into that workflow; this does **not** assert an existing `.hooky` API or in-game support. Test on a separate installation, back up saves and mods, and verify the actual target's behavior.

Visual-block compilation still requires a versioned source representation with stable IDs, typed resource references and placements; a game-specific adapter that rejects unsupported blocks and produces the required files and registrations; and an actual behavior interface if the target needs one. For example, an interaction that checks a flag and shows a message only works if the target genuinely exposes or compiles both operations. The local simulator is not such an interface. These are future design proposals, **not v1 manifest fields or an existing Longshot API**, and they need in-game tests for replacements and truly referenced additions.

The repository's [license](../LICENSE) applies to its documentation and reference code and restricts commercial use and distribution. Discuss any proposed collaboration and third-party distribution with the rights holders. No rights to Longshot, game assets or third-party trademarks are granted.
