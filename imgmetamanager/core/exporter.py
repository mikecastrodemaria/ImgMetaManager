"""Metadata export: JSON, CSV, TXT, Markdown and HTML.

JSON and CSV keep a stable English schema so scripts can rely on them. TXT,
Markdown and HTML follow the language selected through
:mod:`imgmetamanager.i18n`.
"""
from __future__ import annotations

import csv
import html
import io
import json
import os
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..i18n import colon, current_language, t
from .model import ImageMeta, MetaItem
from .utils import safe_name

#: Supported export formats mapped to their file extension.
EXPORT_FORMATS: "OrderedDict[str, str]" = OrderedDict([
    ("json", ".json"),
    ("csv", ".csv"),
    ("txt", ".txt"),
    ("markdown", ".md"),
    ("html", ".html"),
])

#: Clipboard layouts offered in the interface.
CLIPBOARD_FORMATS = ("Tag = value", "TSV (spreadsheet)", "JSON", "Values only")


def _filter(meta: ImageMeta, groups: Optional[Iterable[str]], query: str,
            sensitive_only: bool) -> List[MetaItem]:
    """Select the entries matching the group, search and sensitivity filters."""
    wanted = set(groups) if groups else None
    needle = query.strip().lower()
    result = []
    for item in meta.items:
        if wanted is not None and item.group not in wanted:
            continue
        if sensitive_only and not item.sensitive:
            continue
        if needle and needle not in item.tag.lower() and needle not in item.value.lower():
            continue
        result.append(item)
    return result


# ------------------------------------------------------------------- renderers
def _to_json(metas: Sequence[ImageMeta], selected: Dict[str, List[MetaItem]],
             provenance: Optional[Dict[str, Any]] = None) -> str:
    """Render a machine-readable document; repeated tags collapse into lists."""
    payload = {
        "generator": "ImgMetaManager",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "image_count": len(metas),
        "images": [],
    }
    for meta in metas:
        groups: "OrderedDict[str, OrderedDict[str, object]]" = OrderedDict()
        for item in selected[meta.path]:
            bucket = groups.setdefault(item.group, OrderedDict())
            if item.tag in bucket:
                existing = bucket[item.tag]
                if isinstance(existing, list):
                    existing.append(item.value)
                else:
                    bucket[item.tag] = [existing, item.value]
            else:
                bucket[item.tag] = item.value
        entry = {
            "file": meta.filename,
            "path": meta.path,
            "format": meta.file_format,
            "width": meta.width,
            "height": meta.height,
            "size_bytes": meta.file_size,
            "metadata_count": meta.metadata_count,
            "sensitive_count": meta.sensitive_count,
            "groups": groups,
        }
        if meta.warnings:
            entry["warnings"] = meta.warnings
        if not meta.ok:
            entry["error"] = meta.error
        found = (provenance or {}).get(meta.path)
        if found is not None:
            entry["provenance"] = found.to_dict()
        payload["images"].append(entry)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _to_csv(metas: Sequence[ImageMeta], selected: Dict[str, List[MetaItem]],
            delimiter: str = ",") -> str:
    """Render one row per entry, with CRLF endings for spreadsheet software."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL,
                        lineterminator="\r\n")
    # English headers: CSV is a machine format, so its schema stays stable.
    writer.writerow(["file", "path", "group", "tag", "value", "sensitive"])
    for meta in metas:
        for item in selected[meta.path]:
            writer.writerow([
                meta.filename, meta.path, item.group, item.tag,
                item.value.replace("\r\n", "\n"), "true" if item.sensitive else "",
            ])
    return buffer.getvalue()


def _to_txt(metas: Sequence[ImageMeta], selected: Dict[str, List[MetaItem]],
            group_labels: Optional[Dict[str, str]] = None) -> str:
    """Render an aligned plain-text report, sensitive entries starred."""
    labels = group_labels or {}
    lines: List[str] = []
    for index, meta in enumerate(metas):
        if index:
            lines.append("")
        header = f"=== {meta.filename} ==="
        lines.append(header)
        width_label = max(len(t("export_path")), len(t("export_dimensions")), 10)
        lines.append(f"{t('export_path').ljust(width_label)} : {meta.path}")
        if meta.dimensions:
            lines.append(f"{t('export_dimensions').ljust(width_label)} : "
                         f"{meta.dimensions} px ({meta.file_format})")
        if not meta.ok:
            lines.append(f"{t('export_error').ljust(width_label)} : {meta.error}")
        current_group = None
        width = max((len(item.tag) for item in selected[meta.path]), default=0)
        for item in selected[meta.path]:
            if item.group != current_group:
                current_group = item.group
                lines.append("")
                lines.append(f"[{labels.get(current_group, current_group).upper()}]")
            marker = " *" if item.sensitive else "  "
            value = item.value.replace("\n", "\n" + " " * (width + 5))
            lines.append(f"{marker}{item.tag.ljust(width)} : {value}")
    lines.append("")
    lines.append(t("export_legend"))
    return "\n".join(lines)


def _to_markdown(metas: Sequence[ImageMeta], selected: Dict[str, List[MetaItem]],
                 group_labels: Optional[Dict[str, str]] = None) -> str:
    """Render one Markdown table per group, sensitive tags in bold."""
    labels = group_labels or {}
    stamp = datetime.now().strftime("%d/%m/%Y %H:%M")
    lines: List[str] = [f"# {t('export_doc_title')}", "",
                        f"*{t('export_generated', date=stamp)}*", ""]
    for meta in metas:
        lines.append(f"## {meta.filename}")
        lines.append("")
        lines.append(f"- {t('export_path')}{colon()}`{meta.path}`")
        if meta.dimensions:
            lines.append(f"- {t('export_dimensions')}{colon()}{meta.dimensions} px, "
                         f"{meta.file_format}")
        lines.append(f"- {t('export_entries')}{colon()}{meta.metadata_count} "
                     f"({t('export_of_which', count=meta.sensitive_count)})")
        lines.append("")
        current_group = None
        for item in selected[meta.path]:
            if item.group != current_group:
                if current_group is not None:
                    lines.append("")
                current_group = item.group
                lines.append(f"### {labels.get(current_group, current_group)}")
                lines.append("")
                lines.append(f"| {t('export_col_tag')} | {t('export_col_value')} |")
                lines.append("| --- | --- |")
            value = item.value.replace("|", "\\|").replace("\n", "<br>")
            tag = f"**{item.tag}**" if item.sensitive else item.tag
            lines.append(f"| {tag} | {value} |")
        lines.append("")
    return "\n".join(lines)


_HTML_CSS = """
body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;margin:2rem auto;
max-width:60rem;padding:0 1rem;color:#1c1c1e;background:#fff}
h1{font-size:1.6rem}h2{margin-top:2.5rem;border-bottom:2px solid #eee;padding-bottom:.3rem}
h3{margin:1.5rem 0 .4rem;font-size:1rem;text-transform:uppercase;letter-spacing:.05em;color:#666}
table{border-collapse:collapse;width:100%;font-size:.9rem}
td{border-bottom:1px solid #eee;padding:.35rem .5rem;vertical-align:top;word-break:break-word}
td.k{width:16rem;font-weight:600;color:#333}tr.s td.k::after{content:' ⚠';color:#c0392b}
.meta{color:#666;font-size:.85rem}
@media(prefers-color-scheme:dark){body{background:#161618;color:#e8e8ea}
h2{border-color:#333}td{border-color:#2a2a2e}td.k{color:#cfcfd4}.meta{color:#9a9aa2}}
"""


def _to_html(metas: Sequence[ImageMeta], selected: Dict[str, List[MetaItem]],
             group_labels: Optional[Dict[str, str]] = None) -> str:
    """Render a self-contained page that follows the reader's colour scheme."""
    labels = group_labels or {}
    title = html.escape(t("export_doc_title"))
    stamp = datetime.now().strftime("%d/%m/%Y %H:%M")
    out = [f"<!doctype html><html lang=\"{current_language()}\"><head><meta charset=\"utf-8\">",
           "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
           f"<title>{title}</title>", f"<style>{_HTML_CSS}</style></head><body>",
           f"<h1>{title}</h1>",
           f"<p class=\"meta\">{html.escape(t('export_generated', date=stamp))}, "
           f"{html.escape(t('export_images', count=len(metas)))}</p>"]
    for meta in metas:
        out.append(f"<h2>{html.escape(meta.filename)}</h2>")
        out.append(f"<p class=\"meta\">{html.escape(meta.path)}<br>"
                   f"{html.escape(meta.dimensions)} px · {html.escape(meta.file_format)} · "
                   f"{meta.metadata_count} {html.escape(t('export_entries')).lower()}</p>")
        current_group = None
        for item in selected[meta.path]:
            if item.group != current_group:
                if current_group is not None:
                    out.append("</table>")
                current_group = item.group
                out.append(f"<h3>{html.escape(labels.get(current_group, current_group))}</h3>")
                out.append("<table>")
            row_class = ' class="s"' if item.sensitive else ""
            out.append(f"<tr{row_class}><td class=\"k\">{html.escape(item.tag)}</td>"
                       f"<td>{html.escape(item.value)}</td></tr>")
        if current_group is not None:
            out.append("</table>")
    out.append("</body></html>")
    return "\n".join(out)


# ------------------------------------------------------------------------- API
def metadata_to_string(
    metas: Sequence[ImageMeta],
    fmt: str = "json",
    *,
    groups: Optional[Iterable[str]] = None,
    query: str = "",
    sensitive_only: bool = False,
    group_labels: Optional[Dict[str, str]] = None,
    csv_delimiter: str = ",",
    provenance: Optional[Dict[str, Any]] = None,
) -> str:
    """Serialise one or more analyses into the requested format.

    Args:
        metas: The analyses to render.
        fmt: One of the keys of :data:`EXPORT_FORMATS`.
        groups: Keep only these group keys.
        query: Case-insensitive substring matched against tags and values.
        sensitive_only: Keep only potentially identifying entries.
        group_labels: Group key mapped to its display label, for the human formats.
        csv_delimiter: Field separator for CSV; use ``";"`` for French Excel.
        provenance: AI provenance results keyed by image path. Included in the
            JSON output only, which is the format meant to be parsed.

    Raises:
        ValueError: if ``fmt`` is not a known format.
    """
    key = fmt.strip().lower()
    if key not in EXPORT_FORMATS:
        raise ValueError(f"Unknown export format: {fmt}")
    selected = {meta.path: _filter(meta, groups, query, sensitive_only) for meta in metas}
    if key == "json":
        return _to_json(metas, selected, provenance)
    if key == "csv":
        return _to_csv(metas, selected, csv_delimiter)
    if key == "txt":
        return _to_txt(metas, selected, group_labels)
    if key == "markdown":
        return _to_markdown(metas, selected, group_labels)
    return _to_html(metas, selected, group_labels)


def export_metadata(
    metas: Sequence[ImageMeta],
    fmt: str,
    destination: os.PathLike,
    *,
    groups: Optional[Iterable[str]] = None,
    query: str = "",
    sensitive_only: bool = False,
    group_labels: Optional[Dict[str, str]] = None,
    csv_delimiter: str = ",",
    provenance: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write the export to a file and return its path.

    CSV is written as UTF-8 with a byte-order mark so Excel opens accented
    characters correctly; every other format is plain UTF-8.
    """
    text = metadata_to_string(metas, fmt, groups=groups, query=query,
                              sensitive_only=sensitive_only, group_labels=group_labels,
                              csv_delimiter=csv_delimiter, provenance=provenance)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoding = "utf-8-sig" if fmt.lower() == "csv" else "utf-8"
    path.write_text(text, encoding=encoding, newline="")
    return path


def export_per_image_zip(
    metas: Sequence[ImageMeta],
    fmt: str,
    destination: os.PathLike,
    **kwargs,
) -> Path:
    """Write one export document per image into a single ZIP archive.

    Files are named after each image; duplicate names get a numeric suffix.
    """
    suffix = EXPORT_FORMATS[fmt.lower()]
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        used: Dict[str, int] = {}
        for meta in metas:
            base = safe_name(Path(meta.filename).stem)
            count = used.get(base, 0)
            used[base] = count + 1
            name = f"{base}{'' if not count else f'-{count}'}{suffix}"
            archive.writestr(name, metadata_to_string([meta], fmt, **kwargs))
    return path


def rows_to_text(rows: Sequence[Sequence[str]], style: str = CLIPBOARD_FORMATS[0]) -> str:
    """Format ``[group, tag, value]`` rows for the clipboard.

    Args:
        rows: The selected table rows.
        style: One of :data:`CLIPBOARD_FORMATS`.
    """
    if not rows:
        return ""
    if style == "TSV (spreadsheet)":
        return "\n".join("\t".join(str(cell) for cell in row) for row in rows)
    if style == "JSON":
        return json.dumps(
            OrderedDict((str(row[1]), str(row[2])) for row in rows),
            ensure_ascii=False, indent=2)
    if style == "Values only":
        return "\n".join(str(row[2]) for row in rows)
    return "\n".join(f"{row[1]} = {row[2]}" for row in rows)
