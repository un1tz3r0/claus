"""MIME type detection: libmagic bindings, or the ``file`` command as fallback."""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
from concurrent.futures import Executor
from typing import Any


class MimeDetector:
    backend = "none"

    async def detect(self, path: str) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


class LibmagicDetector(MimeDetector):
    """``python-magic`` (ctypes) or ``file-magic`` (official bindings), run in a thread pool.

    libmagic handles are not thread-safe, so each pool thread gets its own handle.
    """

    def __init__(self, magic_mod: Any, executor: Executor | None = None) -> None:
        self._magic = magic_mod
        self._executor = executor
        self._local = threading.local()
        if hasattr(magic_mod, "Magic"):
            self.backend = "python-magic"
        else:
            self.backend = "file-magic"

    def _detect_sync(self, path: str) -> str | None:
        real = os.path.realpath(path)
        try:
            if self.backend == "python-magic":
                m = getattr(self._local, "m", None)
                if m is None:
                    m = self._local.m = self._magic.Magic(mime=True)
                return m.from_file(real) or None
            return self._magic.detect_from_filename(real).mime_type or None
        except Exception:
            return None

    async def detect(self, path: str) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._detect_sync, path)


class FileCommandDetector(MimeDetector):
    """Runs ``file -b -L --mime-type -- p1 p2 ...``, batching concurrent requests."""

    backend = "file"

    def __init__(self, file_path: str = "file", batch_size: int = 64, delay: float = 0.005) -> None:
        self._file = file_path
        self._batch_size = batch_size
        self._delay = delay
        self._pending: list[tuple[str, asyncio.Future[str | None]]] = []
        self._handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task] = set()

    async def detect(self, path: str) -> str | None:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str | None] = loop.create_future()
        self._pending.append((path, fut))
        if len(self._pending) >= self._batch_size:
            self._flush()
        elif self._handle is None:
            self._handle = loop.call_later(self._delay, self._flush)
        return await fut

    def _flush(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        batch, self._pending = self._pending, []
        if batch:
            task = asyncio.get_running_loop().create_task(self._run(batch))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, batch: list[tuple[str, asyncio.Future[str | None]]]) -> None:
        lines: list[str] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                self._file, "-b", "-L", "--mime-type", "--", *(p for p, _ in batch),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            lines = out.decode(errors="replace").splitlines()
        except OSError:
            pass
        for i, (_, fut) in enumerate(batch):
            if fut.done():
                continue
            mime = lines[i].strip() if i < len(lines) else ""
            # error lines ("cannot open ...") contain no '/' type separator
            fut.set_result(mime if "/" in mime and " " not in mime else None)

    async def aclose(self) -> None:
        self._flush()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


class NullDetector(MimeDetector):
    backend = "none"

    async def detect(self, path: str) -> str | None:
        return None


def make_detector(executor: Executor | None = None, prefer: str = "auto") -> MimeDetector:
    """Pick the best available backend. ``prefer`` is auto | magic | file."""
    if prefer in ("auto", "magic"):
        try:
            import magic  # type: ignore[import-not-found]

            if hasattr(magic, "Magic") or hasattr(magic, "detect_from_filename"):
                return LibmagicDetector(magic, executor)
        except (ImportError, OSError):  # OSError: libmagic shared library missing
            pass
    file_cmd = shutil.which("file")
    if file_cmd:
        return FileCommandDetector(file_cmd)
    return NullDetector()
