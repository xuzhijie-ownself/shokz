"""RunDoctorUseCase -- Sprint 10 read-only diagnostics for `shokz doctor`.

The use case runs a fixed list of checks against the user's environment
and config, returning a `DoctorResult` of named `CheckResult`s. NO
filesystem mutations beyond a transient writability probe; NO network
calls; NO manifest mutations.

External surfaces (subprocess, shutil) are injected as Callables so the
unit tests can drive each branch deterministically.

Checks (executed in order, stable):
  - ffmpeg          PASS / FAIL  (shutil.which("ffmpeg"))
  - ffprobe         PASS / FAIL  (shutil.which("ffprobe"))
  - yt-dlp          PASS / FAIL  (yt_dlp.version.__version__ via callable)
  - output_dir      PASS / FAIL  (Sprint 9 symlink helper as predicate)
  - output_dir_writable
                    PASS / FAIL  (transient mkdir + tempfile.touch + unlink)
  - disk_free       PASS / WARN  (free vs safety_multiplier * 1 GiB floor;
                                   WARN, not FAIL: free space is dynamic)

`has_failures` reflects the FAIL count only; WARN does NOT trip it.
The CLI exits 0 on `not has_failures`, 1 otherwise.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from shokz.adapters.inbound.cli._runtime import assert_output_dir_safe
from shokz.config.schema import AppConfig
from shokz.domain.errors import NameOutsideOutputDir

_log = logging.getLogger("shokz.usecase.doctor")

# Sprint 10: WARN floor for disk-free check. 1 GiB * safety_multiplier
# is a sane minimum for any meaningful download workload (a single
# Big-Buck-Bunny mp3 ~5 MiB; a 60-track playlist easily hits hundreds
# of MiB). Below this, the user's next download is likely to fail.
_DISK_WARN_FLOOR_BYTES: Final[int] = 1024 * 1024 * 1024  # 1 GiB

CheckStatus = Literal["PASS", "WARN", "FAIL"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Single named diagnostic outcome."""

    name: str
    status: CheckStatus
    message: str


@dataclass(frozen=True, slots=True)
class DoctorResult:
    """Aggregate of all check outcomes from a `shokz doctor` run."""

    checks: tuple[CheckResult, ...]

    @property
    def has_failures(self) -> bool:
        """True iff any check is FAIL. WARN does NOT count -- the CLI
        uses this to pick its exit code (0 vs 1)."""
        return any(c.status == "FAIL" for c in self.checks)


class RunDoctorUseCase:
    """Coordinator: runs each check in sequence, aggregates results."""

    def __init__(
        self,
        config: AppConfig,
        *,
        which: Callable[[str], str | None],
        disk_free_bytes: Callable[[Path], int],
        ytdlp_version: Callable[[], str],
    ) -> None:
        self._config = config
        self._which = which
        self._disk_free_bytes = disk_free_bytes
        self._ytdlp_version = ytdlp_version

    async def execute(self) -> DoctorResult:
        checks: list[CheckResult] = []
        checks.append(self._check_external_tool("ffmpeg"))
        checks.append(self._check_external_tool("ffprobe"))
        checks.append(self._check_ytdlp())
        checks.append(self._check_output_dir_safe())
        checks.append(self._check_output_dir_writable())
        checks.append(self._check_disk_free())
        return DoctorResult(checks=tuple(checks))

    def _check_external_tool(self, cmd: str) -> CheckResult:
        path = self._which(cmd)
        if path is None:
            return CheckResult(
                name=cmd,
                status="FAIL",
                message=f"{cmd} not found on PATH; install it (e.g. `brew install ffmpeg`)",
            )
        return CheckResult(name=cmd, status="PASS", message=f"found at {path}")

    def _check_ytdlp(self) -> CheckResult:
        try:
            version = self._ytdlp_version()
        except Exception as e:
            return CheckResult(
                name="yt-dlp",
                status="FAIL",
                message=f"yt-dlp version probe failed: {e}",
            )
        return CheckResult(
            name="yt-dlp", status="PASS", message=f"version {version}"
        )

    def _check_output_dir_safe(self) -> CheckResult:
        """Reuse the Sprint 9 symlink helper as a predicate (catch the
        raise instead of letting it propagate)."""
        try:
            assert_output_dir_safe(self._config)
        except NameOutsideOutputDir as e:
            return CheckResult(
                name="output_dir",
                status="FAIL",
                message=str(e),
            )
        return CheckResult(
            name="output_dir",
            status="PASS",
            message=f"{self._config.general.output_dir} is not symlinked",
        )

    def _check_output_dir_writable(self) -> CheckResult:
        """Probe writability: mkdir(parents, exist_ok) + tempfile.touch +
        unlink. Any OSError is FAIL with the original errno message."""
        output_dir = self._config.general.output_dir
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=output_dir, prefix=".shokz-doctor-", delete=True
            ):
                pass
        except OSError as e:
            return CheckResult(
                name="output_dir_writable",
                status="FAIL",
                message=f"cannot write to {output_dir}: {e}",
            )
        return CheckResult(
            name="output_dir_writable",
            status="PASS",
            message=f"{output_dir} is writable",
        )

    def _check_disk_free(self) -> CheckResult:
        """WARN (not FAIL) when free disk is below the safety-multiplier-
        scaled floor. Disk free is dynamic; transient low-disk shouldn't
        block other commands."""
        try:
            free = self._disk_free_bytes(self._config.general.output_dir)
        except OSError as e:
            return CheckResult(
                name="disk_free",
                status="FAIL",
                message=f"cannot stat {self._config.general.output_dir}: {e}",
            )
        threshold = int(_DISK_WARN_FLOOR_BYTES * self._config.disk.safety_multiplier)
        if free < threshold:
            return CheckResult(
                name="disk_free",
                status="WARN",
                message=(
                    f"only {free // (1024 * 1024)} MiB free; recommend "
                    f">= {threshold // (1024 * 1024)} MiB for typical workloads"
                ),
            )
        return CheckResult(
            name="disk_free",
            status="PASS",
            message=f"{free // (1024 * 1024 * 1024)} GiB free",
        )
