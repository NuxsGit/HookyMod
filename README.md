# HookyMod · HookyCreator

HookyMod is a local toolkit for inspecting and modifying game resources. HookyCreator is its visual workspace: it explores user-supplied models, textures, sounds, animations and WebM videos, prepares standard exports, and assembles working copies. The original Python/Tk explorer and command-line tools are still available.

**Bring your own legally obtained files.** No game dump or proprietary asset is required to run the test suite. Format support is partial: successful export or replacement does not guarantee visual accuracy or compatibility in game.

## Quick start

Python 3.10 or newer is required. Install the dependencies from the repository:

```bash
python -m pip install -r requirements.txt
python -m hookycreator /path/to/romfs --host 127.0.0.1 --open
```

The workspace opens at `http://127.0.0.1:8765/`. `--host 127.0.0.1` restricts the browser server to the local machine; do not expose it to an untrusted network. The project may also be a folder of extracted assets. Add `--work /path/to/work` to change where working copies and caches are stored.

For the original explorer, install Tkinter and run `python AssetViewer.py`. The native Tauri window and standalone backend are described in [docs/DESKTOP.md](docs/DESKTOP.md).

## Models: export and return

1. In **Edit → Models**, open a model to inspect it in the interactive 3D view (WebGL2, with a static PNG fallback). Decoded triangles are not reduced for display; oversized models may be refused by safety limits. Drag to orbit, use the wheel to zoom, and optionally enable wireframe or experimental FSR 1. Click **Export glTF / OBJ** to produce glTF 2.0 (`.gltf` + `.bin`), OBJ and MTL, available decodable PNGs, a notice and a report. The panel shows the absolute folder and offers a ZIP containing only that export. Exporting again replaces files with the same names in that folder: save your edits elsewhere.
2. Recognized explicit material texture references are resolved per material. When references are missing, name matching is heuristic and identified as such; missing or ambiguous references are not connected arbitrarily. `TEXTURES.md` and `RAPPORT_MODELE.txt` describe the limitations. Some BFRES 10.x rigid pieces are placed in their rest pose; unsupported variants are reported. The exported model does not reconstruct a rig, complete game pose, animations, morphs, shaders or other LODs. Check UVs and rendering in your 3D software.
3. After editing, use **Import a model**: select a triangulated OBJ, GLB or glTF 2.0 with any external `.bin` files, analyze it, and explicitly choose a compatible target. Only **geometry** is imported; target materials and textures are retained rather than those of the source.
4. Injection writes a copy under `hookycreator_work/assets/`, never the original project file. Only one shape and one LOD are accepted in certain BFRES 9.x structures, and existing buffers have fixed capacity. BFRES 10.x targets, skeletons, morphs, bounds requiring rewriting, unsupported attributes and other unverified structures are refused. It is normal to find no compatible target. Checks and rereading do not prove in-game safety.

Provided UVs and normals must be representable by the target. Missing normals are not invented in exports, although a preview may compute its own face normals. OBJ polygons must be triangulated before injection. Required glTF extensions, compressed buffers, deformations, sparse accessors and network access are not supported.

See the [workspace guide](docs/HOOKYCREATOR.md) for details on limitations, PNG maps and batch operations.

## Animations and sounds

- **Animations:** the experimental viewer reads supported BFRES 10.x FSKA Euler skeletal clips and matches bones by name to a compatible structured model. Select a clip and model in **Animations**, or choose a linked animation in the model inspector. Playback uses 30 frames per second by convention, not a rate verified from the file. The viewer retains up to eight skin influences; unsupported skeletons and curve types are refused. Curves can be exported as JSON, but animation and rig are **not** included in glTF/OBJ exports or injected into a game. Physics, materials and full game fidelity are not reproduced.
- **Sounds:** the experimental BWAV reader supports complete PCM16 or DSP-ADPCM mono/stereo and the existing mono Opus variant. It can export WAV, FLAC, MP3 and Ogg Vorbis, or remux supported Opus without re-encoding. Loop markers are not retained by the converted exports. A replacement must match the original sample rate and channel count; it creates an experimental PCM16 BWAV working copy even when the original was Opus or DSP. BARS banks, prefetch audio, other layouts and codecs, events and RESTBL are not rewritten. **In-game compatibility has not been validated.**

## Video (experimental)

The **Videos** view lists WebM files, streams them with seeking when the browser supports their codecs, and downloads the current file. Replacement copies a supplied WebM verbatim into the work folder, without transcoding or changing the original. The check covers only the EBML header, WebM DocType and Segment bounds; it does **not** validate tracks, codecs, duration, resolution or game compatibility. Streaming is limited to 1 GiB and replacement input to 32 MiB. NDVE data, separate audio and RESTBL are neither interpreted nor updated, so a valid WebM is not necessarily a usable game replacement.

## Other features

- Interface: light, dark and NewGenOldTendo themes, with French, English, Russian and Chinese UI translations in Settings. Some backend diagnostics and native-window messages may remain in French.

- BNTX textures: preview, export PNG and replace an image in a working copy for encodable formats. ASTC/BC7 encoding is not supported; some decoders require `texture2ddecoder`.
- SARC archives and `.zs`: inspect, extract and rebuild, using user-supplied zstd dictionaries. Missing dictionaries and incomplete data are reported.
- Session: navigation, filters, selection, inspector and drafts are saved per project. Modified copies and settings are separate from the session. An abrupt shutdown may lose the last save; see [performance and session recovery](docs/PERFORMANCE.md).
- Mod creation: assemble working copies and preparatory mob records. **Building deletes and recreates `mods/<name>` without a backup**; a mob record does not create a playable actor, and RESTBL is not updated automatically.

## Command line

```bash
# Diagnostics and inspection
python -m hookymod.cli doctor /path/to/romfs
python -m hookymod.cli scan /path/to/romfs
python -m hookymod.cli list archive.pack.zs --recursive

# Standard export; use --formats gltf or --formats obj for one format
python -m hookymod.cli export-model model.bfres -o model_export
python -m hookymod.cli export-model model.bfres -o model_export --no-textures

# Editable PNG / OBJ project and conservative rebuilding
python -m hookymod.cli translate unpack --romfs /path/to/romfs -p project
python -m hookymod.cli translate status -p project
python -m hookymod.cli translate pack -p project -o mods --mod-name MyMod

# Batch export with a report of errors and limitations
python -m hookycreator export-all /path/to/romfs -o export_all
```

The `export-model` CLI searches neighboring textures by name and links only an unambiguous single albedo to a single material; the association remains heuristic. Multi-image BNTX files and ambiguous output names are skipped and reported. For OBJ input it retains per-corner UV and normal indices, but does not read source MTL libraries or textures. Nonzero LOD requests for prebuilt BFRES 10.x are refused. Choose a dedicated output folder: the CLI may replace files with the same names. In the Tk explorer, glTF/OBJ export does not automatically link textures. The translator writes only supported geometry and reports refusals. Check `--help` for available commands and options.

The default scan limit is 200,000 recognized files. Incomplete scans and unreadable entries are reported; `--scan-limit 0` removes the limit but may use more memory.

## Tests and builds

```bash
python -m pip install -r requirements.txt pytest pytest-subtests pillow
python -m pytest -q
# JavaScript UI tests, without additional npm dependencies
for f in tests/*_test.js; do node "$f" || break; done
```

Format tests use synthetic fixtures. A synthetic software-browser WebGL2 / FSR smoke test is described in [docs/PERFORMANCE.md](docs/PERFORMANCE.md). Blender interoperability is tested optionally when Blender is installed, solely as a glTF/OBJ consumer. Additional native tests and Windows/Linux prerequisites are documented in [docs/DESKTOP.md](docs/DESKTOP.md). The Rust core accelerates some operations but does not yet replace the Python application; see [docs/RUST.md](docs/RUST.md).

## Documentation and provenance

- [Workspace, formats and limitations](docs/HOOKYCREATOR.md)
- [Native window and build](docs/DESKTOP.md)
- [Session recovery and performance](docs/PERFORMANCE.md)
- [Asset inspection and dictionaries](docs/OUVRIR_LES_ASSETS.md)
- [Provenance, references and third-party dependencies](docs/PROVENANCE.md)

The repository provides no game, keys or proprietary assets. Each user is responsible for checking their rights and applicable licenses. Third-party components retain their own attributions and licenses.

## Support the project

Solana address: `y8eeEqNTo7pANwRVvwsrNDu9gpPTFYW5FrP2oMUtfhZ`.
