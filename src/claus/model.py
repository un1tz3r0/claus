"""Shared data model: listing blocks and entries, and their summary status."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    NA = "na"  # not a regular file: no summary
    CLASSIFYING = "classifying"
    EXCLUDED = "excluded"  # filtered out by rules / size
    CACHED = "cached"
    HELD = "held"  # waiting behind the confirmation gate
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"  # declined, --no-generate, claude missing, budget, ...
    WOULD = "would"  # --dry-run: would be generated

    @property
    def final(self) -> bool:
        return self in FINAL


FINAL = {Status.NA, Status.EXCLUDED, Status.CACHED, Status.DONE, Status.ERROR, Status.SKIPPED, Status.WOULD}


@dataclass(eq=False)
class Entry:
    name: str  # display name (as listed)
    path: str  # absolute path
    lst: os.stat_result  # lstat
    st: os.stat_result | None  # stat following symlinks (None: dangling)
    target: str | None = None  # symlink target text
    block: "Block | None" = None
    index: int = 0  # global row index
    cols: list[str] = field(default_factory=list)  # formatted ls -l columns
    status: Status = Status.NA
    summary: str | None = None
    error: str | None = None
    mime: str | None = None
    hash: str | None = None
    rule: str | None = None  # reason for the include/exclude decision
    event: str | None = None  # cache event recorded for this path

    @property
    def is_link(self) -> bool:
        return stat.S_ISLNK(self.lst.st_mode)

    @property
    def is_dir(self) -> bool:
        return stat.S_ISDIR(self.lst.st_mode)

    @property
    def is_regular(self) -> bool:
        """Regular file, or symlink to one (summarizable)."""
        return self.st is not None and stat.S_ISREG(self.st.st_mode)

    @property
    def display_path(self) -> str:
        """Path as the user would type it (block title + name)."""
        if self.block is not None and self.block.title and self.block.dirpath:
            p = os.path.join(self.block.title, self.name)
            return p[2:] if p.startswith("./") and len(p) > 2 else p
        return self.name


@dataclass(eq=False)
class Block:
    """One group of rows: either the file arguments, or one directory."""

    title: str | None  # header ("dir:") or None for no header
    dirpath: str | None  # absolute path of the directory; None for the file-args block
    entries: list[Entry] = field(default_factory=list)
    total_kib: int | None = None  # "total N" (None for the file-args block)
    widths: list[int] = field(default_factory=list)
    name_width: int = 0
    show_header: bool = False
    present: set[str] = field(default_factory=set)  # all names in the directory
    excluded: set[str] = field(default_factory=set)  # paths classified and excluded
    error: str | None = None  # e.g. permission denied while scanning
