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
