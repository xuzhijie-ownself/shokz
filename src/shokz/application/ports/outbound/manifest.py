"""ManifestPort -- record + read append-only ledger (Sprint 4 + 4.5 + 8.5).

Sprint 8.5: `iter_failures` lets `RetryFailedUseCase` consume the failure
audit log as input. Mirrors `iter_all` semantics: linear scan, append-
order, OSError wrapped as ManifestReadError per spec C1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from shokz.domain.models import FailureEntry, ManifestEntry


@runtime_checkable
class ManifestPort(Protocol):
    """Append-only ledger of completed downloads + per-track failures.

    Sprint 4: record + record_failure (write side, fsync'd).
    Sprint 4.5: find_by_track + iter_all (read side, for skip-existing
    + library list/show + reconciliation).
    """

    async def record(self, entry: ManifestEntry) -> None:
        """Append a successful-track entry. Durable on return."""

    async def record_failure(self, entry: FailureEntry) -> None:
        """Append a per-track failure entry. Durable on return."""

    async def find_by_track(self, source: str, track_id: str) -> ManifestEntry | None:
        """Return the MOST RECENT manifest entry for (source, track_id), or None.

        Manifest is append-only; on re-downloads (--force) the same track_id can
        appear multiple times. Latest wins.
        """

    def iter_all(self) -> AsyncIterator[ManifestEntry]:
        """Async iterator over every manifest entry, in append order."""

    def iter_failures(self) -> AsyncIterator[FailureEntry]:
        """Sprint 8.5: async iterator over every failure entry, in append order.

        Mirrors iter_all semantics. Implementations MUST wrap OSError as
        ManifestReadError naming the path (spec C1) so the CLI can surface
        an actionable message instead of routing through the catch-all.
        """
