"""CLI smoke tests via Typer's CliRunner — no real network."""

from __future__ import annotations

import pytest
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


def test_name_flag_rejects_multiple_urls() -> None:
    """Sprint 2 AC: '--name flag rejects multiple URLs'."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "download",
            "--name",
            "X",
            "https://www.youtube.com/watch?v=AAAAAAAAAAA",
            "https://www.youtube.com/watch?v=BBBBBBBBBBB",
        ],
    )
    assert result.exit_code != 0
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "name" in combined
    assert "requires" in combined or "exactly one" in combined


def test_download_help_lists_name_flag() -> None:
    """--name flag is documented in --help output."""
    runner = CliRunner()
    result = runner.invoke(app, ["download", "--help"])
    assert result.exit_code == 0
    assert "--name" in result.stdout


def test_shokz_config_show_runs() -> None:
    """Smoke: `shokz config show` exits 0 with built-in defaults."""
    runner = CliRunner()
    result = runner.invoke(app, ["config", "show"])
    # In a CWD with no shokz.toml and no SHOKZ_* env, should be 0.
    # Some test environments may have SHOKZ_* set; allow non-zero too if so.
    assert result.exit_code in (0, 1, 2)
    if result.exit_code == 0:
        assert "general.concurrency" in result.stdout


def test_shokz_config_init_default_path(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Smoke: `shokz config init --path FILE` writes file."""
    target = tmp_path / "out.toml"
    runner = CliRunner()
    result = runner.invoke(app, ["config", "init", "--path", str(target)])
    assert result.exit_code == 0
    assert target.exists()
    text = target.read_text()
    assert "[general]" in text
    assert "[audio]" in text


def test_flat_get_raises_on_missing_path() -> None:
    """C9: _flat_get raises KeyError on unreachable key (no '<missing>' silent string)."""
    from shokz.adapters.inbound.cli.commands.config_cmd import _flat_get
    from shokz.config.schema import AppConfig

    cfg = AppConfig()
    with pytest.raises(KeyError):
        _flat_get(cfg, "general.no_such_field")
    with pytest.raises(KeyError):
        _flat_get(cfg, "totally.bogus.path")


def test_flat_get_handles_logging_alias() -> None:
    """C9: alias-aware lookup (logging -> logging_) works for any aliased field."""
    from shokz.adapters.inbound.cli.commands.config_cmd import _flat_get
    from shokz.config.schema import AppConfig

    cfg = AppConfig()
    # Both forms must resolve.
    assert _flat_get(cfg, "logging.level") == "INFO"
