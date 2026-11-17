"""Row formatting shared by all text renderers."""

from __future__ import annotations

import time

from claus.lsfmt import (cols_text, display_width, human_size, name_kind, safe_name,
                         time_col, total_line, type_name)
from claus.model import Block, Entry, Status

Segment = tuple[str, str | None]  # (text, style key)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

ANSI = {
    "dir": "1;34", "link": "1;36", "orphan": "1;31", "exec": "1;32", "other": "1;33",
    "summary": "2", "pending": "2", "error": "31", "warn": "33", "header": "1",
}


def spinner_frame(now: float | None = None) -> str:
    now = time.monotonic() if now is None else now
    return SPINNER[int(now * 10) % len(SPINNER)]


def summary_part(e: Entry, dry_run: bool = False, live: bool = False) -> Segment:
    s = e.status
    if s in (Status.CACHED, Status.DONE):
        return (e.summary or "", "summary")
    if s is Status.ERROR:
        return ("! " + (e.error or "error"), "error")
    if dry_run:
        if s is Status.WOULD:
            return (f"+ would summarize ({e.rule})", "pending")
        if s is Status.EXCLUDED:
            return (f"- {e.rule}", "pending")
    if not live:
        return ("", None)
    if s is Status.RUNNING:
        return (spinner_frame(), "pending")
    if s in (Status.QUEUED, Status.CLASSIFYING):
        return ("·", "pending")
    if s is Status.HELD:
        return ("· awaiting confirmation", "pending")
    return ("", None)


def row_segments(e: Entry, block: Block, dry_run: bool = False, live: bool = False,
                 path_names: bool = False) -> list[Segment]:
    name = safe_name(e.display_path if path_names else e.name)
    kind = name_kind(e)
    segs: list[Segment] = [(cols_text(e, block.widths) + " ", None),
                           (name, None if kind == "file" else kind)]
    shown = display_width(name)
    if e.target is not None:
        t = " -> " + safe_name(e.target)
        segs.append((t, None))
        shown += display_width(t)
    text, style = summary_part(e, dry_run, live)
    if text:
        pad = 0 if path_names else max(0, block.name_width - shown)
        segs.append((" " * pad + "  ", None))
        segs.append((text, style))
    return segs


def fit(segs: list[Segment], width: int | None) -> list[Segment]:
    """Truncate segments to ``width`` display columns, ending with an ellipsis."""
    if not width:
        return segs
    total = sum(display_width(t) for t, _ in segs)
    if total <= width:
        return segs
    out: list[Segment] = []
    room = width - 1
    for text, style in segs:
        if room <= 0:
            break
        w = display_width(text)
        if w <= room:
            out.append((text, style))
            room -= w
            continue
        cut = ""
        for ch in text:
            cw = display_width(ch)
            if cw > room:
                break
            cut += ch
            room -= cw
        out.append((cut, style))
        room = 0
    last_style = out[-1][1] if out else None
    out.append(("…", last_style))
    return out


def ansi(segs: list[Segment], color: bool) -> str:
    if not color:
        return "".join(t for t, _ in segs).rstrip()
    parts = []
    for text, style in segs:
        code = ANSI.get(style or "")
        parts.append(f"\x1b[{code}m{text}\x1b[0m" if code and text else text)
    return "".join(parts).rstrip()


def header_lines(block: Block, human: bool, first: bool) -> list[str]:
    lines: list[str] = []
    if block.dirpath is None:
        return lines
    if block.show_header:
        if not first:
            lines.append("")
        lines.append(f"{safe_name(block.title or block.dirpath)}:")
    if block.error is None:
        lines.append(total_line(block, human))
    return lines


def entry_dict(e: Entry) -> dict:
    st = e.lst
    d = {
        "path": e.display_path,
        "abspath": e.path,
        "name": e.name,
        "type": type_name(st),
        "mode": e.cols[0] if e.cols else None,
        "nlink": st.st_nlink,
        "owner": e.cols[2] if len(e.cols) > 2 else None,
        "group": e.cols[3] if len(e.cols) > 3 else None,
        "size": st.st_size,
        "size_human": human_size(st.st_size),
        "mtime": time_col(st.st_mtime, "iso"),
        "mtime_ns": st.st_mtime_ns,
        "target": e.target,
        "mime": e.mime,
        "hash": e.hash,
        "status": e.status.value,
        "summary": e.summary,
        "error": e.error,
        "rule": e.rule,
        "event": e.event,
    }
    return d
