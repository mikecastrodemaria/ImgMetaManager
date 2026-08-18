# ImgMetaManager

Read, export, copy and remove the metadata of your images. A local Gradio web
interface plus a command line, running the same code on Linux, Windows and macOS.

For JPEG, PNG and WebP, metadata is stripped at the container level: the
compressed pixels are copied byte for byte, so nothing is re-encoded and nothing
is lost.

*[Version française du README](README.fr.md)*

---

## Install

Python 3.10 or newer is required.

### Quickest path

```bash
git clone https://github.com/mikecastrodemaria/ImgMetaManager.git
cd ImgMetaManager
```

Then, depending on your system:

| System | Command |
| --- | --- |
| Linux, macOS | `./run.sh` |
| Windows | double-click `run.bat` |

The script creates the virtual environment, installs the dependencies on first
run, starts the local server and opens `http://127.0.0.1:7860`.

### As a Python package

```bash
python -m pip install -e .
imgmetamanager
```

To read iPhone photos (HEIC/HEIF) and AVIF files:

```bash
python -m pip install "imgmetamanager[heif]"
```

### Interface language

The interface ships in English and French. It follows your system locale and
falls back to English; `--lang en` or `--lang fr` overrides it, as does the
`IMM_LANG` environment variable.

---

## The interface

### Loading images

Two entry points, which can be combined:

- **Files** tab: drag and drop one or more images
- **Folder** tab: type a local path (`/home/me/Photos`, `C:\Users\me\Pictures`,
  `/Users/me/Pictures`), with or without sub-folders

The folder mode works on the real files of the machine. It is the one that lets
you overwrite originals.

### Reading

The **Preview and metadata** tab shows the image, a summary (format, dimensions,
size, GPS position, detected AI generator) and a table of every entry found,
grouped by family:

| Group | Contents |
| --- | --- |
| File | name, folder, size, modification date, SHA-256 |
| Image | format, dimensions, colour mode, resolution, frame count |
| EXIF | camera, lens, exposure, dates, serial numbers |
| GPS | degrees/minutes/seconds, decimal position, map link |
| IPTC | caption, keywords, credit, location, author |
| XMP | Adobe packet flattened into key/value pairs |
| PNG text | tEXt, zTXt and iTXt chunks |
| Generative AI | prompt, negative prompt, sampler, seed, model (A1111, ComfyUI, NovelAI) |
| Comments | JPEG COM segments, GIF comment |
| ICC profile | description and size of the colour profile |
| Embedded thumbnail | EXIF thumbnail, which may show the image before cropping |

Three filters drive the table: per-group checkboxes, a full-text search over
tags and values, and a "sensitive data only" toggle.

An entry counts as sensitive when it identifies a person, a place or a device:
GPS, serial numbers, author name, MakerNote, city, contact.

### Exporting

The **Export** tab produces a downloadable file for the displayed image or for
the whole batch:

- **JSON**: full grouped structure, for automated processing
- **CSV**: one row per entry, UTF-8 with a byte-order mark so Excel opens it cleanly
- **TXT**: readable report, sensitive entries starred
- **Markdown**: one table per group, ready to paste into documentation
- **HTML**: self-contained page, light and dark themes

Tick "one file per image" to get a ZIP archive instead of a single document.
Tick "apply the filters" to export only what the table currently shows.

JSON and CSV keep a stable English schema whatever the interface language, so
scripts that parse them keep working. TXT, Markdown and HTML follow the
selected language.

### Copying

Click a table row and it joins the **Copy items** area. The "add every visible
row" button takes the whole filtered table at once. Four layouts:

```
Tag = value        Make = ACME
TSV (spreadsheet)  EXIF	Make	ACME
JSON               { "Make": "ACME" }
Values only        ACME
```

The copy icon on the text box sends the content to the system clipboard.

### Removing

The **Remove** tab pre-selects the categories present in the displayed image.
Three destinations:

| Destination | Effect |
| --- | --- |
| New file | writes `name-clean.ext` next to the original, which stays untouched |
| Output folder | writes the cleaned files into the folder of your choice |
| Overwrite the original | rewrites the file after an explicit confirmation and a `.bak` backup |

The "dry run" box lists what would be removed without writing anything. The
report shows, per file, the categories removed, the entry count and the size
before and after.

---

## Lossless removal

| Format | Method | Pixels |
| --- | --- | --- |
| JPEG | APP1, APP13 and COM segments filtered out | untouched |
| PNG | tEXt, zTXt, iTXt and eXIf chunks filtered out | untouched |
| WebP | RIFF chunks filtered out, VP8X flags updated | untouched |
| TIFF, GIF, BMP, ICO, HEIC | re-encoded through Pillow | recompressed |

The report shows ✅ for a lossless clean and ♻️ for a re-encode.

Three safety rules:

- "Remove everything" spares the ICC profile, which drives colour rendering.
  Tick it explicitly to remove it.
- Removing EXIF also removes GPS and the thumbnail: they live in the same block.
  Removing GPS alone rebuilds the EXIF block and keeps the rest.
- Structural TIFF tags (width, compression, strip offsets) stay in place.
  Removing them would break the file.

---

## Command line

The web interface is the default. Three sub-commands work without a browser.

```bash
# Web interface
imgmetamanager                          # 127.0.0.1:7860, opens a browser
imgmetamanager ui --port 8080 --no-browser
imgmetamanager --lang fr ui             # interface in French

# Read
imgmetamanager show photo.jpg
imgmetamanager show ~/Photos -r -f json
imgmetamanager show photo.jpg --sensitive        # identifying entries only
imgmetamanager show photo.jpg -g gps exif -s iso # filter by group and by word

# Export
imgmetamanager export ~/Photos -r -f csv -o inventory.csv
imgmetamanager export photo.jpg -f html -o report.html

# Remove
imgmetamanager strip ~/Photos --dry-run
imgmetamanager strip photo.jpg                        # writes photo-clean.jpg
imgmetamanager strip ~/Photos -r --out ~/Photos-clean
imgmetamanager strip photo.jpg --remove gps thumbnail
imgmetamanager strip ~/Photos --in-place              # automatic .bak backup
```

Categories accepted by `--remove`: `exif`, `gps`, `iptc`, `xmp`, `png_text`,
`comment`, `thumbnail`, `icc`, `other`, `all`.

Exit codes: `0` on success, `1` when a file failed, `2` when no file was found.

---

## Privacy and networking

The application runs on your machine. No image leaves the computer and no
network call is made to analyse a file.

The server listens on `127.0.0.1:7860` by default. When that port is taken, by
another Gradio app such as a Stable Diffusion WebUI, the next free port is used
and the terminal says which one. An explicit `--port` is honoured as given, so a
clash there is reported as an error rather than moved somewhere unexpected. `--share` creates a temporary
public link through Gradio and then disables local folder access and overwriting
originals. Use `--host 0.0.0.0` to expose the application on your local network,
knowingly.

Gradio only serves files that sit under the allowed roots: the temporary work
directory and your home directory. Cleaned files written outside those roots
land on disk without being offered for download, which the report states.

---

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e .

.venv/bin/python -m pytest tests -q     # 88 tests
.venv/bin/python -m pyflakes imgmetamanager tests
```

Continuous integration replays the tests on Ubuntu, Windows and macOS, under
Python 3.10 and 3.12.

### Releasing

Bump `version` in `pyproject.toml`, add the matching section to `CHANGELOG.md`,
then push to the default branch. The **Release** workflow tags the commit,
re-runs the tests, builds the wheel and the sdist, and publishes a GitHub
release whose notes come from the changelog. It is idempotent, so a version
already published is left alone; the Actions tab can also run it by hand.

### Layout

```
imgmetamanager/
├── app.py            Gradio interface; MetaApp holds the logic and tests alone
├── cli.py            ui / show / export / strip sub-commands
├── gr_compat.py      Gradio 5.x and 6.x compatibility
├── i18n.py           English and French strings, language detection
└── core/
    ├── containers.py byte-level JPEG, PNG and WebP readers and writers
    ├── reader.py     metadata extraction
    ├── writer.py     removal, with a Pillow fallback
    ├── exporter.py   JSON, CSV, TXT, Markdown, HTML, ZIP
    ├── tags.py       tag tables, enumerations, value formatting
    ├── model.py      ImageMeta and MetaItem
    └── utils.py      paths, sizes, hashing
```

The core has no dependency on Gradio. `imgmetamanager.core` works as a library:

```python
from imgmetamanager.core import read_metadata, strip_metadata, export_metadata

meta = read_metadata("photo.jpg")
print(meta.find("Make", "exif").value)         # ACME
print(dict(meta.removable_counts()))           # {'exif': 10, 'gps': 7, ...}

report = strip_metadata("photo.jpg", kinds=["gps", "thumbnail"])
print(report.target, report.lossless)          # photo-clean.jpg True

export_metadata([meta], "json", "meta.json")
```

---

## Dependencies

| Package | Role |
| --- | --- |
| gradio | web interface |
| pillow | image decoding, previews |
| piexif | selective EXIF block rewriting |
| defusedxml | hardened XMP parsing |
| pillow-heif | optional, HEIC/HEIF/AVIF support |

## Licence

MIT. See [LICENSE](LICENSE).
