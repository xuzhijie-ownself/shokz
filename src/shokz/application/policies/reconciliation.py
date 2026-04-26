"""ReconciliationPolicy -- detect orphan files + orphan manifest entries.

Sprint 4 SF-4 (silent-failure-hunter) introduced an orphan-state window: kill
between os.replace and manifest.record leaves an .mp3 with no manifest entry.
This policy is the recovery story.

Two failure shapes:
  - orphan_files:   *.mp3 in downloads/ NOT referenced by any manifest entry
  - orphan_entries: manifest entries whose mp3_path does NOT exist on disk
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shokz.application.ports.outbound.filesystem import FileSystemPort
from shokz.application.ports.outbound.manifest import ManifestPort
from shokz.domain.models import ManifestEntry


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    # py-rev fix #3: tuples are immutable -> safe as direct defaults.
    ok: tuple[tuple[ManifestEntry, Path], ...] = ()
    orphan_files: tuple[Path, ...] = ()
    orphan_entries: tuple[ManifestEntry, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.orphan_files and not self.orphan_entries


@dataclass(frozen=True, slots=True)
class ReconciliationPolicy:
    """Compare manifest entries to disk state under output_dir."""

    manifest: ManifestPort
    filesystem: FileSystemPort
    output_dir: Path

    async def scan(self) -> ReconciliationReport:
        entries: list[ManifestEntry] = []
        async for entry in self.manifest.iter_all():
            entries.append(entry)

        # Set of mp3_path values claimed by the manifest, normalized to absolute paths.
        manifest_paths: set[Path] = {self.output_dir / e.mp3_path for e in entries}

        # Disk state: top-level *.mp3 in output_dir (NOT recursive into .tmp/ or .shokz/).
        disk_mp3s: set[Path] = set()
        if self.output_dir.exists():
            for child in self.output_dir.iterdir():
                if child.is_file() and child.suffix == ".mp3":
                    disk_mp3s.add(child)

        ok: list[tuple[ManifestEntry, Path]] = []
        orphan_entries: list[ManifestEntry] = []
        for e in entries:
            p = self.output_dir / e.mp3_path
            if p in disk_mp3s:
                ok.append((e, p))
            else:
                orphan_entries.append(e)

        orphan_files = tuple(sorted(disk_mp3s - manifest_paths))

        return ReconciliationReport(
            ok=tuple(ok),
            orphan_files=orphan_files,
            orphan_entries=tuple(orphan_entries),
        )
