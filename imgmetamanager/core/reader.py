"""Metadata extraction from image files."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import ExifTags, Image, ImageOps, IptcImagePlugin

# defusedxml hardens the parsing of XMP packets; fall back to the standard library.
try:
    from defusedxml import ElementTree as ET
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as ET

from ..i18n import t
from . import containers as ct
from .model import ImageMeta, MetaItem
from .tags import (
    GROUP_AI, GROUP_COMMENT, GROUP_EXIF, GROUP_FILE, GROUP_GPS, GROUP_ICC,
    GROUP_IMAGE, GROUP_IPTC, GROUP_OTHER, GROUP_PNG_TEXT, GROUP_THUMBNAIL,
    GROUP_XMP, IPTC_TAGS, format_value, is_sensitive, is_structural,
)
from .utils import (
    decode_text, hex_preview, human_size, iso_time, safe_name, sha256_of, truncate,
)

#: True when pillow-heif is installed, which adds HEIC/HEIF and AVIF support.
HEIF_AVAILABLE = False
try:  # pragma: no cover - depends on the installation
    import pillow_heif

    pillow_heif.register_heif_opener()
    try:
        pillow_heif.register_avif_opener()
    except AttributeError:
        pass
    HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover
    pass

# One gigapixel: large enough for a panorama, low enough that a decompression
# bomb raises an error instead of exhausting memory.
Image.MAX_IMAGE_PIXELS = 1_000_000_000

#: Human-readable list of the formats this build can read.
SUPPORTED_READ = (
    "JPEG, PNG, WebP, TIFF, GIF, BMP, ICO, PPM/PGM"
    + (", HEIC/HEIF, AVIF" if HEIF_AVAILABLE else "")
)

#: Keys of ``Image.info`` handled by a dedicated reader, kept out of "Other".
_INFO_HANDLED = {
    "exif", "xmp", "icc_profile", "photoshop", "iptc", "comment", "parameters",
    "dpi", "jfif", "jfif_version", "jfif_unit", "jfif_density", "adobe",
    "adobe_transform", "progression", "progressive", "transparency", "gamma",
    "srgb", "chromaticity", "aspect", "loop", "duration", "background",
    "version", "extension", "bits", "compression", "interlace", "palette",
    "timestamp", "resolution", "resolution_unit", "icc_profile_size",
}


# --------------------------------------------------------------------- helpers
def _add(meta: ImageMeta, group: str, tag: str, value: Any, *,
         removable: Optional[str] = None, note: str = "", raw: Any = None) -> None:
    """Format a value and append it to the result, skipping empty entries."""
    if removable and is_structural(tag):
        removable = None  # removing these tags would break the file
    text = value if isinstance(value, str) else format_value(tag, value)
    text = truncate(text.strip(), 20000)
    if text == "":
        return
    meta.add(MetaItem(
        group=group, tag=tag, value=text, raw=raw if raw is not None else value,
        removable=removable, sensitive=is_sensitive(group, tag), note=note,
    ))


def _tag_name(tag_id: int, table: Dict[int, str]) -> str:
    """Resolve a numeric tag id, falling back to its hexadecimal form."""
    return table.get(tag_id, f"Tag_0x{tag_id:04X}")


# ----------------------------------------------------------------- file group
def _read_file_group(meta: ImageMeta, path: Path, with_hash: bool) -> None:
    """Collect filesystem facts: name, size, dates, permissions, digest."""
    stat = path.stat()
    meta.file_size = stat.st_size
    _add(meta, GROUP_FILE, "FileName", path.name)
    _add(meta, GROUP_FILE, "Directory", str(path.parent))
    _add(meta, GROUP_FILE, "FileSize", f"{human_size(stat.st_size)} ({stat.st_size} bytes)")
    _add(meta, GROUP_FILE, "FileModifyDate", iso_time(stat.st_mtime))
    if os.name != "nt":
        _add(meta, GROUP_FILE, "FilePermissions", oct(stat.st_mode & 0o777))
    if with_hash:
        try:
            _add(meta, GROUP_FILE, "SHA256", sha256_of(path))
        except OSError:
            pass


def _read_image_group(meta: ImageMeta, img: Image.Image) -> None:
    """Collect image geometry: format, size, colour mode, frames."""
    meta.file_format = img.format or ""
    meta.width, meta.height = img.size
    meta.mode = img.mode
    _add(meta, GROUP_IMAGE, "Format", img.format or "unknown")
    _add(meta, GROUP_IMAGE, "ImageSize", f"{img.width} × {img.height} px")
    megapixels = img.width * img.height / 1_000_000
    precision = 2 if megapixels >= 0.1 else 4
    _add(meta, GROUP_IMAGE, "Megapixels", f"{megapixels:.{precision}f}")
    _add(meta, GROUP_IMAGE, "ColorMode", img.mode)
    if img.mode == "P" and img.palette is not None:
        _add(meta, GROUP_IMAGE, "PaletteColors", str(len(img.palette.colors or {})))
    dpi = img.info.get("dpi")
    if dpi:
        try:
            _add(meta, GROUP_IMAGE, "Resolution", f"{float(dpi[0]):.0f} × {float(dpi[1]):.0f} dpi")
        except (TypeError, ValueError, IndexError):
            pass
    frames = getattr(img, "n_frames", 1)
    if frames and frames > 1:
        _add(meta, GROUP_IMAGE, "FrameCount", str(frames))
        _add(meta, GROUP_IMAGE, "Animated", t("value_yes"))
    if img.info.get("progressive") or img.info.get("progression"):
        _add(meta, GROUP_IMAGE, "Progressive", t("value_yes"))
    if "transparency" in img.info:
        _add(meta, GROUP_IMAGE, "Transparency", t("value_yes"))


# -------------------------------------------------------------------- EXIF/GPS
def _decimal_from_dms(dms: Any, ref: Any) -> Optional[float]:
    """Convert a degrees/minutes/seconds triple plus its hemisphere to degrees."""
    try:
        degrees, minutes, seconds = (float(x) for x in dms)
    except (TypeError, ValueError):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    if str(ref).upper().strip() in ("S", "W"):
        value = -value
    return value


def _format_dms(dms: Any, ref: Any) -> Optional[str]:
    """Render a coordinate as ``48° 51′ 29.76″ N``, easier to read than a triple."""
    try:
        degrees, minutes, seconds = (float(x) for x in dms)
    except (TypeError, ValueError):
        return None
    text = f"{degrees:.0f}° {minutes:.0f}′ {seconds:.2f}″"
    suffix = str(ref).strip().strip("\x00")
    return f"{text} {suffix}".strip()


def _read_exif(meta: ImageMeta, img: Image.Image) -> None:
    """Collect the EXIF IFDs: main, Exif, Interop, GPS and thumbnail."""
    try:
        exif = img.getexif()
    except Exception as exc:  # noqa: BLE001
        meta.warnings.append(t("warn_exif_unreadable", error=exc))
        return
    if not exif:
        return

    def dump_ifd(ifd: Any, table: Dict[int, str], group: str, removable: str) -> None:
        for tag_id, value in ifd.items():
            name = _tag_name(int(tag_id), table)
            if name == "GPSInfo" and group == GROUP_EXIF:
                continue  # handled separately below
            if name in ("ExifOffset", "InteroperabilityOffset", "MakerNoteSafety"):
                continue  # pointers, not data
            note = t("note_binary_truncated") if isinstance(value, bytes) and \
                len(value) > 512 else ""
            _add(meta, group, name, value, removable=removable, note=note)

    dump_ifd(exif, ExifTags.TAGS, GROUP_EXIF, GROUP_EXIF)
    for ifd_id in (ExifTags.IFD.Exif, ExifTags.IFD.Interop):
        try:
            sub_ifd = exif.get_ifd(ifd_id)
        except Exception:  # noqa: BLE001
            continue
        if sub_ifd:
            dump_ifd(sub_ifd, ExifTags.TAGS, GROUP_EXIF, GROUP_EXIF)

    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except Exception:  # noqa: BLE001
        gps = {}
    if gps:
        for tag_id, value in gps.items():
            name = _tag_name(int(tag_id), ExifTags.GPSTAGS)
            if name in ("GPSLatitude", "GPSLongitude"):
                ref = gps.get(1 if name == "GPSLatitude" else 3, "")
                pretty = _format_dms(value, ref)
                if pretty:
                    _add(meta, GROUP_GPS, name, pretty, removable=GROUP_GPS, raw=value)
                    continue
            _add(meta, GROUP_GPS, name, value, removable=GROUP_GPS)
        latitude = _decimal_from_dms(gps.get(2), gps.get(1, ""))
        longitude = _decimal_from_dms(gps.get(4), gps.get(3, ""))
        if latitude is not None and longitude is not None:
            _add(meta, GROUP_GPS, "Position", f"{latitude:.6f}, {longitude:.6f}",
                 removable=GROUP_GPS, note=t("note_gps_decimal"))
            _add(meta, GROUP_GPS, "MapLink",
                 f"https://www.openstreetmap.org/?mlat={latitude:.6f}&mlon={longitude:.6f}"
                 f"#map=16/{latitude:.6f}/{longitude:.6f}",
                 removable=GROUP_GPS)

    # IFD1 holds the embedded thumbnail, which may predate a crop.
    try:
        ifd1 = exif.get_ifd(ExifTags.IFD.IFD1)
    except Exception:  # noqa: BLE001
        ifd1 = {}
    if ifd1:
        for tag_id, value in ifd1.items():
            name = _tag_name(int(tag_id), ExifTags.TAGS)
            if name in ("JpegIFOffset", "JpegIFByteCount"):
                continue
            _add(meta, GROUP_THUMBNAIL, name, value, removable=GROUP_THUMBNAIL)
        _add(meta, GROUP_THUMBNAIL, "ThumbnailPresent", t("value_yes"),
             removable=GROUP_THUMBNAIL, note=t("note_thumbnail"))


# ------------------------------------------------------------------------ IPTC
def _read_iptc(meta: ImageMeta, img: Image.Image) -> None:
    """Collect the IPTC IIM datasets carried by the Photoshop resource block."""
    try:
        info = IptcImagePlugin.getiptcinfo(img)
    except Exception:  # noqa: BLE001
        return
    if not info:
        return
    for key, value in info.items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        name = IPTC_TAGS.get(key, f"IPTC_{key[0]}:{key[1]}")
        if isinstance(value, (list, tuple)):
            parts = [decode_text(v) or "" if isinstance(v, bytes) else str(v) for v in value]
            text = ", ".join(p for p in parts if p)
        elif isinstance(value, bytes):
            if name == "RecordVersion" and len(value) == 2:
                text = str(int.from_bytes(value, "big"))
            else:
                text = decode_text(value) or hex_preview(value)
        else:
            text = str(value)
        _add(meta, GROUP_IPTC, name, text, removable=GROUP_IPTC)


# ------------------------------------------------------------------------- XMP
_NS_RE = re.compile(r"^\{(?P<uri>[^}]+)\}(?P<local>.+)$")

#: Namespace URIs mapped to the prefixes photographers recognise.
_XMP_PREFIXES = {
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "Iptc4xmpCore",
    "http://iptc.org/std/Iptc4xmpExt/2008-02-29/": "Iptc4xmpExt",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://ns.google.com/photos/1.0/camera/": "GCamera",
}


def _xmp_prefix(uri: str) -> str:
    """Map a namespace URI to a short prefix, deriving one when unknown."""
    if uri in _XMP_PREFIXES:
        return _XMP_PREFIXES[uri]
    tail = uri.rstrip("/#").rsplit("/", 1)[-1]
    return tail or "xmp"


def _qname(tag: str) -> str:
    """Turn ``{namespace}local`` into ``prefix:local``."""
    match = _NS_RE.match(tag)
    if not match:
        return tag
    return f"{_xmp_prefix(match.group('uri'))}:{match.group('local')}"


def _flatten_xmp(element: "ET.Element", prefix: str, out: List[Tuple[str, str]]) -> None:
    """Flatten an XMP tree into ``(name, value)`` pairs.

    RDF containers (``Alt``, ``Bag``, ``Seq``) collapse into a single
    semicolon-separated value under the name of their parent property.
    """
    for attr_name, attr_value in element.attrib.items():
        name = _qname(attr_name)
        if name.startswith("rdf:") or name.startswith("xmlns"):
            continue
        out.append((name, attr_value.strip()))
    for child in element:
        name = _qname(child.tag)
        if name in ("rdf:RDF", "rdf:Description"):
            _flatten_xmp(child, prefix, out)
            continue
        if name in ("rdf:Alt", "rdf:Bag", "rdf:Seq"):
            values = [(item.text or "").strip() for item in child if (item.text or "").strip()]
            if values:
                out.append((prefix or "value", "; ".join(values)))
            else:
                _flatten_xmp(child, prefix, out)
            continue
        if name == "rdf:li":
            text = (child.text or "").strip()
            if text:
                out.append((prefix or "rdf:li", text))
            continue
        text = (child.text or "").strip()
        if text:
            out.append((name, text))
        for attr_name, attr_value in child.attrib.items():
            attr_label = _qname(attr_name)
            if attr_label.startswith("rdf:") or attr_label.startswith("xmlns"):
                continue
            out.append((f"{name}/{attr_label}", attr_value.strip()))
        if len(child):
            _flatten_xmp(child, name, out)


def _read_xmp(meta: ImageMeta, raw: bytes) -> None:
    """Collect every XMP packet found in the raw bytes of the file."""
    packets = ct.find_xmp_packets(raw)
    if not packets:
        return
    seen: set = set()
    for packet in packets:
        try:
            root = ET.fromstring(packet)
        except Exception:  # noqa: BLE001 - malformed XML, or rejected by defusedxml
            _add(meta, GROUP_XMP, "XMPPacket", truncate(packet, 4000), removable=GROUP_XMP,
                 note=t("note_xmp_unparsed"))
            continue
        pairs: List[Tuple[str, str]] = []
        _flatten_xmp(root, "", pairs)
        for name, value in pairs:
            if not value or (name, value) in seen:
                continue
            seen.add((name, value))
            _add(meta, GROUP_XMP, name, value, removable=GROUP_XMP)
    if not any(item.group == GROUP_XMP for item in meta.items):
        _add(meta, GROUP_XMP, "XMPPacket", t("value_present_empty"), removable=GROUP_XMP)


# ------------------------------------------------------------ PNG and comments
def _read_png_text(meta: ImageMeta, img: Image.Image) -> None:
    """Collect the PNG ``tEXt``, ``zTXt`` and ``iTXt`` chunks."""
    text_keys: Dict[str, str] = {}
    for source in (getattr(img, "text", None) or {}, img.info):
        for key, value in source.items():
            if isinstance(value, str) and key not in _INFO_HANDLED:
                text_keys.setdefault(key, value)
    for key, value in text_keys.items():
        if key == ct.XMP_PACKET_KEYWORD:
            continue  # already covered by the XMP reader
        _add(meta, GROUP_PNG_TEXT, key, value, removable=GROUP_PNG_TEXT)
    parameters = img.info.get("parameters")
    if isinstance(parameters, str):
        _add(meta, GROUP_PNG_TEXT, "parameters", parameters, removable=GROUP_PNG_TEXT)


def _read_comments(meta: ImageMeta, img: Image.Image, raw: bytes) -> None:
    """Collect free-text comments and note any non-standard APPn segment."""
    comment = img.info.get("comment")
    if isinstance(comment, bytes):
        comment = decode_text(comment)
    if comment:
        _add(meta, GROUP_COMMENT, "Comment", comment, removable=GROUP_COMMENT)
    if ct.is_jpeg(raw):
        for segment in ct.iter_jpeg_segments(raw):
            kind = ct.jpeg_app_kind(segment)
            if kind == "comment":
                text = decode_text(segment.payload)
                if text and text != comment:
                    _add(meta, GROUP_COMMENT, "JPEGComment", text, removable=GROUP_COMMENT)
            elif kind == "other":
                label = (decode_text(segment.payload[:24]) or "").strip()
                detail = f"{segment.size} bytes" + (f" ({label})" if label else "")
                _add(meta, GROUP_OTHER, f"{segment.name}Segment", detail,
                     removable=GROUP_OTHER, note=t("note_app_segment"))


def _read_icc(meta: ImageMeta, img: Image.Image) -> None:
    """Describe the embedded ICC colour profile, if any."""
    profile = img.info.get("icc_profile")
    if not profile:
        return
    description = ""
    try:
        import io

        from PIL import ImageCms

        cms_profile = ImageCms.getOpenProfile(io.BytesIO(profile))
        description = ImageCms.getProfileDescription(cms_profile).strip()
    except Exception:  # noqa: BLE001 - ImageCms may be missing or the profile invalid
        pass
    _add(meta, GROUP_ICC, "ICCProfile", description or f"{len(profile)} bytes",
         removable=GROUP_ICC, note=t("note_icc"))
    _add(meta, GROUP_ICC, "ICCProfileSize", f"{len(profile)} bytes", removable=GROUP_ICC)


# ----------------------------------------------------------------- generative AI
_A1111_SETTINGS = re.compile(r"([A-Za-z][A-Za-z0-9 /_.-]*?):\s*([^,]+)(?:,|$)")

#: Fields where image generators store their settings.
_AI_SOURCE_TAGS = ("parameters", "prompt", "workflow", "Comment", "UserComment",
                   "Description", "Software", "Dream", "sd-metadata")


def _read_ai(meta: ImageMeta) -> None:
    """Recognise the metadata written by image generators.

    Understands the AUTOMATIC1111 text block (prompt, negative prompt and a
    comma-separated settings line), the ComfyUI JSON graphs and the ``Software``
    marker left by hosted generators.
    """
    sources = {
        item.tag: item.value for item in meta.items
        if item.group in (GROUP_PNG_TEXT, GROUP_EXIF, GROUP_COMMENT)
        and item.tag in _AI_SOURCE_TAGS
    }
    raw_params = sources.get("parameters") or ""
    if not raw_params and "Steps:" in sources.get("UserComment", ""):
        raw_params = sources["UserComment"]
    if raw_params and "Steps:" in raw_params:
        head, _, tail = raw_params.partition("Negative prompt:")
        negative, _, settings_line = tail.partition("Steps:")
        if not tail:
            head, _, settings_line = raw_params.partition("Steps:")
            negative = ""
        _add(meta, GROUP_AI, "Generator", t("ai_a1111"))
        _add(meta, GROUP_AI, "Prompt", head.strip(), removable=GROUP_PNG_TEXT)
        if negative.strip():
            _add(meta, GROUP_AI, "NegativePrompt", negative.strip(), removable=GROUP_PNG_TEXT)
        for key, value in _A1111_SETTINGS.findall("Steps:" + settings_line):
            _add(meta, GROUP_AI, key.strip().replace(" ", ""), value.strip(),
                 removable=GROUP_PNG_TEXT)
    for key in ("prompt", "workflow"):
        blob = sources.get(key)
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            continue
        nodes = len(data.get("nodes", data)) if isinstance(data, dict) else len(data)
        _add(meta, GROUP_AI, "Generator", "ComfyUI")
        _add(meta, GROUP_AI, f"ComfyUI_{key}", t("ai_graph", count=nodes),
             removable=GROUP_PNG_TEXT, note=t("note_ai_raw"))
    software = sources.get("Software", "")
    if software and not meta.find("Generator", GROUP_AI):
        for marker in ("Midjourney", "DALL", "NovelAI", "Firefly", "Stable Diffusion", "Flux"):
            if marker.lower() in software.lower():
                _add(meta, GROUP_AI, "Generator", software)
                break


# ----------------------------------------------------------------------- other
def _read_other(meta: ImageMeta, img: Image.Image) -> None:
    """Collect the leftover ``Image.info`` keys no dedicated reader claimed."""
    for key, value in img.info.items():
        if key in _INFO_HANDLED or key == ct.XMP_PACKET_KEYWORD:
            continue
        if any(item.tag == key for item in meta.items):
            continue
        if isinstance(value, (bytes, bytearray)) and len(value) > 256:
            _add(meta, GROUP_OTHER, key, f"<{len(value)} bytes>", removable=GROUP_OTHER)
        elif isinstance(value, (str, int, float, bytes, tuple, list)):
            _add(meta, GROUP_OTHER, key, value, removable=GROUP_OTHER)


# --------------------------------------------------------------------- reading
def read_metadata(path: os.PathLike, with_hash: bool = True) -> ImageMeta:
    """Analyse one image file and return everything it carries.

    Never raises: a missing file, an unknown format or a malformed metadata
    block produce an :class:`~imgmetamanager.core.model.ImageMeta` with
    ``ok=False`` and a filled ``error``.

    Args:
        path: The image to analyse.
        with_hash: Compute the SHA-256 digest of the file. Turn it off for
            large batches where the digest is not needed.
    """
    file_path = Path(path).expanduser()
    meta = ImageMeta(path=str(file_path), filename=file_path.name)
    if not file_path.is_file():
        meta.ok = False
        meta.error = t("err_not_found", path=file_path)
        return meta
    try:
        _read_file_group(meta, file_path, with_hash)
        raw = file_path.read_bytes()
        with Image.open(file_path) as img:
            img.load()
            _read_image_group(meta, img)
            _read_exif(meta, img)
            _read_iptc(meta, img)
            _read_png_text(meta, img)
            _read_comments(meta, img, raw)
            _read_icc(meta, img)
            _read_other(meta, img)
        _read_xmp(meta, raw)
        _read_ai(meta)
    except FileNotFoundError:
        meta.ok = False
        meta.error = t("err_not_found", path=file_path)
    except Image.UnidentifiedImageError:
        meta.ok = False
        meta.error = t("err_unknown_format")
    except Exception as exc:  # noqa: BLE001 - always return a usable report
        meta.ok = False
        meta.error = f"{type(exc).__name__}: {exc}"
    return meta


def make_preview(path: os.PathLike, dest_dir: os.PathLike, max_side: int = 1600) -> Optional[str]:
    """Render a downscaled PNG preview and return its path.

    The EXIF orientation is applied so the preview matches what an image viewer
    shows. The file name embeds the source modification time and size, so a
    modified file yields a new URL and the browser cannot serve a stale preview.

    Returns:
        The preview path, or ``None`` when the image cannot be decoded.
    """
    source = Path(path)
    target_dir = Path(dest_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        stat = source.stat()
        stamp = f"{int(stat.st_mtime)}-{stat.st_size}"
    except OSError:
        stamp = "0"
    key = hashlib.sha1(f"{source.resolve()}|{stamp}".encode("utf-8")).hexdigest()[:12]
    target = target_dir / f"preview_{key}_{safe_name(source.stem)[:40]}.png"
    if target.exists():
        return str(target)
    try:
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img) or img
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGBA" if "A" in img.mode else "RGB")
            img.thumbnail((max_side, max_side))
            img.save(target, format="PNG")
        return str(target)
    except Exception:  # noqa: BLE001 - a missing preview is not fatal
        return None
