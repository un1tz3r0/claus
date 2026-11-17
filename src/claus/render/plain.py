"""Plain-text and JSON Lines renderers (no third-party dependencies)."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TextIO

from claus.model import Block, Entry, Status
from claus.render.common import ansi, entry_dict, fit, header_lines, row_segments


class _Base:
    def __init__(self, out: TextIO | None = None, err: TextIO | None = None,
                 stdin: TextIO | None = None, color: bool = False) -> None:
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.stdin = stdin or sys.stdin
        self.err_color = color and self.err.isatty()
        self.blocks: list[Block] = []

    def start(self) -> None:
        pass

    def warn(self, message: str) -> None:
        self._clear_progress()
        msg = f"claus: {message}"
        if self.err_color:
            msg = f"\x1b[33m{msg}\x1b[0m"
        print(msg, file=self.err, flush=True)

    def _clear_progress(self) -> None:
        pass

    async def confirm(self, message: str) -> bool:
        self._clear_progress()
        self.err.write(f"claus: {message} [y/N] ")
        self.err.flush()
        answer = await asyncio.to_thread(self.stdin.readline)
        return answer.strip().lower() in ("y", "yes")


class PlainRenderer(_Base):
    """Ordered output after completion, or ``stream`` rows as they become final.

    With ``progress`` (stdout is a TTY), a counter is shown on stderr meanwhile.
    """

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None,
                 stdin: TextIO | None = None, color: bool = False, width: int | None = None,
                 stream: bool = False, progress: bool = False, human: bool = False,
                 dry_run: bool = False) -> None:
        super().__init__(out, err, stdin, color)
        self.color = color
        self.width = width
        self.stream = stream
        self.progress = progress and self.err.isatty()
        self.human = human
        self.dry_run = dry_run
        self._printed: set[int] = set()
        self._todo = 0
        self._done = 0
        self._progress_shown = False

    def _line(self, e: Entry, block: Block) -> str:
        segs = row_segments(e, block, self.dry_run, live=False, path_names=self.stream)
        return ansi(fit(segs, self.width), self.color)

    def add_block(self, block: Block) -> None:
        self.blocks.append(block)
        for e in block.entries:
            if e.is_regular:
                self._todo += 1
            if self.stream and e.status.final:
                self._emit(e)
        self._show_progress()

    def _emit(self, e: Entry) -> None:
        if e.index in self._printed or e.block is None:
            return
        self._printed.add(e.index)
        self._clear_progress()
        print(self._line(e, e.block), file=self.out, flush=True)

    def update(self, e: Entry) -> None:
        if e.is_regular and e.status.final:
            self._done += 1
        if self.stream and e.status.final:
            self._emit(e)
        self._show_progress()

    def _show_progress(self) -> None:
        if not self.progress:
            return
        self.err.write(f"\rclaus: {self._done}/{self._todo} files classified or summarized ")
        self.err.flush()
        self._progress_shown = True

    def _clear_progress(self) -> None:
        if self._progress_shown:
            self.err.write("\r\x1b[K")
            self.err.flush()
            self._progress_shown = False

    def finish(self) -> None:
        self._clear_progress()
        self.progress = False
        if self.stream:
            for block in self.blocks:
                for e in block.entries:
                    self._emit(e)
            return
        first = True
        lines: list[str] = []
        for block in self.blocks:
            lines += header_lines(block, self.human, first)
            first = False
            lines += [self._line(e, block) for e in block.entries]
        if lines:
            self.out.write("\n".join(lines) + "\n")
            self.out.flush()


class JsonRenderer(_Base):
    """JSON Lines: one object per entry, in listing order unless ``stream``."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None,
                 stdin: TextIO | None = None, stream: bool = False) -> None:
        super().__init__(out, err, stdin)
        self.stream = stream
        self._printed: set[int] = set()

    def _emit(self, e: Entry) -> None:
        if e.index in self._printed:
            return
        self._printed.add(e.index)
        print(json.dumps(entry_dict(e), ensure_ascii=False), file=self.out, flush=self.stream)

    def add_block(self, block: Block) -> None:
        self.blocks.append(block)
        if self.stream:
            for e in block.entries:
                if e.status.final:
                    self._emit(e)

    def update(self, e: Entry) -> None:
        if self.stream and e.status.final:
            self._emit(e)

    def finish(self) -> None:
        for block in self.blocks:
            for e in block.entries:
                self._emit(e)
        self.out.flush()


__all__ = ["JsonRenderer", "PlainRenderer", "Status"]
