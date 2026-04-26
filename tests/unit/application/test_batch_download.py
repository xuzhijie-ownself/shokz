"""Unit tests for BatchDownloadUseCase — Sprint 1 scenarios using fakes."""

from __future__ import annotations

from pathlib import Path
from pathlib import Path as _Path

import pytest

from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.application.use_cases.batch_download import (
    BatchDownloadInput,
    BatchDownloadUseCase,
)
from shokz.domain.models import TrackStatus
from shokz.domain.presets import SWIM_STANDARD
from tests.fakes import FakeAudioEncoder, FakeProgressReporter, FakeVideoSource


def _resolver_factory(output_dir: _Path) -> FilenameResolver:
    return FilenameResolver(output_dir=output_dir)


_URL_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
_URL_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb"
_URL_C = "https://www.youtube.com/watch?v=ccccccccccc"


@pytest.mark.asyncio
async def test_use_case_orchestration_three_urls_all_succeed(tmp_path: Path) -> None:
    """Sprint 1 AC: 3 URLs → 3 succeeded; ports called the right number of times."""
    source = FakeVideoSource()
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
    )

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A, _URL_B, _URL_C),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=3,
        )
    )

    assert result.succeeded == 3
    assert result.failed == 0
    assert len(source.resolve_calls) == 3
    assert len(source.download_calls) == 3
    assert len(encoder.encode_calls) == 3

    # Each encode received the raw path emitted by the source for the same track.
    encoded_dest_names = {dest.name for _, dest, _ in encoder.encode_calls}
    expected_partials = {
        "aaaaaaaaaaa.mp3.partial",
        "bbbbbbbbbbb.mp3.partial",
        "ccccccccccc.mp3.partial",
    }
    assert encoded_dest_names == expected_partials

    # Final files use Sprint 2 title-based naming (FakeVideoSource synthesizes
    # title="Title for {id}") -- NOT the {id}.mp3 of Sprint 1.
    finals = sorted((tmp_path / "downloads").glob("*.mp3"))
    assert {p.name for p in finals} == {
        "Title for aaaaaaaaaaa.mp3",
        "Title for bbbbbbbbbbb.mp3",
        "Title for ccccccccccc.mp3",
    }

    # Raw files cleaned up (keep_raw default False).
    assert sorted((tmp_path / "downloads" / ".tmp").glob("*.fake")) == []


@pytest.mark.asyncio
async def test_failure_is_isolated_per_track(tmp_path: Path) -> None:
    """Sprint 1 AC: one failure doesn't kill the batch; partial success surfaced."""
    source = FakeVideoSource(fail_resolve_for=frozenset({_URL_B}))
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
    )

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A, _URL_B, _URL_C),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=3,
        )
    )

    assert result.succeeded == 2
    assert result.failed == 1
    failed = next(r for r in result.results if r.status is TrackStatus.FAILED)
    assert failed.track is None  # resolve failed, no track resolved
    assert failed.error is not None
    assert "fake-fail resolve" in failed.error


@pytest.mark.asyncio
async def test_keep_raw_preserves_tmp_file(tmp_path: Path) -> None:
    source = FakeVideoSource()
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
    )

    await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
            keep_raw=True,
        )
    )

    raws = list((tmp_path / "downloads" / ".tmp").glob("*.fake"))
    assert len(raws) == 1


@pytest.mark.asyncio
async def test_no_source_can_handle_url_raises(tmp_path: Path) -> None:
    source = FakeVideoSource()
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
    )

    result = await use_case.execute(
        BatchDownloadInput(
            urls=("https://vimeo.com/12345",),  # FakeVideoSource won't claim this URL
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )

    # _process_one catches ValueError from _select_source and produces a FAILED
    # TrackResult — the batch survives, the unsupported URL is reported.
    assert result.succeeded == 0
    assert result.failed == 1
    assert result.results[0].status is TrackStatus.FAILED
    assert result.results[0].track is None
    assert result.results[0].error is not None
    assert "no source can handle" in result.results[0].error.lower()


@pytest.mark.asyncio
async def test_unexpected_exception_in_resolve_is_isolated(tmp_path: Path) -> None:
    """Sprint 1 non-functional: per-track failure isolation must catch ANY exception type."""

    class _ExplodingSource(FakeVideoSource):
        async def resolve(self, url: str):  # type: ignore[override]
            self.resolve_calls.append(url)
            raise RuntimeError("BOOM — non-ShokzError exception escaping resolve")

    source = _ExplodingSource()
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
    )

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A, _URL_B),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=2,
        )
    )

    assert result.succeeded == 0
    assert result.failed == 2
    for r in result.results:
        assert r.status is TrackStatus.FAILED
        assert r.error is not None
        assert "BOOM" in r.error or "unexpected" in r.error.lower()
