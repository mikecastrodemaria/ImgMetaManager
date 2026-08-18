"""Shared helpers: paths, sizes, dates, hashing."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

#: File extensions the folder scanner treats as images.
IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".jif",
    ".png", ".apng",
    ".webp",
    ".tif", ".tiff",
    ".gif", ".bmp", ".dib", ".ico",
    ".heic", ".heif", ".avif",
    ".ppm", ".pgm", ".pbm", ".pnm",
    ".tga", ".jp2", ".j2k", ".jpf", ".jpx",
}

_UNITS = ("B", "KB", "MB", "GB", "TB")


def human_size(num_bytes: int) -> str:
    """Format a byte count for humans: ``1536`` becomes ``'1.5 KB'``."""
    size = float(num_bytes)
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def iso_time(timestamp: float) -> str:
    """Format a POSIX timestamp as ``YYYY-MM-DD HH:MM:SS``, or ``''`` on failure."""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return ""


def sha256_of(path: os.PathLike, chunk_size: int = 1 << 20) -> str:
    """Return the SHA-256 digest of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_image_file(path: os.PathLike) -> bool:
    """Tell whether a path carries a known image extension."""
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def scan_folder(folder: os.PathLike, recursive: bool = False) -> List[Path]:
    """List the image files of a folder, sorted case-insensitively.

    Raises:
        NotADirectoryError: if ``folder`` is not a directory.
    """
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    walker: Iterable[Path] = root.rglob("*") if recursive else root.glob("*")
    return sorted(
        (p for p in walker if p.is_file() and is_image_file(p)),
        key=lambda p: str(p).lower(),
    )


_UNSAFE = re.compile(r"[^A-Za-z0-9._\- ]+")


def safe_name(name: str, fallback: str = "image") -> str:
    """Turn any string into a file name valid on the three operating systems.

    Windows rejects ``: ? * < > | " \\ /`` in file names, so every character
    outside a conservative allow-list is replaced with an underscore.
    """
    cleaned = _UNSAFE.sub("_", name).strip(" .")
    return cleaned or fallback


def unique_path(path: os.PathLike) -> Path:
    """Return a path that does not exist yet, appending ``-1``, ``-2``, ..."""
    candidate = Path(path)
    if not candidate.exists():
        return candidate
    stem, suffix, parent = candidate.stem, candidate.suffix, candidate.parent
    index = 1
    while True:
        alt = parent / f"{stem}-{index}{suffix}"
        if not alt.exists():
            return alt
        index += 1


def truncate(text: str, limit: int = 4000) -> str:
    """Shorten ``text`` to ``limit`` characters and report how much was cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [+{len(text) - limit} characters]"


def hex_preview(data: bytes, length: int = 16) -> str:
    """Render the first bytes of a blob as spaced hexadecimal."""
    head = data[:length].hex(" ")
    return head + ("…" if len(data) > length else "")


def decode_text(data: bytes) -> Optional[str]:
    """Decode a binary blob to text when it plausibly is text.

    Tries UTF-8, both UTF-16 byte orders, then Latin-1, and accepts the result
    only when over 90% of its characters are printable.

    Returns:
        The decoded string, or ``None`` when the blob looks binary.
    """
    for encoding in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        text = text.replace("\x00", "").strip()
        if not text:
            continue
        printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
        if printable / max(len(text), 1) > 0.9:
            return text
    return None
