"""Directory scanning (``scandir`` in a thread pool) producing listing blocks."""

from __future__ import annotations

import asyncio
import locale
import os
import stat
from dataclasses import dataclass, field
from typing import AsyncIterator

from claus.model import Block, Entry


@dataclass
class ScanOptions:
    all_mode: str = "none"  # none | almost (-A) | all (-a)
    recursive: bool = False
    depth: int | None = None  # None = unlimited (when recursive)
    sort: str = "name"  # name | time | size
    reverse: bool = False
    skip_dirs: list[str] = field(default_factory=list)


class ScanError(Exception):
    pass


def _make_entry(name: str, path: str) -> Entry:
    lst = os.lstat(path)
    st: os.stat_result | None = lst
    target = None
    if stat.S_ISLNK(lst.st_mode):
        try:
            target = os.readlink(path)
        except OSError:
            target = "?"
        try:
            st = os.stat(path)
        except OSError:
            st = None
    return Entry(name=name, path=path, lst=lst, st=st, target=target)


def _sort_key_name(name: str):
    try:
        return locale.strxfrm(name)
    except (ValueError, OSError):
        return name


def sort_entries(entries: list[Entry], opts: ScanOptions) -> list[Entry]:
    entries.sort(key=lambda e: _sort_key_name(e.name))
    if opts.sort == "time":
        entries.sort(key=lambda e: e.lst.st_mtime_ns, reverse=True)
    elif opts.sort == "size":
        entries.sort(key=lambda e: e.lst.st_size, reverse=True)
    if opts.reverse:
        entries.reverse()
    return entries


def _hidden_ok(name: str, opts: ScanOptions) -> bool:
    return not name.startswith(".") or opts.all_mode != "none"


def scan_dir_sync(dirpath: str, opts: ScanOptions) -> tuple[list[Entry], set[str]]:
    """List one directory. Returns ``(displayed entries, all names present)``."""
    entries: list[Entry] = []
    present: set[str] = set()
    with os.scandir(dirpath) as it:
        for de in it:
            present.add(de.name)
            if not _hidden_ok(de.name, opts):
                continue
            try:
                entries.append(_make_entry(de.name, os.path.join(dirpath, de.name)))
            except FileNotFoundError:
                continue  # vanished between scandir and lstat
    if opts.all_mode == "all":
        for dot in (".", ".."):
            entries.append(_make_entry(dot, os.path.normpath(os.path.join(dirpath, dot))))
            entries[-1].name = dot
    return sort_entries(entries, opts), present


async def scan(args: list[str], opts: ScanOptions, errors: list[str]) -> AsyncIterator[Block]:
    """Yield blocks in ``ls`` order: file arguments first, then directories (DFS for -R).

    Problems with arguments are appended to ``errors`` (serious: exit status 2);
    unreadable subdirectories produce a block with ``error`` set (minor).
    """
    files: list[Entry] = []
    dirs: list[tuple[str, str]] = []  # (title, abspath)
    for arg in args:
        absp = os.path.abspath(arg)
        try:
            e = await asyncio.to_thread(_make_entry, arg, absp)
        except OSError as ex:
            errors.append(f"cannot access '{arg}': {ex.strerror}")
            continue
        is_dir = e.is_dir or (e.is_link and e.st is not None and stat.S_ISDIR(e.st.st_mode)
                              and arg.endswith("/"))
        if is_dir:
            dirs.append((arg, absp))
        else:
            files.append(e)

    show_headers = opts.recursive or (len(files) + len(dirs)) > 1
    if files:
        block = Block(title=None, dirpath=None, entries=sort_entries(files, opts))
        for e in block.entries:
            e.block = block
        yield block

    sorted_dirs = sorted(dirs, key=lambda d: _sort_key_name(d[0]))
    if opts.reverse:
        sorted_dirs.reverse()
    for title, absp in sorted_dirs:
        async for block in _scan_tree(title, absp, 0, opts, show_headers):
            yield block


async def _scan_tree(title: str, absp: str, depth: int, opts: ScanOptions, show_header: bool
                     ) -> AsyncIterator[Block]:
    block = Block(title=title, dirpath=absp, show_header=show_header)
    try:
        entries, present = await asyncio.to_thread(scan_dir_sync, absp, opts)
    except OSError as ex:
        block.error = f"cannot open directory '{title}': {ex.strerror}"
        yield block
        return
    block.entries, block.present = entries, present
    for e in entries:
        e.block = block
    yield block

    if not opts.recursive or (opts.depth is not None and depth >= opts.depth):
        return
    for e in entries:
        if e.name in (".", "..") or not e.is_dir or e.name in opts.skip_dirs:
            continue
        async for sub in _scan_tree(os.path.join(title, e.name), e.path, depth + 1, opts, True):
            yield sub
