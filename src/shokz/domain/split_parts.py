"""Part naming + enumeration for `shokz split` -- pure domain (Sprint 12).

WHY THIS IS DOMAIN, NOT AN ADAPTER DETAIL
-----------------------------------------
`(part 01)` is zero-padded and 1-indexed because the Shokz device's file
browser sorts lexicographically: `(part 10)` must not sort before
`(part 2)`. That is a rule about the target hardware -- a business rule --
so it belongs here, and it is the single source of truth for BOTH the
use case and the ffmpeg adapter.

WHAT WENT WRONG IN SPRINT 11 (this module is the fix)
-----------------------------------------------------
Sprint 11 built an ffmpeg printf template in the *use case*
(`f"{stem} (part %02d){suffix}"`), handed naming authority to ffmpeg, and
then had the *adapter* reverse-engineer "what did I just produce?" by
scanning the directory from 1 upward until it hit a gap.

A directory scan cannot distinguish a file THIS RUN WROTE from a file that
was ALREADY THERE. Four defects fell out of that one sentence:

  * `--force` re-split reported stale parts from the previous split as
    freshly written (HIGH -- silent audio corruption on-device).
  * A failed segment's cleanup could delete a PREVIOUS successful split.
  * The no-clobber guard checked only `(part 01)`, so a series with its
    first part deleted was silently re-split on top of.
  * `str(template) % n` raised a raw TypeError for any title containing
    `%` -- e.g. `100% Focus.mp3`, which `download` will happily create.

So: naming lives here, `%` never touches a format string, and enumeration
is an explicit regex over the real directory contents -- never a
walk-until-gap, never a glob (Sprint 8b already learned that `[`/`*`/`?`
in titles break globs), never printf.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final

# The one place the `(part NN)` format is spelled. `\d+` (not `\d{2}`) so a
# series written at any pad width -- including by an older shokz -- is still
# found and cleaned up.
PART_PATTERN: Final[str] = r" \(part (\d+)\)"

# Never narrower than 2: `(part 1)` would sort after `(part 10)`.
_MIN_PAD_WIDTH: Final[int] = 2


def pad_width(total_parts: int) -> int:
    """Digits needed so all `total_parts` names sort into play order.

    Sprint 11 hard-coded 2. A 24-hour source at the minimum segment length
    yields tens of thousands of parts, where `(part 9999)` would sort after
    `(part 10000)`. Width follows the real count.
    """
    return max(_MIN_PAD_WIDTH, len(str(max(1, total_parts))))


def part_name(stem: str, index: int, suffix: str, width: int) -> str:
    """Name of part `index` (1-based) of a file `<stem><suffix>`.

    `stem` is inserted VERBATIM. It is never a format string and never a
    regex -- a stem of `100% Focus` or even `%02d %s` survives intact.
    """
    if index < 1:
        raise ValueError(f"part index is 1-based, got {index}")
    return f"{stem} (part {index:0{width}d}){suffix}"


def existing_parts(directory: Path, stem: str, suffix: str) -> list[Path]:
    """Every part file of `<stem><suffix>` already in `directory`, in play order.

    Sorted by the PARSED part number, not lexicographically, so a directory
    holding a mix of pad widths (`(part 01)` and `(part 002)`) still comes
    back in the right order.

    Finds series with HOLES -- if the user listened to part 01 and deleted
    it, parts 02.. are still reported. That is precisely what the
    walk-until-first-gap idiom got wrong.

    `stem` and `suffix` are regex-escaped, so titles containing `(`, `)`,
    `[`, `]`, `+`, `.` or `%` are matched literally.

    Returns `[]` for a missing directory -- the no-clobber guard runs before
    the output dir necessarily exists.
    """
    pattern = re.compile(
        f"^{re.escape(stem)}{PART_PATTERN}{re.escape(suffix)}$"
    )
    found: list[tuple[int, Path]] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                match = pattern.match(entry.name)
                if match is not None:
                    found.append((int(match.group(1)), Path(entry.path)))
    except (FileNotFoundError, NotADirectoryError):
        return []
    found.sort(key=lambda pair: pair[0])
    return [path for _, path in found]
