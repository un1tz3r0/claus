"""Scanner → classifier → generation queue → renderer (DESIGN §7)."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import Executor
from dataclasses import dataclass, field
from typing import Protocol

from claus.cache import Cache
from claus.config import ConfigResolver, Effective
from claus.hashing import hash_file
from claus.lsfmt import layout_block
from claus.mime import MimeDetector
from claus.model import Block, Entry, Status
from claus.rules import Decision, decide
from claus.scan import ScanOptions, scan
from claus.summarizer import ClaudeMissing, Job, Summarizer, SummaryError

log = logging.getLogger("claus")


class Renderer(Protocol):
    def start(self) -> None: ...
    def add_block(self, block: Block) -> None: ...
    def update(self, entry: Entry) -> None: ...
    async def confirm(self, message: str) -> bool: ...
    def warn(self, message: str) -> None: ...
    def finish(self) -> None: ...


@dataclass
class RunOptions:
    scan: ScanOptions = field(default_factory=ScanOptions)
    human: bool = False
    time_style: str = "ls"
    dry_run: bool = False
    no_generate: bool = False
    yes: bool = False
    refresh: bool = False
    any_fingerprint: bool = False
    jobs: int = 4
    confirm_threshold: int = 25  # < 0: never ask
    interactive: bool = False  # stdin is a TTY: may ask for confirmation
    retention_days: float = 30.0
    classify_concurrency: int = 64


@dataclass(eq=False)
class PendingJob:
    job: Job
    entries: list[Entry]
    state: Status = Status.QUEUED


class Pipeline:
    def __init__(self, opts: RunOptions, resolver: ConfigResolver, cache: Cache,
                 detector: MimeDetector, summarizer: Summarizer, renderer: Renderer,
                 executor: Executor | None = None) -> None:
        self.opts = opts
        self.resolver = resolver
        self.cache = cache
        self.detector = detector
        self.summarizer = summarizer
        self.renderer = renderer
        self.executor = executor
        self.sem = asyncio.Semaphore(opts.classify_concurrency)
        self.queue: asyncio.Queue[PendingJob | None] = asyncio.Queue()
        self.inflight: dict[tuple[str, str], PendingJob] = {}
        self.held: list[PendingJob] = []
        self.admitted = 0
        self.index = 0
        self.serious: list[str] = []  # exit status 2
        self.minor = 0  # exit status 1
        self.generated = 0
        self.failed = 0
        self._fatal_warned = False

    # -- helpers ------------------------------------------------------------

    async def _effective(self, d: str) -> Effective:
        return await asyncio.to_thread(self.resolver.for_dir, d)

    async def _in_pool(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)

    def _set(self, e: Entry, status: Status, **kw) -> None:
        e.status = status
        for k, v in kw.items():
            setattr(e, k, v)
        self.renderer.update(e)

    # -- classification (DESIGN §5.2) -------------------------------------

    @staticmethod
    def _decide(eff: Effective, e: Entry, mime: str | None, known: bool) -> Decision:
        d = decide(eff.rules, e.name, e.path, mime, mime_known=known)
        # A symlink must also pass the rules under its real path (privacy rules).
        if e.is_link:
            real = os.path.realpath(e.path)
            if real != e.path:
                d2 = decide(eff.rules, os.path.basename(real), real, mime, mime_known=known)
                if d2.include is False:
                    return d2
                if d2.include is None:
                    return Decision(None)
        return d

    async def classify(self, e: Entry) -> None:
        async with self.sem:
            try:
                await self._classify(e)
            except FileNotFoundError:
                self._set(e, Status.SKIPPED, error="vanished")
            except OSError as ex:
                self.minor += 1
                self._set(e, Status.ERROR, error=ex.strerror or str(ex))
            except Exception as ex:  # never let one file kill the run
                log.exception("classifying %s", e.path)
                self.minor += 1
                self._set(e, Status.ERROR, error=f"{type(ex).__name__}: {ex}")

    async def _classify(self, e: Entry) -> None:
        assert e.st is not None and e.block is not None
        st = e.st
        eff = await self._effective(os.path.dirname(e.path))
        filt = eff.settings["filter"]
        if filt["skip_empty"] and st.st_size == 0:
            return self._exclude(e, "empty file")
        if st.st_size > eff.max_size:
            return self._exclude(e, f"larger than filter.max_size ({filt['max_size']})")

        row = await self.cache.lookup_path(e.path)
        fast = row is not None and row.matches(st)
        mime = row.mime if fast and row else None
        d = self._decide(eff, e, mime, mime is not None)
        if d.undetermined:
            mime = await self.detector.detect(e.path)
            # if detection failed, MIME rules simply do not match
            d = self._decide(eff, e, mime, True)
        e.mime = mime
        e.rule = d.reason()
        if not d.include:
            return self._exclude(e, e.rule)

        if fast and row is not None:
            h = row.hash
            if mime and row.mime is None and not self.opts.dry_run:
                await self.cache.set_mime(h, mime)
        else:
            h = await self._in_pool(hash_file, e.path)
            if not self.opts.dry_run:
                e.event = await self.cache.record_file(e.path, st, h, mime)
        e.hash = h

        srow = None
        if not self.opts.refresh:
            srow = await self.cache.get_summary(h, eff.fingerprint, self.opts.any_fingerprint)
        if srow is not None:
            self._set(e, Status.CACHED, summary=srow.summary)
        elif self.opts.dry_run:
            self._set(e, Status.WOULD)
        elif self.opts.no_generate:
            self._set(e, Status.SKIPPED, error="not cached")
        elif self.summarizer.fatal is not None:
            self._set(e, Status.SKIPPED, error=self.summarizer.fatal.message)
        else:
            self._submit(e, eff, h, st)

    def _exclude(self, e: Entry, reason: str) -> None:
        if e.block is not None and e.block.dirpath is not None:
            e.block.excluded.add(e.path)
        self._set(e, Status.EXCLUDED, rule=reason)

    # -- generation ---------------------------------------------------------

    def _submit(self, e: Entry, eff: Effective, h: str, st: os.stat_result) -> None:
        key = (h, eff.fingerprint)
        pending = self.inflight.get(key)
        if pending is not None:
            pending.entries.append(e)
            self._set(e, pending.state)
            return
        job = Job(path=e.path, name=e.name, mime=e.mime, size=st.st_size, hash=h,
                  fingerprint=eff.fingerprint, cfg=eff.summary, mtime_ns=st.st_mtime_ns)
        pending = PendingJob(job, [e])
        self.inflight[key] = pending
        threshold = self.opts.confirm_threshold
        if self.opts.yes or threshold < 0 or self.admitted < threshold:
            self.admitted += 1
            self._set(e, Status.QUEUED)
            self.queue.put_nowait(pending)
        else:
            pending.state = Status.HELD
            self._set(e, Status.HELD)
            self.held.append(pending)

    def _finish_job(self, pending: PendingJob, status: Status, **kw) -> None:
        pending.state = status
        self.inflight.pop((pending.job.hash, pending.job.fingerprint), None)
        for e in pending.entries:
            self._set(e, status, **kw)

    async def _worker(self) -> None:
        while True:
            pending = await self.queue.get()
            if pending is None:
                return
            await self._generate(pending)

    async def _generate(self, pending: PendingJob) -> None:
        pending.state = Status.RUNNING
        for e in pending.entries:
            self._set(e, Status.RUNNING)
        job = pending.job
        try:
            result = await self.summarizer.summarize(job)
        except SummaryError as ex:
            if ex.fatal:
                if not self._fatal_warned:
                    self._fatal_warned = True
                    if isinstance(ex, ClaudeMissing):
                        self.renderer.warn(f"{ex.message}; listing without new summaries")
                    else:
                        self.renderer.warn(f"summaries stopped: {ex.message}")
                    self.minor += 1
                self._finish_job(pending, Status.SKIPPED, error=ex.message)
            elif not ex.retryable and "changed during run" in ex.message:
                self._finish_job(pending, Status.SKIPPED, error=ex.message)
            else:
                self.failed += 1
                self.minor += 1
                self._finish_job(pending, Status.ERROR, error=ex.message)
            return
        except Exception as ex:
            log.exception("generating %s", job.path)
            self.failed += 1
            self.minor += 1
            self._finish_job(pending, Status.ERROR, error=f"{type(ex).__name__}: {ex}")
            return
        await self.cache.put_summary(job.hash, job.fingerprint, result.summary, result.model, result.cost_usd)
        self.generated += 1
        self._finish_job(pending, Status.DONE, summary=result.summary)

    async def _resolve_held(self) -> None:
        if not self.held:
            return
        n = len(self.held)
        if self.summarizer.fatal is not None:
            ok = False
        elif self.opts.interactive:
            ok = await self.renderer.confirm(
                f"{n} more summaries needed ({self.admitted} already started). Continue?"
            )
        else:
            self.renderer.warn(
                f"{n} more summaries needed beyond the confirmation threshold "
                f"({self.opts.confirm_threshold}); skipped (use -y to generate them)"
            )
            ok = False
        held, self.held = self.held, []
        for pending in held:
            if ok:
                pending.state = Status.QUEUED
                for e in pending.entries:
                    self._set(e, Status.QUEUED)
                self.queue.put_nowait(pending)
            else:
                self._finish_job(pending, Status.SKIPPED, error="declined")

    # -- orchestration -------------------------------------------------------

    async def _reconcile_after(self, block: Block, tasks: list[asyncio.Task]) -> None:
        if tasks:
            await asyncio.gather(*tasks)
        assert block.dirpath is not None
        await self.cache.reconcile_dir(block.dirpath, block.present, block.excluded)

    async def run(self, args: list[str]) -> int:
        generating = not (self.opts.dry_run or self.opts.no_generate)
        workers = [asyncio.create_task(self._worker()) for _ in range(max(1, self.opts.jobs))] if generating else []
        self.renderer.start()
        try:
            async with asyncio.TaskGroup() as tg:
                async for block in scan(args, self.opts.scan, self.serious):
                    if block.error:
                        self.minor += 1
                        self.renderer.warn(block.error)
                    layout_block(block, self.opts.human, self.opts.time_style)
                    for e in block.entries:
                        e.index = self.index
                        self.index += 1
                        e.status = Status.CLASSIFYING if e.is_regular else Status.NA
                    self.renderer.add_block(block)
                    tasks = [tg.create_task(self.classify(e)) for e in block.entries if e.is_regular]
                    if block.dirpath is not None and not block.error and not self.opts.dry_run:
                        tg.create_task(self._reconcile_after(block, tasks))
            await self._resolve_held()
            for _ in workers:
                self.queue.put_nowait(None)
            if workers:
                await asyncio.gather(*workers)
            if not self.opts.dry_run:
                try:
                    await self.cache.maybe_auto_purge(self.opts.retention_days)
                except Exception:  # best effort
                    log.debug("auto purge failed", exc_info=True)
        finally:
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            self.renderer.finish()
        for msg in self.serious:
            self.renderer.warn(msg)
        if self.serious:
            return 2
        return 1 if self.minor else 0
