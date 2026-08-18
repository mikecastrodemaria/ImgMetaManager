"""Shared pytest fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fixtures as fx  # noqa: E402
from imgmetamanager.i18n import set_language  # noqa: E402


@pytest.fixture(autouse=True)
def english_labels():
    """Pin the language so assertions do not depend on the host locale."""
    set_language("en")
    yield
    set_language("en")


@pytest.fixture()
def jpeg(tmp_path: Path) -> Path:
    return fx.make_jpeg(tmp_path / "photo.jpg", thumbnail=True)


@pytest.fixture()
def png(tmp_path: Path) -> Path:
    return fx.make_png(tmp_path / "image.png")


@pytest.fixture()
def webp(tmp_path: Path) -> Path:
    return fx.make_webp(tmp_path / "image.webp")


@pytest.fixture()
def gif(tmp_path: Path) -> Path:
    return fx.make_gif(tmp_path / "anim.gif")


@pytest.fixture()
def tiff(tmp_path: Path) -> Path:
    return fx.make_tiff(tmp_path / "scan.tif")
