# TODO

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped (see CHANGELOG).
Section references (§) point to `DESIGN.md`.

## M0 — Project setup
- [x] Clarify requirements
- [x] Verify `claude` CLI flags (`--json-schema`, `--effort` with haiku, `--tools ""`, subscription auth)
- [x] `git init`
- [x] DESIGN.md, TODO.md, CHANGELOG.md, README.md, INSTALL.md, USAGE.md, LICENSE (MIT)
- [x] Create public GitHub repo, push

## M1 — Skeleton & packaging
- [ ] `pyproject.toml` (hatchling, src layout, extras `magic`/`rich`/`all`/`dev`, entry point)
- [ ] `src/claus/__init__.py` (version), `__main__.py`
- [ ] `.gitignore`, `uv` dev environment, `pytest` config
- [ ] Logging / `-v` verbosity plumbing

## M2 — Config & rules (§4)
- [ ] Built-in defaults incl. privacy rules (§4.4)
- [ ] TOML loading: global (`XDG_CONFIG_HOME`), `--config`, `--no-config`
- [ ] Directory `.claus.toml` discovery (root → dir), cached per directory
- [ ] Trust levels and forbidden keys (§4.2), with warnings
- [ ] Scalar layering (innermost wins)
- [ ] Rule parser: `[!|?](+|-)type:pattern`, types `mime`/`name`/`iname`/`ext`/`path`
- [ ] Rule resolution: strength filter, outermost-wins for `!`, innermost/last-wins otherwise
- [ ] Lazy evaluation: decide without MIME when possible
- [ ] Settings fingerprint (§4.6)
- [ ] Size parsing (`16MiB`)

## M3 — MIME & hashing
- [ ] `python-magic` back end (thread pool)
- [ ] `file --mime-type -b` subprocess fallback, batched
- [ ] Streaming BLAKE2b-256 hashing in the thread pool
- [ ] `max_size` / `skip_empty` handling

## M4 — Cache (§5)
- [ ] DB actor thread, WAL, busy_timeout, schema v1 + migrations table
- [ ] Path fast-path validation `(dev, ino, size, mtime_ns)`
- [ ] Upsert paths/contents; events new/changed/copied/moved
- [ ] Removal detection per scanned directory; orphan marking
- [ ] Summary get/put by `(hash, fingerprint)`; `--any-fingerprint`
- [ ] GC: stat all paths, purge expired orphans, events retention, `--vacuum`
- [ ] Opportunistic daily purge

## M5 — Summarizer (§6)
- [ ] Command builder (stdin and add-dir modes)
- [ ] Private empty cwd under the cache dir
- [ ] Prompt templating and truncation note
- [ ] Output parsing (`structured_output`, fallback `result`)
- [ ] Post-processing (whitespace, truncation)
- [ ] Timeouts, retries with jitter, global rate-limit backoff
- [ ] Kill the process group on cancel
- [ ] `claude` missing → one warning, no summaries

## M6 — Pipeline (§7)
- [ ] Scanner (`scandir` in a thread, sorting, `-a/-A`, `-R`, `--depth`, `skip_dirs`)
- [ ] Classifier tasks with a semaphore
- [ ] Generation queue, worker pool, in-flight dedup
- [ ] Confirmation gate (threshold, `-y`, `--no-generate`, non-TTY behavior)
- [ ] `--dry-run`, `--refresh`
- [ ] Clean shutdown on Ctrl-C

## M7 — Output (§8)
- [ ] `lsfmt`: mode string, nlink, owner/group cache, sizes (`-h`), times, symlink targets
- [ ] Plain ordered renderer
- [ ] Plain stream renderer (`--stream`)
- [ ] JSONL renderer
- [ ] rich Live renderer (placeholders, spinner, width truncation, pause for prompt)
- [ ] Plain TTY fallback with stderr progress
- [ ] `--color`

## M8 — Subcommands (§9)
- [ ] Subcommand dispatch with `ls` default
- [ ] `explain`
- [ ] `gc`
- [ ] `info`
- [ ] `config`
- [ ] Exit codes

## M9 — Tests (§11)
- [ ] Fake `claude` script
- [ ] Rules / config unit tests
- [ ] Cache event tests (move, copy, remove, orphan expiry)
- [ ] lsfmt tests
- [ ] Integration tests via `--json`
- [ ] Opt-in live test (`CLAUS_LIVE=1`)

## M10 — Release polish
- [ ] Bring README / INSTALL / USAGE in line with the implemented behavior
- [ ] Tag v0.1.0

## Later (§12)
- [ ] Persistent `stream-json` workers
- [ ] Multi-file batching
- [ ] textual TUI
- [ ] `--watch`
- [ ] Budget caps
