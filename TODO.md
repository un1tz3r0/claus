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
- [x] `pyproject.toml` (hatchling, src layout, extras `magic`/`rich`/`all`/`dev`, entry point)
- [x] `src/claus/__init__.py` (version), `__main__.py`
- [x] `.gitignore`, `uv` dev environment, `pytest` config
- [x] Logging / `-v` verbosity plumbing (done with the real CLI in M8)

## M2 — Config & rules (§4)
- [x] Built-in defaults incl. privacy rules (§4.4)
- [x] TOML loading: global (`XDG_CONFIG_HOME`), `--config`, `--no-config`
- [x] Directory `.claus.toml` discovery (root → dir), cached per directory
- [x] Trust levels and forbidden keys (§4.2), with warnings
- [x] Scalar layering (innermost wins)
- [x] Rule parser: `[!|?](+|-)type:pattern`, types `mime`/`name`/`iname`/`ext`/`path`
- [x] Rule resolution: strength filter, outermost-wins for `!`, innermost/last-wins otherwise
- [x] Lazy evaluation: decide without MIME when possible
- [x] Settings fingerprint (§4.6)
- [x] Size parsing (`16MiB`)

## M3 — MIME & hashing
- [x] `python-magic` back end (thread pool, one handle per thread), plus `file-magic`
- [x] `file --mime-type -b` subprocess fallback, batched
- [x] Streaming BLAKE2b-256 hashing in the thread pool
- [x] `max_size` / `skip_empty` handling

## M4 — Cache (§5)
- [x] DB actor thread, WAL, busy_timeout, schema v1 + version check
- [x] Path fast-path validation `(dev, ino, size, mtime_ns)`
- [x] Upsert paths/contents; events new/changed/copied/moved
- [x] Removal detection per scanned directory; orphan marking
- [x] Summary get/put by `(hash, fingerprint)`; `--any-fingerprint`
- [x] GC: stat all paths, purge expired orphans, events retention, `--vacuum`
- [x] Opportunistic daily purge

## M5 — Summarizer (§6)
- [x] Command builder (stdin and add-dir modes)
- [x] Private empty cwd under the cache dir
- [x] Prompt templating and truncation note
- [x] Output parsing (`structured_output`, fallback `result`)
- [x] Post-processing (whitespace, truncation)
- [x] Timeouts, retries with jitter, global rate-limit backoff
- [x] Kill the process group on cancel
- [x] `claude` missing → one warning, no summaries
- [x] Fatal errors (usage limit, not logged in) stop generation once
- [x] Length limit also in the user prompt and schema description (found in live testing)

## M6 — Pipeline (§7)
- [x] Scanner (`scandir` in a thread, sorting, `-a/-A`, `-R`, `--depth`, `skip_dirs`)
- [x] Classifier tasks with a semaphore
- [x] Generation queue, worker pool, in-flight dedup
- [x] Confirmation gate (threshold, `-y`, `--no-generate`, non-TTY behavior)
- [x] `--dry-run`, `--refresh`
- [x] Clean shutdown on Ctrl-C, SIGTERM and SIGHUP (verified: no orphaned `claude` processes)

## M7 — Output (§8)
- [x] `lsfmt`: mode string, nlink, owner/group cache, sizes (`-h`), times, symlink targets
- [x] Plain ordered renderer
- [x] Plain stream renderer (`--stream`)
- [x] JSONL renderer
- [x] rich Live renderer (placeholders, spinner, width truncation, pause for prompt)
- [x] Plain TTY fallback with stderr progress
- [x] `--color`

## M8 — Subcommands (§9)
- [x] Subcommand dispatch with `ls` default
- [x] `explain`
- [x] `gc`
- [x] `info`
- [x] `config`
- [x] Exit codes

## M9 — Tests (§11)
- [x] Fake `claude` script
- [x] Rules / config unit tests
- [x] Cache event tests (move, copy, remove, orphan expiry)
- [x] lsfmt tests
- [x] Renderer tests (plain, JSON stream, rich)
- [x] Integration tests via `--json`
- [x] Opt-in live test (`CLAUS_LIVE=1`)

## M10 — Release polish
- [x] Bring README / INSTALL / USAGE in line with the implemented behavior
- [x] Tag v0.1.0

## Later (§12)
- [ ] Persistent `stream-json` workers (avoid the ~6 s CLI start-up per file)
- [ ] Multi-file batching
- [ ] textual TUI
- [ ] `--watch`
- [x] Budget caps (`budget_usd` / `--budget`, done in 0.1.0)
- [ ] Publish to PyPI
- [ ] CI (GitHub Actions running the test suite)
