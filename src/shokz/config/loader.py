"""Layered config loader: built-in < user TOML < project TOML < env < CLI.

Source-tracking returns a `dict[str, str]` mapping flat key -> source label,
so `shokz config show` can prove which layer set each value.
"""

from __future__ import annotations

import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from shokz.config.defaults import BUILTIN_DEFAULTS
from shokz.config.schema import AppConfig

_USER_CONFIG_PATH: Path = Path.home() / ".config" / "shokz" / "config.toml"
_PROJECT_CONFIG_PATH: Path = Path("./shokz.toml")
_ENV_PREFIX: str = "SHOKZ_"


@dataclass(frozen=True, slots=True)
class ConfigWithSource:
    """The validated config plus per-key origin tracking."""

    config: AppConfig
    sources: Mapping[
        str, str
    ]  # flat dotted key -> "built-in" / "env SHOKZ_..." / file path / "CLI"
    loaded_files: tuple[Path, ...]  # which TOML files were actually read
    missing_files: tuple[Path, ...]  # which TOML files were searched-for but absent


class ConfigLoadError(Exception):
    """A TOML file was malformed or a value failed validation."""


def load_config(
    cli_overrides: Mapping[str, Any] | None = None,
    *,
    user_toml: Path | None = None,
    project_toml: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ConfigWithSource:
    """Merge layers and return validated AppConfig + source map."""
    user_toml = user_toml if user_toml is not None else _USER_CONFIG_PATH
    project_toml = project_toml if project_toml is not None else _PROJECT_CONFIG_PATH
    env = env if env is not None else os.environ

    layers: list[tuple[str, dict[str, Any]]] = [("built-in", dict(BUILTIN_DEFAULTS))]
    loaded: list[Path] = []
    missing: list[Path] = []

    for path in (user_toml, project_toml):
        if path.exists():
            try:
                parsed = _load_toml_flat(path)
            except tomllib.TOMLDecodeError as e:
                raise ConfigLoadError(f"failed to parse TOML at {path}: {e}") from e
            except OSError as e:
                # silent-failure F3: file existed at exists() but became
                # unreadable before open(); also catches PermissionError.
                raise ConfigLoadError(f"failed to read TOML at {path}: {e}") from e
            layers.append((str(path), parsed))
            loaded.append(path)
        else:
            missing.append(path)

    env_layer = _load_env_flat(env)
    if env_layer:
        layers.append(("env", env_layer))

    cli_overrides = dict(cli_overrides) if cli_overrides else {}
    if cli_overrides:
        layers.append(("CLI", cli_overrides))

    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for label, layer in layers:
        for key, value in layer.items():
            merged[key] = value
            if label == "env":
                sources[key] = f"env {_ENV_PREFIX}{key.upper().replace('.', '__')}"
            else:
                sources[key] = label

    nested = _unflatten(merged)
    try:
        config = AppConfig.model_validate(nested)
    except ValidationError as e:
        raise ConfigLoadError(_format_validation_error(e, sources)) from e

    return ConfigWithSource(
        config=config,
        sources=sources,
        loaded_files=tuple(loaded),
        missing_files=tuple(missing),
    )


def _load_toml_flat(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        nested = tomllib.load(f)
    return _flatten(nested)


def _load_env_flat(env: Mapping[str, str]) -> dict[str, Any]:
    """Convert SHOKZ_GENERAL__CONCURRENCY=7 -> {'general.concurrency': 7}."""
    out: dict[str, Any] = {}
    for k, v in env.items():
        if not k.startswith(_ENV_PREFIX):
            continue
        rest = k[len(_ENV_PREFIX) :]
        if not rest:
            continue
        flat_key = rest.lower().replace("__", ".")
        out[flat_key] = _coerce_env_string(v)
    return out


def _coerce_env_string(s: str) -> Any:
    """Best-effort env-string -> int / float / bool / str. Pydantic re-validates.

    Sprint 3 review fix C4: reject inf/nan (would let sleep_requests=inf through),
    leave ~/path strings alone (Pydantic + a field_validator on Path will expanduser).
    """
    stripped = s.strip()
    low = stripped.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        # Reject leading-zero ambiguity: int("01") silently strips. Plain digits only.
        if stripped and stripped.lstrip("-+").isdigit():
            return int(stripped)
    except ValueError:
        pass
    try:
        val = float(stripped)
        if not math.isfinite(val):
            return s  # leave as string; Pydantic will reject inf/nan cleanly
        # Only return float if it actually parsed -- isdigit above caught int case
        if "." in stripped or "e" in low:
            return val
    except ValueError:
        pass
    return s


def _flatten(nested: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in nested.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, Mapping):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _unflatten(flat: Mapping[str, Any]) -> dict[str, Any]:
    """Sprint 3 review fix C1: raise ConfigLoadError on scalar/dict collision.

    Without this guard, insertion order silently determined whether a deeper key
    or a shallower scalar won, producing data loss with no validation error.
    """
    out: dict[str, Any] = {}
    for key, value in flat.items():
        node: dict[str, Any] = out
        parts = key.split(".")
        for i, part in enumerate(parts[:-1]):
            existing = node.get(part)
            if existing is not None and not isinstance(existing, dict):
                raise ConfigLoadError(
                    f"key conflict: {'.'.join(parts[: i + 1])!r} is set to a scalar "
                    f"AND used as a prefix of {key!r}; pick one form"
                )
            if part not in node:
                node[part] = {}
            node = node[part]
        last = parts[-1]
        if isinstance(node.get(last), dict):
            raise ConfigLoadError(
                f"key conflict: {key!r} is a scalar but already used as a prefix "
                f"by another key; pick one form"
            )
        node[last] = value
    return out


def _format_validation_error(e: ValidationError, sources: Mapping[str, str]) -> str:
    """Sprint 3 review fix C8: include the source file (or layer) for each error."""
    lines: list[str] = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid value")
        src = sources.get(loc, "unknown source")
        lines.append(f"  {loc}: {msg}  (from: {src})")
    return "config validation failed:\n" + "\n".join(lines)
