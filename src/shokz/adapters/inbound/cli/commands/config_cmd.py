"""`shokz config show|init|path` -- Sprint 3."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from shokz.config.loader import (
    ConfigLoadError,
    ConfigWithSource,
    load_config,
)

config_app = typer.Typer(
    name="config",
    help="Inspect or initialize shokz configuration.",
    no_args_is_help=True,
    add_completion=False,
)

# Sprint 3 review fix C6: a hand-written commented TOML template.
# tomli_w cannot emit comments; satisfying the AC ("commented sections") requires
# a literal template. AppConfig() values are inlined here as defaults; if a
# default changes, update this template AND a future test will catch the drift.
_SAMPLE_TOML: str = """\
# shokz configuration -- copy this file to ./shokz.toml or
# ~/.config/shokz/config.toml. Every key here is optional; built-in defaults
# apply when omitted.
#
# Precedence (low -> high):
#   built-in < ~/.config/shokz/config.toml < ./shokz.toml < env (SHOKZ_*) < CLI flags
#
# `shokz config show` annotates each value with its source layer.

[general]
output_dir = "./downloads"        # where final MP3s land (relative or absolute)
concurrency = 3                   # parallel downloads (1..16)
keep_raw = false                  # keep .tmp/ raw downloads after encode

[audio]
preset = "swim-standard"          # swim-low | swim-standard | swim-high | custom
bitrate_kbps = 64                 # used when preset="custom"
channels = 1                      # 1=mono (Shokz spec), 2=stereo
sample_rate_hz = 44100

[filenames]
template = "{title}"              # tokens: {title} {uploader} {id} {source} {duration}
collision = "suffix"              # suffix only in v0.3 (overwrite|skip|fail in v0.7+)
fat_safe = true                   # FAT/exFAT-safe sanitization
max_length = 120                  # max bytes for the filename stem

[sources.youtube]
ejs_source = "ejs:github"         # required for long videos (anti-bot)
sleep_requests = 1.0              # politeness delay between yt-dlp requests

[logging]
level = "INFO"                    # DEBUG | INFO | WARNING | ERROR | CRITICAL
"""


@config_app.command("show")
def config_show() -> None:
    """Print the effective config and the source layer for each value."""
    try:
        loaded = load_config()
    except ConfigLoadError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    typer.echo(_render_show(loaded))


@config_app.command("init")
def config_init(
    path: Path = typer.Option(
        Path("./shokz.toml"),
        "--path",
        "-p",
        help="Where to write the sample config.",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing file."),
) -> None:
    """Write a commented sample shokz.toml at the given path."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # Sprint 3 review fix C3: atomic exclusive open closes the TOCTOU window
    # between exists() and open(). Concurrent writers cannot race past us.
    mode = "w" if force else "x"
    try:
        with path.open(mode, encoding="utf-8") as f:
            f.write(_SAMPLE_TOML)
    except FileExistsError:
        typer.echo(
            f"error: {path} exists; pass --force to overwrite",
            err=True,
        )
        sys.exit(1)
    typer.echo(f"wrote {path}")


@config_app.command("path")
def config_path() -> None:
    """List which config files were loaded (and which were searched-for but absent)."""
    try:
        loaded = load_config()
    except ConfigLoadError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    typer.echo("loaded:")
    for p in loaded.loaded_files:
        typer.echo(f"  {p}")
    typer.echo("missing (not found):")
    for p in loaded.missing_files:
        typer.echo(f"  {p}")


def _render_show(loaded: ConfigWithSource) -> str:
    """Pretty-print the effective config with per-key source annotation."""
    lines = ["# shokz effective config (key = value  # source: ...)\n"]
    for key in sorted(loaded.sources):
        value = _flat_get(loaded.config, key)
        src = loaded.sources[key]
        lines.append(f"{key} = {value!r}  # source: {src}")
    return "\n".join(lines)


def _flat_get(model: object, dotted_key: str) -> object:
    """Walk a Pydantic model by dotted key (e.g. 'general.concurrency').

    Sprint 3 review fix C9: raise KeyError on missing path instead of returning
    a placeholder string. Source-tracking dict keys must always be reachable.
    """
    node: object = model
    for part in dotted_key.split("."):
        # Handle Pydantic alias collisions (e.g. logging -> logging_) by
        # consulting model_fields metadata generically.
        candidate = part
        if not hasattr(node, candidate):
            fields = getattr(type(node), "model_fields", None)
            if fields:
                for field_name, field_info in fields.items():
                    if getattr(field_info, "alias", None) == part:
                        candidate = field_name
                        break
        if not hasattr(node, candidate):
            raise KeyError(f"config path {dotted_key!r} unreachable at segment {part!r}")
        node = getattr(node, candidate)
    return node
