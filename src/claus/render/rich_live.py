"""Live-updating terminal renderer built on ``rich`` (optional dependency).

Rows are printed permanently, in order, as soon as they and every row before them
are final. The rows after that point are shown in a live region at the bottom,
with placeholders and spinners for summaries in progress.
"""

from __future__ import annotations

import asyncio
import contextlib

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from claus.model import Block, Entry, Status
from claus.render.common import fit, header_lines, row_segments

STYLES = {
    "dir": "bold blue", "link": "bold cyan", "orphan": "bold red", "exec": "bold green",
    "other": "bold yellow", "summary": "dim", "pending": "dim", "error": "red",
}


class RichRenderer:
    def __init__(self, console: Console | None = None, human: bool = False,
                 dry_run: bool = False) -> None:
        self.console = console or Console(highlight=False, soft_wrap=False)
        self.human = human
        self.dry_run = dry_run
        self.items: list[Text | Entry] = []
        self.flushed = 0
        self.first_block = True
        self.live: Live | None = None
        self._ticker: asyncio.Task | None = None
        self._counts = {"done": 0, "todo": 0}

    # -- rendering ----------------------------------------------------------

    def _text(self, e: Entry, live: bool) -> Text:
        assert e.block is not None
        segs = fit(row_segments(e, e.block, self.dry_run, live=live), self.console.width)
        t = Text(no_wrap=True, overflow="ellipsis")
        for text, style in segs:
            t.append(text, STYLES.get(style or "", ""))
        t.rstrip()
        return t

    def _render_item(self, item: Text | Entry, live: bool) -> Text:
        return item if isinstance(item, Text) else self._text(item, live)

    def _renderable(self) -> Group:
        height = max(3, self.console.size.height - 2)
        rows = [self._render_item(i, True) for i in self.items[self.flushed:self.flushed + height]]
        running = sum(1 for i in self.items[self.flushed:]
                      if isinstance(i, Entry) and i.status is Status.RUNNING)
        footer = Text(
            f"claus: {self._counts['done']}/{self._counts['todo']} files done, {running} generating",
            style="dim",
        )
        return Group(*rows, footer)

    def _is_final(self, item: Text | Entry) -> bool:
        return isinstance(item, Text) or item.status.final

    def _flush(self) -> None:
        while self.flushed < len(self.items) and self._is_final(self.items[self.flushed]):
            self.console.print(self._render_item(self.items[self.flushed], False))
            self.flushed += 1

    async def _tick(self) -> None:
        while True:
            if self.live is not None:
                self.live.refresh()
            await asyncio.sleep(0.1)

    # -- Renderer protocol --------------------------------------------------

    def start(self) -> None:
        self.live = Live(console=self.console, auto_refresh=False, transient=True,
                         get_renderable=self._renderable, redirect_stdout=False,
                         redirect_stderr=False)
        self.live.start()
        self._ticker = asyncio.get_running_loop().create_task(self._tick())

    def add_block(self, block: Block) -> None:
        for line in header_lines(block, self.human, self.first_block):
            self.items.append(Text(line))
        self.first_block = False
        for e in block.entries:
            if e.is_regular:
                self._counts["todo"] += 1
            self.items.append(e)
        self._flush()

    def update(self, e: Entry) -> None:
        if e.is_regular and e.status.final:
            self._counts["done"] += 1
        self._flush()

    def warn(self, message: str) -> None:
        self.console.print(Text(f"claus: {message}", style="yellow"))

    async def confirm(self, message: str) -> bool:
        if self._ticker:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
        if self.live:
            self.live.stop()
        try:
            answer = await asyncio.to_thread(self.console.input, f"claus: {message} [y/N] ")
        except EOFError:
            answer = ""
        if self.live:
            self.live.start()
        self._ticker = asyncio.get_running_loop().create_task(self._tick())
        return answer.strip().lower() in ("y", "yes")

    def finish(self) -> None:
        if self._ticker:
            self._ticker.cancel()
        if self.live:
            self.live.stop()
            self.live = None
        while self.flushed < len(self.items):
            self.console.print(self._render_item(self.items[self.flushed], False))
            self.flushed += 1
