"""NullProgressReporter — the only ProgressReporterPort impl shipped today.

The Port + null impl exist as surgical hook points in
`BatchDownloadUseCase` (start/finish per track). Sprint 6 originally
planned a RichProgressReporter but it was DROPPED in the Sprint 6 retro
(scope-discipline call). Adding a real progress reporter later is a
plug-in change at the composition root; the use case stays unchanged.
"""

from __future__ import annotations

from shokz.domain.models import TrackStatus


class NullProgressReporter:
    def start(self, track_id: str, label: str) -> None:
        return None

    def finish(self, track_id: str, status: TrackStatus, message: str | None = None) -> None:
        return None
