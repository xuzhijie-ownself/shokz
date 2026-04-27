"""Domain error taxonomy.

Sprint 1 ships the minimal taxonomy the use case needs. Sprint 7 expands it
with the full §7 + §7.1 translation table from the plan.
"""

from __future__ import annotations


class ShokzError(Exception):
    """Base class for every shokz domain error."""


# Source / resolution
class SourceUnavailable(ShokzError):
    """Video deleted, private, or 404.

    TERMINAL: never retry -- the video isn't coming back; further attempts
    waste rate budget and surprise the user with delayed failure.
    """


# Sprint 7: classified source errors per §7.1 translation table.
class AuthRequired(ShokzError):
    """Age-gated, members-only, region-locked, or sign-in required.

    TERMINAL: never retry -- the swimmer needs to add cookies / change
    region / wait for membership. Burning retries pollutes the rate limit.
    """


class FormatUnavailable(ShokzError):
    """yt-dlp couldn't find the requested audio format.

    TERMINAL: never retry -- the source's format menu won't change between
    attempts; the user needs to relax --preset or update yt-dlp.
    """


class RateLimited(ShokzError):
    """HTTP 429 / IP throttled by the source.

    RETRYABLE with LONG backoff (default 5s, 30s, 120s exponential). Retrying
    too fast just extends the throttle window.

    Carries an optional `retry_after_seconds` hint when the source surfaces
    one (some yt-dlp extractors parse the Retry-After header into the error
    message); the future RetryPolicy MAY honor this in lieu of its default
    backoff sequence.
    """

    def __init__(self, msg: str = "", retry_after_seconds: int | None = None) -> None:
        super().__init__(msg)
        self.retry_after_seconds = retry_after_seconds


class NetworkError(ShokzError):
    """Transient HTTP 5xx, connection reset, DNS hiccup, etc.

    RETRYABLE with SHORT backoff (default 1s linear). Most disappear on the
    first retry.
    """


# Download
class DownloadFailed(ShokzError):
    """yt-dlp returned a non-zero exit or unrecognized error.

    NOTE (Sprint 7 Phase 1): The classifier that produces this fallback
    does NOT yet exist. Today the pre-Sprint-7 inline matcher in
    ytdlp_source.py raises this for ANY unrecognized error AND for several
    cases that Sprint 7 Phase 2 will reclassify (HTTP 429, age-gate, 5xx,
    format-not-available). Phase 2 lands the §7.1 helper.

    Future role (post-Phase 2): default classification when no §7.1 pattern
    matches. Retried ONCE; the use case also bumps
    `BatchDownloadResult.unclassified_yt_dlp_errors` so the user sees the
    §7.1 table needs an update.
    """


# Encoding
class EncodingFailed(ShokzError):
    """ffmpeg returned a non-zero exit, or output was unusable."""


# Filename / path errors (Sprint 2)
class NameOutsideOutputDir(ShokzError):
    """A filename / --name override resolves outside the configured output_dir."""


class FilenameCollision(ShokzError):
    """Filename collision and policy disallows resolution (Sprint 3 'fail' policy)."""


class NameAmbiguous(ShokzError):
    """--name was provided with multiple URLs (semantically ambiguous)."""


class NameInvalid(ShokzError):
    """The --name override (or template-rendered name) sanitizes to empty / invalid."""


# Manifest / integrity errors (Sprint 4)
class SourceFileCorrupt(ShokzError):
    """yt-dlp reported success but the raw file is missing, 0-byte, or unreadable."""


class ManifestInconsistent(ShokzError):
    """The on-disk manifest disagrees with the actual files (Sprint 4.5 + reconciliation).

    Sprint 8 (M3): ALSO raised when an ENOSPC during `_append_with_fsync`
    leaves a final file on disk with no manifest row. Carry the underlying
    `DiskFull` as `__cause__` for diagnosis; the recoverable signal is THIS
    class (reconciliation will catch it on next startup).
    """


class ManifestReadError(ShokzError):
    """Manifest file unreadable or wholly corrupt (Sprint 4.5 SF-1 + SF-7)."""


# Sprint 8: cross-process lock + disk safety classes (v1.0 marketing primitives).
class AnotherRunInProgress(ShokzError):
    """Another `shokz` process is currently holding the lock on this output_dir.

    TERMINAL: never retry -- the user must wait for the holder to finish or
    Ctrl+C. Stderr message includes the holder's PID and the lock path.
    """


class StaleLock(ShokzError):
    """The lock file references a process that is no longer running (or is
    a different process per start-time check, indicating PID reuse), OR the
    lock meta JSON is corrupt (truncated by a prior SIGKILL).

    TERMINAL: user must manually `rm` the lock files (we never auto-delete
    a file we don't fully understand). Stderr message includes the dead
    PID (or corruption diagnosis) and explicit `rm` path guidance.

    Phase 1 GAN HIGH#2: carries optional `raw_meta_bytes` so callers can
    log the unparseable meta for diagnosis (Sprint 9 `shokz doctor` will
    surface this; for now the FileLockPolicy logs at WARNING).
    """

    def __init__(self, msg: str = "", *, raw_meta_bytes: bytes | None = None) -> None:
        super().__init__(msg)
        self.raw_meta_bytes = raw_meta_bytes


class LockOwnerUnknown(ShokzError):
    """The lock holder PID is alive but `os.kill(pid, 0)` raised
    `PermissionError` -- typically because the holder runs as another user.

    TERMINAL: refusing to assume stale (would risk corrupting another user's
    in-progress run). Stderr message names the PID and recommends contacting
    that user.
    """


class DiskFull(ShokzError):
    """Insufficient disk space (pre-flight failure OR ENOSPC at runtime).

    TERMINAL when raised by the batch-level pre-flight (entire batch aborts).
    Per-track when raised during encode / atomic_move / manifest append; the
    use case's batch-level circuit aborts the rest of the batch on first
    DiskFull (Sprint 8 GAN B3) to avoid log spam from cascading failures.

    Phase 1 GAN HIGH#1: carries optional `need_bytes` + `have_bytes` so the
    structured event stream (Sprint 9 `--ui json`) and unit tests can
    inspect the numeric values without re-parsing the formatted message.
    """

    def __init__(
        self,
        msg: str = "",
        *,
        need_bytes: int | None = None,
        have_bytes: int | None = None,
    ) -> None:
        super().__init__(msg)
        self.need_bytes = need_bytes
        self.have_bytes = have_bytes
