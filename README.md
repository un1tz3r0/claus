# claus

**`ls -l` with a one-line AI summary of each text file.**

```
$ claus -h src/claus
total 144K
-rw-rw-r-- 1 me me  16K Nov 17 15:28 cache.py       Persistent SQLite cache layer: single-thread DB actor, schema for files/hashes/summaries/events, dedup, GC and purge.
-rw-rw-r-- 1 me me  21K Nov 17 15:38 cli.py         Command-line interface for claus: ls/explain/info/gc/config subcommands, argument parsing, and async run pipeline.
-rw-rw-r-- 1 me me 3.0K Nov 17 15:34 model.py       Shared data model for the claus directory lister: Status enum, Entry and Block dataclasses for listing rows.
-rw-rw-r-- 1 me me  13K Nov 17 15:29 pipeline.py    Pipeline orchestrating scan, classification, cached summary lookup, and async summary generation queue for claus.
drwxrwxr-x 2 me me 4.0K Nov 17 15:38 __pycache__
drwxrwxr-x 3 me me 4.0K Nov 17 15:31 render
```
*(Real output for claus's own source, trimmed.)*

## What it does

- Lists files like `ls -l`, then adds a short summary for each file that passes your
  filters. By default these are text-like files, detected by MIME type with libmagic.
- Generates summaries with the [`claude`](https://claude.com/claude-code) CLI. The
  defaults are Haiku with low effort, no tools and no session persistence, which costs
  about $0.0005–0.001 per file.
- Caches each summary by content hash in SQLite, so a file is summarized once. Moved
  and copied files reuse their summary. Summaries for deleted contents are kept for a
  configurable time before they are purged.
- Lets you filter with layered TOML dotfiles (global → per-directory tree → command
  line). Rules can match MIME type, file name or extension, or a path substring, and
  `!` and `?` priority modifiers decide which rule wins.
- Is asynchronous: hashing and MIME detection run in thread pools, and several
  `claude` workers run in parallel. On a terminal, summaries fill in live.
- Never sends obvious secrets (`.env`, keys, `.ssh/`, …) to the model, even through
  symlinks. Asks before generating many summaries at once, and can stop at a spending
  budget.

## Quick start

```sh
uv tool install 'claus[all] @ git+https://github.com/un1tz3r0/claus'
claus --dry-run .     # what would be summarized, and why (no API calls)
claus -h .            # list with summaries
```

You need a logged-in [`claude`](https://claude.com/claude-code) CLI. A subscription
login or an `ANTHROPIC_API_KEY` both work.

## Documentation

- [INSTALL.md](INSTALL.md): requirements and installation
- [USAGE.md](USAGE.md): commands, options, configuration and filter rules
- [DESIGN.md](DESIGN.md): how it works
- [CHANGELOG.md](CHANGELOG.md), [TODO.md](TODO.md)

## License

MIT. See [LICENSE](LICENSE).
