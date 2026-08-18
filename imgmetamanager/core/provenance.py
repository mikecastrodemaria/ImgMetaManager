"""Read-only detection of AI provenance signals.

Three independent detectors, each backed by an optional dependency that the
module degrades away from cleanly when it is missing:

1. **C2PA / Content Credentials** (``c2pa-python``): a cryptographically signed
   manifest embedded in the file, listing the tool that produced it and the
   certificate that signed it.
2. **TrustMark watermark** (``trustmark``): an invisible watermark Adobe's
   pipeline embeds in the pixels, which survives resizing and re-encoding. The
   decoder loads a model on first use, so it only runs when asked for.
3. **Declarative metadata**: generative parameters written by AUTOMATIC1111 or
   ComfyUI, and the IPTC ``DigitalSourceType`` field Google and Meta write. This
   layer is plain text: anyone can write it, and anyone can erase it.

What the result does and does not mean
--------------------------------------
A found signal tells you something about the file's history. The **absence** of
a signal proves nothing at all: signatures and watermarks are stripped by a
screenshot, a re-encode, or a metadata cleaner, and the vast majority of AI
images never carried one. This module therefore never reports an image as
authentic or as human-made. Three outcomes exist: signals found, signals found
but invalid, and no signal, which is inconclusive.

The module reads. It never writes to the image.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..i18n import t

#: Verdict returned when at least one valid signal was found.
VERDICT_SIGNALS = "signals"
#: Verdict returned when a signal exists but does not validate.
VERDICT_INVALID = "invalid"
#: Verdict returned when no signal was found. This is not evidence of anything.
VERDICT_NONE = "none"

#: IPTC digital source types that describe generated or partly generated media.
#: https://cv.iptc.org/newscodes/digitalsourcetype/
AI_SOURCE_TYPES = {
    "trainedalgorithmicmedia": "trainedAlgorithmicMedia",
    "compositewithtrainedalgorithmicmedia": "compositeWithTrainedAlgorithmicMedia",
    "algorithmicmedia": "algorithmicMedia",
    "algorithmicallyenhanced": "algorithmicallyEnhanced",
}

#: IPTC always writes the full newscodes URI, so the path segment is the value.
_SOURCE_TYPE_RE = re.compile(r"digitalsourcetype/([A-Za-z]+)", re.IGNORECASE)

#: C2PA validation states that mean the signature checks out.
_VALID_STATES = {"valid", "trusted"}


# --------------------------------------------------------------- availability
def c2pa_available() -> bool:
    """Tell whether the optional ``c2pa-python`` package is installed."""
    try:
        import c2pa
    except Exception:  # noqa: BLE001 - a broken install must read as absent
        return False
    return c2pa is not None


def trustmark_available() -> bool:
    """Tell whether the optional ``trustmark`` package is installed."""
    try:
        import trustmark
    except Exception:  # noqa: BLE001
        return False
    return trustmark is not None


def missing_dependencies() -> List[str]:
    """List the optional packages that would widen the detection."""
    missing = []
    if not c2pa_available():
        missing.append("c2pa-python")
    if not trustmark_available():
        missing.append("trustmark")
    return missing


# ------------------------------------------------------------------- result
@dataclass
class Provenance:
    """What the detectors found in one file."""

    path: str = ""

    c2pa: Optional[Dict[str, Any]] = None
    """Summary of the active C2PA manifest, or ``None`` when there is none."""

    watermark: Optional[Tuple[bool, str]] = None
    """``(present, payload)`` from TrustMark, or ``None`` when not checked."""

    declared: Optional[str] = None
    """Declarative signals found in the metadata, as one readable line."""

    checked_watermark: bool = False
    """True when the watermark decoder actually ran."""

    errors: List[str] = field(default_factory=list)
    """Non-fatal problems, such as a detector that could not run."""

    @property
    def c2pa_valid(self) -> Optional[bool]:
        """Whether the C2PA signature validates, or ``None`` without a manifest."""
        if not self.c2pa:
            return None
        return bool(self.c2pa.get("valid"))

    @property
    def has_watermark(self) -> bool:
        """Whether the watermark decoder found a payload."""
        return bool(self.watermark and self.watermark[0])

    @property
    def verdict(self) -> str:
        """One of :data:`VERDICT_SIGNALS`, :data:`VERDICT_INVALID`, :data:`VERDICT_NONE`.

        A manifest that fails validation outranks the other signals: it means the
        file was altered after it was signed, which the reader needs to know
        before anything else.
        """
        if self.c2pa is not None and not self.c2pa_valid:
            return VERDICT_INVALID
        if self.c2pa or self.has_watermark or self.declared:
            return VERDICT_SIGNALS
        return VERDICT_NONE

    @property
    def verdict_label(self) -> str:
        """The verdict as a translated sentence."""
        return t(f"prov_verdict_{self.verdict}")

    def details(self) -> List[Tuple[str, str]]:
        """Readable ``(label, value)`` pairs describing every signal found."""
        rows: List[Tuple[str, str]] = []
        if self.c2pa:
            rows.append((t("prov_c2pa_generator"), self.c2pa.get("claim_generator") or "?"))
            if self.c2pa.get("issuer"):
                rows.append((t("prov_c2pa_issuer"), self.c2pa["issuer"]))
            if self.c2pa.get("signed_at"):
                rows.append((t("prov_c2pa_signed_at"), self.c2pa["signed_at"]))
            if self.c2pa.get("digital_source_type"):
                rows.append((t("prov_c2pa_source_type"),
                             self.c2pa["digital_source_type"]))
            rows.append((t("prov_c2pa_state"), self.c2pa.get("validation_state") or "?"))
        if self.checked_watermark:
            if self.has_watermark:
                rows.append((t("prov_watermark"),
                             t("prov_watermark_found", payload=self.watermark[1])))
            else:
                rows.append((t("prov_watermark"), t("prov_watermark_absent")))
        if self.declared:
            rows.append((t("prov_declared"), self.declared))
        return rows

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-ready mapping, for the export formats."""
        payload: Dict[str, Any] = {
            "verdict": self.verdict,
            "verdict_label": self.verdict_label,
            "c2pa": self.c2pa,
            "declared": self.declared,
            "watermark_checked": self.checked_watermark,
            "disclaimer": t("prov_disclaimer"),
        }
        if self.checked_watermark and self.watermark is not None:
            payload["watermark"] = {"present": self.watermark[0], "payload": self.watermark[1]}
        if self.errors:
            payload["errors"] = self.errors
        return payload

    def as_lines(self) -> List[str]:
        """Plain ASCII lines for a terminal, safe on a Windows code page."""
        lines = [self.verdict_label]
        lines.extend(f"  {label}: {value}" for label, value in self.details())
        lines.extend(f"  ! {problem}" for problem in self.errors)
        lines.append(f"  {t('prov_disclaimer')}")
        return lines


# --------------------------------------------------------------------- C2PA
def read_c2pa(path: os.PathLike) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read the active C2PA manifest of a file.

    Returns:
        A ``(manifest, error)`` pair. Both are ``None`` when the file simply
        carries no manifest, which is the ordinary case and not a failure.
    """
    try:
        import c2pa
    except Exception:  # noqa: BLE001
        return None, None

    try:
        reader = c2pa.Reader(str(path))
    except c2pa.C2paError.ManifestNotFound:
        return None, None  # the normal case: no Content Credentials in this file
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "ManifestNotFound" in message or "no JUMBF data" in message:
            return None, None
        return None, t("prov_c2pa_error", error=message)

    try:
        data = json.loads(reader.json())
        active = data.get("manifests", {}).get(data.get("active_manifest", ""), {})
        signature = active.get("signature_info") or {}
        state = data.get("validation_state") or _validation_state(reader)
        manifest = {
            "claim_generator": active.get("claim_generator"),
            "title": active.get("title"),
            "format": active.get("format"),
            "issuer": signature.get("issuer"),
            "signed_at": signature.get("time"),
            "algorithm": signature.get("alg"),
            "validation_state": state,
            "valid": str(state or "").strip().lower() in _VALID_STATES,
            "embedded": _is_embedded(reader),
            "digital_source_type": _assertion_source_type(active),
        }
        return manifest, None
    except Exception as exc:  # noqa: BLE001
        return None, t("prov_c2pa_error", error=exc)
    finally:
        try:
            reader.close()
        except Exception:  # noqa: BLE001
            pass


def _assertion_source_type(manifest: Dict[str, Any]) -> Optional[str]:
    """Read the digital source type declared inside the signed assertions.

    C2PA producers record it in their ``c2pa.actions`` assertion. Coming from
    the signed manifest, it carries far more weight than the same field sitting
    unsigned in XMP, so it is reported with the manifest rather than with the
    declarative layer.
    """
    for assertion in manifest.get("assertions") or []:
        found = _source_type_from_text(json.dumps(assertion.get("data") or {}))
        if found:
            return found
    return None


def _validation_state(reader: Any) -> Optional[str]:
    """Read the validation state, tolerating API differences between versions."""
    try:
        state = reader.get_validation_state()
    except Exception:  # noqa: BLE001
        return None
    return state if isinstance(state, str) else str(state)


def _is_embedded(reader: Any) -> Optional[bool]:
    """Tell whether the manifest travels inside the file or points elsewhere."""
    try:
        value = reader.is_embedded
        return bool(value() if callable(value) else value)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- TrustMark
_trustmark_instance: Any = None
_trustmark_lock = threading.Lock()


def _get_trustmark() -> Any:
    """Return the shared TrustMark decoder, building it on first use.

    Construction downloads roughly 40 MB of model weights and takes a few
    seconds, so it happens once per process and only when a watermark check is
    actually requested. The decoder is pinned to the CPU: this is a desktop
    tool, and a GPU brings nothing to a 0.1 second decode.
    """
    global _trustmark_instance
    with _trustmark_lock:
        if _trustmark_instance is None:
            from trustmark import TrustMark

            _trustmark_instance = TrustMark(
                verbose=False, model_type="Q", device="cpu", loadRemover=False)
        return _trustmark_instance


def read_watermark(path: os.PathLike) -> Tuple[Optional[Tuple[bool, str]], Optional[str]]:
    """Decode the invisible TrustMark watermark of an image.

    Returns:
        A ``((present, payload), error)`` pair. The first element is ``None``
        when the decoder could not run at all.
    """
    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    if not trustmark_available():
        return None, t("prov_needs", package="trustmark")
    try:
        decoder = _get_trustmark()
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            secret, present, _schema = decoder.decode(rgb)
        return (bool(present), str(secret) if present else ""), None
    except Exception as exc:  # noqa: BLE001
        return None, t("prov_watermark_error", error=exc)


# ------------------------------------------------------- declarative metadata
def _source_type_from_text(text: str) -> Optional[str]:
    """Find an AI digital source type inside a blob of metadata text.

    Matches the newscodes URI first, then a bare value for the writers that
    store one. Longer names are tested first, so
    ``compositeWithTrainedAlgorithmicMedia`` is never reported as
    ``trainedAlgorithmicMedia``.
    """
    for match in _SOURCE_TYPE_RE.finditer(text):
        candidate = match.group(1).lower()
        if candidate in AI_SOURCE_TYPES:
            return AI_SOURCE_TYPES[candidate]
    lowered = text.lower()
    for key in sorted(AI_SOURCE_TYPES, key=len, reverse=True):
        if key in lowered:
            return AI_SOURCE_TYPES[key]
    return None


def read_declared(path: os.PathLike, meta: Any = None) -> Tuple[Optional[str], Optional[str]]:
    """Collect the declarative signals: generator parameters and source type.

    Args:
        path: The image to inspect.
        meta: An :class:`~imgmetamanager.core.model.ImageMeta` already produced
            for this file, reused instead of reading it again.

    Returns:
        A ``(summary, error)`` pair, the summary being one readable line or
        ``None`` when nothing declarative was found.
    """
    signals: List[str] = []
    try:
        if meta is None:
            from .reader import read_metadata

            meta = read_metadata(path, with_hash=False)
        generator = meta.find("Generator", "ai") if meta.ok else None
        if generator is not None:
            signals.append(t("prov_declared_generator", name=generator.value))

        source_type = None
        for item in (meta.items if meta.ok else []):
            if "digitalsourcetype" in item.tag.lower():
                source_type = _source_type_from_text(item.value) or \
                    _source_type_from_text(item.tag + ":" + item.value)
                if source_type:
                    break
        if source_type is None and (not meta.ok or "xmp" in meta.group_names()):
            # Fall back to the raw XMP packets, for the writers whose layout the
            # flattener does not surface. Scanning the whole file instead would
            # pick up the copy inside a signed C2PA manifest and mislabel it as
            # unsigned metadata. Skipped when the analysis found no XMP at all,
            # so switching images never re-reads a large file for nothing.
            from . import containers as ct

            for packet in ct.find_xmp_packets(Path(path).read_bytes()):
                source_type = _source_type_from_text(packet)
                if source_type:
                    break
        if source_type:
            signals.append(t("prov_declared_source_type", value=source_type))
    except Exception as exc:  # noqa: BLE001
        return (", ".join(signals) or None), str(exc)
    return (", ".join(signals) or None), None


# --------------------------------------------------------------------- API
def detect(path: os.PathLike, check_watermark: bool = False,
           meta: Any = None) -> Provenance:
    """Look for AI provenance signals in one file.

    C2PA manifests and declarative metadata are read every time: both are cheap.
    The invisible watermark is only decoded when ``check_watermark`` is set,
    because the first call loads a model.

    Never raises. A corrupt or unreadable file yields an empty result whose
    ``errors`` list explains what went wrong.

    Args:
        path: The image to inspect.
        check_watermark: Run the TrustMark decoder as well.
        meta: A metadata analysis already produced for this file, reused instead
            of reading it a second time.
    """
    result = Provenance(path=str(path))
    try:
        if not Path(path).is_file():
            result.errors.append(t("err_not_found", path=path))
            return result
    except Exception as exc:  # noqa: BLE001
        result.errors.append(str(exc))
        return result

    manifest, error = read_c2pa(path)
    result.c2pa = manifest
    if error:
        result.errors.append(error)

    declared, error = read_declared(path, meta)
    result.declared = declared
    if error:
        result.errors.append(error)

    if check_watermark:
        result.checked_watermark = True
        watermark, error = read_watermark(path)
        result.watermark = watermark
        if error:
            result.errors.append(error)
            result.checked_watermark = watermark is not None
    return result
