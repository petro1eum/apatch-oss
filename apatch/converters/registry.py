"""Registry of strip output converters."""

from __future__ import annotations

from typing import Callable, Dict, Optional

Converters = Dict[str, Callable[..., dict]]


def get_converter(name: str) -> Optional[Callable[..., dict]]:
    from apatch.converters import ts_module

    registry: Converters = {
        "hook": ts_module.convert_hook,
        "component": ts_module.convert_component,
        "util": ts_module.convert_util,
        "native_cpp": None,  # handled by native_converter subprocess
    }
    return registry.get(name)
