"""Gradio interface: preview, metadata, export and removal.

:class:`MetaApp` holds every event handler and knows nothing about the widget
tree, so the whole behaviour can be tested without starting a server.
:func:`build_interface` wires those handlers to the Gradio components.
"""
from __future__ import annotations

import getpass
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import gradio as gr

from . import gr_compat as gc
from .core.exporter import (
    CLIPBOARD_FORMATS, EXPORT_FORMATS, export_metadata, export_per_image_zip,
    metadata_to_string, rows_to_text,
)
from .core.model import ImageMeta
from .core.provenance import (
    VERDICT_INVALID, VERDICT_SIGNALS, detect as detect_provenance,
    missing_dependencies, trustmark_available,
)
from .core.reader import SUPPORTED_READ, make_preview, read_metadata
from .core.tags import GROUP_ICC, GROUP_ORDER, REMOVABLE_KINDS
from .core.utils import human_size, is_image_file, safe_name, scan_folder
from .core.writer import DEFAULT_KINDS, plan_removal, strip_metadata
from .i18n import group_label, help_text, t

def _work_dir() -> Path:
    """Return a work directory private to the current user."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - environments without a named user
        user = "default"
    return Path(tempfile.gettempdir()) / f"imgmetamanager-{safe_name(user, 'default')}"


WORK_DIR = _work_dir()
PREVIEW_DIR = WORK_DIR / "previews"
EXPORT_DIR = WORK_DIR / "exports"

#: Export formats the code viewer can syntax-highlight.
_CODE_LANGUAGES = {"json": "json", "markdown": "markdown", "html": "html"}

#: Previews older than this are deleted at start-up, in seconds.
PREVIEW_MAX_AGE = 24 * 3600


def prepare_work_dir() -> None:
    """Create the work directories and delete previews older than a day."""
    for folder in (WORK_DIR, PREVIEW_DIR, EXPORT_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    try:
        WORK_DIR.chmod(0o700)  # meaningful on Unix, a no-op on Windows
    except OSError:
        pass
    cutoff = time.time() - PREVIEW_MAX_AGE
    for stale in PREVIEW_DIR.glob("preview_*.png"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            pass


@dataclass
class Entry:
    """One image loaded in the session."""

    meta: ImageMeta
    origin: str = "upload"
    """``upload`` for a file sent through the browser, ``local`` for a scanned folder."""
    preview: Optional[str] = None
    """Path of the generated preview, or ``None`` when the image failed to decode."""
    signature: Tuple[float, int] = (0.0, 0)
    """Modification time and size, used to skip re-reading unchanged files."""

    @property
    def path(self) -> str:
        return self.meta.path

    @property
    def name(self) -> str:
        return self.meta.filename


# --------------------------------------------------------------------- helpers
def _signature(path: Path) -> Tuple[float, int]:
    """Return the ``(mtime, size)`` pair identifying a file version."""
    try:
        stat = path.stat()
        return (stat.st_mtime, stat.st_size)
    except OSError:
        return (0.0, 0)


_placeholder_cache: Optional[str] = None


def _placeholder() -> str:
    """Return a grey thumbnail standing in for files that failed to decode."""
    global _placeholder_cache
    if _placeholder_cache and Path(_placeholder_cache).exists():
        return _placeholder_cache
    from PIL import Image, ImageDraw

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    target = PREVIEW_DIR / "_illisible.png"
    image = Image.new("RGB", (320, 240), (60, 60, 66))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 309, 229), outline=(150, 150, 160), width=2)
    draw.text((120, 110), "?", fill=(220, 220, 230))
    image.save(target, format="PNG")
    _placeholder_cache = str(target)
    return _placeholder_cache


def analyse(paths: Sequence[os.PathLike], origin: str,
            previous: Optional[Sequence[Entry]] = None) -> List[Entry]:
    """Analyse a list of files, reusing results already computed.

    Args:
        paths: Image files to read.
        origin: ``upload`` or ``local``, recorded on every new entry.
        previous: Entries from a former call; those whose file is unchanged are
            returned as-is instead of being read again.
    """
    cache: Dict[str, Entry] = {}
    for entry in previous or []:
        cache[str(Path(entry.path))] = entry
    entries: List[Entry] = []
    for raw in paths:
        path = Path(raw)
        key = str(path)
        known = cache.get(key)
        signature = _signature(path)
        if known is not None and known.signature == signature:
            entries.append(known)
            continue
        meta = read_metadata(path)
        preview = make_preview(path, PREVIEW_DIR) if meta.ok else None
        meta.preview_path = preview
        entries.append(Entry(meta=meta, origin=origin, preview=preview, signature=signature))
    return entries


def _gallery(entries: Sequence[Entry]) -> List[Tuple[str, str]]:
    """Build the ``(image, caption)`` pairs the gallery expects."""
    return [(entry.preview or _placeholder(), entry.name) for entry in entries]


def _clamp(index: int, entries: Sequence[Entry]) -> int:
    """Keep a selection index inside the bounds of the loaded entries."""
    if not entries:
        return 0
    return max(0, min(int(index or 0), len(entries) - 1))


def _current(entries: Sequence[Entry], index: int) -> Optional[Entry]:
    """Return the selected entry, or ``None`` when nothing is loaded."""
    if not entries:
        return None
    return entries[_clamp(index, entries)]


def _rows(meta: ImageMeta, groups: Optional[Sequence[str]], query: str,
          sensitive_only: bool) -> List[List[str]]:
    """Build the filtered table rows, with group keys replaced by their labels."""
    raw = meta.to_rows(groups=groups or None, query=query or "",
                       sensitive_only=bool(sensitive_only))
    return [[group_label(row[0]), row[1], row[2]] for row in raw]


def _group_choices(meta: ImageMeta) -> List[Tuple[str, str]]:
    """List the ``(label, key)`` choices for the group filter of one image."""
    return [(group_label(key), key) for key in meta.group_names()]


def _kind_choices(meta: Optional[ImageMeta]) -> List[Tuple[str, str]]:
    """List the removal categories, annotated with their count for this image."""
    counts = meta.removable_counts() if meta else {}
    choices = []
    for key in REMOVABLE_KINDS:
        count = counts.get(key, 0)
        label = group_label(key) + (f" ({count})" if count else "")
        choices.append((label, key))
    return choices


def _summary(entry: Optional[Entry]) -> str:
    """Render the Markdown summary card shown next to the preview."""
    if entry is None:
        return f"### —\n{t('msg_no_image')}"
    meta = entry.meta
    if not meta.ok:
        return f"### {meta.filename}\n\n> {t('msg_read_error', error=meta.error)}"
    removable = sum(meta.removable_counts().values())
    lines = [
        f"### {meta.filename}",
        "",
        f"**{meta.metadata_count}** {t('sum_entries')} · "
        f"**{meta.sensitive_count}** {t('sum_sensitive')} · "
        f"**{removable}** {t('sum_removable')}",
        "",
        "| | |",
        "| --- | --- |",
        f"| {t('sum_format')} | {meta.file_format} ({meta.mode}) |",
        f"| {t('sum_dimensions')} | {meta.dimensions} px |",
        f"| {t('sum_size')} | {human_size(meta.file_size)} |",
    ]
    position = meta.find("Position", "gps")
    if position is not None:
        lines.append(f"| GPS | `{position.value}` |")
    generator = meta.find("Generator", "ai")
    if generator is not None:
        lines.append(f"| IA | {generator.value} |")
    lines.append("")
    lines.append(f"<small>`{meta.path}`</small>")
    if meta.warnings:
        lines.append("")
        lines.extend(f"> {warning}" for warning in meta.warnings)
    return "\n".join(lines)


#: Neutral markers. No check mark: a found signal is information, not a verdict
#: of authenticity, and an absent one is not a verdict either.
_VERDICT_ICONS = {VERDICT_SIGNALS: "🔎", VERDICT_INVALID: "⚠️"}


def _provenance_markdown(entry: Optional["Entry"], check_watermark: bool = False) -> str:
    """Render the AI provenance block for the viewer."""
    if entry is None or not entry.meta.ok:
        return f"*{t('msg_no_image')}*"
    result = detect_provenance(entry.path, check_watermark=check_watermark,
                               meta=entry.meta)
    icon = _VERDICT_ICONS.get(result.verdict, "")
    lines = [f"{icon} **{result.verdict_label}**".strip(), ""]
    details = result.details()
    if details:
        lines.append("| | |")
        lines.append("| --- | --- |")
        lines.extend(f"| {label} | {value} |" for label, value in details)
        lines.append("")
    if not result.checked_watermark:
        lines.append(f"*{t('prov_not_checked')}*")
        lines.append("")
    for problem in result.errors:
        lines.append(f"> {problem}")
    missing = missing_dependencies()
    if missing:
        lines.append(f"*{t('prov_install_hint', packages=', '.join(missing))}*")
        lines.append("")
    lines.append(f"<small>{t('prov_disclaimer')}</small>")
    return "\n".join(lines)


def _status(message: str, level: str = "info") -> str:
    """Prefix a status message with an icon matching its severity."""
    icons = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "error": "⛔"}
    return f"{icons.get(level, '')} {message}".strip()


def _servable(path: Path, allowed_roots: Sequence[Path]) -> bool:
    """Tell whether Gradio may serve this file to the browser.

    Only paths under one of the allowed roots can be downloaded.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in allowed_roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


# ----------------------------------------------------------------------- logic
class MetaApp:
    """Every event handler of the interface, free of any widget reference.

    Each method takes the current state plus the widget values and returns the
    new values, which makes them straightforward to call from tests.

    Args:
        allow_local: Allow reading local folders and overwriting originals.
            Turned off in shared mode.
        allowed_roots: Directories Gradio may serve files from.
    """

    def __init__(self, allow_local: bool = True,
                 allowed_roots: Optional[Sequence[Path]] = None) -> None:
        self.allow_local = allow_local
        self.roots = list(allowed_roots or [WORK_DIR])
        prepare_work_dir()

    # ------------------------------------------------------------ shared renders
    def render_view(self, entries: List[Entry], index: int, query: str, sensitive_only: bool):
        """Refresh the whole viewer for the selected image."""
        entry = _current(entries, index)
        meta = entry.meta if entry else None
        groups = [key for key in (meta.group_names() if meta else [])]
        rows = _rows(meta, groups, query, sensitive_only) if meta else []
        default_kinds = [k for k in (meta.removable_counts() if meta else {}) if k != GROUP_ICC]
        return (
            gr.update(value=entry.preview if entry else None),
            _summary(entry),
            gr.update(choices=_group_choices(meta) if meta else [], value=groups),
            gr.update(value=rows),
            gr.update(choices=_kind_choices(meta), value=default_kinds),
            _provenance_markdown(entry),
        )

    def render_table(self, entries: List[Entry], index: int, groups: List[str],
                     query: str, sensitive_only: bool):
        """Refresh only the metadata table, after a filter changed."""
        entry = _current(entries, index)
        if entry is None:
            return gr.update(value=[])
        return gr.update(value=_rows(entry.meta, groups, query, sensitive_only))

    # ------------------------------------------------------------------ loading
    def load_uploads(self, files: Optional[List[str]], entries: List[Entry]):
        """Analyse the uploaded files, keeping the entries scanned from folders."""
        kept = [e for e in (entries or []) if e.origin != "upload"]
        fresh = analyse([f for f in (files or []) if is_image_file(f)], "upload", entries)
        merged = kept + fresh
        message = t("msg_loaded", count=len(merged)) if merged else t("msg_no_image")
        return merged, 0, gr.update(value=_gallery(merged), selected_index=0 if merged else None), \
            _status(message, "ok" if merged else "info")

    def load_folder(self, folder: str, recursive: bool, entries: List[Entry]):
        """Scan a local folder, keeping the entries that came from uploads."""
        current = list(entries or [])
        if not self.allow_local:
            return current, 0, gr.update(), _status(t("msg_folder_blocked"), "warn")
        target = (folder or "").strip().strip('"').strip("'")
        if not target:
            return current, 0, gr.update(), _status(t("msg_folder_missing", folder="—"), "warn")
        try:
            found = scan_folder(target, recursive)
        except (NotADirectoryError, OSError):
            return current, 0, gr.update(), _status(t("msg_folder_missing", folder=target), "error")
        if not found:
            return current, 0, gr.update(), _status(t("msg_folder_empty"), "warn")
        kept = [e for e in current if e.origin != "local"]
        merged = kept + analyse(found, "local", current)
        return merged, 0, gr.update(value=_gallery(merged), selected_index=0), \
            _status(t("msg_loaded", count=len(merged)), "ok")

    def clear_all(self):
        """Drop every loaded image and reset the upload widget."""
        return [], 0, gr.update(value=[]), _status(t("msg_no_image")), gr.update(value=None)

    def reload_all(self, entries: List[Entry], index: int):
        """Re-read every loaded file from disk, ignoring the cache."""
        refreshed = analyse([e.path for e in (entries or [])], "local", None)
        for original, updated in zip(entries or [], refreshed):
            updated.origin = original.origin
        return refreshed, _clamp(index, refreshed), \
            gr.update(value=_gallery(refreshed)), \
            _status(t("msg_loaded", count=len(refreshed)), "ok")

    def pick(self, evt: gr.SelectData, entries: List[Entry]):
        """Handle a click in the gallery and return the new selection index."""
        return _clamp(evt.index if isinstance(evt.index, int) else 0, entries or [])

    # ------------------------------------------------------------- provenance
    def render_provenance(self, entries: List[Entry], index: int,
                          check_watermark: bool = False):
        """Render the provenance block for the selected image."""
        return gr.update(value=_provenance_markdown(_current(entries, index),
                                                    check_watermark))

    def check_watermark(self, entries: List[Entry], index: int):
        """Run the watermark decoder on demand, from the button."""
        if not trustmark_available():
            return gr.update(value=f"*{t('prov_needs', package='trustmark')}*")
        return self.render_provenance(entries, index, check_watermark=True)

    # ---------------------------------------------------------------- clipboard
    def add_row(self, evt: gr.SelectData, entries: List[Entry], index: int, groups: List[str],
                query: str, sensitive_only: bool, buffer: List[List[str]], style: str):
        """Add the clicked table row to the clipboard buffer, without duplicates."""
        entry = _current(entries, index)
        if entry is None or not isinstance(evt.index, (list, tuple)):
            return buffer or [], gr.update()
        rows = _rows(entry.meta, groups, query, sensitive_only)
        row_index = int(evt.index[0])
        if not 0 <= row_index < len(rows):
            return buffer or [], gr.update()
        updated = list(buffer or [])
        if rows[row_index] not in updated:
            updated.append(rows[row_index])
        return updated, gr.update(value=rows_to_text(updated, style))

    def add_visible(self, entries: List[Entry], index: int, groups: List[str], query: str,
                    sensitive_only: bool, buffer: List[List[str]], style: str):
        """Add every currently visible row to the clipboard buffer."""
        entry = _current(entries, index)
        if entry is None:
            return buffer or [], gr.update()
        updated = list(buffer or [])
        for row in _rows(entry.meta, groups, query, sensitive_only):
            if row not in updated:
                updated.append(row)
        return updated, gr.update(value=rows_to_text(updated, style))

    def clear_buffer(self):
        """Empty the clipboard buffer."""
        return [], gr.update(value="")

    def restyle(self, buffer: List[List[str]], style: str):
        """Re-render the clipboard buffer in another layout."""
        return gr.update(value=rows_to_text(buffer or [], style))

    # ------------------------------------------------------------------- export
    def do_export(self, entries: List[Entry], index: int, scope: str, fmt: str, use_filters: bool,
                  groups: List[str], query: str, sensitive_only: bool, per_image: bool):
        """Write the export file and return it for download, with a preview."""
        pool = list(entries or [])
        if not pool:
            return gr.update(value=None), gr.update(value=""), _status(t("msg_nothing_to_export"), "warn")
        if scope == t("scope_current"):
            entry = _current(pool, index)
            metas = [entry.meta] if entry else []
        else:
            metas = [e.meta for e in pool]
        if not metas:
            return gr.update(value=None), gr.update(value=""), _status(t("msg_nothing_to_export"), "warn")
        options: Dict[str, Any] = {
            "group_labels": {key: group_label(key) for key in GROUP_ORDER},
        }
        if use_filters:
            options.update(groups=groups or None, query=query or "",
                           sensitive_only=bool(sensitive_only))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if per_image and len(metas) > 1:
            destination = EXPORT_DIR / f"metadonnees-{stamp}-{fmt}.zip"
            export_per_image_zip(metas, fmt, destination, **options)
            preview = t("msg_export_done", name=destination.name)
        else:
            destination = EXPORT_DIR / f"metadonnees-{stamp}{EXPORT_FORMATS[fmt]}"
            export_metadata(metas, fmt, destination, **options)
            preview = metadata_to_string(metas, fmt, **options)
            if len(preview) > 40000:
                preview = preview[:40000] + "\n…"
        return (
            gr.update(value=str(destination)),
            gr.update(value=preview, language=_CODE_LANGUAGES.get(fmt)),
            _status(t("msg_export_done", name=destination.name), "ok"),
        )

    # ----------------------------------------------------------------- cleaning
    def do_clean(self, entries: List[Entry], index: int, scope: str, kinds: List[str],
                 dest_mode: str, out_dir: str, backup: bool, confirm: bool, dry_run: bool):
        """Remove metadata from the selected images and report what happened.

        Refuses to overwrite originals unless local access is enabled, every
        target came from a local folder, and the confirmation box is ticked.
        """
        pool = list(entries or [])
        empty = (pool, gr.update(value=[]), gr.update(value=None))
        if not pool:
            return (*empty, _status(t("msg_no_image"), "warn"))
        if not kinds:
            return (*empty, _status(t("msg_pick_kinds"), "warn"))
        if scope == t("scope_current"):
            entry = _current(pool, index)
            targets = [entry] if entry else []
        else:
            targets = [e for e in pool if e.meta.ok]
        if not targets:
            return (*empty, _status(t("msg_no_image"), "warn"))

        in_place = dest_mode == t("dest_inplace")
        if in_place:
            if not self.allow_local:
                return (*empty, _status(t("msg_inplace_blocked"), "error"))
            if any(target.origin != "local" for target in targets):
                return (*empty, _status(t("msg_inplace_blocked"), "error"))
            if not confirm:
                return (*empty, _status(t("msg_need_confirm"), "warn"))
        destination_dir = (out_dir or "").strip() or None
        if dest_mode == t("dest_dir") and not destination_dir:
            destination_dir = str(WORK_DIR / "cleaned")

        rows: List[List[str]] = []
        produced: List[str] = []
        ok_count = failed = 0
        for target in targets:
            meta = target.meta
            plan = plan_removal(meta, kinds)
            planned = ", ".join(f"{group_label(k)} ({n})" for k, n in plan.items()) or "—"
            if dry_run:
                rows.append([meta.filename, planned, str(meta.metadata_count),
                             str(max(meta.metadata_count - sum(plan.values()), 0)), "🔍"])
                ok_count += 1
                continue
            report = strip_metadata(
                meta.path,
                target=None if dest_mode == t("dest_copy") else destination_dir,
                kinds=kinds, in_place=in_place, backup=backup,
            )
            if not report.ok:
                rows.append([meta.filename, planned, str(meta.metadata_count), "—",
                             f"⛔ {report.error}"])
                failed += 1
                continue
            ok_count += 1
            after = read_metadata(report.target, with_hash=False)
            removed_labels = ", ".join(group_label(k) for k in report.removed_kinds)
            state_icon = "✅" if report.lossless else "♻️"
            note = "" if report.lossless else f" {t('clean_reencoded')}"
            if report.backup:
                note += " · " + t("clean_backup_note", name=Path(report.backup).name)
            rows.append([
                Path(report.target).name,
                removed_labels or planned,
                f"{meta.metadata_count} · {human_size(report.size_before)}",
                f"{after.metadata_count} · {human_size(report.size_after)}",
                f"{state_icon}{note}",
            ])
            produced.append(report.target)
            if in_place:
                target.meta = after
                target.signature = _signature(Path(report.target))
                target.preview = make_preview(report.target, PREVIEW_DIR)
                target.meta.preview_path = target.preview

        downloads = [p for p in produced if _servable(Path(p), self.roots)]
        if dry_run:
            message = _status(t("msg_dry_run"), "info")
        else:
            level = "ok" if failed == 0 else "warn"
            message = _status(t("msg_clean_done", ok=ok_count, failed=failed), level)
            if produced:
                folders = sorted({str(Path(p).parent) for p in produced})
                message += " " + t("msg_written_to", folder=" · ".join(folders))
        return (pool, gr.update(value=rows),
                gr.update(value=downloads or None, visible=bool(downloads)), message)



def toggle_destination(mode: str):
    """Show the output folder and the overwrite options only when they apply."""
    return (
        gr.update(visible=mode == t("dest_dir")),
        gr.update(visible=mode == t("dest_inplace")),
        gr.update(visible=mode == t("dest_inplace")),
    )


# ------------------------------------------------------------------- interface
def build_interface(allow_local: bool = True,
                    allowed_roots: Optional[Sequence[Path]] = None) -> gr.Blocks:
    """Build the Gradio application and wire it to a :class:`MetaApp`."""
    app = MetaApp(allow_local=allow_local, allowed_roots=allowed_roots)

    # ------------------------------------------------------------------ layout
    blocks_style, _ = gc.place_style(theme=gr.themes.Soft(), css=_CSS)
    with gr.Blocks(title=t("app_title"), fill_width=True, **blocks_style) as demo:
        entries_state = gr.State([])
        index_state = gr.State(0)
        buffer_state = gr.State([])

        gr.Markdown(f"# 🖼️ {t('app_title')}\n{t('app_tagline')}")

        with gr.Row():
            # --------------------------------------------------- left-hand panel
            with gr.Column(scale=3, min_width=280):
                with gr.Tabs():
                    with gr.Tab(t("tab_files")):
                        files_in = gr.Files(
                            label=t("upload_label"),
                            file_types=["image"], type="filepath", height=170,
                        )
                    with gr.Tab(t("tab_folder")):
                        folder_in = gr.Textbox(
                            label=t("folder_label"), placeholder=t("folder_placeholder"),
                            interactive=app.allow_local,
                        )
                        recursive_in = gr.Checkbox(label=t("folder_recursive"), value=False,
                                                   interactive=app.allow_local)
                        folder_btn = gr.Button(t("folder_scan"), variant="primary",
                                               interactive=app.allow_local)
                        if not app.allow_local:
                            gr.Markdown(f"*{t('msg_folder_blocked')}*")
                status_md = gr.Markdown(_status(t("msg_no_image")))
                gallery = gr.Gallery(label=t("gallery_label"), columns=3, height=260,
                                     allow_preview=False, object_fit="cover")
                with gr.Row():
                    reload_btn = gr.Button(t("reload"), size="sm")
                    clear_btn = gr.Button(t("clear_all"), size="sm", variant="stop")

            # -------------------------------------------------- right-hand panel
            with gr.Column(scale=7):
                with gr.Tabs():
                    # ----------------------------------------------------- viewer
                    with gr.Tab(t("tab_view")):
                        with gr.Row():
                            preview_img = gr.Image(
                                label=t("preview_label"), type="filepath", height=340,
                                interactive=False, show_label=True,
                                **gc.supported(gr.Image, buttons=["fullscreen", "download"]),
                            )
                            summary_md = gr.Markdown(_summary(None))
                        with gr.Accordion(t("prov_section"), open=True):
                            provenance_md = gr.Markdown(_provenance_markdown(None))
                            with gr.Row():
                                watermark_btn = gr.Button(
                                    t("prov_check_watermark"), size="sm", scale=1,
                                    interactive=trustmark_available())
                                gr.Markdown(f"*{t('prov_watermark_hint')}*", scale=3)
                        with gr.Row():
                            group_filter = gr.CheckboxGroup(label=t("filter_groups"), choices=[],
                                                            value=[], scale=3)
                            search_in = gr.Textbox(label=t("filter_search"), scale=2)
                            sensitive_in = gr.Checkbox(label=t("filter_sensitive"), value=False,
                                                       scale=1)
                        table = gr.Dataframe(
                            headers=[t("table_group"), t("table_tag"), t("table_value")],
                            datatype=["str", "str", "str"], interactive=False, wrap=True,
                            max_height=430, column_widths=["16%", "24%", "60%"],
                            **gc.supported(gr.Dataframe, buttons=["copy", "fullscreen"],
                                           show_row_numbers=False),
                        )
                        with gr.Accordion(t("copy_section"), open=False):
                            gr.Markdown(f"*{t('copy_hint')}*")
                            with gr.Row():
                                copy_style = gr.Radio(choices=list(CLIPBOARD_FORMATS),
                                                      value=CLIPBOARD_FORMATS[0],
                                                      label=t("copy_style"), scale=3)
                                add_all_btn = gr.Button(t("copy_add_all"), size="sm", scale=1)
                                clear_copy_btn = gr.Button(t("copy_clear"), size="sm", scale=1)
                            copy_box = gr.Textbox(label=t("copy_box"), lines=6, max_lines=18,
                                                  **gc.copy_button(gr.Textbox))

                    # ----------------------------------------------------- export
                    with gr.Tab(t("tab_export")):
                        with gr.Row():
                            export_scope = gr.Radio(
                                choices=[t("scope_current"), t("scope_all")],
                                value=t("scope_current"), label=t("export_scope"))
                            export_fmt = gr.Radio(choices=list(EXPORT_FORMATS),
                                                  value="json", label=t("export_format"))
                        with gr.Row():
                            export_filtered = gr.Checkbox(label=t("export_filtered"), value=False)
                            export_zip = gr.Checkbox(label=t("export_zip"), value=False)
                        export_btn = gr.Button(t("export_run"), variant="primary")
                        export_status = gr.Markdown()
                        export_file = gr.File(label=t("export_file"))
                        export_code = gr.Code(label=t("export_preview"), lines=18,
                                              **gc.supported(gr.Code, wrap_lines=True,
                                                             show_line_numbers=False))

                    # --------------------------------------------------- cleaning
                    with gr.Tab(t("tab_clean")):
                        clean_kinds = gr.CheckboxGroup(label=t("clean_kinds"),
                                                       choices=_kind_choices(None),
                                                       value=list(DEFAULT_KINDS))
                        with gr.Row():
                            clean_scope = gr.Radio(choices=[t("scope_current"), t("scope_all")],
                                                   value=t("scope_current"), label=t("clean_scope"))
                            clean_dest = gr.Radio(
                                choices=[t("dest_copy"), t("dest_dir"), t("dest_inplace")],
                                value=t("dest_copy"), label=t("clean_dest"))
                        clean_outdir = gr.Textbox(label=t("clean_outdir"), visible=False,
                                                  placeholder=str(WORK_DIR / "cleaned"))
                        with gr.Row():
                            clean_backup = gr.Checkbox(label=t("clean_backup"), value=True,
                                                       visible=False)
                            clean_confirm = gr.Checkbox(label=t("clean_confirm"), value=False,
                                                        visible=False)
                            clean_dry = gr.Checkbox(label=t("clean_dryrun"), value=False)
                        clean_btn = gr.Button(t("clean_run"), variant="stop")
                        clean_status = gr.Markdown()
                        clean_table = gr.Dataframe(
                            headers=[t("clean_col_file"), t("clean_col_removed"),
                                     t("clean_col_before"), t("clean_col_after"),
                                     t("clean_col_status")],
                            datatype=["str"] * 5, interactive=False, wrap=True,
                            label=t("clean_result"), max_height=300)
                        clean_files = gr.Files(label=t("clean_files"), height=140, visible=False)

                    with gr.Tab(t("tab_help")):
                        gr.Markdown(help_markdown(app.allow_local))

        # ---------------------------------------------------------------- events
        view_outputs = [preview_img, summary_md, group_filter, table, clean_kinds,
                        provenance_md]
        view_inputs = [entries_state, index_state, search_in, sensitive_in]
        table_inputs = [entries_state, index_state, group_filter, search_in, sensitive_in]

        files_in.change(app.load_uploads, [files_in, entries_state],
                        [entries_state, index_state, gallery, status_md]) \
                .then(app.render_view, view_inputs, view_outputs)
        folder_btn.click(app.load_folder, [folder_in, recursive_in, entries_state],
                         [entries_state, index_state, gallery, status_md]) \
                  .then(app.render_view, view_inputs, view_outputs)
        folder_in.submit(app.load_folder, [folder_in, recursive_in, entries_state],
                         [entries_state, index_state, gallery, status_md]) \
                 .then(app.render_view, view_inputs, view_outputs)
        reload_btn.click(app.reload_all, [entries_state, index_state],
                         [entries_state, index_state, gallery, status_md]) \
                  .then(app.render_view, view_inputs, view_outputs)
        clear_btn.click(app.clear_all, None,
                        [entries_state, index_state, gallery, status_md, files_in]) \
                 .then(app.render_view, view_inputs, view_outputs)
        gallery.select(app.pick, entries_state, index_state) \
               .then(app.render_view, view_inputs, view_outputs)

        for control in (group_filter, sensitive_in):
            control.change(app.render_table, table_inputs, table)
        search_in.change(app.render_table, table_inputs, table)

        watermark_btn.click(app.check_watermark, [entries_state, index_state],
                            provenance_md)

        table.select(app.add_row,
                     [entries_state, index_state, group_filter, search_in, sensitive_in,
                      buffer_state, copy_style],
                     [buffer_state, copy_box])
        add_all_btn.click(app.add_visible,
                          [entries_state, index_state, group_filter, search_in, sensitive_in,
                           buffer_state, copy_style],
                          [buffer_state, copy_box])
        clear_copy_btn.click(app.clear_buffer, None, [buffer_state, copy_box])
        copy_style.change(app.restyle, [buffer_state, copy_style], copy_box)

        clean_dest.change(toggle_destination, clean_dest,
                          [clean_outdir, clean_backup, clean_confirm])

        export_btn.click(app.do_export,
                         [entries_state, index_state, export_scope, export_fmt, export_filtered,
                          group_filter, search_in, sensitive_in, export_zip],
                         [export_file, export_code, export_status])

        clean_btn.click(app.do_clean,
                        [entries_state, index_state, clean_scope, clean_kinds, clean_dest,
                         clean_outdir, clean_backup, clean_confirm, clean_dry],
                        [entries_state, clean_table, clean_files, clean_status]) \
                 .then(app.render_view, view_inputs, view_outputs) \
                 .then(lambda entries: gr.update(value=_gallery(entries or [])),
                       entries_state, gallery)

    return demo


_CSS = """
.gradio-container {max-width: 1600px !important;}
"""


def help_markdown(allow_local: bool = True) -> str:
    """Render the Help tab, in the active language."""
    return help_text(allow_local, SUPPORTED_READ)
