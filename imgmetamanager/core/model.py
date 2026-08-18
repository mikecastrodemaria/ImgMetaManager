"""Data structures describing the metadata of an image."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .tags import GROUP_ORDER


@dataclass
class MetaItem:
    """A single metadata entry: a tag, its readable value and its origin."""

    group: str
    """Group key such as ``exif``, ``gps`` or ``iptc``."""

    tag: str
    """Standard tag name, left untranslated."""

    value: str
    """Value formatted for display."""

    raw: Any = None
    """Raw value as read from the file. May not be JSON-serialisable."""

    removable: Optional[str] = None
    """Removal category, or ``None`` when the entry cannot be removed."""

    sensitive: bool = False
    """True when the entry can identify a person, a place or a device."""

    note: str = ""
    """Short explanation shown alongside the value."""

    def as_dict(self) -> Dict[str, Any]:
        """Return a compact dict, omitting the fields left at their default."""
        data: Dict[str, Any] = {"group": self.group, "tag": self.tag, "value": self.value}
        if self.sensitive:
            data["sensitive"] = True
        if self.note:
            data["note"] = self.note
        return data


@dataclass
class ImageMeta:
    """Everything the reader found in one image file."""

    path: str
    filename: str
    ok: bool = True
    """False when the file could not be read; see :attr:`error`."""
    error: str = ""
    items: List[MetaItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    file_format: str = ""
    width: int = 0
    height: int = 0
    mode: str = ""
    file_size: int = 0
    preview_path: Optional[str] = None
    """Path to a generated preview image, when one was produced."""

    # ------------------------------------------------------------------ access
    def add(self, item: MetaItem) -> None:
        """Append one entry."""
        self.items.append(item)

    def groups(self) -> "OrderedDict[str, List[MetaItem]]":
        """Group the entries, following the canonical display order.

        Unexpected groups, if any, are appended after the known ones.
        """
        buckets: "OrderedDict[str, List[MetaItem]]" = OrderedDict()
        for key in GROUP_ORDER:
            found = [item for item in self.items if item.group == key]
            if found:
                buckets[key] = found
        for item in self.items:
            if item.group not in buckets:
                buckets.setdefault(item.group, []).append(item)
        return buckets

    def group_names(self) -> List[str]:
        """Return the keys of the groups actually present in this image."""
        return list(self.groups().keys())

    def removable_counts(self) -> "OrderedDict[str, int]":
        """Count the removable entries per category."""
        counts: "OrderedDict[str, int]" = OrderedDict()
        for item in self.items:
            if item.removable:
                counts[item.removable] = counts.get(item.removable, 0) + 1
        return counts

    @property
    def metadata_count(self) -> int:
        """Number of entries that come from the file itself.

        Filesystem and image-geometry entries are excluded: they describe the
        file rather than the metadata stored inside it.
        """
        return sum(1 for item in self.items if item.group not in ("file", "image"))

    @property
    def sensitive_count(self) -> int:
        """Number of potentially identifying entries."""
        return sum(1 for item in self.items if item.sensitive)

    @property
    def dimensions(self) -> str:
        """Pixel dimensions as ``'width × height'``, or ``''`` when unknown."""
        return f"{self.width} × {self.height}" if self.width and self.height else ""

    def find(self, tag: str, group: Optional[str] = None) -> Optional[MetaItem]:
        """Return the first entry matching ``tag``, optionally within ``group``."""
        for item in self.items:
            if item.tag == tag and (group is None or item.group == group):
                return item
        return None

    # ----------------------------------------------------------- serialisation
    def to_dict(self, include_file_info: bool = True) -> Dict[str, Any]:
        """Return a JSON-ready mapping of the whole analysis.

        Repeated tags inside a group collapse into a list of values.
        """
        groups: "OrderedDict[str, Any]" = OrderedDict()
        for key, items in self.groups().items():
            if not include_file_info and key in ("file", "image"):
                continue
            bucket: "OrderedDict[str, Any]" = OrderedDict()
            for item in items:
                if item.tag in bucket:
                    existing = bucket[item.tag]
                    if isinstance(existing, list):
                        existing.append(item.value)
                    else:
                        bucket[item.tag] = [existing, item.value]
                else:
                    bucket[item.tag] = item.value
            groups[key] = bucket
        payload: Dict[str, Any] = {
            "file": self.filename,
            "path": self.path,
            "format": self.file_format,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.file_size,
            "metadata_count": self.metadata_count,
            "sensitive_count": self.sensitive_count,
            "groups": groups,
        }
        if self.warnings:
            payload["warnings"] = self.warnings
        if not self.ok:
            payload["error"] = self.error
        return payload

    def to_rows(self, groups: Optional[Iterable[str]] = None,
                query: str = "", sensitive_only: bool = False) -> List[List[str]]:
        """Return table rows ``[group, tag, value]`` matching the filters.

        Args:
            groups: Keep only these group keys. ``None`` keeps every group.
            query: Case-insensitive substring matched against tag and value.
            sensitive_only: Keep only the potentially identifying entries.
        """
        wanted = set(groups) if groups else None
        needle = query.strip().lower()
        rows: List[List[str]] = []
        for item in self.items:
            if wanted is not None and item.group not in wanted:
                continue
            if sensitive_only and not item.sensitive:
                continue
            if needle and needle not in item.tag.lower() and needle not in item.value.lower():
                continue
            rows.append([item.group, item.tag, item.value])
        return rows
