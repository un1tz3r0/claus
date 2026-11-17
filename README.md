# claus

**`ls -l` with a one-line AI summary of each text file.**

```
$ claus -h src/claus
-rw-r--r-- 1 me me 4.1K Nov 17 11:40 cache.py      SQLite cache: paths, content hashes, summaries, move/copy events, GC
-rw-r--r-- 1 me me 2.3K Nov 17 11:38 cli.py        argparse front end dispatching ls/explain/gc/info/config subcommands
-rw-r--r-- 1 me me 3.0K Nov 17 11:39 rules.py      Parses and resolves +/- include rules with !/? priority across config layers
-rw-r--r-- 1 me me  12K Nov 17 11:41 logo.png
```
*(Illustrative output. claus is not implemented yet; see Status.)*

## What it does

- Lists files like `ls -l`, then adds a short summary for each file that passes your
  filters. By default these are text-like files, detected by MIME type with libmagic.
- Generates summaries with the [`claude`](https://claude.com/claude-code) CLI. The
  defaults are Haiku with low effort, no tools and no session persistence, so each
  summary costs a fraction of a cent.
- Caches each summary by content hash in SQLite, so a file is summarized once. Moved
  and copied files reuse their summary. Summaries for deleted contents are kept for a
  configurable time before they are purged.
- Lets you filter with layered TOML dotfiles (global → per-directory tree → command
  line). Rules can match MIME type, file name or extension, or a path substring, and
  `!` and `?` priority modifiers decide which rule wins.
- Is asynchronous: hashing and MIME detection run in thread pools, and several
  `claude` workers run in parallel. On a terminal, summaries fill in live.
- Never sends obvious secrets (`.env`, keys, `.ssh/`, …) to the model by default, and
  asks for confirmation before generating many summaries at once.

## Status

**Pre-alpha: only the design exists.** See [DESIGN.md](DESIGN.md) for the
architecture, [TODO.md](TODO.md) for progress and [CHANGELOG.md](CHANGELOG.md)
for changes.

## Documentation

- [INSTALL.md](INSTALL.md): requirements and installation
- [USAGE.md](USAGE.md): commands, options, configuration and filter rules
- [DESIGN.md](DESIGN.md): how it works

## License

MIT. See [LICENSE](LICENSE).
