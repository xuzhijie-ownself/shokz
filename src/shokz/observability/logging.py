"""Logging setup: stdlib `logging` + RichHandler + optional JSON formatter.

Correlation IDs (`run_id`, `track_id`) are bound via `contextvars` and injected
into every log record via a `logging.Filter`. This is the single logging stack
for the project; structlog is intentionally NOT used (see plan §0.6 GAN audit).

For Sprint 0 this module exposes the configuration entry point but is not yet
wired into a CLI. Sprint 1 wires it through the composition root.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any, Final

from rich.logging import RichHandler

# ----------------------------------------------------------------------
# Correlation IDs — bound by use cases / CLI entry, read by the filter
# ----------------------------------------------------------------------
_run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
_track_id_var: ContextVar[str | None] = ContextVar("track_id", default=None)


def set_run_id(run_id: str) -> None:
    """Bind the current run ID for log correlation."""
    _run_id_var.set(run_id)


def set_track_id(track_id: str | None) -> None:
    """Bind the current track ID for log correlation. None to clear."""
    _track_id_var.set(track_id)


class _CorrelationFilter(logging.Filter):
    """Inject run_id / track_id from contextvars onto every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get() or "-"
        record.track_id = _track_id_var.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """Minimal JSON line formatter for `--ui json` mode (Sprint 9)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "run_id": getattr(record, "run_id", "-"),
            "track_id": getattr(record, "track_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_LOG_FORMAT_RICH: Final[str] = "[%(run_id)s|%(track_id)s] %(message)s"


def configure_logging(
    *,
    level: str = "INFO",
    json_mode: bool = False,
) -> None:
    """Configure the root logger.

    - level: "DEBUG" | "INFO" | "WARNING" | "ERROR"
    - json_mode: True for one-JSON-event-per-line on stdout (Sprint 9 UI).
                 False (default) routes to RichHandler for human terminals.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Wipe any pre-existing handlers (idempotent for tests)
    for h in list(root.handlers):
        root.removeHandler(h)

    handler: logging.Handler
    if json_mode:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
    else:
        handler = RichHandler(
            rich_tracebacks=True,
            show_time=True,
            show_path=False,
            markup=False,
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT_RICH))

    handler.addFilter(_CorrelationFilter())
    root.addHandler(handler)


__all__ = [
    "configure_logging",
    "set_run_id",
    "set_track_id",
]
