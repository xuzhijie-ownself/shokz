"""Library query use cases -- list / show / verify (Sprint 4.5)."""

from __future__ import annotations

from dataclasses import dataclass

from shokz.application.policies.reconciliation import (
    ReconciliationPolicy,
    ReconciliationReport,
)
from shokz.application.ports.outbound.manifest import ManifestPort
from shokz.domain.models import ManifestEntry


@dataclass(frozen=True, slots=True)
class ListLibraryUseCase:
    manifest: ManifestPort

    async def execute(self) -> tuple[ManifestEntry, ...]:
        entries: list[ManifestEntry] = []
        async for e in self.manifest.iter_all():
            entries.append(e)
        return tuple(entries)


@dataclass(frozen=True, slots=True)
class ShowLibraryUseCase:
    manifest: ManifestPort

    async def execute(self, track_id: str, source: str) -> ManifestEntry | None:
        return await self.manifest.find_by_track(source, track_id)


@dataclass(frozen=True, slots=True)
class VerifyLibraryUseCase:
    reconciliation: ReconciliationPolicy

    async def execute(self) -> ReconciliationReport:
        return await self.reconciliation.scan()
