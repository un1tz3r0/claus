# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

Each entry may include a **Deviations** subsection. It records places where the
implementation differs from the original request or from `DESIGN.md`, and why.

## [Unreleased]

## [0.1.0] - 2026-11-17

First working version.

### Added
- **Listing** (`claus [ls]`): `ls -l`-style columns plus a summary column, with
  - `-a`, `-A`, `-R`, `--depth`, `-h`, `-t`, `-S`, `-r` and `--time-style`
  - `listing.skip_dirs` for recursion
  - `ls`-style headers and `total` lines
  - symlink targets
  - locale-aware sorting
  - type-based name colors
  - control characters in names shown as `?`
- **Filter rules** `[!|?](+|-)type:pattern` with the types `mime`, `name`, `iname`,
  `ext` and `path`, resolved by strength and layer (DESIGN §4.3).
  - MIME detection is skipped when the result doesn't depend on it.
  - Symlinks are checked under their real path too.
- **Configuration** layers: built-in → global `config.toml` → directory `.claus.toml`
  files → command line, plus:
  - trust levels for directory files
  - validation with warnings
  - `claus config [--paths] [DIR]`
- **Built-in privacy rules** that keep secret-like files away from the model. They can
  be turned off only in the global config.
- **MIME detection** with `python-magic` or `file-magic` (one handle per thread), or a
  batched `file --mime-type` subprocess as the fallback.
- **SQLite cache** behind a single-threaded DB actor (WAL), with:
  - an unchanged-file fast path based on stat data
  - BLAKE2b-256 content hashes
  - summaries keyed by `(hash, settings fingerprint)`
  - `new`/`changed`/`copied`/`moved`/`removed` events
  - per-directory reconciliation
  - orphan retention, automatic purging (at most once a day), and `claus gc [--vacuum]`
- **`claus info [--json]`** shows a file's hash, its other paths, its history and its
  summaries.
- **`claus explain`** shows the rule trace for a file.
- **Summarizer**: one-shot `claude -p` calls with structured JSON output, all tools
  turned off, no session persistence, no setting sources, no MCP servers and no slash
  commands, run from an empty private working directory. It also has:
  - timeouts
  - retries with jitter
  - a global pause on rate limits
  - fatal-error detection (claude missing, usage limit, not logged in) that stops
    generation once
  - a skip for files that change while the run is in progress
- **Pipeline**: classification tasks bounded by a semaphore, a worker pool
  (`--jobs`), merging of duplicate contents within a run (one call each), and a
  confirmation gate (`confirm_threshold`, `-y`; non-interactive runs skip instead of
  asking).
- **Generation control**: `--dry-run`, `--no-generate`, `--refresh`,
  `--any-fingerprint`, and a per-run spending cap (`budget_usd` / `--budget`).
- **Output**:
  - a live `rich` display: rows printed in order as they finish, plus a live tail and
    a progress footer
  - plain output, ordered or `--stream`
  - `--json` (JSON Lines)
  - `--color`, which honors `NO_COLOR`
- **Exit codes** like `ls`: 0, 1 or 2, and 130 when interrupted.
- **Clean shutdown** on Ctrl-C, SIGTERM and SIGHUP: the `claude` process groups are
  killed.
- **Tests** (73, plus 1 opt-in live test): a fake `claude`, and unit tests for rules,
  config, cache, summarizer, formatting and renderers, plus end-to-end CLI tests.
- Packaging: `pyproject.toml` (hatchling, `src/` layout), extras `magic`, `rich` and
  `all`, a `dev` dependency group, a `claus` console script, and `uv.lock`.
- Project documents: `DESIGN.md`, `TODO.md`, `CHANGELOG.md`, `README.md`,
  `INSTALL.md`, `USAGE.md`, and `LICENSE` (MIT).

### Deviations (from the original request)
- **File delivery: stdin instead of `claude --file`.** `--file` takes
  `file_id:path` specs for files already uploaded to the Anthropic Files API, so it
  cannot send local files. The default is to pipe the contents on stdin with all tools
  disabled. The requested `--add-dir` approach is available as `delivery = "add-dir"`.
  (DESIGN §2, §6.1)
- **`--bare` is not used.** It ignores OAuth/subscription logins. Features are turned
  off with separate flags instead (DESIGN §6.2).
- **Wider default MIME filter.** The default includes `text/*` plus common text-like
  `application/*` types (JSON, XML, JS, scripts, TOML, YAML, SQL, NDJSON), because
  libmagic does not report these as `text/*`. All defaults are weak (`?`) rules that
  any config can replace (DESIGN §4.4).
- **Restricted directory config.** For safety, per-directory `.claus.toml` files
  can set only filters by default (DESIGN §4.2).
- **Built-in privacy exclusions.** Strong exclusions for secret-like files
  (`.env`, keys, `.ssh/`, …) can be turned off only in the global config
  (DESIGN §4.4).

### Deviations (from DESIGN.md as first drafted)
- **Summary length limit in three places.** In live testing, Haiku often exceeded
  `max_length` when the limit was only in the system prompt. It now also appears in
  the user prompt and in the JSON schema's field description. The fingerprint changed
  accordingly (DESIGN §2, §6.2, §6.3).
- **New settings and options:**
  - `prompt_add_dir`: a separate template for `add-dir` delivery
  - `budget_usd` / `--budget`: moved forward from "future work"
  - `--confirm-threshold`, where `-1` means never ask
  - `--max-bytes`, `--system-prompt` and `--time-style`
  - `claus info --json`, `gc --retention-days` and `config DIR`
- **`--no-config`** ignores directory `.claus.toml` files as well as the global
  config, so runs can be fully reproducible.
- **Safer reconciliation.** Rows for files that are present but were not classified
  in this scan (such as hidden files in a listing without `-a`) are kept, rather than
  dropped as "not seen" (DESIGN §5.3).
- **Symlinks are checked under their real path** as well as their own path. This was
  added to close a privacy gap: a link to `~/.ssh/...` with an innocent name
  (DESIGN §4.3).
- **SIGTERM and SIGHUP** are handled like Ctrl-C, so killed runs leave no orphaned
  `claude` processes.
- **`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`** is set for `claude` calls unless
  already set.
- **Per-operation transactions.** Cache writes are not batched across operations.
  Each operation is one short transaction, which proved fast enough in WAL mode.
- **Module layout.** Added `model.py` and `scan.py`. The rich renderer is
  `render/rich_live.py`, and the JSON renderer lives in `render/plain.py`.
- **`--dry-run` writes nothing** to the cache. Files are still hashed so cached
  summaries can be shown.
- **Scope.** Persistent `stream-json` workers, multi-file batching, a textual TUI and
  `--watch` remain future work (TODO "Later").
