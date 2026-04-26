"""Typer root app — wires subcommands and serves as the package entry point.

`pyproject.toml` declares: shokz = "shokz.adapters.inbound.cli.app:run"
"""

from __future__ import annotations

import typer

from shokz import __version__
from shokz.adapters.inbound.cli.commands.download import download_command

app = typer.Typer(
    name="shokz",
    help="YouTube to MP3 downloader for Shokz swimming headphones.",
    no_args_is_help=True,
    add_completion=False,
)

app.command("download")(download_command)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"shokz {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """shokz — YouTube to MP3 for Shokz swim headphones."""


def run() -> None:
    """Console-script entry point."""
    app()
