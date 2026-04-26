"""ManifestPort -- record successful tracks + failures, append-only."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shokz.domain.models import FailureEntry, ManifestEntry


@runtime_checkable
class ManifestPort(Protocol):
    """Append-only ledger of completed downloads + per-track failures.

    Implementations MUST fsync the file fd AND its parent dir after each
    append (Sprint 4 DoD ratchet — silent-failure-hunter F3 from v0.2.0).
    """

    async def record(self, entry: ManifestEntry) -> None:
        """Append a successful-track entry. Durable on return."""

    async def record_failure(self, entry: FailureEntry) -> None:
        """Append a per-track failure entry. Durable on return."""
