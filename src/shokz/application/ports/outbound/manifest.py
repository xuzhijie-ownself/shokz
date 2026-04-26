"""ManifestPort -- record + read append-only ledger (Sprint 4 + 4.5)."""

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
