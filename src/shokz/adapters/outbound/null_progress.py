"""NullProgressReporter — no-op for tests and non-interactive runs.

Sprint 6 ships RichProgressReporter and JsonProgressReporter.
"""

from __future__ import annotations

from shokz.domain.models import TrackStatus


class NullProgressReporter:
    def start(self, track_id: str, label: str) -> None:
        return None

    def finish(self, track_id: str, status: TrackStatus, message: str | None = None) -> None:
        return None
