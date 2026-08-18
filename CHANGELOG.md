# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every release carries the source archives GitHub generates plus a built wheel
and sdist, so `pip install` works straight from the downloaded file.

## [Unreleased]

Nothing yet.

## [1.1.0] - 2026-08-18

### Added

- **AI provenance detection**, read-only, in the context of article 50 of the
  EU AI Act. Three independent detectors, each behind an optional dependency
  that degrades away cleanly when it is missing:
  - **C2PA / Content Credentials** manifests (`pip install c2pa-python`):
    producing tool, signing certificate, signing time and validation state. A
    file with no manifest is the ordinary case, not an error.
  - **Invisible TrustMark watermark** (`pip install trustmark`), decoded only on
    demand from a button or the `--watermark` flag, since the first call
    downloads a 40 MB model. Never runs while browsing.
  - **Declarative metadata**: generator parameters and the IPTC
    `DigitalSourceType` field, labelled as unsigned. A source type found inside
    a signed C2PA manifest is credited to the manifest instead.
- Provenance section in the preview tab, `--provenance` on the `show` and
  `export` sub-commands, and a `provenance` block in the JSON exports.
- `provenance`, `c2pa` and `watermark` installation extras.

### Wording

The absence of a mark is never presented as evidence. No output describes an
image as authentic, genuine or not AI-generated. Three outcomes exist: signals
found, signals found but invalid, and no signal, which is inconclusive.

## [1.0.1] - 2026-08-18

### Fixed

- The interface refused to start when port 7860 was already taken, which is the
  normal state of a machine running a Stable Diffusion WebUI or ComfyUI. Without
  an explicit `--port`, the next free port is now used and the terminal says
  which one. An explicit `--port` stays honoured as given: a clash there is
  reported as a clear error instead of a traceback, and the launch no longer
  crashes if the port disappears between the check and the bind.

## [1.0.0] - 2026-08-18

First public release.

### Added

- **Reading**: EXIF, GPS, IPTC, XMP, PNG text chunks, JPEG and GIF comments, ICC
  profile, embedded thumbnail, and the settings written by image generators
  (AUTOMATIC1111, ComfyUI, NovelAI).
- **Preview** with the EXIF orientation applied, a summary card and a metadata
  table filtered by group, by full-text search and by sensitivity.
- **Sensitivity flagging** for entries that identify a person, a place or a
  device: GPS, serial numbers, author names, MakerNote, locations.
- **Export** to JSON, CSV, TXT, Markdown and HTML, for one image or a whole
  batch, as a single document or a ZIP archive of per-image files.
- **Clipboard** support: click table rows and copy them as `Tag = value`, TSV,
  JSON or values only.
- **Removal** by category or all at once, with a dry-run mode, a `-clean` copy,
  an output folder, or in-place rewriting with a `.bak` backup.
- **Lossless removal** for JPEG, PNG and WebP: the containers are rewritten at
  the segment and chunk level, leaving the compressed pixels byte-identical.
  Other formats go through Pillow and the report flags the re-encode.
- **Command line** with `ui`, `show`, `export` and `strip` sub-commands.
- **Core library** (`imgmetamanager.core`) usable without Gradio.
- **English and French** interface, detected from the system locale.
- Cross-platform launchers (`run.sh`, `run.bat`) and continuous integration on
  Ubuntu, Windows and macOS under Python 3.10 and 3.12.

### Safety rules

- Removing everything spares the ICC profile, which drives colour rendering.
- Removing EXIF also removes GPS and the embedded thumbnail, which share the
  same block.
- Structural TIFF tags are never removed.
- Overwriting an original requires an explicit confirmation and writes a backup.

[Unreleased]: https://github.com/mikecastrodemaria/ImgMetaManager/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/mikecastrodemaria/ImgMetaManager/releases/tag/v1.1.0
[1.0.1]: https://github.com/mikecastrodemaria/ImgMetaManager/releases/tag/v1.0.1
[1.0.0]: https://github.com/mikecastrodemaria/ImgMetaManager/releases/tag/v1.0.0
