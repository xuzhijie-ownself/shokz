"""Sprint 11 -- strict-TDD tests for SplitAudioUseCase.

`shokz split <file.mp3> --hours 1` chops a long MP3 into hour-sized
parts so an 11-hour audiobook becomes 12 navigable files on a Shokz
device (which has no seek-within-track UI worth using underwater).

Design constraints that shape these tests:
  - LOSSLESS: ffmpeg `-c copy` stream-copies; no re-encode, no quality
    loss, seconds not minutes even on a 326 MB source.
  - NO MANIFEST COUPLING: split operates on any mp3 on disk and never
    reads or writes manifest.jsonl. That keeps skip-existing, retry, and
    reconciliation completely untouched (and dodges a schema migration).
  - NO CLOBBER: refuses to overwrite existing part files unless --force.

Strict TDD: RED phase. `SplitAudioUseCase`, its dataclasses, and the
`SplitFailed` error do not exist yet -- this module ImportErrors on the
first run.

Gherkin scenarios encoded as test functions:
  1. 3h source + --hours 1 -> 3 parts, named `Title (part 01..03).mp3`
  2. Source shorter than one segment -> exactly 1 part (not an error)
  3. Missing source file -> SplitFailed naming the path
  4. hours <= 0 -> SplitFailed (invalid segment length)
  5. ffmpeg failure -> SplitFailed carrying the adapter's message
  6. Existing part file + no --force -> SplitFailed (no silent clobber)
  7. Existing part file + --force -> proceeds, overwrites
  8. Unicode title survives the part-name template
  9. --output writes parts to a different dir; source stays put
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from shokz.application.use_cases.split_audio import (
    SplitAudioInput,
    SplitAudioResult,
    SplitAudioUseCase,
)
from shokz.domain.errors import SplitFailed

# ---------- fakes ----------


@dataclass
class _FakeSegmentingEncoder:
    """Stands in for FfmpegEncoder.segment().

    `parts_to_emit` controls how many part files the fake "ffmpeg"
    produces. `raise_on_segment` simulates an ffmpeg non-zero exit.
    Records the (src, template, seconds) it was called with so tests can
    assert the use case built the right ffmpeg invocation.
    """

    parts_to_emit: int = 3
    raise_on_segment: Exception | None = None
    calls: list[tuple[Path, Path, int]] = field(default_factory=list)

    async def segment(
        self, src: Path, dest_template: Path, segment_seconds: int
    ) -> tuple[Path, ...]:
        self.calls.append((src, dest_template, segment_seconds))
        if self.raise_on_segment is not None:
            raise self.raise_on_segment
        produced: list[Path] = []
        for n in range(1, self.parts_to_emit + 1):
            part = Path(str(dest_template) % n)
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(b"MP3" * 100)
            produced.append(part)
        return tuple(produced)

    # Unused by the split use case, present to satisfy AudioEncoderPort.
    async def encode(self, *_a: object, **_k: object) -> object: ...
    async def probe_duration(self, *_a: object, **_k: object) -> float:
        return 0.0


def _source_mp3(tmp_path: Path, name: str = "Long Audiobook.mp3") -> Path:
    src = tmp_path / name
    src.write_bytes(b"ID3" + b"\x00" * 4096)
    return src


def _uc(encoder: _FakeSegmentingEncoder) -> SplitAudioUseCase:
    return SplitAudioUseCase(encoder=encoder)  # type: ignore[arg-type]


# ---------- scenarios ----------


@pytest.mark.asyncio
async def test_three_hour_source_splits_into_three_parts(tmp_path: Path) -> None:
    """Scenario 1: happy path. Parts are 1-indexed and zero-padded so
    they sort correctly in the Shokz device's file browser."""
    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder(parts_to_emit=3)
    result = await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))

    assert isinstance(result, SplitAudioResult)
    assert len(result.parts) == 3
    assert [p.name for p in result.parts] == [
        "Long Audiobook (part 01).mp3",
        "Long Audiobook (part 02).mp3",
        "Long Audiobook (part 03).mp3",
    ]
    assert all(p.exists() for p in result.parts)
    # Source is left untouched -- split never deletes the original.
    assert src.exists()
    # 1.0 hours -> 3600s handed to the encoder.
    assert enc.calls[0][2] == 3600
    assert result.segment_seconds == 3600


@pytest.mark.asyncio
async def test_source_shorter_than_one_segment_yields_single_part(
    tmp_path: Path,
) -> None:
    """Scenario 2: a 20-minute file with --hours 1 is NOT an error --
    ffmpeg emits a single part and we report it honestly."""
    src = _source_mp3(tmp_path, "Short Clip.mp3")
    enc = _FakeSegmentingEncoder(parts_to_emit=1)
    result = await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))

    assert len(result.parts) == 1
    assert result.parts[0].name == "Short Clip (part 01).mp3"


@pytest.mark.asyncio
async def test_missing_source_raises_split_failed(tmp_path: Path) -> None:
    """Scenario 3: the use case validates the source BEFORE shelling out
    to ffmpeg, so the user gets a clean message instead of an ffmpeg
    stderr dump."""
    missing = tmp_path / "does-not-exist.mp3"
    enc = _FakeSegmentingEncoder()
    with pytest.raises(SplitFailed, match=r"does-not-exist\.mp3"):
        await _uc(enc).execute(SplitAudioInput(source=missing, hours=1.0))
    # ffmpeg was never invoked.
    assert enc.calls == []


@pytest.mark.asyncio
async def test_non_positive_hours_raises_split_failed(tmp_path: Path) -> None:
    """Scenario 4: `--hours 0` would make ffmpeg spin forever emitting
    zero-length segments. Reject it up front."""
    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder()
    with pytest.raises(SplitFailed, match="hours"):
        await _uc(enc).execute(SplitAudioInput(source=src, hours=0.0))
    assert enc.calls == []


@pytest.mark.asyncio
async def test_encoder_failure_surfaces_as_split_failed(tmp_path: Path) -> None:
    """Scenario 5: an ffmpeg non-zero exit reaches the caller as
    SplitFailed (a ShokzError), not a raw subprocess exception."""
    from shokz.domain.errors import EncodingFailed

    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder(
        raise_on_segment=EncodingFailed("ffmpeg exploded")
    )
    with pytest.raises(SplitFailed, match="ffmpeg exploded"):
        await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))


@pytest.mark.asyncio
async def test_existing_part_refuses_without_force(tmp_path: Path) -> None:
    """Scenario 6: no silent clobber. If `(part 01)` already exists we
    stop and name it, rather than overwriting a previous split."""
    src = _source_mp3(tmp_path)
    (tmp_path / "Long Audiobook (part 01).mp3").write_bytes(b"OLD")
    enc = _FakeSegmentingEncoder()

    with pytest.raises(SplitFailed, match="already exists"):
        await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))
    assert enc.calls == []
    # The pre-existing part was NOT touched.
    assert (tmp_path / "Long Audiobook (part 01).mp3").read_bytes() == b"OLD"


@pytest.mark.asyncio
async def test_existing_part_overwritten_with_force(tmp_path: Path) -> None:
    """Scenario 7: --force is the explicit opt-in to re-split."""
    src = _source_mp3(tmp_path)
    stale = tmp_path / "Long Audiobook (part 01).mp3"
    stale.write_bytes(b"OLD")
    enc = _FakeSegmentingEncoder(parts_to_emit=2)

    result = await _uc(enc).execute(
        SplitAudioInput(source=src, hours=1.0, force=True)
    )
    assert len(result.parts) == 2
    assert stale.read_bytes() != b"OLD"  # the fake rewrote it


@pytest.mark.asyncio
async def test_unicode_title_survives_part_template(tmp_path: Path) -> None:
    """Scenario 8: the real driver for this feature is an 11-hour
    Chinese audiobook. CJK characters must survive the part naming."""
    src = _source_mp3(tmp_path, "我救下了一位喝醉酒的少婦.mp3")
    enc = _FakeSegmentingEncoder(parts_to_emit=2)
    result = await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))

    assert result.parts[0].name == "我救下了一位喝醉酒的少婦 (part 01).mp3"
    assert result.parts[1].name == "我救下了一位喝醉酒的少婦 (part 02).mp3"
    assert all(p.exists() for p in result.parts)


@pytest.mark.asyncio
async def test_output_dir_redirects_parts_leaving_source_in_place(
    tmp_path: Path,
) -> None:
    """Scenario 9: `--output ~/swim-parts/` sends the parts elsewhere so
    you can keep the archive master separate from the device-bound copy.
    The dir is created if absent."""
    src = _source_mp3(tmp_path)
    parts_dir = tmp_path / "for-device"  # does NOT exist yet
    enc = _FakeSegmentingEncoder(parts_to_emit=2)

    result = await _uc(enc).execute(
        SplitAudioInput(source=src, hours=1.0, output_dir=parts_dir)
    )
    assert parts_dir.is_dir()
    assert all(p.parent == parts_dir for p in result.parts)
    assert src.parent == tmp_path  # source untouched, still in place


@pytest.mark.asyncio
async def test_fractional_hours_are_honoured(tmp_path: Path) -> None:
    """`--hours 0.5` -> 1800-second segments. Supports the 'chapters are
    ~30 min' case without forcing integer hours."""
    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder(parts_to_emit=4)
    result = await _uc(enc).execute(SplitAudioInput(source=src, hours=0.5))

    assert enc.calls[0][2] == 1800
    assert result.segment_seconds == 1800


# ---------- CLI wiring (RED phase 2) ----------
#
# `FfmpegEncoder.segment` is monkeypatched throughout so no real ffmpeg
# subprocess runs; we are testing the CLI's validation + exit codes +
# rendering, not ffmpeg itself.


def _patch_ffmpeg_segment(
    monkeypatch: pytest.MonkeyPatch, *, parts: int = 2
) -> None:
    async def _fake_segment(
        _self: object, src: Path, dest_template: Path, segment_seconds: int
    ) -> tuple[Path, ...]:
        produced = []
        for n in range(1, parts + 1):
            part = Path(str(dest_template) % n)
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(b"MP3" * 50)
            produced.append(part)
        return tuple(produced)

    monkeypatch.setattr(
        "shokz.adapters.outbound.ffmpeg_encoder.FfmpegEncoder.segment",
        _fake_segment,
    )


def test_split_cli_happy_path_exits_zero_and_lists_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`shokz split <file> --hours 1` exits 0 and names each part it
    wrote, so the user can confirm what landed on the device."""
    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    src = _source_mp3(tmp_path)
    _patch_ffmpeg_segment(monkeypatch, parts=3)

    result = CliRunner().invoke(app, ["split", str(src), "--hours", "1"])
    assert result.exit_code == 0, result.output
    assert "part 01" in result.output
    assert "part 03" in result.output
    assert "3" in result.output  # the count is surfaced


def test_split_cli_missing_source_exits_one_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing source is a clean exit-1 with an actionable message --
    never a Python traceback."""
    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    _patch_ffmpeg_segment(monkeypatch)
    missing = tmp_path / "nope.mp3"

    result = CliRunner().invoke(app, ["split", str(missing)])
    assert result.exit_code == 1, result.output
    assert "not found" in result.output.lower()
    assert "Traceback" not in result.output


def test_split_cli_refuses_clobber_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A previous split's parts are not silently overwritten."""
    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    src = _source_mp3(tmp_path)
    (tmp_path / "Long Audiobook (part 01).mp3").write_bytes(b"OLD")
    _patch_ffmpeg_segment(monkeypatch)

    result = CliRunner().invoke(app, ["split", str(src)])
    assert result.exit_code == 1, result.output
    assert "already exists" in result.output.lower()
    assert (tmp_path / "Long Audiobook (part 01).mp3").read_bytes() == b"OLD"


def test_split_cli_rejects_non_positive_hours_with_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--hours 0` is an invalid invocation, not a runtime failure, so it
    exits 2 -- matching the convention set by --name/--all/--since."""
    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    src = _source_mp3(tmp_path)
    _patch_ffmpeg_segment(monkeypatch)

    result = CliRunner().invoke(app, ["split", str(src), "--hours", "0"])
    assert result.exit_code == 2, result.output
