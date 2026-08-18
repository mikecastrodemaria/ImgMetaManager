"""Core library: reading, removing and exporting image metadata.

This package has no dependency on Gradio and can be used on its own::

    from imgmetamanager.core import read_metadata, strip_metadata

    meta = read_metadata("photo.jpg")
    report = strip_metadata("photo.jpg", kinds=["gps"])
"""
from .model import ImageMeta, MetaItem
from .reader import read_metadata, make_preview, SUPPORTED_READ
from .writer import strip_metadata, StripReport, plan_removal
from .exporter import export_metadata, EXPORT_FORMATS, rows_to_text

__all__ = [
    "ImageMeta", "MetaItem", "read_metadata", "make_preview", "SUPPORTED_READ",
    "strip_metadata", "StripReport", "plan_removal",
    "export_metadata", "EXPORT_FORMATS", "rows_to_text",
]
