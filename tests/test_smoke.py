"""Smoke tests — Sprint 0 acceptance.

Proves: package importable, version set, logging configurable. Nothing more.
"""

from __future__ import annotations

import logging
import re

import shokz
from shokz.observability.logging import configure_logging, set_run_id, set_track_id


def test_version_set() -> None:
    """Sprint 0 AC: `__version__` is a PEP 440 SemVer string."""
    assert isinstance(shokz.__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+", shokz.__version__), shokz.__version__


def test_logging_configures_without_error() -> None:
    """Sprint 0 AC: logging setup is idempotent and side-effect-clean."""
    configure_logging(level="INFO")
    configure_logging(level="DEBUG", json_mode=True)
    configure_logging(level="INFO")
    # If we got here without exception, the contract holds.


def test_correlation_ids_bind() -> None:
    """Sprint 0 AC: contextvars bind without error and emit through a log call."""
    configure_logging(level="DEBUG")
    set_run_id("2026-04-26T20-30-12")
    set_track_id("eiV0nvJ9fRM")
    logging.getLogger("shokz.test").info("smoke")
    set_track_id(None)  # clear
