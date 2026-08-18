"""AI provenance detection.

Every test that needs an optional dependency skips when it is missing, so a CI
run without the extras stays green.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import fixtures as fx
from imgmetamanager.core import provenance as prov
from imgmetamanager.core.provenance import (
    VERDICT_INVALID, VERDICT_NONE, VERDICT_SIGNALS, detect,
)

needs_c2pa = pytest.mark.skipif(not prov.c2pa_available(),
                                reason="c2pa-python is not installed")
needs_trustmark = pytest.mark.skipif(not prov.trustmark_available(),
                                     reason="trustmark is not installed")

#: Signed reference image from the C2PA reference implementation.
C2PA_FIXTURE_URL = ("https://raw.githubusercontent.com/contentauth/c2pa-rs/main/"
                    "sdk/tests/fixtures/C.jpg")


@pytest.fixture(scope="session")
def signed_jpeg(tmp_path_factory) -> Path:
    """Download the reference signed image, skipping the test when offline."""
    import urllib.error
    import urllib.request

    target = tmp_path_factory.mktemp("c2pa") / "C.jpg"
    try:
        with urllib.request.urlopen(C2PA_FIXTURE_URL, timeout=30) as response:
            target.write_bytes(response.read())
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - network
        pytest.skip(f"cannot download the C2PA fixture: {exc}")
    return target


# ------------------------------------------------------------------ no signal
def test_bare_png_reports_no_signal(tmp_path: Path):
    """A plain PNG has nothing to find, and that is not an error."""
    path = fx.make_png(tmp_path / "bare.png", text=False, exif=False, xmp=False)
    result = detect(path)
    assert result.verdict == VERDICT_NONE
    assert result.c2pa is None
    assert result.declared is None
    assert result.errors == [], "a missing manifest is the normal case"


@needs_c2pa
def test_manifest_not_found_is_not_an_error(tmp_path: Path):
    """c2pa raises ManifestNotFound on an unsigned file; that must stay quiet."""
    path = fx.make_png(tmp_path / "bare.png")
    manifest, error = prov.read_c2pa(path)
    assert manifest is None and error is None


def test_never_claims_authenticity(tmp_path: Path):
    """No wording may turn an absent signal into a verdict about the image."""
    path = fx.make_png(tmp_path / "bare.png", text=False, exif=False, xmp=False)
    text = " ".join(detect(path).as_lines()).lower()
    for forbidden in ("authentic", "genuine", "not ai", "human-made", "real photo"):
        assert forbidden not in text
    assert "inconclusive" in text
    assert "proves nothing" in text


# ---------------------------------------------------------------------- C2PA
@needs_c2pa
def test_signed_fixture_is_read(signed_jpeg: Path):
    result = detect(signed_jpeg)
    assert result.verdict == VERDICT_SIGNALS
    assert result.c2pa is not None
    assert result.c2pa["issuer"] == "C2PA Test Signing Cert"
    assert result.c2pa["validation_state"].lower() == "valid"
    assert result.c2pa["valid"] is True
    assert "c2pa-rs" in (result.c2pa["claim_generator"] or "")
    assert result.c2pa["signed_at"].startswith("2024-")


@needs_c2pa
def test_signed_source_type_is_credited_to_the_manifest(signed_jpeg: Path):
    """A source type inside the signed manifest is not 'declared, unsigned'."""
    result = detect(signed_jpeg)
    assert result.c2pa["digital_source_type"] == "algorithmicMedia"
    assert result.declared is None


@needs_c2pa
def test_tampering_invalidates_the_manifest(signed_jpeg: Path, tmp_path: Path):
    """Editing the pixels after signing must read as an invalid signature.

    The manifest itself is left untouched, so the producer and the issuer stay
    readable: what breaks is the hash covering the image data.
    """
    raw = bytearray(signed_jpeg.read_bytes())
    start_of_scan = raw.find(b"\xff\xda")
    assert start_of_scan != -1, "unexpected fixture layout"
    for offset in range(start_of_scan + 2000, start_of_scan + 2400):
        if raw[offset] not in (0xFF, 0xFE):
            raw[offset] ^= 0x01  # flip a bit without forging a JPEG marker
    tampered = tmp_path / "tampered.jpg"
    tampered.write_bytes(bytes(raw))

    result = detect(tampered)
    assert result.verdict == VERDICT_INVALID
    assert result.c2pa is not None
    assert result.c2pa["valid"] is False
    assert result.c2pa["issuer"] == "C2PA Test Signing Cert"
    assert "altered after it was signed" in result.verdict_label


# ----------------------------------------------------------------- watermark
@needs_trustmark
def test_watermark_roundtrip(tmp_path: Path):
    """A TrustMark payload written into the pixels comes back out."""
    from trustmark import TrustMark

    try:
        encoder = TrustMark(verbose=False, model_type="Q", device="cpu",
                            loadRemover=False)
    except Exception as exc:  # pragma: no cover - the model is fetched online
        pytest.skip(f"TrustMark model unavailable: {exc}")
    marked = tmp_path / "marked.png"
    encoder.encode(fx.base_image((512, 512)).convert("RGB"), "IMMTEST").save(marked)

    result = detect(marked, check_watermark=True)
    assert result.checked_watermark is True
    assert result.has_watermark is True
    assert result.watermark[1].startswith("IMMTEST")
    assert result.verdict == VERDICT_SIGNALS


@needs_trustmark
def test_watermark_absent_on_a_plain_image(tmp_path: Path):
    path = tmp_path / "plain.png"
    fx.base_image((512, 512)).convert("RGB").save(path)
    result = detect(path, check_watermark=True)
    if not result.checked_watermark:  # pragma: no cover - the model is fetched online
        pytest.skip(f"TrustMark model unavailable: {result.errors}")
    assert result.checked_watermark is True
    assert result.has_watermark is False
    assert result.verdict == VERDICT_NONE


def test_watermark_is_never_decoded_unless_asked(tmp_path: Path, monkeypatch):
    """The model must not load during an ordinary read."""
    def explode(*_args, **_kwargs):
        raise AssertionError("the watermark decoder ran on its own")

    monkeypatch.setattr(prov, "read_watermark", explode)
    result = detect(fx.make_png(tmp_path / "a.png"))
    assert result.checked_watermark is False
    assert result.watermark is None


# --------------------------------------------------------------- declarative
def test_generator_parameters_are_declarative(tmp_path: Path):
    params = ("a cat\nNegative prompt: blur\n"
              "Steps: 20, Sampler: Euler, Seed: 1, Model: sdxl")
    path = fx.make_png(tmp_path / "ai.png", ai_params=params)
    result = detect(path)
    assert result.verdict == VERDICT_SIGNALS
    assert "Stable Diffusion" in result.declared


def test_digital_source_type_in_xmp(tmp_path: Path):
    """The IPTC field Google and Meta write is picked up from XMP."""
    packet = fx.XMP_TEMPLATE.format(title="t", creator="c", city="x").replace(
        "<photoshop:City>x</photoshop:City>",
        '<Iptc4xmpExt:DigitalSourceType>'
        'http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia'
        '</Iptc4xmpExt:DigitalSourceType>')
    path = tmp_path / "declared.png"
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_itxt("XML:com.adobe.xmp", packet)
    fx.base_image().save(path, format="PNG", pnginfo=info)

    result = detect(path)
    assert result.declared is not None
    assert "trainedAlgorithmicMedia" in result.declared
    assert result.verdict == VERDICT_SIGNALS


@pytest.mark.parametrize("value,expected", [
    ("http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia",
     "trainedAlgorithmicMedia"),
    ("http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia",
     "compositeWithTrainedAlgorithmicMedia"),
    ("trainedAlgorithmicMedia", "trainedAlgorithmicMedia"),
    ("http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture", None),
    ("nothing here", None),
])
def test_source_type_parsing(value: str, expected):
    assert prov._source_type_from_text(value) == expected


# -------------------------------------------------------------- robustness
def test_missing_file_returns_an_empty_result():
    result = detect("/no/such/image.jpg")
    assert result.verdict == VERDICT_NONE
    assert result.errors and "not found" in result.errors[0].lower()


def test_corrupt_file_does_not_raise(tmp_path: Path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    result = detect(path, check_watermark=True)
    assert isinstance(result.verdict, str), "a corrupt file must still return a result"


def test_output_survives_a_windows_code_page(tmp_path: Path):
    """The terminal block must encode on a cp1252 console."""
    from imgmetamanager.i18n import set_language

    path = fx.make_png(tmp_path / "a.png")
    for language in ("en", "fr"):
        set_language(language)
        text = "\n".join(detect(path).as_lines())
        text.encode("cp1252")  # raises if a character has no representation
        assert "—" not in text, "no em dash"
        assert all(ord(char) < 0x2100 for char in text), "no pictographs"
    set_language("en")


def test_degrades_without_the_optional_packages(tmp_path: Path, monkeypatch):
    """With neither extra installed, detection still runs on what it can read."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.split(".")[0] in ("c2pa", "trustmark"):
            raise ImportError(f"{name} is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    monkeypatch.setattr(prov, "_trustmark_instance", None)

    assert prov.c2pa_available() is False
    assert prov.trustmark_available() is False
    assert set(prov.missing_dependencies()) == {"c2pa-python", "trustmark"}

    params = "a cat\nSteps: 20, Sampler: Euler, Seed: 1"
    path = fx.make_png(tmp_path / "ai.png", ai_params=params)
    result = detect(path, check_watermark=True)

    assert result.c2pa is None, "no C2PA reader, so no manifest"
    assert result.declared is not None, "declarative detection needs no extra"
    assert result.verdict == VERDICT_SIGNALS
    assert result.checked_watermark is False
    assert any("trustmark" in error for error in result.errors)


def test_export_carries_the_provenance(tmp_path: Path):
    from imgmetamanager.core import read_metadata
    from imgmetamanager.core.exporter import metadata_to_string

    path = fx.make_png(tmp_path / "a.png")
    meta = read_metadata(path, with_hash=False)
    payload = json.loads(metadata_to_string(
        [meta], "json", provenance={meta.path: detect(path, meta=meta)}))
    block = payload["images"][0]["provenance"]
    assert block["verdict"] in (VERDICT_SIGNALS, VERDICT_INVALID, VERDICT_NONE)
    assert block["watermark_checked"] is False
    assert "proves nothing" in block["disclaimer"]
