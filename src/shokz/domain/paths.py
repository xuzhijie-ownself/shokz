"""Path-traversal guards -- pure domain."""

from __future__ import annotations

from pathlib import Path

from shokz.domain.errors import NameOutsideOutputDir


def is_path_within(child: Path, parent: Path) -> bool:
    """True iff child resolves under parent (no symlink escape)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def assert_within(child: Path, parent: Path) -> None:
    """Raise NameOutsideOutputDir if child does not resolve under parent."""
    if not is_path_within(child, parent):
        raise NameOutsideOutputDir(f"path {child} escapes the output directory {parent}")
