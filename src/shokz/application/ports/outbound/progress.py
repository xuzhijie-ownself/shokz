"""ProgressReporterPort — abstract per-track progress reporting."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shokz.domain.models import TrackStatus


@runtime_checkable
class ProgressReporterPort(Protocol):
    """Report per-track progress to humans (Rich) or machines (JSON / null)."""

    def start(self, track_id: str, label: str) -> None:
        """Mark a track as started."""

    def finish(self, track_id: str, status: TrackStatus, message: str | None = None) -> None:
        """Mark a track as finished with the given outcome."""
