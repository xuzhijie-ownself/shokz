"""FilenameResolver -- pure logic for choosing the final MP3 path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from shokz.domain.errors import FilenameCollision, NameInvalid
from shokz.domain.filenames import (
    DEFAULT_TEMPLATE,
    fallback_stem,
    render_template,
    sanitize_filename,
)
from shokz.domain.models import Track
from shokz.domain.paths import assert_within

_DEFAULT_EXT: Final[str] = ".mp3"


@dataclass(frozen=True, slots=True)
class FilenameResolver:
    """Pure resolver -- given track + (override?) + exists fn -> final Path."""

    output_dir: Path
    template: str = DEFAULT_TEMPLATE

    def resolve(
        self,
        track: Track,
        *,
        name_override: str | None,
        exists: Callable[[Path], bool],
    ) -> Path:
        if name_override is not None:
            stem = sanitize_filename(name_override)
            if not stem:
                # F2 (silent-failure fix): user-input failure is a validation
                # error, NOT a security/traversal one. NameInvalid maps cleanly
                # to CLI exit 2 (invalid invocation).
                raise NameInvalid(
                    f"--name {name_override!r} sanitizes to empty after FAT-safety; "
                    f"pick a name with at least one alphanumeric character"
                )
        else:
            rendered = render_template(track, self.template)
            stem = sanitize_filename(rendered) or fallback_stem(track)

        candidate = self.output_dir / f"{stem}{_DEFAULT_EXT}"
        assert_within(candidate, self.output_dir)
        if not exists(candidate):
            return candidate

        n = 2
        while True:
            candidate = self.output_dir / f"{stem} ({n}){_DEFAULT_EXT}"
            assert_within(candidate, self.output_dir)
            if not exists(candidate):
                return candidate
            n += 1
            if n > 9999:
                raise FilenameCollision(
                    f"exhausted {n - 1} suffix attempts for stem {stem!r} in {self.output_dir} "
                    "(this is almost certainly a bug or runaway state -- "
                    "clean ./downloads/ and retry)"
                )
