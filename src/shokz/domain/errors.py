"""Domain error taxonomy.

Sprint 1 ships the minimal taxonomy the use case needs. Sprint 7 expands it
with the full §7 + §7.1 translation table from the plan.
"""

from __future__ import annotations


class ShokzError(Exception):
    """Base class for every shokz domain error."""


# Source / resolution
class SourceUnavailable(ShokzError):
    """Video deleted, private, or 404."""


# Download
class DownloadFailed(ShokzError):
    """yt-dlp returned a non-zero exit or unrecognized error."""


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
    """The on-disk manifest disagrees with the actual files (Sprint 4.5 + reconciliation)."""
