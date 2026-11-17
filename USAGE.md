# Using claus

## Quick start

```sh
claus                     # current directory, like ls -l, with summaries
claus -h ~/notes          # human-readable sizes
claus -R --depth 2 src    # recurse two levels
claus --no-generate -R .  # cached summaries only, never calls claude
claus --dry-run -R .      # show what would be summarized, and why
claus --json . | jq .     # machine-readable output (one JSON object per line)
claus explain .env        # why is (or isn't) this file summarized?
```

The first time a directory is listed, new summaries are generated in parallel (4
`claude` workers by default; each call takes several seconds, most of it CLI
start-up). Later listings are instant: unchanged files are recognized by their stat
data without being read, and moved or copied files are recognized by content hash.

If more than `confirm_threshold` (default 25) new summaries are needed, claus starts
the first 25 right away and asks before doing the rest. `-y` skips the question. If
stdin is not a terminal (in scripts), the extra files are skipped with a warning, so
claus never hangs waiting for an answer.

Ctrl-C (or SIGTERM) stops a run cleanly. Running `claude` processes are killed, and
summaries that already finished stay cached.

## Commands

| Command | Purpose |
|---|---|
| `claus [ls] [OPTIONS] [PATH...]` | List with summaries (the default command). |
| `claus explain [--include/--exclude/--rule R] PATH...` | Show which filter rules match each file, and which one wins. |
| `claus info [--json] PATH...` | Show the cache record: hash, MIME type, other paths with the same content, history (new/changed/moved/copied/removed), summaries. |
| `claus gc [--vacuum] [--dry-run] [--retention-days D]` | Check every known path, forget deleted ones, and purge expired orphaned contents. |
| `claus config [--paths] [DIR]` | Show the effective merged configuration for `DIR` (default `.`), or with `--paths` only the files it comes from. |

To list a file whose name is a command name, use `claus ls gc` or `claus ./gc`.

Every command accepts `--config PATH`, `--no-config`, `--cache PATH`, `-v` (repeat
for more detail), `--help` and `--version`. As in `ls`, `-h` means human-readable
sizes, so help is `--help` only.

## Listing options

| Option | Meaning |
|---|---|
| `-a`, `-A` | Show hidden entries (`-A` leaves out `.` and `..`). |
| `-R`, `--depth N` | Recurse, optionally limited to N levels (`--depth` implies `-R`). Directories named in `listing.skip_dirs` are skipped. |
| `-h` | Human-readable sizes. |
| `-t`, `-S`, `-r` | Sort by modification time, by size, or reverse the order. |
| `--time-style ls\|iso\|relative` | Timestamp format. |
| `--include R`, `--exclude R`, `--rule R` | Add filter rules for this run (see below). `--include name:*.log` is the same as `--rule +name:*.log`. |
| `--model M`, `--effort E` | Model and effort for `claude` (default `haiku`, `low`). |
| `--max-length N`, `--max-bytes N` | Summary length limit, and how many bytes of each file are sent. |
| `--prompt T`, `--system-prompt T` | Prompt templates (see *Prompts*). |
| `--delivery stdin\|add-dir` | Send the file on stdin (default), or give the model read access to its directory. |
| `-j N`, `--jobs N` | Parallel `claude` workers. |
| `--budget USD` | Stop generating once this run has spent this much (as reported by `claude`). |
| `--confirm-threshold N` | Ask before generating more than N new summaries (`-1`: never ask). |
| `-y`, `--yes` | Don't ask. |
| `--no-generate` | Show cached summaries only. |
| `--dry-run` | Classify only and write nothing to the cache. Shows `+ would summarize (rule)` or `- reason` for each file. |
| `--refresh` | Regenerate summaries even when they are cached. |
| `--any-fingerprint` | Accept cached summaries made with other prompt or model settings. |
| `--stream` | When piped, print rows as they finish instead of in listing order. Rows show paths instead of names. |
| `--json` | One JSON object per entry (JSON Lines). Fields: `path`, `abspath`, `name`, `type`, `mode`, `nlink`, `owner`, `group`, `size`, `size_human`, `mtime`, `mtime_ns`, `target`, `mime`, `hash`, `status`, `summary`, `error`, `rule`, `event`. |
| `--color auto\|always\|never` | Colorize names and summaries. `auto` follows the terminal and honors `NO_COLOR`. |
| `--plain` | No live display, even on a terminal. |

### Output modes

- **Terminal with `rich` installed**: rows appear in order as soon as they're ready.
  Rows still waiting for a summary sit at the bottom with `·` (queued) or a spinner
  (generating), plus a progress footer.
- **Terminal without `rich`, or with `--plain`**: a progress counter on stderr, then
  the full listing.
- **Piped**: the complete listing in order once everything is done, with summaries
  never truncated. `--stream` prints rows as they finish instead.

### Exit status

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Minor problems: some summaries failed, a file or directory was unreadable, or generation was stopped (claude missing, usage limit, budget) |
| 2 | Serious trouble: bad arguments, a path argument that doesn't exist, a broken config file |
| 130 | Interrupted |

## Configuration

Configuration is read from these layers, outermost first:

1. built-in defaults
2. `~/.config/claus/config.toml` (honors `$XDG_CONFIG_HOME`; override with `--config`)
3. every `.claus.toml` from `/` down to the file's directory
4. command-line options

For ordinary settings, the innermost layer wins. Filter rules from all layers
accumulate (see below). `--no-config` ignores both the global and the directory
files.

By default, a `.claus.toml` in a directory may only change the **filter**, because it
might come from a repository you cloned. In the global config, set
`trust_dir_config = "all"` to also allow these `[summary]` settings: `model`,
`effort`, `max_length`, `max_bytes`, `delivery`, `system_prompt`, `prompt`,
`prompt_add_dir`. Set it to `"none"` to ignore directory files completely.
Directory files can never set the `claude` executable, extra arguments, environment,
concurrency, confirmation threshold, budget, cache or listing settings. Keys that
aren't allowed are ignored with a warning.

Example global `~/.config/claus/config.toml`:

```toml
[summary]
model = "haiku"
effort = "low"
max_length = 100
concurrency = 6
budget_usd = 0.50          # per run; 0 = unlimited

[filter]
rules = [
  "-ext:lock,min.js,map",  # never summarize lockfiles or minified files
  "+name:Makefile",
]

[cache]
retention_days = 60

[listing]
skip_dirs = [".git", "node_modules", "__pycache__", ".venv", ".tox", "target"]
time_style = "ls"
```

Example `~/code/project/.claus.toml`:

```toml
[filter]
rules = [
  "-path:/fixtures/",      # skip test fixtures in this project
  "!-path:/generated/",    # and no subdirectory may re-include generated code
]
max_size = "1MiB"
```

`claus config` shows the merged result for a directory, and `claus config --paths`
shows which files it came from. Every option is listed in DESIGN §4.5.

## Filter rules

```
[!|?] (+|-) type:pattern
```

- `+` includes the file for summarization. `-` excludes it. A file no rule matches is
  excluded.
- Types:
  - `mime:text/*`: glob on the MIME type (from libmagic)
  - `name:*.md`: glob on the file name
  - `iname:readme*`: the same, case-insensitive
  - `ext:py,rs`: one or more extensions
  - `path:/vendor/`: substring of the absolute path
- Priority:
  - `!` (strong) beats normal rules, which beat `?` (weak) rules.
  - When strong rules conflict, the **outermost** one wins. A global `!` rule can't be
    overridden, and a directory's `!` rule binds all its subdirectories.
  - When normal or weak rules conflict, the **innermost** one wins, and the **last**
    one within a file.

The built-in defaults are weak includes for text-like MIME types (`?+mime:text/*`,
`?+mime:application/json`, `…/xml`, `…/javascript`, `…/x-*script`, `…/toml`,
`…/yaml`, `…/sql`, …) plus strong excludes for secret-like files (`.env*`, `*.pem`,
`*.key`, SSH keys, `*secret*`, `*credential*`, `.netrc`, `.pgpass`, `*.kdbx`, and
anything under `/.git/`, `/.ssh/` or `/.gnupg/`). The privacy excludes can be turned
off only in the global config, with `builtin_privacy_rules = false`.

Symlinks must be allowed under both their own path and their target's real path, so
a harmless-looking link to a key file is still excluded.

Files that are empty (`filter.skip_empty`) or larger than `filter.max_size` (default
16 MiB) are never hashed or summarized.

Filters decide only which files get **summaries**. Every file still appears in the
listing, as with `ls`.

## Prompts

`prompt`, `prompt_add_dir` and `system_prompt` are templates with these
placeholders: `{name}`, `{path}`, `{mime}`, `{size}`, `{max_length}`, and
`{truncated}` (which says how much of a large file was sent). The defaults are in
DESIGN §6.3. Changing a template, the model, the effort or the length limit changes
the **fingerprint**, so summaries are generated again unless you pass
`--any-fingerprint`.

## Cache

- Location: `~/.cache/claus/cache.sqlite` (honors `$XDG_CACHE_HOME`; override with
  `--cache` or `cache.path`).
- Summaries are keyed by **content hash + settings fingerprint**. Renamed, moved and
  copied files reuse existing summaries, and `claus info` shows the history.
- When a listed directory no longer contains a file, claus forgets that path. Its
  content and summary are kept for `retention_days` (default 30) in case they show up
  somewhere else. Expired contents are purged automatically, at most once a day.
- `claus gc` also checks paths in directories you no longer list. `--vacuum`
  compacts the database file.
- Several `claus` processes can share the cache at the same time.
