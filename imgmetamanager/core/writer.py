"""Metadata removal, without re-encoding wherever the format allows it."""
from __future__ import annotations

import os
import shutil
import struct
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from PIL import Image, ImageSequence

from ..i18n import t
from . import containers as ct
from .model import ImageMeta
from .tags import (
    GROUP_COMMENT, GROUP_EXIF, GROUP_GPS, GROUP_ICC, GROUP_IPTC, GROUP_OTHER,
    GROUP_PNG_TEXT, GROUP_THUMBNAIL, GROUP_XMP, REMOVABLE_KINDS,
)
from .utils import unique_path

try:
    import piexif
except ImportError:  # pragma: no cover
    piexif = None

#: What ``"all"`` expands to. The ICC profile is spared: it drives colour
#: rendering, so removing it changes how the image looks.
DEFAULT_KINDS = (
    GROUP_EXIF, GROUP_GPS, GROUP_IPTC, GROUP_XMP, GROUP_PNG_TEXT,
    GROUP_COMMENT, GROUP_THUMBNAIL, GROUP_OTHER,
)

#: Every category the writer accepts, ICC profile included.
ALL_KINDS = tuple(REMOVABLE_KINDS)

#: Formats rewritten at the container level, with the pixels left untouched.
LOSSLESS_FORMATS = {"JPEG", "PNG", "WEBP"}


@dataclass
class StripReport:
    """Outcome of one cleaning operation."""

    source: str
    target: str = ""
    ok: bool = True
    error: str = ""
    lossless: bool = True
    """False when the pixels went through a re-encode."""
    removed: Dict[str, int] = field(default_factory=lambda: OrderedDict())
    """Bytes removed per category."""
    warnings: List[str] = field(default_factory=list)
    size_before: int = 0
    size_after: int = 0
    backup: str = ""
    """Path of the ``.bak`` copy, when one was made."""
    reencoded: bool = False

    @property
    def bytes_saved(self) -> int:
        """How much smaller the cleaned file is, never negative."""
        return max(self.size_before - self.size_after, 0)

    @property
    def removed_kinds(self) -> List[str]:
        """Categories that actually lost bytes."""
        return [kind for kind, count in self.removed.items() if count]


def normalise_kinds(kinds: Optional[Iterable[str]]) -> Set[str]:
    """Expand a list of category names into a concrete set.

    ``"all"`` expands to :data:`DEFAULT_KINDS`, ``"everything"`` adds the ICC
    profile on top. Removing EXIF implies removing GPS and the thumbnail, since
    all three share the same block. Unknown names are dropped.
    """
    if not kinds:
        return set(DEFAULT_KINDS)
    requested = {str(k).strip().lower() for k in kinds}
    if "all" in requested:
        requested.discard("all")
        requested |= set(DEFAULT_KINDS)
    if "everything" in requested:
        requested.discard("everything")
        requested |= set(ALL_KINDS)
    if GROUP_EXIF in requested:
        requested |= {GROUP_GPS, GROUP_THUMBNAIL}
    return {k for k in requested if k in ALL_KINDS}


def plan_removal(meta: ImageMeta, kinds: Optional[Iterable[str]] = None) -> "OrderedDict[str, int]":
    """Report what would be removed from an image for the requested categories.

    Returns:
        Category name mapped to the number of entries it would lose.
    """
    wanted = normalise_kinds(kinds)
    plan: "OrderedDict[str, int]" = OrderedDict()
    for kind, count in meta.removable_counts().items():
        if kind in wanted:
            plan[kind] = count
    return plan


# ------------------------------------------------------------- partial EXIF
def _rewrite_exif_blob(blob: bytes, kinds: Set[str]) -> Optional[bytes]:
    """Drop GPS and/or the thumbnail from an EXIF block, keeping the rest.

    Args:
        blob: An EXIF block, with or without the ``Exif\\x00\\x00`` prefix.
        kinds: The categories to remove.

    Returns:
        The rebuilt block, keeping the prefix when the input had one, or
        ``None`` when piexif is missing or the block cannot be rewritten.
    """
    if piexif is None:
        return None
    has_prefix = blob.startswith(ct.EXIF_PREFIX)
    try:
        exif_dict = piexif.load(blob if has_prefix else ct.EXIF_PREFIX + blob)
    except Exception:  # noqa: BLE001
        return None
    if GROUP_GPS in kinds:
        exif_dict["GPS"] = {}
    if GROUP_THUMBNAIL in kinds:
        exif_dict["thumbnail"] = None
        exif_dict["1st"] = {}
    try:
        rebuilt = piexif.dump(exif_dict)
    except Exception:  # noqa: BLE001 - some MakerNote blobs do not survive a dump
        return None
    return rebuilt if has_prefix else rebuilt[len(ct.EXIF_PREFIX):]


# -------------------------------------------------------------------- JPEG
def _strip_jpeg(data: bytes, kinds: Set[str], report: StripReport) -> bytes:
    """Rebuild a JPEG without the requested segments, pixels untouched."""
    partial_exif = bool(kinds & {GROUP_GPS, GROUP_THUMBNAIL}) and GROUP_EXIF not in kinds

    def keep(segment: ct.JpegSegment):
        kind = ct.jpeg_app_kind(segment)
        if kind == "exif":
            if GROUP_EXIF in kinds:
                report.removed[GROUP_EXIF] = report.removed.get(GROUP_EXIF, 0) + segment.size
                return None
            if partial_exif:
                rebuilt = _rewrite_exif_blob(segment.payload, kinds)
                if rebuilt is None:
                    report.warnings.append(t("warn_exif_not_rewritable"))
                    report.removed[GROUP_EXIF] = report.removed.get(GROUP_EXIF, 0) + segment.size
                    return None
                saved = len(segment.payload) - len(rebuilt)
                if saved:
                    key = GROUP_GPS if GROUP_GPS in kinds else GROUP_THUMBNAIL
                    report.removed[key] = report.removed.get(key, 0) + saved
                return rebuilt
            return segment.payload
        if kind in ("jfif", "adobe", "structure"):
            return segment.payload  # required to decode the image
        if kind in kinds:
            report.removed[kind] = report.removed.get(kind, 0) + segment.size
            return None
        return segment.payload

    return ct.rebuild_jpeg(data, keep)


# --------------------------------------------------------------------- PNG
def _strip_png(data: bytes, kinds: Set[str], report: StripReport) -> bytes:
    """Rebuild a PNG without the requested chunks, pixels untouched."""
    partial_exif = bool(kinds & {GROUP_GPS, GROUP_THUMBNAIL}) and GROUP_EXIF not in kinds
    out = bytearray(ct.PNG_SIGNATURE)
    for chunk in ct.iter_png_chunks(data):
        if chunk.ctype in (b"IHDR", b"IDAT", b"IEND"):
            out += data[chunk.start:chunk.end]
            continue
        kind = ct.png_chunk_kind(chunk)
        if kind == "exif" and partial_exif:
            rebuilt = _rewrite_exif_blob(chunk.payload, kinds)
            if rebuilt is not None:
                saved = len(chunk.payload) - len(rebuilt)
                if saved:
                    key = GROUP_GPS if GROUP_GPS in kinds else GROUP_THUMBNAIL
                    report.removed[key] = report.removed.get(key, 0) + saved
                out += struct.pack(">I", len(rebuilt)) + b"eXIf" + rebuilt
                out += struct.pack(">I", zlib.crc32(b"eXIf" + rebuilt) & 0xFFFFFFFF)
                continue
        if kind != "structure" and kind in kinds:
            report.removed[kind] = report.removed.get(kind, 0) + chunk.size
            continue
        out += data[chunk.start:chunk.end]
    return bytes(out)


# -------------------------------------------------------------------- WebP
def _strip_webp(data: bytes, kinds: Set[str], report: StripReport) -> bytes:
    """Rebuild a WebP without the requested chunks, pixels untouched."""
    drop = {k for k in (GROUP_EXIF, GROUP_XMP, GROUP_ICC) if k in kinds}
    partial_exif = bool(kinds & {GROUP_GPS, GROUP_THUMBNAIL}) and GROUP_EXIF not in kinds
    for chunk in ct.iter_riff_chunks(data):
        kind = ct.webp_chunk_kind(chunk)
        if kind in drop:
            report.removed[kind] = report.removed.get(kind, 0) + chunk.size
    out = ct.rebuild_webp(data, drop)
    if partial_exif:
        body = bytearray()
        for chunk in ct.iter_riff_chunks(out):
            if chunk.ctype == b"EXIF":
                rebuilt = _rewrite_exif_blob(chunk.payload, kinds)
                if rebuilt is not None:
                    saved = len(chunk.payload) - len(rebuilt)
                    if saved:
                        key = GROUP_GPS if GROUP_GPS in kinds else GROUP_THUMBNAIL
                        report.removed[key] = report.removed.get(key, 0) + saved
                    body += b"EXIF" + struct.pack("<I", len(rebuilt)) + rebuilt
                    if len(rebuilt) & 1:
                        body += b"\x00"
                    continue
            body += out[chunk.start:chunk.end]
        out = b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + bytes(body)
    return out


# ------------------------------------------------------- generic fallback
def _strip_with_pillow(source: Path, target: Path, kinds: Set[str],
                       report: StripReport) -> None:
    """Clean any other format by decoding and re-encoding it through Pillow.

    Multi-frame files keep their frames, their timing and their loop count.
    JPEG re-encoding reuses the original quantisation tables and subsampling,
    which keeps the loss minimal.
    """
    report.lossless = False
    report.reencoded = True
    with Image.open(source) as img:
        img.load()
        fmt = img.format or ""
        params: Dict[str, object] = {}
        if GROUP_ICC not in kinds and img.info.get("icc_profile"):
            params["icc_profile"] = img.info["icc_profile"]
        if fmt == "JPEG":
            params.update(quality="keep", subsampling="keep")
        elif fmt == "TIFF":
            compression = img.info.get("compression")
            if compression:
                params["compression"] = compression
        frames = getattr(img, "n_frames", 1)
        if frames > 1 and fmt in ("GIF", "TIFF", "WEBP", "PNG"):
            sequence = [frame.copy() for frame in ImageSequence.Iterator(img)]
            for frame in sequence:
                frame.info = {}
            first, rest = sequence[0], sequence[1:]
            params["save_all"] = True
            params["append_images"] = rest
            for key in ("duration", "loop"):
                if key in img.info:
                    params[key] = img.info[key]
            first.save(target, format=fmt, **params)
        else:
            clean = img.copy()
            clean.info = {}
            clean.save(target, format=fmt, **params)
    report.warnings.append(t("warn_reencoded", format=detect_format(source)))


def detect_format(path: Path) -> str:
    """Return the Pillow format name of a file, or its extension as a fallback."""
    try:
        with Image.open(path) as img:
            return img.format or path.suffix.lstrip(".").upper()
    except Exception:  # noqa: BLE001
        return path.suffix.lstrip(".").upper()


# --------------------------------------------------------------------- API
def strip_metadata(
    source: os.PathLike,
    target: Optional[os.PathLike] = None,
    kinds: Optional[Iterable[str]] = None,
    *,
    in_place: bool = False,
    backup: bool = True,
    suffix: str = "-clean",
    overwrite: bool = False,
) -> StripReport:
    """Remove the requested metadata and write the result.

    JPEG, PNG and WebP files are rewritten at the container level, so the
    compressed pixels are copied byte for byte. Every other format goes through
    Pillow, which re-encodes and sets ``reencoded`` on the report.

    The modification time of the source is copied onto the result, so cleaned
    photos keep their place in a chronological listing.

    Args:
        source: The image to clean.
        target: Destination file or folder. A path with no extension is treated
            as a folder. Defaults to ``<name><suffix><ext>`` next to the source.
        kinds: Categories to remove; ``["all"]`` covers everything but the ICC
            profile.
        in_place: Rewrite the source file itself.
        backup: With ``in_place``, copy the original to ``<name>.bak`` first.
        suffix: Suffix of the cleaned file when no explicit target is given.
        overwrite: Allow replacing an existing destination instead of picking a
            free name next to it.

    Returns:
        A :class:`StripReport`. Never raises: failures land in ``ok`` and
        ``error``.
    """
    src = Path(source).expanduser()
    report = StripReport(source=str(src))
    wanted = normalise_kinds(kinds)
    if not wanted:
        report.ok = False
        report.error = t("err_no_kinds")
        return report
    if not src.is_file():
        report.ok = False
        report.error = t("err_not_found", path=src)
        return report

    if in_place:
        destination = src
    elif target is None:
        destination = src.with_name(f"{src.stem}{suffix}{src.suffix}")
    else:
        target_path = Path(target).expanduser()
        # A path with no extension, or ending in a separator, means a folder.
        if (target_path.is_dir() or not target_path.suffix
                or str(target).endswith((os.sep, "/"))):
            target_path.mkdir(parents=True, exist_ok=True)
            destination = target_path / f"{src.stem}{suffix}{src.suffix}"
        else:
            destination = target_path
    if not in_place and destination.exists() and not overwrite:
        destination = unique_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp = destination.with_name(destination.name + ".imm-tmp")
    try:
        data = src.read_bytes()
        report.size_before = len(data)
        if ct.is_jpeg(data):
            payload = _strip_jpeg(data, wanted, report)
        elif ct.is_png(data):
            payload = _strip_png(data, wanted, report)
        elif ct.is_webp(data):
            payload = _strip_webp(data, wanted, report)
        else:
            payload = None

        if payload is not None:
            temp.write_bytes(payload)
            report.size_after = len(payload)
        else:
            _strip_with_pillow(src, temp, wanted, report)
            report.size_after = temp.stat().st_size

        if in_place and backup:
            backup_path = unique_path(src.with_name(src.name + ".bak"))
            shutil.copy2(src, backup_path)
            report.backup = str(backup_path)
        os.replace(temp, destination)
        try:
            stat = src.stat()
            os.utime(destination, (stat.st_atime, stat.st_mtime))
        except OSError:
            pass
        report.target = str(destination)
    except Exception as exc:  # noqa: BLE001
        report.ok = False
        report.error = f"{type(exc).__name__}: {exc}"
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return report
