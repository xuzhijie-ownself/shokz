"""DiskGuardPolicy -- Sprint 8 batch-level disk pre-flight.

Sprint 8 GAN B3: ONE pre-flight per `execute()` after resolve-all, summing
batch estimates * multiplier vs. `shutil.disk_usage(output_dir).free`.
First DiskFull aborts the rest of the batch (no per-track guard re-runs).

Sprint 8 GAN L4: tracks with None filesize_approx are skipped from the
SUM but logged at WARNING. With `require_estimate=True`, raises DiskFull
saying "source did not report filesize_approx; pass --allow-unknown-size
or set [disk] require_estimate = false".

Sprint 8 GAN M2: human-readable error message uses `humanfriendly.format_size
(bytes_val, binary=True)` for IEC units ("GiB" not "GB") -- matches the
Gherkin scenario assertion.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import humanfriendly

from shokz.domain.errors import DiskFull

_log = logging.getLogger("shokz.policy.disk_guard")


@dataclass(frozen=True, slots=True)
class DiskGuardPolicy:
    """Per-batch disk pre-flight check.

    safety_multiplier: estimated_total * multiplier must fit in free space.
    require_estimate: if True, refuse batches where any track lacks an
        estimate (forces strict pre-flight; default False = best-effort).
    """

    safety_multiplier: float = 2.0
    require_estimate: bool = False

    def check_batch(
        self,
        output_dir: Path,
        estimates: list[int | None],
    ) -> None:
        """Raise DiskFull if `sum(known_estimates) * safety_multiplier > free`.

        - None entries are skipped from the sum (logged at WARNING).
        - With `require_estimate=True`, any None raises DiskFull immediately
          (Sprint 8 GAN L4).
        - DiskFull carries `need_bytes` and `have_bytes` attributes (Phase 1
          GAN HIGH#1) so downstream code can render or audit the values.
        """
        # L4: strict mode rejects unknown-size sources.
        unknown_count = sum(1 for e in estimates if e is None)
        if unknown_count > 0 and self.require_estimate:
            raise DiskFull(
                f"source did not report filesize_approx for "
                f"{unknown_count} of {len(estimates)} tracks; "
                "pass `--allow-unknown-size` or set [disk] require_estimate = false"
            )
        if unknown_count > 0:
            _log.warning(
                "skip disk guard for %d/%d tracks (no filesize_approx); "
                "consider [disk] require_estimate=true for strict pre-flight",
                unknown_count,
                len(estimates),
            )

        known_total = sum(e for e in estimates if e is not None)
        if known_total == 0:
            # Nothing to check (all sources were unknown-size and we're in
            # best-effort mode).
            return

        need = int(known_total * self.safety_multiplier)
        have = shutil.disk_usage(output_dir).free
        if have < need:
            need_str = humanfriendly.format_size(need, binary=True)
            have_str = humanfriendly.format_size(have, binary=True)
            raise DiskFull(
                f"insufficient disk: need {need_str} free; have {have_str}",
                need_bytes=need,
                have_bytes=have,
            )
