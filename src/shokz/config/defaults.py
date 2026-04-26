"""Built-in defaults — derived from AppConfig field defaults.

Sprint 3 review fix C12: single source of truth (was duplicated as a hand-edited
flat dict). Now AppConfig is the authority; this module just flattens it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shokz.config.schema import AppConfig


def _flatten(nested: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in nested.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, Mapping):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


# Built once at import time; AppConfig defaults are the source of truth.
BUILTIN_DEFAULTS: dict[str, Any] = _flatten(AppConfig().model_dump(by_alias=True))
