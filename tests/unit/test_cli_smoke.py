"""CLI smoke tests via Typer's CliRunner — no real network."""

from __future__ import annotations

from typer.testing import CliRunner

from shokz import __version__
from shokz.adapters.inbound.cli.app import app


def test_version_flag_exits_zero() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_args_shows_help() -> None:
    """Typer with no_args_is_help=True exits with code 2 and prints help -- by design."""
    runner = CliRunner()
    result = runner.invoke(app, [])
    # Typer convention: no_args_is_help yields exit_code 2 ("missing command").
    assert result.exit_code in (0, 2)
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "download" in combined or "usage" in combined


def test_download_help_lists_options() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["download", "--help"])
    assert result.exit_code == 0
    out = result.stdout
    assert "--output" in out
    assert "--concurrency" in out
    assert "--keep-raw" in out
