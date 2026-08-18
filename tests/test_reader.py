"""Metadata extraction."""
from __future__ import annotations

from pathlib import Path

import pytest

import fixtures as fx
from imgmetamanager.core import read_metadata
from imgmetamanager.core.tags import (
    GROUP_AI, GROUP_COMMENT, GROUP_EXIF, GROUP_GPS, GROUP_IPTC, GROUP_PNG_TEXT, GROUP_XMP,
)


def test_jpeg_exposes_every_group(jpeg: Path):
    meta = read_metadata(jpeg)
    assert meta.ok, meta.error
    groups = meta.group_names()
    for expected in (GROUP_EXIF, GROUP_GPS, GROUP_IPTC, GROUP_XMP, GROUP_COMMENT):
        assert expected in groups, f"missing group: {expected}"
    assert meta.find("Make", GROUP_EXIF).value == "ACME"
    assert meta.find("Model", GROUP_EXIF).value == "SuperCam X1"
    assert meta.find("Caption-Abstract", GROUP_IPTC).value == "IPTC caption"
    assert meta.find("dc:title", GROUP_XMP).value == "XMP title"
    assert "JPEG test comment" in meta.find("Comment", GROUP_COMMENT).value


def test_exif_values_are_formatted(jpeg: Path):
    meta = read_metadata(jpeg)
    assert meta.find("ExposureTime", GROUP_EXIF).value.startswith("1/250 s")
    assert meta.find("FNumber", GROUP_EXIF).value == "f/2.8"
    assert meta.find("UserComment", GROUP_EXIF).value == "Test photo"


def test_gps_converted_to_decimal_and_dms(jpeg: Path):
    meta = read_metadata(jpeg)
    position = meta.find("Position", GROUP_GPS)
    assert position is not None
    latitude, longitude = (float(part) for part in position.value.split(","))
    assert latitude == pytest.approx(48.8583, abs=1e-3)
    assert longitude == pytest.approx(2.2912, abs=1e-3)
    assert meta.find("GPSLatitude", GROUP_GPS).value.startswith("48° 51′")


def test_sensitive_entries_are_flagged(jpeg: Path):
    meta = read_metadata(jpeg)
    sensitive = {item.tag for item in meta.items if item.sensitive}
    assert {"Artist", "BodySerialNumber", "GPSLatitude"} <= sensitive
    assert "Model" not in sensitive


def test_embedded_thumbnail_is_detected(jpeg: Path):
    meta = read_metadata(jpeg)
    assert meta.find("ThumbnailPresent", "thumbnail") is not None


def test_png_text_and_xmp(png: Path):
    meta = read_metadata(png)
    assert meta.find("Author", GROUP_PNG_TEXT).value == "Jean Dupont"
    assert meta.find("dc:title", GROUP_XMP).value == "PNG title"
    assert meta.find("Make", GROUP_EXIF).value == "ACME"


def test_webp_exif_and_xmp(webp: Path):
    meta = read_metadata(webp)
    assert meta.find("Make", GROUP_EXIF).value == "ACME"
    assert meta.find("photoshop:City", GROUP_XMP).value == "Nice"


def test_gif_and_tiff_are_read(gif: Path, tiff: Path):
    assert read_metadata(gif).ok
    meta = read_metadata(tiff)
    assert meta.ok
    assert meta.find("ImageDescription", GROUP_EXIF).value == "TIFF description"


def test_stable_diffusion_parameters(tmp_path: Path):
    params = ("an astronaut cat, 4k\n"
              "Negative prompt: blurry, watermark\n"
              "Steps: 28, Sampler: DPM++ 2M, CFG scale: 7.5, Seed: 12345, "
              "Size: 768x768, Model: sdxl-base")
    path = fx.make_png(tmp_path / "ai.png", ai_params=params)
    meta = read_metadata(path)
    assert meta.find("Generator", GROUP_AI).value.startswith("Stable Diffusion")
    assert "astronaut cat" in meta.find("Prompt", GROUP_AI).value
    assert meta.find("NegativePrompt", GROUP_AI).value == "blurry, watermark"
    assert meta.find("Seed", GROUP_AI).value == "12345"
    assert meta.find("Sampler", GROUP_AI).value == "DPM++ 2M"


def test_missing_file_does_not_raise():
    meta = read_metadata("/path/that/does/not/exist.jpg")
    assert not meta.ok
    assert "not found" in meta.error


def test_file_that_is_not_an_image(tmp_path: Path):
    path = tmp_path / "fake.jpg"
    path.write_bytes(b"this is not an image")
    meta = read_metadata(path)
    assert not meta.ok


def test_malformed_xmp_is_survivable(tmp_path: Path):
    """A broken XMP packet must not abort the whole read."""
    import struct

    source = fx.make_jpeg(tmp_path / "broken.jpg", xmp=False)
    data = source.read_bytes()
    payload = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta><unclosed></x:xmpmeta>"
    patched = (bytearray(data[:2]) + bytes((0xFF, 0xE1))
               + struct.pack(">H", len(payload) + 2) + payload + data[2:])
    source.write_bytes(bytes(patched))
    meta = read_metadata(source, with_hash=False)
    assert meta.ok
    assert meta.find("Make", GROUP_EXIF).value == "ACME"


def test_structural_tags_are_not_removable(tiff: Path):
    meta = read_metadata(tiff)
    width = meta.find("ImageWidth")
    assert width is not None and width.removable is None


def test_row_filters(jpeg: Path):
    meta = read_metadata(jpeg)
    assert all(row[0] == GROUP_GPS for row in meta.to_rows(groups=[GROUP_GPS]))
    assert all("iso" in row[1].lower() or "iso" in row[2].lower()
               for row in meta.to_rows(query="iso"))
    assert all(item for item in meta.to_rows(sensitive_only=True))


def test_preview_is_generated(jpeg: Path, tmp_path: Path):
    from imgmetamanager.core import make_preview

    first = make_preview(jpeg, tmp_path / "previews")
    assert first and Path(first).exists()
    assert make_preview(jpeg, tmp_path / "previews") == first, "cached on second call"
