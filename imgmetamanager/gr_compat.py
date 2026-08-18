"""Compatibility shim across Gradio versions (5.x and 6.x).

Gradio renamed several parameters between version 5 and version 6:
``show_copy_button`` became ``buttons=["copy"]``, ``col_count`` became
``column_count``, and ``theme`` moved from ``Blocks()`` to ``launch()``. Rather
than pinning one version, the helpers here inspect the installed signatures and
keep only the arguments it actually understands.
"""
from __future__ import annotations

import inspect
from typing import Any, Dict, Type

import gradio as gr

#: Installed Gradio version as a ``(major, minor)`` tuple.
GRADIO_VERSION = tuple(
    int(part) for part in gr.__version__.split(".")[:2] if part.isdigit()
) or (0, 0)


def _accepted(target: Any) -> set:
    """Return the parameter names a class constructor or a function accepts."""
    func = target.__init__ if inspect.isclass(target) else target
    try:
        return set(inspect.signature(func).parameters)
    except (TypeError, ValueError):  # pragma: no cover
        return set()


def supported(target: Any, **kwargs: Any) -> Dict[str, Any]:
    """Keep only the keyword arguments this Gradio version understands."""
    allowed = _accepted(target)
    if not allowed:
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in allowed}


def copy_button(component: Type[Any]) -> Dict[str, Any]:
    """Return the kwargs that enable a component's copy button, any version."""
    allowed = _accepted(component)
    if "buttons" in allowed:
        return {"buttons": ["copy"]}
    if "show_copy_button" in allowed:
        return {"show_copy_button": True}
    return {}


def blocks_kwargs(**kwargs: Any) -> Dict[str, Any]:
    """Keep the arguments ``gr.Blocks()`` accepts in this version."""
    return supported(gr.Blocks, **kwargs)


def launch_kwargs(**kwargs: Any) -> Dict[str, Any]:
    """Keep the arguments ``Blocks.launch()`` accepts in this version."""
    return supported(gr.Blocks.launch, **kwargs)


def place_style(**style: Any) -> "tuple[Dict[str, Any], Dict[str, Any]]":
    """Split styling arguments between ``Blocks()`` and ``launch()``.

    Returns:
        A ``(for_blocks, for_launch)`` pair. Gradio 5 takes ``theme`` and
        ``css`` on ``Blocks()``, Gradio 6 takes them on ``launch()``.
    """
    for_blocks = blocks_kwargs(**style)
    for_launch = {k: v for k, v in launch_kwargs(**style).items() if k not in for_blocks}
    return for_blocks, for_launch
