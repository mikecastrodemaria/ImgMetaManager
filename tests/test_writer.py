"""Metadata removal."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

import fixtures as fx
from imgmetamanager.core import read_metadata, strip_metadata
from imgmetamanager.core.tags import GROUP_EXIF, GROUP_GPS, GROUP_ICC, GROUP_XMP
from imgmetamanager.core.writer import normalise_kinds, plan_removal


def pixel_hash(path: Path) -> str:
    """Hash the decoded pixels, to prove a rewrite did not touch them."""
    with Image.open(path) as img:
        return hashlib.sha256(img.convert("RGB").tobytes()).hexdigest()


@pytest.mark.parametrize("name", ["photo.jpg", "image.png", "image.webp"])
def test_full_removal_is_lossless(tmp_path: Path, name: str):
    maker = {"photo.jpg": fx.make_jpeg, "image.png": fx.make_png, "image.webp": fx.make_webp}
    source = maker[name](tmp_path / name)
    before = read_metadata(source, with_hash=False)
    assert before.metadata_count > 0

    report = strip_metadata(source, kinds=["all"])
    assert report.ok, report.error
    assert report.lossless is True
    assert report.size_after < report.size_before

    after = read_metadata(report.target, with_hash=False)
    assert after.metadata_count == 0
    assert pixel_hash(source) == pixel_hash(report.target), "the pixels were modified"


def test_original_is_left_untouched_by_default(tmp_path: Path):
    source = fx.make_jpeg(tmp_path / "photo.jpg")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = strip_metadata(source, kinds=["all"])
    assert Path(report.target) != source
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("name", ["photo.jpg", "image.png", "image.webp"])
def test_gps_only(tmp_path: Path, name: str):
    maker = {"photo.jpg": fx.make_jpeg, "image.png": fx.make_png, "image.webp": fx.make_webp}
    source = maker[name](tmp_path / name)
    report = strip_metadata(source, tmp_path / f"no-gps-{name}", kinds=["gps"])
    assert report.ok, report.error
    after = read_metadata(report.target, with_hash=False)
    assert GROUP_GPS not in after.group_names()
    assert after.find("Make", GROUP_EXIF).value == "ACME", "the useful EXIF must survive"


def test_thumbnail_only(tmp_path: Path):
    source = fx.make_jpeg(tmp_path / "photo.jpg", thumbnail=True)
    report = strip_metadata(source, tmp_path / "no-thumbnail.jpg", kinds=["thumbnail"])
    after = read_metadata(report.target, with_hash=False)
    assert "thumbnail" not in after.group_names()
    assert after.find("GPSLatitude", GROUP_GPS) is not None
    assert report.size_after < report.size_before


def test_xmp_only(tmp_path: Path):
    source = fx.make_jpeg(tmp_path / "photo.jpg")
    report = strip_metadata(source, tmp_path / "no-xmp.jpg", kinds=["xmp"])
    after = read_metadata(report.target, with_hash=False)
    assert GROUP_XMP not in after.group_names()
    assert after.find("Caption-Abstract", "iptc") is not None


def test_icc_profile_is_kept_by_default(tmp_path: Path):
    from PIL import ImageCms

    source = tmp_path / "icc.jpg"
    profile = ImageCms.createProfile("sRGB")
    fx.base_image().save(source, format="JPEG",
                         icc_profile=ImageCms.ImageCmsProfile(profile).tobytes(),
                         exif=fx.exif_bytes())
    report = strip_metadata(source, tmp_path / "clean.jpg", kinds=["all"])
    after = read_metadata(report.target, with_hash=False)
    assert after.find("ICCProfile", GROUP_ICC) is not None
    assert GROUP_EXIF not in after.group_names()

    report = strip_metadata(source, tmp_path / "clean2.jpg", kinds=["all", "icc"])
    assert read_metadata(report.target, with_hash=False).find("ICCProfile", GROUP_ICC) is None


def test_in_place_writes_a_backup(tmp_path: Path):
    source = fx.make_jpeg(tmp_path / "photo.jpg")
    original = source.read_bytes()
    report = strip_metadata(source, kinds=["all"], in_place=True, backup=True)
    assert report.ok
    assert Path(report.target) == source
    assert Path(report.backup).read_bytes() == original
    assert read_metadata(source, with_hash=False).metadata_count == 0


def test_formats_without_native_rewrite(tmp_path: Path):
    source = fx.make_tiff(tmp_path / "scan.tif")
    report = strip_metadata(source, tmp_path / "scan-clean.tif", kinds=["all"])
    assert report.ok, report.error
    assert report.reencoded is True
    assert report.warnings
    after = read_metadata(report.target, with_hash=False)
    assert after.find("ImageDescription", GROUP_EXIF) is None
    assert after.width and after.height


def test_animated_gif_keeps_its_frames(tmp_path: Path):
    source = fx.make_gif(tmp_path / "anim.gif", frames=4)
    report = strip_metadata(source, tmp_path / "anim-clean.gif", kinds=["all"])
    assert report.ok, report.error
    with Image.open(report.target) as img:
        assert img.n_frames == 4


def test_normalise_kinds():
    assert "gps" in normalise_kinds(["exif"]), "removing EXIF also removes GPS"
    assert "thumbnail" in normalise_kinds(["exif"])
    assert "icc" not in normalise_kinds(["all"])
    assert "icc" in normalise_kinds(["all", "icc"])
    assert normalise_kinds(["unknown"]) == set()


def test_removal_plan(tmp_path: Path):
    meta = read_metadata(fx.make_jpeg(tmp_path / "photo.jpg"), with_hash=False)
    plan = plan_removal(meta, ["gps"])
    assert set(plan) == {"gps"}
    assert plan["gps"] > 0
    assert set(plan_removal(meta, ["all"])) >= {"exif", "gps", "iptc", "xmp", "comment"}


def test_no_category_selected(tmp_path: Path):
    source = fx.make_jpeg(tmp_path / "photo.jpg")
    report = strip_metadata(source, kinds=["unknown"])
    assert not report.ok


def test_missing_file():
    report = strip_metadata("/nexiste/pas.jpg", kinds=["all"])
    assert not report.ok
    assert "not found" in report.error


def test_never_overwrites_silently(tmp_path: Path):
    source = fx.make_jpeg(tmp_path / "photo.jpg")
    first = strip_metadata(source, kinds=["all"])
    second = strip_metadata(source, kinds=["all"])
    assert Path(first.target).exists() and Path(second.target).exists()
    assert first.target != second.target
