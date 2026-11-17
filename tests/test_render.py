import io
import os

import pytest

from claus.lsfmt import layout_block
from claus.model import Block, Entry, Status
from claus.render.plain import JsonRenderer, PlainRenderer


def make_block(tmp_path, names):
    block = Block(title=str(tmp_path), dirpath=str(tmp_path), show_header=True)
    for i, n in enumerate(names):
        p = tmp_path / n
        p.write_text(n)
        st = os.lstat(p)
        e = Entry(name=n, path=str(p), lst=st, st=st, block=block, index=i,
                  status=Status.CLASSIFYING)
        block.entries.append(e)
    layout_block(block, human=False, time_style="iso")
    return block


def finish_all(renderer, block):
    for i, e in enumerate(reversed(block.entries)):  # complete out of order
        e.status, e.summary = Status.DONE, f"summary {e.name}"
        renderer.update(e)


def test_plain_ordered_output(tmp_path):
    out = io.StringIO()
    r = PlainRenderer(out=out, err=io.StringIO())
    block = make_block(tmp_path, ["a.txt", "bb.txt"])
    r.start()
    r.add_block(block)
    finish_all(r, block)
    r.finish()
    lines = out.getvalue().splitlines()
    assert lines[0].endswith(":") and lines[1].startswith("total")
    assert lines[2].endswith("a.txt   summary a.txt")  # names padded to a column
    assert lines[3].endswith("bb.txt  summary bb.txt")


def test_plain_width_truncation(tmp_path):
    out = io.StringIO()
    r = PlainRenderer(out=out, err=io.StringIO(), width=60)
    block = make_block(tmp_path, ["a.txt"])
    r.add_block(block)
    block.entries[0].status, block.entries[0].summary = Status.DONE, "x" * 200
    r.update(block.entries[0])
    r.finish()
    row = out.getvalue().splitlines()[-1]
    assert len(row) == 60 and row.endswith("…")


def test_json_stream_order_follows_completion(tmp_path):
    out = io.StringIO()
    r = JsonRenderer(out=out, err=io.StringIO(), stream=True)
    block = make_block(tmp_path, ["a.txt", "b.txt"])
    r.add_block(block)
    finish_all(r, block)
    r.finish()
    names = [line.split('"name": "')[1].split('"')[0] for line in out.getvalue().splitlines()]
    assert names == ["b.txt", "a.txt"]


async def test_rich_renderer_prints_each_row_once_in_order(tmp_path):
    pytest.importorskip("rich")
    from rich.console import Console

    from claus.render.rich_live import RichRenderer

    buf = io.StringIO()
    console = Console(file=buf, width=100, height=10, force_terminal=False, color_system=None)
    r = RichRenderer(console)
    block = make_block(tmp_path, ["a.txt", "b.txt", "c.txt"])
    r.start()
    r.add_block(block)
    finish_all(r, block)
    r.finish()
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    rows = [ln for ln in lines if "summary" in ln]
    assert [ln.split()[-1] for ln in rows] == ["a.txt", "b.txt", "c.txt"]
    assert not any("·" in ln or "generating" in ln for ln in lines)
