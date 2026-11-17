"""Command-line interface (DESIGN §9)."""

from __future__ import annotations

import argparse
import asyncio
import json
import locale
import logging
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from claus import __version__
from claus.cache import Cache, vacuum
from claus.config import ConfigError, ConfigResolver, dump_toml
from claus.hashing import hash_file
from claus.mime import make_detector
from claus.pipeline import Pipeline, RunOptions
from claus.rules import STRENGTH_NAME, decide
from claus.scan import ScanOptions
from claus.summarizer import Summarizer

SUBCOMMANDS = ("ls", "explain", "gc", "info", "config")
log = logging.getLogger("claus")


def _warn(msg: str) -> None:
    print(f"claus: {msg}", file=sys.stderr)


# -- argument parsing ---------------------------------------------------------


def _common(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("configuration")
    g.add_argument("--config", metavar="PATH", help="global config file (default: ~/.config/claus/config.toml)")
    g.add_argument("--no-config", action="store_true", help="ignore the global and directory config files")
    g.add_argument("--cache", metavar="PATH", help="cache database (default: ~/.cache/claus/cache.sqlite)")
    g.add_argument("-v", "--verbose", action="count", default=0, help="more logging (repeatable)")
    p.add_argument("--help", action="help", help="show this help and exit")
    p.add_argument("--version", action="version", version=f"claus {__version__}")


def ls_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claus", add_help=False,
        description="List files like `ls -l`, with cached AI-generated one-line summaries of text files.",
        epilog="Other commands: claus explain|info|gc|config --help",
    )
    p.add_argument("paths", nargs="*", metavar="PATH", help="files or directories (default: .)")
    g = p.add_argument_group("listing")
    g.add_argument("-a", "--all", dest="all_mode", action="store_const", const="all", default="none",
                   help="show hidden entries, including . and ..")
    g.add_argument("-A", "--almost-all", dest="all_mode", action="store_const", const="almost",
                   help="show hidden entries, except . and ..")
    g.add_argument("-R", "--recursive", action="store_true", help="list subdirectories recursively")
    g.add_argument("--depth", type=int, metavar="N", help="recurse at most N levels (implies -R)")
    g.add_argument("-h", "--human-readable", dest="human", action="store_true", help="human-readable sizes")
    g.add_argument("-t", dest="sort", action="store_const", const="time", default="name",
                   help="sort by modification time, newest first")
    g.add_argument("-S", dest="sort", action="store_const", const="size", help="sort by size, largest first")
    g.add_argument("-r", "--reverse", action="store_true", help="reverse the sort order")
    g.add_argument("--time-style", choices=["ls", "iso", "relative"], help="timestamp format")

    g = p.add_argument_group("filter rules")
    g.add_argument("--include", action="append", default=[], metavar="TYPE:PATTERN",
                   help="add an include rule (same as --rule +TYPE:PATTERN)")
    g.add_argument("--exclude", action="append", default=[], metavar="TYPE:PATTERN",
                   help="add an exclude rule (same as --rule -TYPE:PATTERN)")
    g.add_argument("--rule", action="append", default=[], metavar="RULE",
                   help="add a rule, e.g. '!-path:/vendor/' (may be repeated)")

    g = p.add_argument_group("summaries")
    g.add_argument("--model", help="model for claude (default: haiku)")
    g.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="effort level (default: low)")
    g.add_argument("--max-length", type=int, metavar="N", help="maximum summary length in characters")
    g.add_argument("--max-bytes", type=int, metavar="N", help="bytes of each file sent to the model")
    g.add_argument("--prompt", metavar="TEMPLATE", help="prompt template ({name} {path} {mime} {size} {max_length} {truncated})")
    g.add_argument("--system-prompt", metavar="TEMPLATE", help="system prompt template")
    g.add_argument("--delivery", choices=["stdin", "add-dir"], help="how the file reaches the model")
    g.add_argument("-j", "--jobs", type=int, metavar="N", help="parallel claude workers (default: 4)")
    g.add_argument("--budget", type=float, metavar="USD", help="stop generating after spending this much in this run")
    g.add_argument("--confirm-threshold", type=int, metavar="N",
                   help="ask before generating more than N new summaries (-1: never ask)")
    g.add_argument("-y", "--yes", action="store_true", help="do not ask before generating many summaries")
    g.add_argument("--no-generate", action="store_true", help="only show cached summaries")
    g.add_argument("--dry-run", action="store_true", help="show what would be summarized, and why")
    g.add_argument("--refresh", action="store_true", help="regenerate summaries even when cached")
    g.add_argument("--any-fingerprint", action="store_true",
                   help="accept cached summaries made with other prompt/model settings")

    g = p.add_argument_group("output")
    g.add_argument("--stream", action="store_true", help="print rows as they finish (when not a terminal)")
    g.add_argument("--json", action="store_true", help="JSON Lines output, one object per entry")
    g.add_argument("--color", choices=["auto", "always", "never"], default="auto", help="colorize output")
    g.add_argument("--plain", action="store_true", help="no live display, even on a terminal")
    _common(p)
    return p


def sub_parser(cmd: str) -> argparse.ArgumentParser:
    descs = {
        "explain": "Show which filter rules match each file, and which one wins.",
        "info": "Show the cache record for each file: hash, other paths, history, summaries.",
        "gc": "Forget deleted paths and purge orphaned contents older than the retention period.",
        "config": "Show the effective configuration for a directory (default: .).",
    }
    p = argparse.ArgumentParser(prog=f"claus {cmd}", add_help=False, description=descs[cmd])
    if cmd in ("explain", "info"):
        p.add_argument("paths", nargs="+", metavar="PATH")
    if cmd == "explain":
        p.add_argument("--include", action="append", default=[], metavar="TYPE:PATTERN")
        p.add_argument("--exclude", action="append", default=[], metavar="TYPE:PATTERN")
        p.add_argument("--rule", action="append", default=[], metavar="RULE")
    if cmd == "gc":
        p.add_argument("--dry-run", action="store_true", help="report what would be removed")
        p.add_argument("--vacuum", action="store_true", help="compact the database afterwards")
        p.add_argument("--retention-days", type=float, metavar="DAYS", help="override cache.retention_days")
    if cmd == "config":
        p.add_argument("dir", nargs="?", default=".", metavar="DIR")
        p.add_argument("--paths", action="store_true", help="only list the files the config comes from")
    if cmd == "info":
        p.add_argument("--json", action="store_true", help="JSON output")
    _common(p)
    return p


def cli_rules(args: argparse.Namespace) -> list[str]:
    rules = list(getattr(args, "rule", []))
    rules += [r if r[:1] in "+-" else "+" + r for r in getattr(args, "include", [])]
    rules += [r if r[:1] in "+-" else "-" + r for r in getattr(args, "exclude", [])]
    return rules


def cli_settings(args: argparse.Namespace) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for opt, key in (("model", "model"), ("effort", "effort"), ("max_length", "max_length"),
                     ("max_bytes", "max_bytes"), ("system_prompt", "system_prompt"),
                     ("delivery", "delivery"), ("jobs", "concurrency"), ("budget", "budget_usd"),
                     ("confirm_threshold", "confirm_threshold")):
        v = getattr(args, opt, None)
        if v is not None:
            summary[key] = v
    if getattr(args, "prompt", None) is not None:
        summary["prompt"] = summary["prompt_add_dir"] = args.prompt
    out: dict[str, Any] = {"summary": summary}
    if getattr(args, "cache", None):
        out["cache"] = {"path": args.cache}
    if getattr(args, "time_style", None):
        out["listing"] = {"time_style": args.time_style}
    return out


def make_resolver(args: argparse.Namespace) -> ConfigResolver:
    return ConfigResolver(global_path=args.config, use_files=not args.no_config,
                          cli_settings=cli_settings(args), cli_rules=cli_rules(args), warn=_warn)


def setup_logging(verbose: int) -> None:
    level = logging.WARNING if verbose == 0 else logging.INFO if verbose == 1 else logging.DEBUG
    logging.basicConfig(level=level, format="claus: %(levelname)s: %(message)s", stream=sys.stderr)


def _executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 1) * 2), thread_name_prefix="claus-io")


# -- ls -------------------------------------------------------------------------


def choose_renderer(args: argparse.Namespace):
    from claus.render.plain import JsonRenderer, PlainRenderer

    if args.json:
        return JsonRenderer(stream=args.stream)
    tty = sys.stdout.isatty()
    if args.color == "always":
        color = True
    elif args.color == "never":
        color = False
    else:
        color = tty and not os.environ.get("NO_COLOR")
    if tty and not args.plain and not args.stream:
        try:
            from rich.console import Console

            from claus.render.rich_live import RichRenderer
        except ImportError:
            pass
        else:
            console = Console(highlight=False, soft_wrap=False, no_color=not color,
                              force_terminal=True if color else None)
            return RichRenderer(console, human=args.human, dry_run=args.dry_run)
    width = shutil.get_terminal_size().columns if tty else None
    return PlainRenderer(color=color, width=width, stream=args.stream, progress=tty and not args.stream,
                         human=args.human, dry_run=args.dry_run)


def _cancel_on_signals() -> None:
    """Turn SIGTERM/SIGHUP into cancellation of the main task (like Ctrl-C), so
    running claude processes are killed and finished summaries stay cached."""
    import signal

    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is None:
        return
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except (NotImplementedError, RuntimeError, ValueError):
            pass


async def run_ls(args: argparse.Namespace) -> int:
    _cancel_on_signals()
    resolver = make_resolver(args)
    base = resolver.base
    executor = _executor()
    asyncio.get_running_loop().set_default_executor(executor)
    scan_opts = ScanOptions(
        all_mode=args.all_mode, recursive=args.recursive or args.depth is not None,
        depth=args.depth, sort=args.sort, reverse=args.reverse,
        skip_dirs=list(base["listing"]["skip_dirs"]),
    )
    opts = RunOptions(
        scan=scan_opts, human=args.human, time_style=base["listing"]["time_style"],
        dry_run=args.dry_run, no_generate=args.no_generate, yes=args.yes, refresh=args.refresh,
        any_fingerprint=args.any_fingerprint, jobs=int(base["summary"]["concurrency"]),
        confirm_threshold=int(base["summary"]["confirm_threshold"]),
        interactive=sys.stdin.isatty(), retention_days=float(base["cache"]["retention_days"]),
    )
    cache_path = resolver.cache_path()
    cache = Cache(cache_path)
    await cache.open()
    detector = make_detector()
    log.info("cache: %s; mime backend: %s", cache_path, detector.backend)
    summarizer = Summarizer(os.path.join(os.path.dirname(cache_path), "workdir"),
                            budget_usd=float(base["summary"]["budget_usd"]))
    renderer = choose_renderer(args)
    pipeline = Pipeline(opts, resolver, cache, detector, summarizer, renderer)
    try:
        code = await pipeline.run(args.paths or ["."])
    finally:
        await detector.aclose()
        await cache.aclose()
    if summarizer.calls:
        log.info("generated %d summaries via %d claude calls, $%.4f",
                 pipeline.generated, summarizer.calls, summarizer.spent_usd)
    if args.dry_run:
        from claus.model import Status

        n = sum(1 for b in renderer.blocks for e in b.entries if e.status is Status.WOULD)
        print(f"claus: dry run: {n} file(s) would be summarized", file=sys.stderr)
    return code


# -- explain --------------------------------------------------------------------


async def run_explain(args: argparse.Namespace) -> int:
    resolver = make_resolver(args)
    detector = make_detector()
    code = 0
    try:
        for p in args.paths:
            path = os.path.abspath(p)
            try:
                st = os.stat(path)
            except OSError as e:
                _warn(f"cannot access '{p}': {e.strerror}")
                code = 2
                continue
            eff = resolver.for_dir(os.path.dirname(path))
            name = os.path.basename(path)
            mime = await detector.detect(path)
            print(f"{p}")
            print(f"  mime type:   {mime or 'unknown'}   size: {st.st_size}")
            print(f"  fingerprint: {eff.fingerprint}")
            print("  config:      " + ", ".join(eff.sources))
            filt = eff.settings["filter"]
            if filt["skip_empty"] and st.st_size == 0:
                print("  → excluded: empty file (filter.skip_empty)")
            if st.st_size > eff.max_size:
                print(f"  → excluded: larger than filter.max_size ({filt['max_size']})")
            targets = [(name, path)]
            real = os.path.realpath(path)
            if real != path:
                targets.append((os.path.basename(real), real))
            final = True
            for tname, tpath in targets:
                if tpath != path:
                    print(f"  symlink target {tpath} must also be included:")
                d = decide(eff.rules, tname, tpath, mime)
                matched = sorted(d.matched, key=lambda r: r.key(), reverse=True)
                if not matched:
                    print("  no rules match")
                top = matched[0].strength if matched else None
                for r in matched:
                    mark = "→" if r is d.winner else ("·" if r.strength == top else " ")
                    print(f"  {mark} {r.text:<32} {STRENGTH_NAME[r.strength]:<6}  {r.source}")
                final = final and bool(d.include)
            if st.st_size == 0 and filt["skip_empty"] or st.st_size > eff.max_size:
                final = False
            print(f"  result:      {'INCLUDED' if final else 'excluded'}")
    finally:
        await detector.aclose()
    return code


# -- info -----------------------------------------------------------------------


def _ts(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "-"


async def run_info(args: argparse.Namespace) -> int:
    resolver = make_resolver(args)
    cache = Cache(resolver.cache_path())
    code = 0
    try:
        for p in args.paths:
            path = os.path.abspath(p)
            h = None
            if os.path.isfile(path):
                h = await asyncio.to_thread(hash_file, path)
            elif not os.path.lexists(path):
                print(f"claus: note: '{p}' does not exist; showing cached history", file=sys.stderr)
            info = await cache.info(path, h)
            if args.json:
                print(json.dumps(info, ensure_ascii=False, default=str))
                continue
            print(p)
            print(f"  hash:        {info.get('hash') or '-'}")
            c = info.get("content")
            if c:
                print(f"  mime type:   {c.get('mime') or '-'}   size: {c.get('size')}")
                print(f"  first seen:  {_ts(c.get('first_seen'))}"
                      + (f"   orphaned: {_ts(c['orphaned_at'])}" if c.get("orphaned_at") else ""))
            elif h:
                print("  (content not in cache)")
            if info.get("path_row") is None:
                print("  path:        not tracked")
            others = info.get("other_paths") or []
            if others:
                print("  same content at:")
                for o in others:
                    print(f"    {o}")
            sums = info.get("summaries") or []
            if sums:
                print("  summaries:")
                for s in sums:
                    print(f"    [{s['fingerprint']}] {_ts(s['created_at'])} {s.get('model') or ''}")
                    print(f"      {s['summary']}")
            evs = info.get("events") or []
            if evs:
                print("  history:")
                for ev in evs:
                    extra = f" ({'from' if ev['kind'] == 'moved' else 'of'} {ev['other_path']})" if ev.get("other_path") else ""
                    who = "" if ev["path"] == path else f" {ev['path']}"
                    print(f"    {_ts(ev['ts'])} {ev['kind']}{who}{extra}")
    finally:
        await cache.aclose()
    return code


# -- gc -------------------------------------------------------------------------


async def run_gc(args: argparse.Namespace) -> int:
    resolver = make_resolver(args)
    retention = args.retention_days if args.retention_days is not None else float(
        resolver.base["cache"]["retention_days"])
    cache = Cache(resolver.cache_path())
    try:
        stats = await cache.gc(retention, check_paths=True, dry_run=args.dry_run)
        if args.vacuum and not args.dry_run:
            await cache.run(vacuum)
        totals = await cache.stats()
    finally:
        await cache.aclose()
    verb = "would remove" if args.dry_run else "removed"
    print(f"checked {stats.paths_checked} paths; {verb} {stats.paths_removed} missing")
    print(f"{'would purge' if args.dry_run else 'purged'} {stats.contents_purged} contents, "
          f"{stats.summaries_purged} summaries, {stats.events_purged} events "
          f"(retention {retention:g} days)")
    print(f"cache: {totals['paths']} paths, {totals['contents']} contents "
          f"({totals['orphaned']} orphaned), {totals['summaries']} summaries, "
          f"${totals['total_cost_usd']:.4f} spent on cached summaries")
    return 0


# -- config ---------------------------------------------------------------------


def run_config(args: argparse.Namespace) -> int:
    resolver = make_resolver(args)
    eff = resolver.for_dir(args.dir)
    gp = resolver.global_path
    if args.paths:
        print(f"global config: {gp}" + ("" if os.path.isfile(gp) else " (not found)"))
        for layer in eff.layers:
            if layer.kind == "dir":
                print(f"directory config: {layer.source}")
        print(f"cache: {resolver.cache_path()}")
        return 0
    print(f"# effective configuration for {os.path.abspath(args.dir)}")
    print("# sources: " + ", ".join(eff.sources))
    print(f"# summary fingerprint: {eff.fingerprint}\n")
    settings = {k: v for k, v in eff.settings.items()}
    settings["filter"] = dict(settings["filter"])
    seen: set[tuple[str, int, int]] = set()
    rules = []
    for r in eff.rules:
        key = (r.text, r.layer, r.order)
        if key not in seen:
            seen.add(key)
            rules.append(r.text)
    settings["filter"]["rules"] = rules
    print(dump_toml(settings), end="")
    return 0


# -- entry point ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    cmd = "ls"
    if argv and argv[0] in SUBCOMMANDS:
        cmd = argv.pop(0)
    parser = ls_parser() if cmd == "ls" else sub_parser(cmd)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code not in (0, None) else 0
    setup_logging(args.verbose)
    try:
        if cmd == "ls":
            return asyncio.run(run_ls(args))
        if cmd == "explain":
            return asyncio.run(run_explain(args))
        if cmd == "info":
            return asyncio.run(run_info(args))
        if cmd == "gc":
            return asyncio.run(run_gc(args))
        return run_config(args)
    except ConfigError as e:
        _warn(f"configuration error: {e}")
        return 2
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(file=sys.stderr)
        return 130
    except BrokenPipeError:
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 0
