"""Sprint 12 -- domain rules for split part naming + enumeration.

WHY THIS IS DOMAIN, NOT ADAPTER:
Part names look like an ffmpeg detail but they are not. `(part 01)` exists,
zero-padded and 1-indexed, because the Shokz device's file browser sorts
lexicographically -- `(part 10)` must not sort before `(part 2)`. That is a
business rule about the target hardware, so it lives in domain/ and is the
single source of truth for both the use case and the adapter.

Sprint 11 got this wrong: the use case built an ffmpeg printf template
(`(part %02d).mp3`) and handed naming authority to ffmpeg, then the adapter
reverse-engineered "what did I produce?" by scanning until the first gap.
That produced HIGH-severity silent corruption (stale parts from a previous
split reported as freshly written) and a hard crash on any title containing
a '%'. This module exists to make both impossible.

Strict TDD: RED phase. `shokz.domain.split_parts` does not exist yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shokz.domain.split_parts import (
    PART_PATTERN,
    existing_parts,
    pad_width,
    part_name,
)

# ---------- part_name ----------


def test_part_name_is_one_indexed_and_zero_padded() -> None:
    """The whole reason this rule exists: lexicographic sort on-device."""
    assert part_name("Book", 1, ".mp3", width=2) == "Book (part 01).mp3"
    assert part_name("Book", 9, ".mp3", width=2) == "Book (part 09).mp3"
    assert part_name("Book", 10, ".mp3", width=2) == "Book (part 10).mp3"


def test_part_names_sort_lexicographically_in_play_order() -> None:
    """The load-bearing property. If this breaks, the device plays the book
    out of order and the user has no way to tell why."""
    names = [part_name("Book", n, ".mp3", width=2) for n in range(1, 13)]
    assert names == sorted(names), "part names must sort into play order"


def test_pad_width_grows_so_hundreds_of_parts_still_sort() -> None:
    """Sprint 11 hard-coded %02d. A 24h source at --hours 0.001 (3s parts)
    yields ~28800 parts; width must follow the real count, not a constant."""
    assert pad_width(1) == 2  # never narrower than 2 -- (part 01), not (part 1)
    assert pad_width(9) == 2
    assert pad_width(99) == 2
    assert pad_width(100) == 3
    assert pad_width(999) == 3
    assert pad_width(1000) == 4
    assert pad_width(28800) == 5


def test_wide_part_names_still_sort_in_play_order() -> None:
    names = [part_name("Book", n, ".mp3", width=pad_width(150)) for n in range(1, 151)]
    assert names == sorted(names)
    assert names[0] == "Book (part 001).mp3"
    assert names[-1] == "Book (part 150).mp3"


def test_percent_in_stem_is_literal_never_a_format_specifier() -> None:
    """THE Sprint 11 CRASH. `100% Focus.mp3` is a filename `download` will
    happily create (sanitize_filename permits '%'), and Sprint 11's
    `str(template) % n` blew up with a raw TypeError. Here '%' is inert."""
    assert part_name("100% Focus", 1, ".mp3", width=2) == "100% Focus (part 01).mp3"
    assert part_name("Top 10 (50% Off)", 2, ".mp3", width=2) == (
        "Top 10 (50% Off) (part 02).mp3"
    )
    # A stem that looks exactly like a printf template must survive verbatim.
    assert part_name("%02d %s", 3, ".mp3", width=2) == "%02d %s (part 03).mp3"


def test_part_name_preserves_unicode_stem() -> None:
    """The real driver: an 11h Chinese audiobook."""
    assert part_name("我救下了一位少婦", 7, ".mp3", width=2) == (
        "我救下了一位少婦 (part 07).mp3"
    )


def test_part_name_rejects_non_positive_index() -> None:
    """Parts are 1-indexed. A 0 would produce `(part 00)`, which sorts before
    everything and is not a real part."""
    with pytest.raises(ValueError, match="1-based"):
        part_name("Book", 0, ".mp3", width=2)


# ---------- existing_parts ----------


def _touch(directory: Path, name: str) -> Path:
    p = directory / name
    p.write_bytes(b"x")
    return p


def test_existing_parts_finds_the_series_in_play_order(tmp_path: Path) -> None:
    # Deliberately create them out of order to prove we sort.
    for n in (3, 1, 2):
        _touch(tmp_path, f"Book (part 0{n}).mp3")
    found = existing_parts(tmp_path, "Book", ".mp3")
    assert [p.name for p in found] == [
        "Book (part 01).mp3",
        "Book (part 02).mp3",
        "Book (part 03).mp3",
    ]


def test_existing_parts_finds_a_series_with_a_HOLE_in_it(  # noqa: N802 - caps mark a bug-repro test
    tmp_path: Path,
) -> None:
    """THE Sprint 11 GUARD BUG. The old no-clobber check tested only
    `(part 01)`. A user who listened to part 01 and deleted it would then get
    a *silent* re-split on top of the surviving parts 02..12. Enumeration must
    see the whole series, holes included -- 'walk until the first gap' is
    exactly the broken idiom we are replacing."""
    for n in (2, 3, 4):
        _touch(tmp_path, f"Book (part 0{n}).mp3")
    # part 01 is absent
    found = existing_parts(tmp_path, "Book", ".mp3")
    assert [p.name for p in found] == [
        "Book (part 02).mp3",
        "Book (part 03).mp3",
        "Book (part 04).mp3",
    ]


def test_existing_parts_matches_any_pad_width(tmp_path: Path) -> None:
    """A prior split may have used a different width. Enumeration must still
    find those files, or --force would fail to clean them up."""
    _touch(tmp_path, "Book (part 01).mp3")
    _touch(tmp_path, "Book (part 002).mp3")
    _touch(tmp_path, "Book (part 0003).mp3")
    found = existing_parts(tmp_path, "Book", ".mp3")
    assert len(found) == 3
    # Sorted by the PARSED integer, not lexicographically.
    assert [p.name for p in found] == [
        "Book (part 01).mp3",
        "Book (part 002).mp3",
        "Book (part 0003).mp3",
    ]


def test_existing_parts_ignores_the_source_and_unrelated_files(tmp_path: Path) -> None:
    _touch(tmp_path, "Book.mp3")  # the SOURCE -- must never be enumerated
    _touch(tmp_path, "Book (part 01).mp3")
    _touch(tmp_path, "Other Book (part 01).mp3")  # different stem
    _touch(tmp_path, "Book (part 01).txt")  # different suffix
    _touch(tmp_path, "Book (part abc).mp3")  # not a number
    _touch(tmp_path, "notes.txt")
    found = existing_parts(tmp_path, "Book", ".mp3")
    assert [p.name for p in found] == ["Book (part 01).mp3"]


def test_existing_parts_treats_regex_metacharacters_in_stem_as_literal(
    tmp_path: Path,
) -> None:
    """A stem like `Top 10 (50% Off) [Remix]` contains regex metacharacters
    `(`, `)`, `[`, `]` AND a printf `%`. Both must be inert. Sprint 8b already
    learned this lesson for globbing (glob.escape); the same class of bug
    applies to regex."""
    stem = "Top 10 (50% Off) [Remix] a+b"
    _touch(tmp_path, f"{stem} (part 01).mp3")
    _touch(tmp_path, f"{stem} (part 02).mp3")
    # A file that a NAIVE (unescaped) regex would wrongly match.
    _touch(tmp_path, "Top 10 X50Y OffZ QRemixS aab (part 09).mp3")
    found = existing_parts(tmp_path, stem, ".mp3")
    assert [p.name for p in found] == [
        f"{stem} (part 01).mp3",
        f"{stem} (part 02).mp3",
    ]


def test_existing_parts_returns_empty_for_missing_directory(tmp_path: Path) -> None:
    """A not-yet-created output dir has no parts. Must not raise -- the
    no-clobber guard calls this before the dir necessarily exists."""
    assert existing_parts(tmp_path / "nope", "Book", ".mp3") == []


def test_existing_parts_returns_empty_when_none_match(tmp_path: Path) -> None:
    _touch(tmp_path, "Book.mp3")
    assert existing_parts(tmp_path, "Book", ".mp3") == []


def test_part_pattern_is_exported_for_reuse() -> None:
    """The adapter needs to parse its own temp-dir filenames; exporting the
    pattern keeps the format spelled in exactly one place."""
    assert PART_PATTERN is not None
