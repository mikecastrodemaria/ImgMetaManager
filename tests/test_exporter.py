"""Export documents and clipboard formatting."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

import fixtures as fx
from imgmetamanager.core import read_metadata
from imgmetamanager.core.exporter import (
    CLIPBOARD_FORMATS, EXPORT_FORMATS, export_metadata, export_per_image_zip,
    metadata_to_string, rows_to_text,
)


@pytest.fixture()
def metas(tmp_path: Path):
    return [
        read_metadata(fx.make_jpeg(tmp_path / "photo.jpg"), with_hash=False),
        read_metadata(fx.make_png(tmp_path / "image.png"), with_hash=False),
    ]


def test_json_structure(metas):
    payload = json.loads(metadata_to_string(metas, "json"))
    assert payload["image_count"] == 2
    assert payload["images"][0]["groups"]["exif"]["Make"] == "ACME"
    assert payload["images"][0]["groups"]["gps"]["GPSLatitudeRef"] == "N"


def test_csv_is_readable(metas):
    text = metadata_to_string(metas, "csv")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["file", "path", "group", "tag", "value", "sensitive"]
    makes = [row for row in rows if row[3] == "Make"]
    assert makes and makes[0][4] == "ACME"
    assert any(row[5] == "true" for row in rows[1:]), "sensitive entries are flagged"


def test_txt_markdown_and_html(metas):
    txt = metadata_to_string(metas, "txt", group_labels={"exif": "EXIF"})
    assert "photo.jpg" in txt and "Make" in txt
    markdown = metadata_to_string(metas, "markdown")
    assert markdown.startswith("# Image metadata")
    assert "| Tag | Value |" in markdown
    page = metadata_to_string(metas, "html")
    assert page.startswith("<!doctype html>") and "</html>" in page
    assert "&amp;" in page or "ACME" in page


def test_filters_are_applied(metas):
    payload = json.loads(metadata_to_string(metas, "json", groups=["gps"]))
    assert set(payload["images"][0]["groups"]) == {"gps"}
    payload = json.loads(metadata_to_string(metas, "json", sensitive_only=True))
    assert "Model" not in payload["images"][0]["groups"].get("exif", {})
    payload = json.loads(metadata_to_string(metas, "json", query="acme"))
    assert payload["images"][0]["groups"]["exif"] == {"Make": "ACME"}


def test_files_are_written(metas, tmp_path: Path):
    for fmt, suffix in EXPORT_FORMATS.items():
        target = export_metadata(metas, fmt, tmp_path / f"export{suffix}")
        assert target.exists() and target.stat().st_size > 0
    csv_text = (tmp_path / "export.csv").read_text(encoding="utf-8-sig")
    assert csv_text.startswith("file,")


def test_zip_per_image(metas, tmp_path: Path):
    archive = export_per_image_zip(metas, "json", tmp_path / "lot.zip")
    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == ["image.json", "photo.json"]
        content = json.loads(zf.read("photo.json"))
        assert content["image_count"] == 1


def test_unknown_format(metas):
    with pytest.raises(ValueError):
        metadata_to_string(metas, "yaml")


def test_clipboard_layouts():
    rows = [["EXIF", "Make", "ACME"], ["GPS", "Position", "48.8, 2.3"]]
    assert rows_to_text(rows, "Tag = value") == "Make = ACME\nPosition = 48.8, 2.3"
    assert rows_to_text(rows, "TSV (spreadsheet)").splitlines()[0] == "EXIF\tMake\tACME"
    assert json.loads(rows_to_text(rows, "JSON")) == {"Make": "ACME", "Position": "48.8, 2.3"}
    assert rows_to_text(rows, "Values only") == "ACME\n48.8, 2.3"
    assert rows_to_text([], CLIPBOARD_FORMATS[0]) == ""


def test_human_formats_follow_the_language(metas):
    """Human formats translate; the CSV and JSON schemas stay stable."""
    from imgmetamanager.i18n import set_language

    try:
        set_language("fr")
        assert metadata_to_string(metas, "markdown").startswith("# Métadonnées d'images")
        assert "| Tag | Valeur |" in metadata_to_string(metas, "markdown")
        assert '<html lang="fr">' in metadata_to_string(metas, "html")
        set_language("en")
        assert "Path" in metadata_to_string(metas, "txt")
        assert '<html lang="en">' in metadata_to_string(metas, "html")
        assert metadata_to_string(metas, "csv").startswith("file,path,group")
        assert "metadata_count" in metadata_to_string(metas, "json")
    finally:
        set_language("en")
