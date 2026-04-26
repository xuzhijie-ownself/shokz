"""Unit tests for ExpandPlaylistUseCase -- Sprint 5."""

from __future__ import annotations

import pytest

from shokz.application.use_cases.expand_playlist import ExpandPlaylistUseCase
from shokz.domain.errors import SourceUnavailable
from tests.fakes import FakeVideoSource


@pytest.mark.asyncio
async def test_expandplaylistusecase_unit_level() -> None:
    """Sprint 5 AC: 'ExpandPlaylistUseCase -- unit-level'."""
    items = (
        "https://www.youtube.com/watch?v=aaa",
        "https://www.youtube.com/watch?v=bbb",
        "https://www.youtube.com/watch?v=ccc",
    )
    source = FakeVideoSource(playlist_items=items)
    use_case = ExpandPlaylistUseCase(sources=(source,))

    result = await use_case.execute("https://www.youtube.com/playlist?list=PLfake")

    assert result.item_urls == items
    assert result.title == "fake-playlist"


@pytest.mark.asyncio
async def test_expand_playlist_rejects_non_playlist_url() -> None:
    """Returns SourceUnavailable when the source says 'not a playlist'."""
    source = FakeVideoSource(playlist_items=None)  # default -> not a playlist
    use_case = ExpandPlaylistUseCase(sources=(source,))

    with pytest.raises(SourceUnavailable, match="not a playlist"):
        await use_case.execute("https://www.youtube.com/watch?v=single")


@pytest.mark.asyncio
async def test_expand_playlist_rejects_unsupported_url() -> None:
    """Returns ValueError when no source recognizes the URL."""
    source = FakeVideoSource()
    use_case = ExpandPlaylistUseCase(sources=(source,))

    with pytest.raises(ValueError, match="no source can handle"):
        await use_case.execute("https://vimeo.com/12345")
