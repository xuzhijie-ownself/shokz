"""Sprint 12 -- FfmpegEncoder.segment() against REAL ffmpeg.

THIS IS THE FILE THAT SHOULD HAVE EXISTED IN SPRINT 11.

Sprint 11 shipped `segment()` with 14 green tests and a HIGH-severity silent
corruption bug, because every one of those tests ran against a FAKE encoder --
and the fake had *different semantics than the real adapter*:

    fake:    returned what it WROTE       (honest)
    adapter: returned what EXISTED on disk (walk 1..N until first gap)

Those differ the moment the output directory already contains parts from a
previous split. The test suite only ever saw the honest one.

So these tests drive the REAL ffmpeg binary. They are fast (synthetic sine
tones, stream-copy) and ffmpeg is already a hard dependency -- `shokz doctor`
fails without it. They are deliberately NOT gated behind INTEGRATION=1:
gating them is how the bug shipped.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from shokz.adapters.outbound.ffmpeg_encoder import FfmpegEncoder
from shokz.domain.errors import EncodingFailed

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not on PATH (it is a hard dependency; see `shokz doctor`)",
)


# ---------- helpers ----------


async def _make_tone(dest: Path, seconds: int) -> Path:
    """Synthesise a real, decodable MP3 of the given length."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={seconds}",
        "-c:a", "libmp3lame", "-b:a", "64k",
        "-loglevel", "error",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    assert dest.exists(), "failed to synthesise tone"
    assert dest.stat().st_size > 0, "synthesised tone is empty"
    return dest


def _names(paths: tuple[Path, ...] | list[Path]) -> list[str]:
    return [p.name for p in paths]


def _on_disk(directory: Path, suffix: str = ".mp3") -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.suffix == suffix)


# ---------- the core contract ----------


@pytest.mark.asyncio
async def test_segment_returns_exactly_what_it_wrote(tmp_path: Path) -> None:
    """The load-bearing contract: the return value is the parts THIS CALL
    produced -- not an inference from what happens to be on disk."""
    src = await _make_tone(tmp_path / "Book.mp3", seconds=10)
    out = tmp_path / "out"
    out.mkdir()

    parts = await FfmpegEncoder().segment(src, out, "Book", ".mp3", 4)

    assert _names(parts) == [
        "Book (part 01).mp3",
        "Book (part 02).mp3",
        "Book (part 03).mp3",
    ]
    assert all(p.exists() and p.stat().st_size > 0 for p in parts)
    # Everything it claims it wrote is on disk, and nothing else is.
    assert _on_disk(out) == _names(parts)


@pytest.mark.asyncio
async def test_stale_parts_in_dest_are_NOT_reported_as_produced(  # noqa: N802 - caps mark a bug-repro test
    tmp_path: Path,
) -> None:
    """*** THE SPRINT 11 HIGH-SEVERITY BUG. ***

    Reproduces the exact corruption: the destination already holds parts
    04..09 from a previous, finer-grained split. This call produces only 3
    parts. Sprint 11's adapter walked 1..N until the first gap, found all 9
    files present, and reported "9 parts produced" -- so the CLI told the
    user 9 fresh parts were written and they copied six stale ones, holding
    already-covered audio at the wrong boundaries, onto the device.

    The return value MUST contain only the 3 parts this call actually wrote.
    """
    src = await _make_tone(tmp_path / "Book.mp3", seconds=10)
    out = tmp_path / "out"
    out.mkdir()
    for n in range(4, 10):  # stale parts 04..09 from a prior split
        (out / f"Book (part 0{n}).mp3").write_bytes(b"STALE-AUDIO")

    parts = await FfmpegEncoder().segment(src, out, "Book", ".mp3", 4)

    assert len(parts) == 3, (
        f"adapter reported {len(parts)} parts but ffmpeg only wrote 3 -- "
        "it is inferring from disk contents again"
    )
    assert _names(parts) == [
        "Book (part 01).mp3",
        "Book (part 02).mp3",
        "Book (part 03).mp3",
    ]
    # The adapter does not delete the stale files (that is the use case's
    # --force job) -- but it must not CLAIM them either.
    for n in range(4, 10):
        stale = out / f"Book (part 0{n}).mp3"
        assert stale.read_bytes() == b"STALE-AUDIO"
        assert stale not in parts


@pytest.mark.asyncio
async def test_percent_in_stem_does_not_crash(tmp_path: Path) -> None:
    """*** THE SPRINT 11 CRASH. *** `str(template) % n` raised a raw
    TypeError for any title containing '%'. `download` creates such files
    happily -- sanitize_filename permits '%'."""
    src = await _make_tone(tmp_path / "100% Focus.mp3", seconds=6)
    out = tmp_path / "out"
    out.mkdir()

    # No exact count assertion: ffmpeg cuts on frame boundaries, so a 6s tone
    # at 3s may yield 2 parts or 2 + a sub-second tail. The contract under
    # test is that '%' is inert, not ffmpeg's frame arithmetic.
    parts = await FfmpegEncoder().segment(src, out, "100% Focus", ".mp3", 3)

    assert len(parts) >= 2
    assert _names(parts)[0] == "100% Focus (part 01).mp3"
    assert all(p.name.startswith("100% Focus (part ") for p in parts)
    assert all(p.exists() for p in parts)
    # And the '%' survived into the real filenames on disk.
    assert _on_disk(out) == sorted(_names(parts))


@pytest.mark.asyncio
async def test_stem_that_looks_like_a_printf_template_survives(
    tmp_path: Path,
) -> None:
    """Adversarial: a stem that is itself a format string."""
    src = await _make_tone(tmp_path / "raw.mp3", seconds=4)
    out = tmp_path / "out"
    out.mkdir()

    parts = await FfmpegEncoder().segment(src, out, "%02d %s %%", ".mp3", 3)

    assert _names(parts)[0] == "%02d %s %% (part 01).mp3"
    assert parts[0].exists()


# ---------- failure paths ----------


@pytest.mark.asyncio
async def test_failed_segment_leaves_none_of_its_own_partials(
    tmp_path: Path,
) -> None:
    """A failed split must leave a CLEAN directory, or the use case's
    no-clobber guard would refuse the very retry the user needs."""
    not_audio = tmp_path / "Book.mp3"
    not_audio.write_bytes(b"this is definitely not an mp3")
    out = tmp_path / "out"
    out.mkdir()

    with pytest.raises(EncodingFailed):
        await FfmpegEncoder().segment(not_audio, out, "Book", ".mp3", 4)

    assert list(out.iterdir()) == [], "a failed segment left files behind"


@pytest.mark.asyncio
async def test_failed_segment_does_not_delete_a_previous_good_split(
    tmp_path: Path,
) -> None:
    """*** THE SPRINT 11 CLEANUP BUG. *** Sprint 11's `_cleanup_parts` ran a
    walk-until-gap over the DESTINATION directory and unlinked whatever it
    found -- so a failed run could destroy the user's previous, perfectly
    good split. Cleanup must be incapable of touching files this call did
    not create."""
    not_audio = tmp_path / "Book.mp3"
    not_audio.write_bytes(b"not an mp3")
    out = tmp_path / "out"
    out.mkdir()
    previous = {}
    for n in range(1, 13):  # a complete, good previous split
        p = out / f"Book (part {n:02d}).mp3"
        p.write_bytes(f"GOOD-PART-{n}".encode())
        previous[p] = p.read_bytes()

    with pytest.raises(EncodingFailed):
        await FfmpegEncoder().segment(not_audio, out, "Book", ".mp3", 4)

    for path, content in previous.items():
        assert path.exists(), f"cleanup destroyed the user's previous split: {path.name}"
        assert path.read_bytes() == content, f"cleanup corrupted {path.name}"


@pytest.mark.asyncio
async def test_no_temp_directory_is_left_behind(tmp_path: Path) -> None:
    """Success AND failure must both leave zero scratch dirs."""
    src = await _make_tone(tmp_path / "Book.mp3", seconds=6)
    out = tmp_path / "out"
    out.mkdir()
    await FfmpegEncoder().segment(src, out, "Book", ".mp3", 3)
    assert [p for p in out.iterdir() if p.is_dir()] == []

    bad = tmp_path / "Bad.mp3"
    bad.write_bytes(b"nope")
    with pytest.raises(EncodingFailed):
        await FfmpegEncoder().segment(bad, out, "Bad", ".mp3", 3)
    assert [p for p in out.iterdir() if p.is_dir()] == []


@pytest.mark.asyncio
async def test_missing_source_fails_cleanly_not_with_a_traceback(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(EncodingFailed):
        await FfmpegEncoder().segment(
            tmp_path / "nope.mp3", out, "nope", ".mp3", 4
        )
    assert list(out.iterdir()) == []


# ---------- pad width ----------


@pytest.mark.asyncio
async def test_pad_width_follows_the_real_part_count(tmp_path: Path) -> None:
    """Sprint 11 hard-coded `%02d`. Past 99 parts, `(part 100)` sorts BEFORE
    `(part 99)` on-device -- the book plays out of order. Width must follow
    the count, and the names must sort into play order."""
    src = await _make_tone(tmp_path / "Book.mp3", seconds=105)
    out = tmp_path / "out"
    out.mkdir()

    parts = await FfmpegEncoder().segment(src, out, "Book", ".mp3", 1)

    assert len(parts) > 99, f"expected >99 parts to exercise widening, got {len(parts)}"
    names = _names(parts)
    assert names[0] == "Book (part 001).mp3", "pad width did not widen to 3"
    # THE property that matters: lexicographic order == play order.
    assert names == sorted(names)
    # And the returned order is play order.
    assert names == _names(sorted(parts, key=lambda p: p.name))
