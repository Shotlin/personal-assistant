"""What a run really changed, read from the disk, not from what ZCode says it did.

A snapshot is (size, modified time, content hash) per file under the project, skipping the usual
dependency and build folders. Comparing two snapshots gives created, modified and deleted files.
That is then set against ZCode's own claims (its Write/Edit steps) and the differences are
reported, because a model's account of its work is a claim, not a result.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".next",
        "dist",
        "build",
        "target",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".DS_Store",
    }
)
_MAX_FILES = 20_000
_HASH_LIMIT = 2 * 1024 * 1024

Snapshot = dict[str, tuple[int, int, str]]


def snapshot(root: Path) -> tuple[Snapshot, bool]:
    """Every file under ``root`` as ``relative path -> (size, mtime_ns, hash)``.

    Returns ``(files, complete)``; ``complete`` is False if the folder was too large to walk
    fully, in which case nothing may be concluded about files that are not listed.
    """
    files: Snapshot = {}
    complete = True
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(names):
            if name in _SKIP_DIRS:
                continue
            if len(files) >= _MAX_FILES:
                return files, False
            path = Path(current) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            digest = ""
            if info.st_size <= _HASH_LIMIT and path.is_file() and not path.is_symlink():
                try:
                    digest = hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()
                except OSError:
                    digest = ""
            files[str(path.relative_to(root))] = (info.st_size, info.st_mtime_ns, digest)
    return files, complete


@dataclass
class DiskChange:
    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    complete: bool = True

    @property
    def changed(self) -> list[str]:
        return sorted({*self.created, *self.modified, *self.deleted})


def compare(before: Snapshot, after: Snapshot, *, complete: bool = True) -> DiskChange:
    change = DiskChange(complete=complete)
    for path, now in after.items():
        was = before.get(path)
        if was is None:
            change.created.append(path)
        elif was[2] and now[2]:
            if was[2] != now[2]:
                change.modified.append(path)
        elif was[:2] != now[:2]:  # too large to hash: size or time decides
            change.modified.append(path)
    change.deleted = [path for path in before if path not in after]
    for items in (change.created, change.modified, change.deleted):
        items.sort()
    return change


def reconcile(
    change: DiskChange, claimed: list[str], present: dict[str, int] | None = None
) -> list[str]:
    """Plain-words differences between what changed on disk and what ZCode's steps claimed.

    ``present`` maps every file now on disk (that matters) to its size. A file ZCode reported
    writing that exists but did not change was simply written again with the same content; one
    that does not exist is a real discrepancy.
    """
    notes: list[str] = []
    on_disk = set(change.changed)
    here = present or {}
    rewritten = [p for p in claimed if p not in on_disk and p in here]
    missing = [p for p in claimed if p not in on_disk and p not in here]
    if missing:
        notes.append(
            "ZCode reported editing "
            + ", ".join(missing[:6])
            + " but the file does not exist on disk."
        )
    if rewritten:
        notes.append(
            "ZCode wrote "
            + ", ".join(rewritten[:6])
            + " again with the same content it already had (the files exist and are fine)."
        )
    unexplained = [path for path in change.changed if path not in set(claimed)]
    if unexplained and change.complete:
        notes.append(
            "Changed on disk without a matching edit step (likely by a command): "
            + ", ".join(unexplained[:8])
            + "."
        )
    if not change.complete:
        notes.append("The folder was too large to check fully, so file changes may be missed.")
    return notes


def describe_present(paths: list[str], sizes: dict[str, int]) -> str:
    """ "On disk now: a (5,727 bytes), ...": proof the Deep Agent can read instead of guessing."""
    shown = [f"{p} ({sizes[p]:,} bytes)" for p in paths if p in sizes][:12]
    return "Verified on disk now: " + ", ".join(shown) + "." if shown else ""
