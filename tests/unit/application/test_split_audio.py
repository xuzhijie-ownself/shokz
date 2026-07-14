"""Sprint 12 -- SplitAudioUseCase, rewritten after the Sprint 11 corruption bug.

WHY THIS FILE LOOKS DIFFERENT NOW
---------------------------------
Sprint 11 had 14 green tests here and still shipped HIGH-severity silent
corruption. The reason is the single most important lesson of the sprint:

    the FAKE encoder had DIFFERENT SEMANTICS than the REAL adapter.

    fake:    returned the parts it WROTE            (honest)
    adapter: returned the parts that EXISTED on disk (walk 1..N until a gap)

Those agree only when the output directory starts empty. The moment it holds
a previous split, the real adapter reports stale files as freshly written --
and no test could see it, because the fake was strictly better-behaved than
the thing that ships.

So the fake below is now bound to the SAME domain rule the adapter uses
(`domain.split_parts.part_name`) and returns only what it wrote. A fake that
is *easier* than the real thing is not a test, it is a mirror.

The adapter's own behaviour is now pinned separately, against REAL ffmpeg, in
tests/unit/adapters/test_ffmpeg_encoder_segment.py -- the file that should
have existed in Sprint 11.
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
from shokz.domain.split_parts import pad_width, part_name

# ---------- fakes ----------


@dataclass
class _FakeSegmentingEncoder:
    """Mirrors the REAL adapter's contract: names parts with the domain rule,
    and returns EXACTLY what it wrote -- never what happens to be on disk."""

    parts_to_emit: int = 3
    raise_on_segment: Exception | None = None
    calls: list[tuple[Path, Path, str, str, int]] = field(default_factory=list)

    async def segment(
        self,
        src: Path,
        dest_dir: Path,
        stem: str,
        suffix: str,
        segment_seconds: int,
    ) -> tuple[Path, ...]:
        self.calls.append((src, dest_dir, stem, suffix, segment_seconds))
        if self.raise_on_segment is not None:
            raise self.raise_on_segment
        dest_dir.mkdir(parents=True, exist_ok=True)
        width = pad_width(self.parts_to_emit)
        written: list[Path] = []
        for n in range(1, self.parts_to_emit + 1):
            part = dest_dir / part_name(stem, n, suffix, width)
            part.write_bytes(b"NEW-PART")
            written.append(part)
        return tuple(written)

    # Unused here; present to satisfy AudioEncoderPort.
    async def encode(self, *_a: object, **_k: object) -> object: ...
    async def probe_duration(self, *_a: object, **_k: object) -> float:
        return 0.0


def _source_mp3(tmp_path: Path, name: str = "Long Audiobook.mp3") -> Path:
    src = tmp_path / name
    src.write_bytes(b"ID3" + b"\x00" * 4096)
    return src


def _uc(encoder: _FakeSegmentingEncoder) -> SplitAudioUseCase:
    return SplitAudioUseCase(encoder=encoder)  # type: ignore[arg-type]


def _mp3s(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.suffix == ".mp3")


# ---------- happy path ----------


@pytest.mark.asyncio
async def test_splits_into_parts_and_reports_them(tmp_path: Path) -> None:
    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder(parts_to_emit=3)
    result = await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))

    assert isinstance(result, SplitAudioResult)
    assert [p.name for p in result.parts] == [
        "Long Audiobook (part 01).mp3",
        "Long Audiobook (part 02).mp3",
        "Long Audiobook (part 03).mp3",
    ]
    assert all(p.exists() for p in result.parts)
    assert src.exists(), "the source must never be consumed"
    assert result.segment_seconds == 3600
    # The use case hands the adapter a stem+suffix -- never a printf template.
    _src, _dir, stem, suffix, seconds = enc.calls[0]
    assert stem == "Long Audiobook"
    assert suffix == ".mp3"
    assert seconds == 3600


@pytest.mark.asyncio
async def test_fractional_hours_are_honoured(tmp_path: Path) -> None:
    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder(parts_to_emit=4)
    result = await _uc(enc).execute(SplitAudioInput(source=src, hours=0.5))
    assert enc.calls[0][4] == 1800
    assert result.segment_seconds == 1800


@pytest.mark.asyncio
async def test_source_shorter_than_one_segment_yields_a_single_part(
    tmp_path: Path,
) -> None:
    src = _source_mp3(tmp_path, "Short Clip.mp3")
    enc = _FakeSegmentingEncoder(parts_to_emit=1)
    result = await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))
    assert [p.name for p in result.parts] == ["Short Clip (part 01).mp3"]


@pytest.mark.asyncio
async def test_unicode_stem_survives(tmp_path: Path) -> None:
    """The real driver: an 11h Chinese audiobook."""
    src = _source_mp3(tmp_path, "我救下了一位少婦.mp3")
    enc = _FakeSegmentingEncoder(parts_to_emit=2)
    result = await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))
    assert [p.name for p in result.parts] == [
        "我救下了一位少婦 (part 01).mp3",
        "我救下了一位少婦 (part 02).mp3",
    ]


@pytest.mark.asyncio
async def test_output_dir_redirects_parts_and_is_created(tmp_path: Path) -> None:
    src = _source_mp3(tmp_path)
    parts_dir = tmp_path / "for-device"  # does not exist yet
    enc = _FakeSegmentingEncoder(parts_to_emit=2)

    result = await _uc(enc).execute(
        SplitAudioInput(source=src, hours=1.0, output_dir=parts_dir)
    )
    assert parts_dir.is_dir()
    assert all(p.parent == parts_dir for p in result.parts)
    assert src.parent == tmp_path, "source stays put"


# ---------- the no-clobber guard (Sprint 12 hardening) ----------


@pytest.mark.asyncio
async def test_refuses_when_any_part_exists_naming_the_count(tmp_path: Path) -> None:
    src = _source_mp3(tmp_path)
    for n in (1, 2, 3):
        (tmp_path / part_name("Long Audiobook", n, ".mp3", 2)).write_bytes(b"OLD")
    enc = _FakeSegmentingEncoder()

    with pytest.raises(SplitFailed, match="3 existing part"):
        await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))
    assert enc.calls == [], "must refuse BEFORE shelling out to ffmpeg"
    assert (tmp_path / "Long Audiobook (part 01).mp3").read_bytes() == b"OLD"


@pytest.mark.asyncio
async def test_guard_sees_a_series_whose_FIRST_part_was_deleted(  # noqa: N802 - caps mark a bug-repro test
    tmp_path: Path,
) -> None:
    """*** SPRINT 11 GUARD BUG. *** The old check tested only `(part 01)`.
    A user who listened to part 01 and deleted it would get a SILENT
    re-split on top of the surviving parts 02..12 -- mixing two splits."""
    src = _source_mp3(tmp_path)
    for n in (2, 3, 4):  # part 01 deliberately absent
        (tmp_path / part_name("Long Audiobook", n, ".mp3", 2)).write_bytes(b"OLD")
    enc = _FakeSegmentingEncoder()

    with pytest.raises(SplitFailed, match="3 existing part"):
        await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))
    assert enc.calls == []


@pytest.mark.asyncio
async def test_guard_ignores_a_different_files_parts(tmp_path: Path) -> None:
    """Parts of an unrelated book in the same folder must not block us."""
    src = _source_mp3(tmp_path)
    (tmp_path / "Other Book (part 01).mp3").write_bytes(b"OTHER")
    enc = _FakeSegmentingEncoder(parts_to_emit=2)

    result = await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))
    assert len(result.parts) == 2
    assert (tmp_path / "Other Book (part 01).mp3").read_bytes() == b"OTHER"


# ---------- --force (the HIGH-severity fix) ----------


@pytest.mark.asyncio
async def test_force_deletes_the_ENTIRE_old_series_before_resplitting(  # noqa: N802 - caps mark a bug-repro test
    tmp_path: Path,
) -> None:
    """*** THE SPRINT 11 HIGH-SEVERITY BUG, at the use-case level. ***

    Split hourly -> 12 parts. Re-split COARSER with --force -> only 3 parts.
    Sprint 11 let ffmpeg overwrite 01..03 and left 04..12 on disk, then
    reported all 12 as freshly written. The user copied 9 stale parts -- audio
    already covered by the new ones, at the old boundaries -- to the device.

    --force must delete the whole old series FIRST, so what remains is exactly
    the new split and nothing else.
    """
    src = _source_mp3(tmp_path)
    for n in range(1, 13):  # a previous 12-part hourly split
        (tmp_path / part_name("Long Audiobook", n, ".mp3", 2)).write_bytes(b"STALE")
    enc = _FakeSegmentingEncoder(parts_to_emit=3)  # coarser: only 3 new parts

    result = await _uc(enc).execute(
        SplitAudioInput(source=src, hours=4.0, force=True)
    )

    assert len(result.parts) == 3
    # Ground truth on disk: exactly 3 parts, all new. No survivors from the 12.
    assert _mp3s(tmp_path) == [
        "Long Audiobook (part 01).mp3",
        "Long Audiobook (part 02).mp3",
        "Long Audiobook (part 03).mp3",
        "Long Audiobook.mp3",  # the source
    ]
    assert all(p.read_bytes() == b"NEW-PART" for p in result.parts)
    assert result.deleted_stale == 12


@pytest.mark.asyncio
async def test_force_on_a_clean_directory_deletes_nothing(tmp_path: Path) -> None:
    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder(parts_to_emit=2)
    result = await _uc(enc).execute(
        SplitAudioInput(source=src, hours=1.0, force=True)
    )
    assert result.deleted_stale == 0
    assert len(result.parts) == 2


@pytest.mark.asyncio
async def test_force_does_not_touch_a_different_files_parts(tmp_path: Path) -> None:
    src = _source_mp3(tmp_path)
    (tmp_path / part_name("Long Audiobook", 1, ".mp3", 2)).write_bytes(b"STALE")
    (tmp_path / "Other Book (part 01).mp3").write_bytes(b"OTHER")
    enc = _FakeSegmentingEncoder(parts_to_emit=1)

    await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0, force=True))
    assert (tmp_path / "Other Book (part 01).mp3").read_bytes() == b"OTHER"


# ---------- failure paths ----------


@pytest.mark.asyncio
async def test_missing_source_raises_before_touching_ffmpeg(tmp_path: Path) -> None:
    enc = _FakeSegmentingEncoder()
    with pytest.raises(SplitFailed, match=r"nope\.mp3"):
        await _uc(enc).execute(
            SplitAudioInput(source=tmp_path / "nope.mp3", hours=1.0)
        )
    assert enc.calls == []


@pytest.mark.asyncio
async def test_non_positive_hours_raises(tmp_path: Path) -> None:
    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder()
    with pytest.raises(SplitFailed, match="hours"):
        await _uc(enc).execute(SplitAudioInput(source=src, hours=0.0))
    assert enc.calls == []


@pytest.mark.asyncio
async def test_encoder_failure_surfaces_as_split_failed(tmp_path: Path) -> None:
    from shokz.domain.errors import EncodingFailed

    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder(raise_on_segment=EncodingFailed("ffmpeg exploded"))
    with pytest.raises(SplitFailed, match="ffmpeg exploded"):
        await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))


@pytest.mark.asyncio
async def test_encoder_returning_no_parts_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    src = _source_mp3(tmp_path)
    enc = _FakeSegmentingEncoder(parts_to_emit=0)
    with pytest.raises(SplitFailed, match="no parts"):
        await _uc(enc).execute(SplitAudioInput(source=src, hours=1.0))


# ---------- --force safety rails (post-review hardening) ----------


@pytest.mark.asyncio
async def test_force_refuses_when_a_stale_part_cannot_be_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An undeletable stale part surviving beside a SHORTER new series is
    exactly B1's corrupt on-disk state. The first cut of this fix swallowed
    the OSError and split anyway; the reviewers caught it. Now we refuse --
    the invariant the whole sprint rests on is CHECKED, not assumed."""
    src = _source_mp3(tmp_path)
    for n in range(1, 13):
        (tmp_path / part_name("Long Audiobook", n, ".mp3", 2)).write_bytes(b"STALE")
    enc = _FakeSegmentingEncoder(parts_to_emit=3)

    real_unlink = Path.unlink

    def _locked(self: Path, **kw: object) -> None:
        if self.name == "Long Audiobook (part 07).mp3":
            raise PermissionError(13, "Operation not permitted")
        real_unlink(self, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", _locked)

    with pytest.raises(SplitFailed, match="could not remove"):
        await _uc(enc).execute(
            SplitAudioInput(source=src, hours=4.0, force=True)
        )
    assert enc.calls == [], "must abort BEFORE segmenting on top of a survivor"


@pytest.mark.asyncio
async def test_force_preflights_ffmpeg_before_deleting_the_old_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one way v1.2.1 could be WORSE than v1.2.0: ffmpeg vanishes from
    PATH, --force deletes the old parts, and the user is left with nothing.
    Pre-flight the binary before the destructive step."""
    src = _source_mp3(tmp_path)
    for n in range(1, 13):
        (tmp_path / part_name("Long Audiobook", n, ".mp3", 2)).write_bytes(b"OLD")
    enc = _FakeSegmentingEncoder(parts_to_emit=3)

    monkeypatch.setattr(
        "shokz.application.use_cases.split_audio.shutil.which", lambda _c: None
    )

    with pytest.raises(SplitFailed, match="ffmpeg not found"):
        await _uc(enc).execute(
            SplitAudioInput(source=src, hours=4.0, force=True)
        )
    # All 12 previous parts survive: we refused before touching them.
    for n in range(1, 13):
        part = tmp_path / part_name("Long Audiobook", n, ".mp3", 2)
        assert part.read_bytes() == b"OLD", f"part {n} was destroyed"
    assert enc.calls == []


# ---------- CLI ----------
#
# `FfmpegEncoder.segment` is monkeypatched throughout: we are testing the CLI's
# validation, exit codes and rendering -- not ffmpeg, which has its own
# real-binary suite in tests/unit/adapters/test_ffmpeg_encoder_segment.py.


def _patch_segment(monkeypatch: pytest.MonkeyPatch, *, parts: int = 2) -> None:
    async def _fake(
        _self: object,
        src: Path,
        dest_dir: Path,
        stem: str,
        suffix: str,
        segment_seconds: int,
    ) -> tuple[Path, ...]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        width = pad_width(parts)
        written = []
        for n in range(1, parts + 1):
            p = dest_dir / part_name(stem, n, suffix, width)
            p.write_bytes(b"MP3" * 50)
            written.append(p)
        return tuple(written)

    monkeypatch.setattr(
        "shokz.adapters.outbound.ffmpeg_encoder.FfmpegEncoder.segment", _fake
    )


def _invoke(*args: str):  # type: ignore[no-untyped-def]
    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    return CliRunner().invoke(app, ["split", *args])


def test_cli_happy_path_exits_zero_and_lists_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _source_mp3(tmp_path)
    _patch_segment(monkeypatch, parts=3)
    result = _invoke(str(src), "--hours", "1")
    assert result.exit_code == 0, result.output
    assert "part 01" in result.output
    assert "part 03" in result.output


def test_cli_missing_source_exits_one_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_segment(monkeypatch)
    result = _invoke(str(tmp_path / "nope.mp3"))
    assert result.exit_code == 1, result.output
    assert "not found" in result.output.lower()
    assert "Traceback" not in result.output


def test_cli_refuses_clobber_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _source_mp3(tmp_path)
    (tmp_path / part_name("Long Audiobook", 1, ".mp3", 2)).write_bytes(b"OLD")
    _patch_segment(monkeypatch)

    result = _invoke(str(src))
    assert result.exit_code == 1, result.output
    assert "existing part" in result.output.lower()
    assert (tmp_path / "Long Audiobook (part 01).mp3").read_bytes() == b"OLD"


def test_cli_rejects_non_positive_hours_with_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid invocation, not a runtime failure -- exit 2, per the
    convention set by --name / --all / --since."""
    src = _source_mp3(tmp_path)
    _patch_segment(monkeypatch)
    assert _invoke(str(src), "--hours", "0").exit_code == 2


def test_cli_reports_parts_removed_by_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The part count must never silently drop. Sprint 11 left the old parts
    on disk AND counted them as fresh; now a --force that removes 12 and
    writes 2 says so out loud."""
    src = _source_mp3(tmp_path)
    for n in range(1, 13):
        (tmp_path / part_name("Long Audiobook", n, ".mp3", 2)).write_bytes(b"OLD")
    _patch_segment(monkeypatch, parts=2)

    result = _invoke(str(src), "--hours", "6", "--force")
    assert result.exit_code == 0, result.output
    assert "removed 12 part(s)" in result.output
    assert "into 2 part(s)" in result.output
    # Ground truth: exactly the new split remains.
    assert _mp3s(tmp_path) == [
        "Long Audiobook (part 01).mp3",
        "Long Audiobook (part 02).mp3",
        "Long Audiobook.mp3",
    ]


def test_cli_sigint_exits_130_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SIGINT wiring added in Sprint 12 was executed by nothing. A
    KeyboardInterrupt out of the use case must render as a clean
    'interrupted' + exit 130, not a traceback."""
    src = _source_mp3(tmp_path)

    async def _boom(_self: object, _inp: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "shokz.application.use_cases.split_audio.SplitAudioUseCase.execute", _boom
    )

    result = _invoke(str(src))
    assert result.exit_code == 130, result.output
    assert "interrupted" in result.output.lower()
    assert "Traceback" not in result.output
