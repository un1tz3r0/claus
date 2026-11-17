import os
import shutil

import pytest

from claus.cache import DAY, Cache
from claus.hashing import hash_file


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
async def cache(tmp_path):
    clock = Clock()
    c = Cache(str(tmp_path / "c.sqlite"), clock=clock)
    c.test_clock = clock  # type: ignore[attr-defined]
    yield c
    await c.aclose()


async def record(cache, path):
    st = os.stat(path)
    return await cache.record_file(str(path), st, hash_file(str(path)), "text/plain")


async def test_new_unchanged_changed(cache, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("one")
    assert await record(cache, f) == "new"
    row = await cache.lookup_path(str(f))
    assert row is not None and row.matches(os.stat(f)) and row.mime == "text/plain"
    assert await record(cache, f) is None  # same content
    f.write_text("two!")
    assert not row.matches(os.stat(f))
    assert await record(cache, f) == "changed"


async def test_copied_and_moved(cache, tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("same")
    await record(cache, a)
    b = tmp_path / "b.txt"
    shutil.copy(a, b)
    assert await record(cache, b) == "copied"
    c = tmp_path / "c.txt"
    os.rename(a, c)
    assert await record(cache, c) == "moved"
    info = await cache.info(str(c))
    assert info["events"][0]["kind"] == "moved"
    assert info["events"][0]["other_path"] == str(a)
    assert await cache.lookup_path(str(a)) is None


async def test_summary_roundtrip_and_any_fingerprint(cache, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    await record(cache, f)
    h = hash_file(str(f))
    await cache.put_summary(h, "fp1", "a summary", "m", 0.001)
    assert (await cache.get_summary(h, "fp1")).summary == "a summary"
    assert await cache.get_summary(h, "fp2") is None
    assert (await cache.get_summary(h, "fp2", any_fingerprint=True)).summary == "a summary"


async def test_reconcile_orphan_and_purge(cache, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("bye")
    await record(cache, f)
    h = hash_file(str(f))
    await cache.put_summary(h, "fp", "s", None, None)
    hidden = tmp_path / ".hidden"
    hidden.write_text("keep me")
    await record(cache, hidden)
    f.unlink()
    removed = await cache.reconcile_dir(str(tmp_path), {".hidden"}, set())
    assert removed == 1
    assert await cache.lookup_path(str(hidden)) is not None  # present, not classified: kept
    info = await cache.info(str(f), h)
    assert info["content"]["orphaned_at"] is not None
    # still within retention: kept
    stats = await cache.gc(retention_days=30)
    assert stats.contents_purged == 0
    cache.test_clock.t += 31 * DAY
    stats = await cache.gc(retention_days=30, dry_run=True)
    assert stats.contents_purged == 1 and stats.summaries_purged == 1
    assert (await cache.get_summary(h, "fp")) is not None
    stats = await cache.gc(retention_days=30)
    assert stats.contents_purged == 1
    assert (await cache.get_summary(h, "fp")) is None


async def test_reconcile_drops_newly_excluded_silently(cache, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    await record(cache, f)
    assert await cache.reconcile_dir(str(tmp_path), {"a.txt"}, {str(f)}) == 0
    assert await cache.lookup_path(str(f)) is None
    info = await cache.info(str(f))
    assert [e["kind"] for e in info["events"]] == ["new"]


async def test_gc_checks_paths(cache, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    await record(cache, f)
    f.unlink()
    stats = await cache.gc(retention_days=30)
    assert stats.paths_checked == 1 and stats.paths_removed == 1


async def test_auto_purge_once_a_day(cache):
    assert await cache.maybe_auto_purge(30) is not None
    assert await cache.maybe_auto_purge(30) is None
    cache.test_clock.t += DAY + 1
    assert await cache.maybe_auto_purge(30) is not None
