"""Byte-level readers and writers for the JPEG, PNG and WebP containers.

These helpers make it possible to drop metadata blocks **without re-encoding
the pixels**: the compressed image stream is copied verbatim and only the
requested segments or chunks are left out.
"""
from __future__ import annotations

import struct
from typing import Callable, Iterator, List, NamedTuple, Optional, Set

JPEG_SOI = b"\xff\xd8"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: JPEG markers that carry no payload and no length field.
_STANDALONE = {0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8}

EXIF_PREFIX = b"Exif\x00\x00"
XMP_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXT_PREFIX = b"http://ns.adobe.com/xmp/extension/\x00"
ICC_PREFIX = b"ICC_PROFILE\x00"
PHOTOSHOP_PREFIX = b"Photoshop 3.0\x00"
XMP_PACKET_KEYWORD = "XML:com.adobe.xmp"


class JpegSegment(NamedTuple):
    """One JPEG marker segment located inside a file."""

    marker: int      #: Marker byte, e.g. ``0xE1`` for APP1 or ``0xFE`` for COM.
    payload: bytes   #: Segment body, without the marker and the length field.
    start: int       #: Offset of the leading ``0xFF`` byte in the file.
    end: int         #: Offset just past the segment.

    @property
    def name(self) -> str:
        """Human-readable marker name such as ``APP1``, ``COM`` or ``SOS``."""
        if 0xE0 <= self.marker <= 0xEF:
            return f"APP{self.marker - 0xE0}"
        return {0xFE: "COM", 0xDA: "SOS", 0xD9: "EOI"}.get(self.marker, f"FF{self.marker:02X}")

    @property
    def size(self) -> int:
        """Total size of the segment in bytes, marker included."""
        return self.end - self.start


class Chunk(NamedTuple):
    """One PNG chunk or one RIFF/WebP chunk."""

    ctype: bytes     #: Four-character chunk type, e.g. ``b'tEXt'`` or ``b'EXIF'``.
    payload: bytes   #: Chunk body, without type, length and CRC.
    start: int       #: Offset of the chunk in the file.
    end: int         #: Offset just past the chunk.

    @property
    def name(self) -> str:
        return self.ctype.decode("ascii", "replace")

    @property
    def size(self) -> int:
        return self.end - self.start


# --------------------------------------------------------------------------- JPEG
def is_jpeg(data: bytes) -> bool:
    """Tell whether a byte string starts with the JPEG start-of-image marker."""
    return data[:2] == JPEG_SOI


def iter_jpeg_segments(data: bytes) -> Iterator[JpegSegment]:
    """Walk the JPEG marker segments up to and including the start of scan.

    Iteration stops at SOS because everything after it is entropy-coded image
    data rather than structured segments. A truncated or corrupt stream ends
    the iteration silently instead of raising.

    Raises:
        ValueError: if the data is not a JPEG at all.
    """
    if not is_jpeg(data):
        raise ValueError("Not a JPEG file.")
    pos = 2
    size = len(data)
    while pos + 1 < size:
        if data[pos] != 0xFF:
            return  # corrupt stream or padding: stop here
        while pos < size and data[pos] == 0xFF:
            pos += 1  # skip fill bytes
        if pos >= size:
            return
        marker = data[pos]
        pos += 1
        if marker in _STANDALONE:
            continue
        if marker == 0xD9:  # EOI
            yield JpegSegment(marker, b"", pos - 2, pos)
            return
        if pos + 2 > size:
            return
        length = struct.unpack(">H", data[pos:pos + 2])[0]
        if length < 2 or pos + length > size:
            return
        payload = data[pos + 2:pos + length]
        segment = JpegSegment(marker, payload, pos - 2, pos + length)
        yield segment
        if marker == 0xDA:  # SOS: the entropy-coded stream follows
            return
        pos += length


def rebuild_jpeg(data: bytes, keep: Callable[[JpegSegment], Optional[bytes]]) -> bytes:
    """Rebuild a JPEG while filtering its segments.

    Args:
        data: The original JPEG bytes.
        keep: Called once per segment. Return ``None`` to drop the segment, the
            original ``segment.payload`` object to copy it byte for byte, or a
            new payload to replace it.

    Returns:
        The rebuilt JPEG. The scan data is always copied verbatim, so the
        pixels are untouched.
    """
    out = bytearray(JPEG_SOI)
    tail_written = False
    for segment in iter_jpeg_segments(data):
        if segment.marker == 0xDA:
            out += data[segment.start:]  # SOS header, scan data and EOI
            tail_written = True
            break
        new_payload = keep(segment)
        if new_payload is None:
            continue
        if new_payload is segment.payload:
            out += data[segment.start:segment.end]
        else:
            out += bytes((0xFF, segment.marker))
            out += struct.pack(">H", len(new_payload) + 2)
            out += new_payload
    if not tail_written:
        out += b"\xff\xd9"
    return bytes(out)


def jpeg_app_kind(segment: JpegSegment) -> str:
    """Classify a JPEG segment.

    Returns:
        One of ``exif``, ``xmp``, ``icc``, ``iptc``, ``comment``, ``jfif``,
        ``adobe``, ``other`` or ``structure``. The ``adobe`` and ``jfif`` kinds
        are structural: dropping them can change how the image decodes.
    """
    marker, payload = segment.marker, segment.payload
    if marker == 0xFE:
        return "comment"
    if marker == 0xE0:
        return "jfif"
    if marker == 0xE1:
        if payload.startswith(EXIF_PREFIX):
            return "exif"
        if payload.startswith(XMP_PREFIX) or payload.startswith(XMP_EXT_PREFIX):
            return "xmp"
    if marker == 0xE2 and payload.startswith(ICC_PREFIX):
        return "icc"
    if marker == 0xED and payload.startswith(PHOTOSHOP_PREFIX):
        return "iptc"
    if marker == 0xEE and payload.startswith(b"Adobe"):
        return "adobe"  # colour transform flag, required to decode the image
    if 0xE0 <= marker <= 0xEF:
        return "other"
    return "structure"


# --------------------------------------------------------------------------- PNG
#: Chunks needed to render the image, animated PNG included. Never dropped,
#: except ``iCCP`` which goes only when the caller asks for it explicitly.
PNG_KEEP_ALWAYS = {
    b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB",
    b"sBIT", b"bKGD", b"hIST", b"pHYs", b"sPLT", b"acTL", b"fcTL", b"fdAT",
    b"cICP", b"mDCv", b"cLLi", b"iCCP",
}


def is_png(data: bytes) -> bool:
    """Tell whether a byte string starts with the PNG signature."""
    return data[:8] == PNG_SIGNATURE


def iter_png_chunks(data: bytes) -> Iterator[Chunk]:
    """Walk the PNG chunks from the signature to IEND.

    Raises:
        ValueError: if the data is not a PNG.
    """
    if not is_png(data):
        raise ValueError("Not a PNG file.")
    pos = 8
    size = len(data)
    while pos + 8 <= size:
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > size:
            return
        yield Chunk(ctype, data[pos + 8:pos + 8 + length], pos, end)
        pos = end
        if ctype == b"IEND":
            return


def png_text_keyword(chunk: Chunk) -> str:
    """Read the keyword of a ``tEXt``, ``zTXt`` or ``iTXt`` chunk."""
    raw = chunk.payload.split(b"\x00", 1)[0]
    return raw.decode("latin-1", "replace")


def png_chunk_kind(chunk: Chunk) -> str:
    """Classify a PNG chunk.

    Returns:
        One of ``xmp``, ``png_text``, ``exif``, ``icc``, ``other`` or
        ``structure``. Anything classified as ``structure`` is required to
        render the image.
    """
    ctype = chunk.ctype
    if ctype in (b"tEXt", b"zTXt", b"iTXt"):
        if png_text_keyword(chunk) == XMP_PACKET_KEYWORD:
            return "xmp"
        return "png_text"
    if ctype == b"eXIf":
        return "exif"
    if ctype == b"iCCP":
        return "icc"
    if ctype == b"tIME":
        return "other"
    if ctype in PNG_KEEP_ALWAYS:
        return "structure"
    return "other"


# --------------------------------------------------------------------------- WebP
def is_webp(data: bytes) -> bool:
    """Tell whether a byte string is a RIFF container holding WebP data."""
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def iter_riff_chunks(data: bytes) -> Iterator[Chunk]:
    """Walk the RIFF chunks of a WebP file, honouring the even-size padding.

    Raises:
        ValueError: if the data is not a WebP file.
    """
    if not is_webp(data):
        raise ValueError("Not a WebP file.")
    pos = 12
    size = min(len(data), struct.unpack("<I", data[4:8])[0] + 8)
    while pos + 8 <= size:
        ctype = data[pos:pos + 4]
        length = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        end = pos + 8 + length + (length & 1)  # chunks are padded to an even size
        if pos + 8 + length > len(data):
            return
        yield Chunk(ctype, data[pos + 8:pos + 8 + length], pos, min(end, len(data)))
        pos = end


#: Feature bits of the VP8X chunk, as defined by the WebP container spec.
WEBP_FLAGS = {"icc": 0x20, "exif": 0x08, "xmp": 0x04}


def webp_chunk_kind(chunk: Chunk) -> str:
    """Classify a WebP chunk as ``exif``, ``xmp``, ``icc`` or ``structure``."""
    return {b"EXIF": "exif", b"XMP ": "xmp", b"ICCP": "icc"}.get(chunk.ctype, "structure")


def rebuild_webp(data: bytes, drop_kinds: Set[str]) -> bytes:
    """Rebuild a WebP file without the requested metadata chunks.

    The VP8X feature flags are cleared for every dropped kind and the RIFF
    size field is recomputed, so the result stays a valid WebP file.

    Args:
        data: The original WebP bytes.
        drop_kinds: Any of ``exif``, ``xmp`` and ``icc``.
    """
    body = bytearray()
    for chunk in iter_riff_chunks(data):
        kind = webp_chunk_kind(chunk)
        if kind in drop_kinds:
            continue
        if chunk.ctype == b"VP8X" and len(chunk.payload) >= 1:
            flags = chunk.payload[0]
            for name, bit in WEBP_FLAGS.items():
                if name in drop_kinds:
                    flags &= ~bit & 0xFF
            payload = bytes([flags]) + chunk.payload[1:]
            body += b"VP8X" + struct.pack("<I", len(payload)) + payload
            if len(payload) & 1:
                body += b"\x00"
            continue
        body += data[chunk.start:chunk.end]
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + bytes(body)


# ----------------------------------------------------------------------- raw XMP
def find_xmp_packets(data: bytes) -> List[str]:
    """Extract every XMP packet found anywhere in a file.

    Scanning the raw bytes works across every container, which keeps XMP
    support independent of the image format.
    """
    packets: List[str] = []
    start = 0
    while True:
        begin = data.find(b"<x:xmpmeta", start)
        if begin == -1:
            break
        end = data.find(b"</x:xmpmeta>", begin)
        if end == -1:
            break
        end += len(b"</x:xmpmeta>")
        chunk = data[begin:end]
        try:
            packets.append(chunk.decode("utf-8"))
        except UnicodeDecodeError:
            packets.append(chunk.decode("latin-1", "replace"))
        start = end
    return packets
