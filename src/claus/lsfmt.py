"""``ls -l`` column formatting."""

from __future__ import annotations

import grp
import math
import os
import pwd
import stat
import time
import unicodedata
from functools import lru_cache

from claus.model import Block, Entry

SIX_MONTHS = 182.5 * 86400


@lru_cache(maxsize=None)
def owner_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


@lru_cache(maxsize=None)
def group_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def human_size(n: int) -> str:
    """Like ``ls -h``: powers of 1024, one decimal below 10, rounded up."""
    if n < 1024:
        return str(n)
    value = float(n)
    for unit in "KMGTPE":
        value /= 1024.0
        if value < 10:
            v = math.ceil(value * 10) / 10
            if v < 10:
                return f"{v:.1f}{unit}"
        v2 = math.ceil(value)
        if v2 < 1024:
            return f"{v2}{unit}"
    return f"{math.ceil(value)}E"


def size_col(st: os.stat_result, human: bool) -> str:
    if stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode):
        return f"{os.major(st.st_rdev)}, {os.minor(st.st_rdev)}"
    return human_size(st.st_size) if human else str(st.st_size)


def time_col(mtime: float, style: str = "ls", now: float | None = None) -> str:
    now = time.time() if now is None else now
    lt = time.localtime(mtime)
    if style == "iso":
        return time.strftime("%Y-%m-%d %H:%M", lt)
    if style == "relative":
        d = now - mtime
        if d < 0:
            return "future"
        for limit, div, unit in ((60, 1, "s"), (3600, 60, "m"), (86400, 3600, "h"),
                                 (86400 * 30, 86400, "d"), (86400 * 365, 86400 * 30, "mo")):
            if d < limit:
                return f"{int(d // div)}{unit} ago"
        return f"{int(d // (86400 * 365))}y ago"
    if now - SIX_MONTHS < mtime <= now + 3600:
        return time.strftime("%b %e %H:%M", lt)
    return time.strftime("%b %e  %Y", lt)


def safe_name(name: str) -> str:
    """Replace control characters (e.g. newlines) like ``ls -q`` on a terminal."""
    if name.isprintable():
        return name
    return "".join(ch if ch.isprintable() or ch == " " else "?" for ch in name)


def display_width(s: str) -> int:
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def entry_cols(e: Entry, human: bool, time_style: str, now: float) -> list[str]:
    st = e.lst
    return [
        stat.filemode(st.st_mode),
        str(st.st_nlink),
        owner_name(st.st_uid),
        group_name(st.st_gid),
        size_col(st, human),
        time_col(st.st_mtime, time_style, now),
    ]


def name_text(e: Entry) -> str:
    s = safe_name(e.name)
    if e.target is not None:
        s += " -> " + safe_name(e.target)
    return s


def layout_block(block: Block, human: bool, time_style: str, now: float | None = None,
                 max_name_width: int = 40) -> None:
    """Fill each entry's ``cols`` and the block's column widths."""
    now = time.time() if now is None else now
    for e in block.entries:
        e.cols = entry_cols(e, human, time_style, now)
    ncols = 6
    block.widths = [max((len(e.cols[i]) for e in block.entries), default=0) for i in range(ncols)]
    block.name_width = min(max((display_width(name_text(e)) for e in block.entries), default=0),
                           max_name_width)
    if block.dirpath is not None:
        kib = sum(getattr(e.lst, "st_blocks", 0) for e in block.entries) // 2
        block.total_kib = kib


def total_line(block: Block, human: bool) -> str:
    kib = block.total_kib or 0
    return f"total {human_size(kib * 1024) if human else kib}"


RIGHT_ALIGNED = {1, 4}  # nlink, size


def cols_text(e: Entry, widths: list[int]) -> str:
    parts = []
    for i, (c, w) in enumerate(zip(e.cols, widths)):
        parts.append(c.rjust(w) if i in RIGHT_ALIGNED else c.ljust(w))
    return " ".join(parts)


def name_kind(e: Entry) -> str:
    """Coarse kind used for coloring: dir | link | exec | file | other."""
    m = e.lst.st_mode
    if stat.S_ISDIR(m):
        return "dir"
    if stat.S_ISLNK(m):
        return "link" if e.st is not None else "orphan"
    if stat.S_ISREG(m):
        return "exec" if m & 0o111 else "file"
    return "other"


def type_name(st: os.stat_result) -> str:
    m = st.st_mode
    for test, name in ((stat.S_ISREG, "file"), (stat.S_ISDIR, "dir"), (stat.S_ISLNK, "symlink"),
                       (stat.S_ISFIFO, "fifo"), (stat.S_ISSOCK, "socket"), (stat.S_ISCHR, "char"),
                       (stat.S_ISBLK, "block")):
        if test(m):
            return name
    return "other"
