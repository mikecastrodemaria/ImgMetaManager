"""Tag tables, sensitivity rules and value formatting."""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Tuple

from .utils import decode_text, hex_preview

# ---------------------------------------------------------------------- groups
GROUP_FILE = "file"
GROUP_IMAGE = "image"
GROUP_EXIF = "exif"
GROUP_GPS = "gps"
GROUP_IPTC = "iptc"
GROUP_XMP = "xmp"
GROUP_PNG_TEXT = "png_text"
GROUP_COMMENT = "comment"
GROUP_ICC = "icc"
GROUP_THUMBNAIL = "thumbnail"
GROUP_AI = "ai"
GROUP_OTHER = "other"

#: Display order of the metadata groups.
GROUP_ORDER = (
    GROUP_FILE, GROUP_IMAGE, GROUP_EXIF, GROUP_GPS, GROUP_IPTC, GROUP_XMP,
    GROUP_PNG_TEXT, GROUP_AI, GROUP_COMMENT, GROUP_ICC, GROUP_THUMBNAIL, GROUP_OTHER,
)

#: Categories the writer knows how to remove from a file.
REMOVABLE_KINDS = (
    GROUP_EXIF, GROUP_GPS, GROUP_IPTC, GROUP_XMP, GROUP_PNG_TEXT,
    GROUP_COMMENT, GROUP_THUMBNAIL, GROUP_ICC, GROUP_OTHER,
)

# -------------------------------------------------------------------- IPTC IIM
#: IPTC IIM dataset numbers mapped to their standard names.
IPTC_TAGS: Dict[Tuple[int, int], str] = {
    (1, 90): "CodedCharacterSet",
    (2, 0): "RecordVersion",
    (2, 5): "ObjectName",
    (2, 7): "EditStatus",
    (2, 10): "Urgency",
    (2, 12): "SubjectReference",
    (2, 15): "Category",
    (2, 20): "SupplementalCategories",
    (2, 22): "FixtureIdentifier",
    (2, 25): "Keywords",
    (2, 26): "ContentLocationCode",
    (2, 27): "ContentLocationName",
    (2, 30): "ReleaseDate",
    (2, 35): "ReleaseTime",
    (2, 37): "ExpirationDate",
    (2, 40): "SpecialInstructions",
    (2, 45): "ReferenceService",
    (2, 47): "ReferenceDate",
    (2, 50): "ReferenceNumber",
    (2, 55): "DateCreated",
    (2, 60): "TimeCreated",
    (2, 62): "DigitalCreationDate",
    (2, 63): "DigitalCreationTime",
    (2, 65): "OriginatingProgram",
    (2, 70): "ProgramVersion",
    (2, 75): "ObjectCycle",
    (2, 80): "By-line",
    (2, 85): "By-lineTitle",
    (2, 90): "City",
    (2, 92): "Sub-location",
    (2, 95): "Province-State",
    (2, 100): "Country-PrimaryLocationCode",
    (2, 101): "Country-PrimaryLocationName",
    (2, 103): "OriginalTransmissionReference",
    (2, 105): "Headline",
    (2, 110): "Credit",
    (2, 115): "Source",
    (2, 116): "CopyrightNotice",
    (2, 118): "Contact",
    (2, 120): "Caption-Abstract",
    (2, 122): "Writer-Editor",
    (2, 130): "ImageType",
    (2, 131): "ImageOrientation",
    (2, 135): "LanguageIdentifier",
}

# ------------------------------------------------------------- sensitive tags
#: Tags that can identify a person, a place or a specific piece of hardware.
SENSITIVE_TAGS = {
    "Artist", "BodySerialNumber", "CameraOwnerName", "CameraSerialNumber",
    "HostComputer", "ImageUniqueID", "InternalSerialNumber", "LensSerialNumber",
    "MakerNote", "OwnerName", "SerialNumber", "XPAuthor",
    "By-line", "By-lineTitle", "Contact", "Credit", "Writer-Editor",
    "City", "Sub-location", "Province-State", "Country-PrimaryLocationName",
}

#: Substrings that flag a tag as sensitive whatever its exact name.
SENSITIVE_SUBSTRINGS = ("serial", "gps", "location", "latitude", "longitude", "owner")


def is_sensitive(group: str, tag: str) -> bool:
    """Tell whether an entry can identify a person, a place or a device.

    Every GPS entry counts as sensitive, as does any tag listed in
    :data:`SENSITIVE_TAGS` or containing one of :data:`SENSITIVE_SUBSTRINGS`.
    """
    if group == GROUP_GPS:
        return True
    if tag in SENSITIVE_TAGS:
        return True
    low = tag.lower()
    return any(token in low for token in SENSITIVE_SUBSTRINGS)


#: TIFF tags that describe the file layout. Removing them breaks the image.
STRUCTURAL_TAGS = {
    "ImageWidth", "ImageLength", "BitsPerSample", "Compression", "PhotometricInterpretation",
    "StripOffsets", "StripByteCounts", "RowsPerStrip", "SamplesPerPixel", "PlanarConfiguration",
    "TileWidth", "TileLength", "TileOffsets", "TileByteCounts", "Predictor", "SampleFormat",
    "ExtraSamples", "ColorMap", "NewSubfileType", "SubfileType", "FillOrder",
    "JPEGInterchangeFormat", "JPEGInterchangeFormatLength", "YCbCrSubSampling",
    "ReferenceBlackWhite", "ExifImageWidth", "ExifImageHeight", "PixelXDimension",
    "PixelYDimension",
}


def is_structural(tag: str) -> bool:
    """Tell whether a tag describes the file layout rather than its history."""
    return tag in STRUCTURAL_TAGS


# ----------------------------------------------------------------- enumerations
#: Numeric EXIF values mapped to their standard labels, as EXIF tools spell them.
ENUMS: Dict[str, Dict[int, str]] = {
    "Orientation": {
        1: "Horizontal (normal)", 2: "Mirror horizontal", 3: "Rotate 180",
        4: "Mirror vertical", 5: "Mirror horizontal and rotate 270 CW",
        6: "Rotate 90 CW", 7: "Mirror horizontal and rotate 90 CW", 8: "Rotate 270 CW",
    },
    "ResolutionUnit": {1: "None", 2: "inches", 3: "cm"},
    "YCbCrPositioning": {1: "Centered", 2: "Co-sited"},
    "ExposureProgram": {
        0: "Not defined", 1: "Manual", 2: "Program AE", 3: "Aperture-priority AE",
        4: "Shutter speed priority AE", 5: "Creative (slow speed)", 6: "Action (high speed)",
        7: "Portrait", 8: "Landscape",
    },
    "MeteringMode": {
        0: "Unknown", 1: "Average", 2: "Center-weighted average", 3: "Spot",
        4: "Multi-spot", 5: "Multi-segment", 6: "Partial", 255: "Other",
    },
    "LightSource": {
        0: "Unknown", 1: "Daylight", 2: "Fluorescent", 3: "Tungsten (incandescent)",
        4: "Flash", 9: "Fine weather", 10: "Cloudy", 11: "Shade", 255: "Other",
    },
    "ColorSpace": {1: "sRGB", 2: "Adobe RGB", 65535: "Uncalibrated"},
    "SensingMethod": {
        1: "Not defined", 2: "One-chip colour area", 3: "Two-chip colour area",
        4: "Three-chip colour area", 7: "Trilinear", 8: "Colour sequential linear",
    },
    "CustomRendered": {0: "Normal", 1: "Custom"},
    "ExposureMode": {0: "Auto", 1: "Manual", 2: "Auto bracket"},
    "WhiteBalance": {0: "Auto", 1: "Manual"},
    "SceneCaptureType": {0: "Standard", 1: "Landscape", 2: "Portrait", 3: "Night"},
    "GainControl": {0: "None", 1: "Low gain up", 2: "High gain up", 3: "Low gain down",
                    4: "High gain down"},
    "Contrast": {0: "Normal", 1: "Low", 2: "High"},
    "Saturation": {0: "Normal", 1: "Low", 2: "High"},
    "Sharpness": {0: "Normal", 1: "Soft", 2: "Hard"},
    "SubjectDistanceRange": {0: "Unknown", 1: "Macro", 2: "Close", 3: "Distant"},
    "FileSource": {1: "Film scanner", 2: "Reflection print scanner", 3: "Digital camera"},
    "Compression": {1: "Uncompressed", 5: "LZW", 6: "JPEG (old-style)", 7: "JPEG",
                    8: "Deflate"},
}

_FLASH_FIRED = {0: "did not fire", 1: "fired"}


def _format_flash(value: int) -> str:
    """Decode the bit field of the EXIF ``Flash`` tag."""
    parts = [_FLASH_FIRED.get(value & 1, "?")]
    if value & 0x18 == 0x18:
        parts.append("auto mode")
    if value & 0x20:
        parts.append("no flash function")
    if value & 0x40:
        parts.append("red-eye reduction")
    return f"{', '.join(parts)} ({value})"


def _rational(value: Any) -> Fraction:
    """Coerce a Pillow ``IFDRational``, a 2-tuple or a number into a Fraction."""
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        return Fraction(int(value.numerator), int(value.denominator) or 1)
    if isinstance(value, tuple) and len(value) == 2:
        return Fraction(int(value[0]), int(value[1]) or 1)
    return Fraction(value).limit_denominator(100000)


def _format_exposure(value: Any) -> str:
    """Render a shutter speed the way a photographer reads it: ``1/250 s``."""
    try:
        frac = _rational(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return str(value)
    if frac == 0:
        return "0 s"
    if frac >= 1:
        return f"{float(frac):.4g} s"
    inverse = 1 / frac
    return f"1/{round(float(inverse)):g} s ({float(frac):.6g} s)"


def _format_fnumber(value: Any) -> str:
    try:
        return f"f/{float(_rational(value)):.3g}"
    except (TypeError, ValueError, ZeroDivisionError):
        return str(value)


def _format_focal(value: Any) -> str:
    try:
        return f"{float(_rational(value)):.4g} mm"
    except (TypeError, ValueError, ZeroDivisionError):
        return str(value)


def _format_ev(value: Any) -> str:
    try:
        return f"{float(_rational(value)):+.3g} EV"
    except (TypeError, ValueError, ZeroDivisionError):
        return str(value)


def _format_user_comment(value: Any) -> str:
    """Decode ``UserComment``, whose first eight bytes name the character set."""
    if isinstance(value, bytes):
        charset, payload = value[:8], value[8:]
        if charset.startswith(b"ASCII"):
            return payload.decode("ascii", "replace").strip("\x00").strip()
        if charset.startswith(b"UNICODE"):
            for encoding in ("utf-16-be", "utf-16-le"):
                try:
                    return payload.decode(encoding).strip("\x00").strip()
                except UnicodeDecodeError:
                    continue
        text = decode_text(value)
        return text if text is not None else f"<{len(value)} bytes> {hex_preview(value)}"
    return str(value)


def _format_xp(value: Any) -> str:
    """Decode the Windows ``XP*`` tags, stored as UTF-16LE byte arrays."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-16-le", "replace").strip("\x00").strip()
    if isinstance(value, tuple):
        return bytes(value).decode("utf-16-le", "replace").strip("\x00").strip()
    return str(value)


#: Per-tag value formatters, applied before the generic rules.
FORMATTERS = {
    "ExposureTime": _format_exposure,
    "ShutterSpeedValue": _format_ev,
    "FNumber": _format_fnumber,
    "ApertureValue": _format_fnumber,
    "MaxApertureValue": _format_fnumber,
    "FocalLength": _format_focal,
    "FocalLengthIn35mmFilm": lambda v: f"{v} mm (35 mm equivalent)",
    "ExposureBiasValue": _format_ev,
    "BrightnessValue": _format_ev,
    "Flash": lambda v: _format_flash(int(v)) if isinstance(v, int) else str(v),
    "UserComment": _format_user_comment,
    "XPTitle": _format_xp,
    "XPComment": _format_xp,
    "XPAuthor": _format_xp,
    "XPKeywords": _format_xp,
    "XPSubject": _format_xp,
}


def format_value(tag: str, value: Any) -> str:
    """Turn a raw tag value into a readable string.

    Applies, in order: the per-tag formatter, the enumeration table, then
    generic rules for bytes, rationals, sequences and mappings. An exotic tag
    never breaks the read: a failing formatter falls through to the generic
    rules.
    """
    if value is None:
        return ""
    formatter = FORMATTERS.get(tag)
    if formatter is not None:
        try:
            return formatter(value)
        except Exception:  # noqa: BLE001 - a broken tag must not break the read
            pass
    if tag in ENUMS and isinstance(value, int):
        label = ENUMS[tag].get(value)
        return f"{label} ({value})" if label else str(value)
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
        text = decode_text(data)
        if text is not None and len(text) < 2000:
            return text
        return f"<{len(data)} bytes> {hex_preview(data)}"
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        try:
            frac = _rational(value)
            if frac.denominator == 1:
                return str(frac.numerator)
            return f"{float(frac):.6g} ({frac.numerator}/{frac.denominator})"
        except (TypeError, ValueError, ZeroDivisionError):
            return str(value)
    if isinstance(value, (tuple, list)):
        return ", ".join(format_value(tag, item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}={format_value(tag, v)}" for k, v in value.items())
    if isinstance(value, str):
        return value.replace("\x00", "").strip()
    return str(value)
