"""Persistent SQLite cache (DESIGN §5).

All access goes through one dedicated thread that owns the single sqlite3
connection (the "DB actor"). Async callers submit functions and await futures.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TypeVar

T = TypeVar("T")

SCHEMA_VERSION = 1
DAY = 86400.0

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS contents (
  hash        TEXT PRIMARY KEY,
  size        INTEGER NOT NULL,
  mime        TEXT,
  first_seen  REAL NOT NULL,
  orphaned_at REAL
);
CREATE TABLE IF NOT EXISTS summaries (
  hash        TEXT NOT NULL REFERENCES contents(hash) ON DELETE CASCADE,
  fingerprint TEXT NOT NULL,
  summary     TEXT NOT NULL,
  model       TEXT,
  cost_usd    REAL,
  created_at  REAL NOT NULL,
  PRIMARY KEY (hash, fingerprint)
);
CREATE TABLE IF NOT EXISTS paths (
  path      TEXT PRIMARY KEY,
  parent    TEXT NOT NULL,
  dev INTEGER, ino INTEGER, size INTEGER, mtime_ns INTEGER,
  hash      TEXT REFERENCES contents(hash),
  last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS paths_parent ON paths(parent);
CREATE INDEX IF NOT EXISTS paths_hash   ON paths(hash);
CREATE TABLE IF NOT EXISTS events (
  ts REAL NOT NULL, kind TEXT NOT NULL,
  path TEXT NOT NULL, other_path TEXT, hash TEXT
);
CREATE INDEX IF NOT EXISTS events_path ON events(path);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""


@dataclass
class PathRow:
    path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int
    hash: str
    mime: str | None

    def matches(self, st: os.stat_result) -> bool:
        return (self.dev, self.ino, self.size, self.mtime_ns) == (
            st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
        )


@dataclass
class SummaryRow:
    hash: str
    fingerprint: str
    summary: str
    model: str | None
    cost_usd: float | None
    created_at: float


@dataclass
class GcStats:
    paths_checked: int = 0
    paths_removed: int = 0
    contents_purged: int = 0
    summaries_purged: int = 0
    events_purged: int = 0


class Cache:
    """Async façade over a single-threaded sqlite3 connection."""

    def __init__(self, path: str, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="claus-db")
        self._conn: sqlite3.Connection | None = None

    # -- actor plumbing -------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        with _tx(conn):
            for stmt in SCHEMA_V1.split(";"):
                if stmt.strip():
                    conn.execute(stmt)
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
                )
            elif int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"cache {self.path} has schema v{row[0]}, newer than this claus (v{SCHEMA_VERSION})"
                )
        return conn

    def _call(self, fn: Callable[..., T], *args: Any) -> T:
        if self._conn is None:
            self._conn = self._open()
        return fn(self._conn, *args)

    async def run(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._call, fn, *args)

    def run_sync(self, fn: Callable[..., T], *args: Any) -> T:
        return self._executor.submit(self._call, fn, *args).result()

    async def open(self) -> None:
        await self.run(lambda conn: None)

    async def aclose(self) -> None:
        def _close(conn: sqlite3.Connection) -> None:
            conn.execute("PRAGMA optimize")
            conn.close()

        if self._conn is not None:
            await self.run(_close)
            self._conn = None
        self._executor.shutdown(wait=True)

    # -- public API (async) ---------------------------------------------------

    async def lookup_path(self, path: str) -> PathRow | None:
        return await self.run(_lookup_path, path)

    async def record_file(
        self, path: str, st: os.stat_result, hash_: str, mime: str | None
    ) -> str | None:
        return await self.run(_record_file, path, st, hash_, mime, self.clock())

    async def set_mime(self, hash_: str, mime: str) -> None:
        await self.run(_set_mime, hash_, mime)

    async def get_summary(self, hash_: str, fingerprint: str, any_fingerprint: bool = False) -> SummaryRow | None:
        return await self.run(_get_summary, hash_, fingerprint, any_fingerprint)

    async def put_summary(
        self, hash_: str, fingerprint: str, summary: str, model: str | None, cost: float | None
    ) -> None:
        await self.run(_put_summary, hash_, fingerprint, summary, model, cost, self.clock())

    async def reconcile_dir(self, parent: str, present: set[str], excluded: set[str]) -> int:
        return await self.run(_reconcile_dir, parent, present, excluded, self.clock())

    async def gc(self, retention_days: float, check_paths: bool = True, dry_run: bool = False) -> GcStats:
        return await self.run(_gc, self.clock(), retention_days, check_paths, dry_run)

    async def maybe_auto_purge(self, retention_days: float) -> GcStats | None:
        return await self.run(_maybe_auto_purge, self.clock(), retention_days)

    async def info(self, path: str, hash_: str | None = None) -> dict:
        return await self.run(_info, path, hash_)

    async def stats(self) -> dict:
        return await self.run(_stats)


# -- synchronous implementations (run on the DB thread) ------------------------


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _lookup_path(conn: sqlite3.Connection, path: str) -> PathRow | None:
    r = conn.execute(
        "SELECT p.path, p.dev, p.ino, p.size, p.mtime_ns, p.hash, c.mime "
        "FROM paths p LEFT JOIN contents c ON c.hash = p.hash WHERE p.path = ?",
        (path,),
    ).fetchone()
    return PathRow(*r) if r else None


def _log(conn: sqlite3.Connection, ts: float, kind: str, path: str, other: str | None, h: str | None) -> None:
    conn.execute(
        "INSERT INTO events(ts, kind, path, other_path, hash) VALUES (?, ?, ?, ?, ?)",
        (ts, kind, path, other, h),
    )


def _orphan_check(conn: sqlite3.Connection, hashes: set[str], now: float) -> None:
    for h in hashes:
        if h and conn.execute("SELECT 1 FROM paths WHERE hash = ? LIMIT 1", (h,)).fetchone() is None:
            conn.execute(
                "UPDATE contents SET orphaned_at = ? WHERE hash = ? AND orphaned_at IS NULL", (now, h)
            )


def _record_file(
    conn: sqlite3.Connection, path: str, st: os.stat_result, h: str, mime: str | None, now: float
) -> str | None:
    """Upsert a path → hash mapping. Returns the event kind recorded (or None)."""
    with _tx(conn):
        conn.execute(
            "INSERT INTO contents(hash, size, mime, first_seen, orphaned_at) VALUES (?, ?, ?, ?, NULL) "
            "ON CONFLICT(hash) DO UPDATE SET orphaned_at = NULL, mime = COALESCE(excluded.mime, mime)",
            (h, st.st_size, mime, now),
        )
        old = conn.execute("SELECT hash FROM paths WHERE path = ?", (path,)).fetchone()
        old_hash = old[0] if old else None
        others = [r[0] for r in conn.execute(
            "SELECT path FROM paths WHERE hash = ? AND path != ?", (h, path)
        )]
        stale = [o for o in others if not os.path.lexists(o)]
        live = [o for o in others if o not in stale]
        for o in stale:
            conn.execute("DELETE FROM paths WHERE path = ?", (o,))
        conn.execute(
            "INSERT INTO paths(path, parent, dev, ino, size, mtime_ns, hash, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET "
            "dev=excluded.dev, ino=excluded.ino, size=excluded.size, mtime_ns=excluded.mtime_ns, "
            "hash=excluded.hash, last_seen=excluded.last_seen",
            (path, os.path.dirname(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, h, now),
        )
        kind: str | None
        if old_hash == h:
            kind = None  # metadata-only change (touch)
        elif old_hash is not None:
            kind = "changed"
            _log(conn, now, kind, path, None, h)
            _orphan_check(conn, {old_hash}, now)
        elif stale:
            kind = "moved"
            for o in stale:
                _log(conn, now, kind, path, o, h)
        elif live:
            kind = "copied"
            _log(conn, now, kind, path, live[0], h)
        else:
            kind = "new"
            _log(conn, now, kind, path, None, h)
        return kind


def _set_mime(conn: sqlite3.Connection, h: str, mime: str) -> None:
    conn.execute("UPDATE contents SET mime = ? WHERE hash = ?", (mime, h))


def _get_summary(conn: sqlite3.Connection, h: str, fp: str, any_fp: bool) -> SummaryRow | None:
    r = conn.execute(
        "SELECT hash, fingerprint, summary, model, cost_usd, created_at FROM summaries "
        "WHERE hash = ? AND fingerprint = ?",
        (h, fp),
    ).fetchone()
    if r is None and any_fp:
        r = conn.execute(
            "SELECT hash, fingerprint, summary, model, cost_usd, created_at FROM summaries "
            "WHERE hash = ? ORDER BY created_at DESC LIMIT 1",
            (h,),
        ).fetchone()
    return SummaryRow(*r) if r else None


def _put_summary(
    conn: sqlite3.Connection, h: str, fp: str, summary: str, model: str | None, cost: float | None, now: float
) -> None:
    with _tx(conn):
        # The content row may have been purged by a concurrent gc; recreate minimally.
        conn.execute(
            "INSERT OR IGNORE INTO contents(hash, size, mime, first_seen) VALUES (?, 0, NULL, ?)", (h, now)
        )
        conn.execute(
            "INSERT INTO summaries(hash, fingerprint, summary, model, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(hash, fingerprint) DO UPDATE SET "
            "summary=excluded.summary, model=excluded.model, cost_usd=excluded.cost_usd, "
            "created_at=excluded.created_at",
            (h, fp, summary, model, cost, now),
        )
def _reconcile_dir(
    conn: sqlite3.Connection, parent: str, present: set[str], excluded: set[str], now: float
) -> int:
    """Forget paths under ``parent`` that are gone or now excluded by the rules.

    ``present``: every base name currently in the directory (hidden ones too).
    ``excluded``: absolute paths that this scan classified as excluded.
    Rows for present files that were not classified (e.g. hidden files listed
    without ``-a``) are left alone. Returns the number of removed files.
    """
    removed = 0
    with _tx(conn):
        rows = conn.execute("SELECT path, hash FROM paths WHERE parent = ?", (parent,)).fetchall()
        hashes: set[str] = set()
        for path, h in rows:
            if os.path.basename(path) not in present:
                _log(conn, now, "removed", path, None, h)
                removed += 1
            elif path not in excluded:
                continue
            conn.execute("DELETE FROM paths WHERE path = ?", (path,))
            hashes.add(h)
        _orphan_check(conn, hashes, now)
    return removed




def _purge(conn: sqlite3.Connection, now: float, retention_days: float, stats: GcStats, dry_run: bool) -> None:
    cutoff = now - retention_days * DAY
    stats.contents_purged = conn.execute(
        "SELECT COUNT(*) FROM contents WHERE orphaned_at IS NOT NULL AND orphaned_at < ?", (cutoff,)
    ).fetchone()[0]
    stats.summaries_purged = conn.execute(
        "SELECT COUNT(*) FROM summaries s JOIN contents c ON c.hash = s.hash "
        "WHERE c.orphaned_at IS NOT NULL AND c.orphaned_at < ?",
        (cutoff,),
    ).fetchone()[0]
    ev_cutoff = now - retention_days * 4 * DAY
    stats.events_purged = conn.execute("SELECT COUNT(*) FROM events WHERE ts < ?", (ev_cutoff,)).fetchone()[0]
    if not dry_run:
        conn.execute("DELETE FROM contents WHERE orphaned_at IS NOT NULL AND orphaned_at < ?", (cutoff,))
        conn.execute("DELETE FROM events WHERE ts < ?", (ev_cutoff,))


def _gc(conn: sqlite3.Connection, now: float, retention_days: float, check_paths: bool, dry_run: bool) -> GcStats:
    stats = GcStats()
    with _tx(conn):
        if check_paths:
            rows = conn.execute("SELECT path, hash FROM paths").fetchall()
            stats.paths_checked = len(rows)
            hashes = set()
            for path, h in rows:
                if not os.path.lexists(path):
                    stats.paths_removed += 1
                    if not dry_run:
                        _log(conn, now, "removed", path, None, h)
                        conn.execute("DELETE FROM paths WHERE path = ?", (path,))
                        hashes.add(h)
            _orphan_check(conn, hashes, now)
        _purge(conn, now, retention_days, stats, dry_run)
        if not dry_run:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('last_purge', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(now),),
            )
    return stats


def _maybe_auto_purge(conn: sqlite3.Connection, now: float, retention_days: float) -> GcStats | None:
    r = conn.execute("SELECT value FROM meta WHERE key = 'last_purge'").fetchone()
    if r is not None and now - float(r[0]) < DAY:
        return None
    return _gc(conn, now, retention_days, False, False)


def vacuum(conn: sqlite3.Connection) -> None:
    conn.execute("VACUUM")


def _info(conn: sqlite3.Connection, path: str, h: str | None) -> dict:
    out: dict[str, Any] = {"path": path}
    prow = conn.execute("SELECT * FROM paths WHERE path = ?", (path,)).fetchone()
    if prow is not None:
        out["path_row"] = dict(prow)
        h = h or prow["hash"]
    out["hash"] = h
    if h:
        c = conn.execute("SELECT * FROM contents WHERE hash = ?", (h,)).fetchone()
        out["content"] = dict(c) if c else None
        out["other_paths"] = [r[0] for r in conn.execute(
            "SELECT path FROM paths WHERE hash = ? AND path != ? ORDER BY path", (h, path)
        )]
        out["summaries"] = [dict(r) for r in conn.execute(
            "SELECT fingerprint, summary, model, cost_usd, created_at FROM summaries "
            "WHERE hash = ? ORDER BY created_at DESC", (h,)
        )]
    out["events"] = [dict(r) for r in conn.execute(
        "SELECT ts, kind, path, other_path, hash FROM events WHERE path = ? OR other_path = ? "
        "ORDER BY ts DESC LIMIT 50", (path, path)
    )]
    return out


def _stats(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "paths": q("SELECT COUNT(*) FROM paths"),
        "contents": q("SELECT COUNT(*) FROM contents"),
        "orphaned": q("SELECT COUNT(*) FROM contents WHERE orphaned_at IS NOT NULL"),
        "summaries": q("SELECT COUNT(*) FROM summaries"),
        "events": q("SELECT COUNT(*) FROM events"),
        "total_cost_usd": q("SELECT COALESCE(SUM(cost_usd), 0) FROM summaries"),
    }
