"""Container parsing and translation consistency."""
from __future__ import annotations

from pathlib import Path

import pytest

import fixtures as fx
from imgmetamanager.core import containers as ct
from imgmetamanager.core.tags import GROUP_ORDER, REMOVABLE_KINDS
from imgmetamanager.i18n import EN, FR, detect_language, group_label, set_language, t


def test_jpeg_segments(tmp_path: Path):
    data = fx.make_jpeg(tmp_path / "a.jpg", thumbnail=True).read_bytes()
    kinds = {ct.jpeg_app_kind(seg) for seg in ct.iter_jpeg_segments(data)}
    assert {"exif", "xmp", "iptc", "comment", "jfif"} <= kinds


def test_png_chunks(tmp_path: Path):
    data = fx.make_png(tmp_path / "a.png").read_bytes()
    chunks = list(ct.iter_png_chunks(data))
    assert chunks[0].ctype == b"IHDR" and chunks[-1].ctype == b"IEND"
    kinds = {ct.png_chunk_kind(chunk) for chunk in chunks}
    assert {"png_text", "xmp", "exif", "structure"} <= kinds


def test_webp_chunks(tmp_path: Path):
    data = fx.make_webp(tmp_path / "a.webp").read_bytes()
    types = {chunk.ctype for chunk in ct.iter_riff_chunks(data)}
    assert b"VP8X" in types and b"EXIF" in types and b"XMP " in types


def test_rebuild_jpeg_is_byte_identical(tmp_path: Path):
    data = fx.make_jpeg(tmp_path / "a.jpg").read_bytes()
    assert ct.rebuild_jpeg(data, lambda seg: seg.payload) == data


def test_xmp_packets(tmp_path: Path):
    data = fx.make_jpeg(tmp_path / "a.jpg").read_bytes()
    packets = ct.find_xmp_packets(data)
    assert len(packets) == 1 and "XMP title" in packets[0]


def test_non_jpeg_input():
    with pytest.raises(ValueError):
        list(ct.iter_jpeg_segments(b"not a jpeg"))


def test_translations_are_aligned():
    assert set(EN) == set(FR)
    for key in list(GROUP_ORDER) + list(REMOVABLE_KINDS):
        assert f"group_{key}" in EN, f"missing label for group {key}"


def test_language_switching():
    try:
        set_language("fr")
        assert group_label("gps") == "GPS / Localisation"
        set_language("en")
        assert group_label("gps") == "GPS / Location"
        set_language("xx")
        assert t("app_title") == "ImgMetaManager"
    finally:
        set_language("en")


@pytest.mark.parametrize("value,expected", [
    ("fr_FR.UTF-8", "fr"), ("fr", "fr"), ("de_DE.UTF-8", "en"),
    ("en_GB.UTF-8", "en"), ("C", "en"), ("", "en"),
])
def test_language_detection(monkeypatch, value: str, expected: str):
    for variable in ("IMM_LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("LANG", value)
    assert detect_language() == expected


def test_declared_versions_agree():
    """pyproject.toml and __init__.py must not drift apart.

    The version is read with a regex rather than a TOML parser: tomllib only
    joined the standard library in 3.11, and the project supports 3.10.
    """
    import pathlib
    import re

    import imgmetamanager

    root = pathlib.Path(__file__).resolve().parent.parent
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    project = text.split("[project]", 1)[1].split("\n[", 1)[0]
    match = re.search(r'^version\s*=\s*"([^"]+)"', project, re.M)
    assert match, "no version found in the [project] section"
    assert imgmetamanager.__version__ == match.group(1)
