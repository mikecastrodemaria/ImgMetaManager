"""Factory for test images that carry real metadata.

Pillow cannot write IPTC, so the Photoshop resource block is assembled by hand
and injected as an APP13 segment. The same technique adds XMP and comments.
"""
from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import Dict, Optional, Tuple

import piexif
from PIL import Image
from PIL.PngImagePlugin import PngInfo

XMP_TEMPLATE = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{creator}</rdf:li></rdf:Seq></dc:creator>
   <photoshop:City>{city}</photoshop:City>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""


def base_image(size: Tuple[int, int] = (64, 48)) -> Image.Image:
    """Build a small deterministic gradient image."""
    img = Image.new("RGB", size)
    for x in range(size[0]):
        for y in range(size[1]):
            img.putpixel((x, y), (x * 4 % 256, y * 5 % 256, (x + y) % 256))
    return img


def exif_bytes(with_gps: bool = True, with_thumbnail: bool = False) -> bytes:
    """Build an EXIF block holding camera, exposure, serial number and GPS."""
    zeroth = {
        piexif.ImageIFD.Make: b"ACME",
        piexif.ImageIFD.Model: b"SuperCam X1",
        piexif.ImageIFD.Software: b"ImgMetaManager tests",
        piexif.ImageIFD.Artist: b"Jean Dupont",
        piexif.ImageIFD.DateTime: b"2024:05:12 09:30:00",
    }
    exif = {
        piexif.ExifIFD.ExposureTime: (1, 250),
        piexif.ExifIFD.FNumber: (28, 10),
        piexif.ExifIFD.ISOSpeedRatings: 400,
        piexif.ExifIFD.BodySerialNumber: b"SN-123456789",
        piexif.ExifIFD.UserComment: b"ASCII\x00\x00\x00Test photo",
    }
    gps: Dict[int, object] = {}
    if with_gps:
        gps = {  # Eiffel Tower, roughly
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((48, 1), (51, 1), (2976, 100)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((2, 1), (17, 1), (2820, 100)),
            piexif.GPSIFD.GPSAltitude: (35, 1),
        }
    payload = {"0th": zeroth, "Exif": exif, "GPS": gps, "1st": {}, "thumbnail": None}
    if with_thumbnail:
        buffer = io.BytesIO()
        base_image((16, 12)).save(buffer, format="JPEG")
        payload["thumbnail"] = buffer.getvalue()
        payload["1st"] = {
            piexif.ImageIFD.XResolution: (72, 1),
            piexif.ImageIFD.YResolution: (72, 1),
            piexif.ImageIFD.ResolutionUnit: 2,
        }
    return piexif.dump(payload)


def iptc_segment(caption: str = "IPTC caption", byline: str = "Jean Dupont",
                 keywords: Tuple[str, ...] = ("test", "metadata")) -> bytes:
    """Build an APP13 Photoshop resource block holding IPTC IIM datasets."""
    def dataset(record: int, tag: int, value: str) -> bytes:
        raw = value.encode("utf-8")
        return struct.pack(">BBBH", 0x1C, record, tag, len(raw)) + raw

    iim = struct.pack(">BBBH", 0x1C, 2, 0, 2) + b"\x00\x02"  # RecordVersion = 2
    iim += dataset(2, 105, "IPTC headline")
    iim += dataset(2, 120, caption)
    iim += dataset(2, 80, byline)
    iim += dataset(2, 90, "Paris")
    for keyword in keywords:
        iim += dataset(2, 25, keyword)
    if len(iim) & 1:
        iim += b"\x00"
    resource = b"8BIM" + struct.pack(">H", 0x0404) + b"\x00\x00" + struct.pack(">I", len(iim)) + iim
    return b"Photoshop 3.0\x00" + resource


def make_jpeg(path: Path, *, gps: bool = True, thumbnail: bool = False,
              iptc: bool = True, xmp: bool = True, comment: bool = True,
              size: Tuple[int, int] = (64, 48)) -> Path:
    """Write a JPEG carrying EXIF, GPS, IPTC, XMP and a comment."""
    base_image(size).save(path, format="JPEG", quality=90,
                          exif=exif_bytes(gps, thumbnail))
    data = path.read_bytes()
    extra: Dict[int, bytes] = {}
    if iptc:
        extra[0xED] = iptc_segment()
    if xmp:
        packet = XMP_TEMPLATE.format(title="XMP title", creator="Jean Dupont", city="Paris")
        extra[0xE1] = b"http://ns.adobe.com/xap/1.0/\x00" + packet.encode("utf-8")
    if comment:
        extra[0xFE] = "JPEG test comment".encode("utf-8")
    if extra:
        # The extra segments must come after the APP1 block Pillow just wrote.
        head_end = 2
        while data[head_end] == 0xFF and data[head_end + 1] in (0xE0, 0xE1):
            head_end += 2 + struct.unpack(">H", data[head_end + 2:head_end + 4])[0]
        body = bytearray(data[:head_end])
        for marker, payload in extra.items():
            body += bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload
        body += data[head_end:]
        path.write_bytes(bytes(body))
    return path


def make_png(path: Path, *, text: bool = True, exif: bool = True, xmp: bool = True,
             ai_params: Optional[str] = None, size: Tuple[int, int] = (64, 48)) -> Path:
    """Write a PNG carrying text chunks, an eXIf chunk and an XMP packet."""
    info = PngInfo()
    if text:
        info.add_text("Author", "Jean Dupont")
        info.add_text("Description", "PNG test image")
        info.add_text("Software", "ImgMetaManager tests")
    if ai_params:
        info.add_text("parameters", ai_params)
    if xmp:
        packet = XMP_TEMPLATE.format(title="PNG title", creator="Jean Dupont", city="Lyon")
        info.add_itxt("XML:com.adobe.xmp", packet)
    kwargs = {"pnginfo": info}
    if exif:
        kwargs["exif"] = exif_bytes(True)
    base_image(size).save(path, format="PNG", **kwargs)
    return path


def make_webp(path: Path, *, exif: bool = True, xmp: bool = True,
              size: Tuple[int, int] = (64, 48)) -> Path:
    """Write a lossless WebP carrying EXIF and XMP chunks."""
    kwargs: Dict[str, object] = {"lossless": True}
    if exif:
        kwargs["exif"] = exif_bytes(True)
    if xmp:
        kwargs["xmp"] = XMP_TEMPLATE.format(
            title="WebP title", creator="Jean Dupont", city="Nice").encode("utf-8")
    base_image(size).save(path, format="WEBP", **kwargs)
    return path


def make_gif(path: Path, frames: int = 3, size: Tuple[int, int] = (32, 24)) -> Path:
    """Write an animated GIF with a comment."""
    # Frames must differ, otherwise Pillow merges the identical ones.
    images = []
    for step in range(frames):
        frame = base_image(size)
        frame.paste((255, step * 40 % 256, 0), (0, 0, 4 + step * 3, 4 + step * 3))
        images.append(frame.convert("P"))
    images[0].save(path, format="GIF", save_all=True, append_images=images[1:],
                   duration=100, loop=0, comment=b"GIF comment")
    return path


def make_tiff(path: Path, size: Tuple[int, int] = (32, 24)) -> Path:
    """Write an uncompressed TIFF with a description and a software tag."""
    base_image(size).save(path, format="TIFF", description="TIFF description",
                          software="ImgMetaManager tests")
    return path
