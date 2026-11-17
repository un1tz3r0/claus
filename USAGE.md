# Using claus

> **Status:** pre-alpha. This describes the planned v0 interface (DESIGN §9) and
> will be updated as it is implemented.

## Quick start

```sh
claus                     # current directory, like ls -l, with summaries
claus -h ~/notes          # human-readable sizes
claus -R --depth 2 src    # recurse two levels
claus --no-generate -R .  # cached summaries only, never calls claude
claus --dry-run -R .      # list what would be summarized, and why
claus --json . | jq .     # machine-readable output (one JSON object per line)
```

The first time a directory is listed, new summaries are generated in parallel. Later
listings are instant: unchanged files are recognized by stat data without being read,
and moved or copied files are recognized by their content hash.

If more than `confirm_threshold` (default 25) new summaries would be generated, claus
starts the first 25 and asks before doing the rest. `-y` skips the question. In
scripts with no terminal on stdin, the extra files are skipped with a warning.

## Commands

| Command | Purpose |
|---|---|
| `claus [ls] [OPTIONS] [PATH...]` | List with summaries (the default command). |
| `claus explain PATH...` | Show which filter rules match a file and which one wins. |
| `claus info PATH...` | Show the cache record: hash, MIME type, other paths with the same content, history, summaries. |
| `claus gc [--vacuum] [--dry-run]` | Forget deleted paths and purge expired orphaned contents. |
| `claus config [--paths]` | Show the effective merged configuration and which files it came from. |

To list a file whose name is a command name, use `claus ls gc` or `claus ./gc`.

## Listing options

| Option | Meaning |
|---|---|
| `-a`, `-A` | Show hidden entries (`-A` leaves out `.` and `..`). |
| `-R`, `--depth N` | Recurse, optionally limited to N levels. Directories in `listing.skip_dirs` are skipped. |
| `-h` | Human-readable sizes. |
| `-t`, `-S`, `-r` | Sort by modification time, by size, or reverse the order. |
| `--include R`, `--exclude R`, `--rule R` | Add filter rules for this run (see below). |
| `--model M`, `--effort E`, `--max-length N`, `--prompt T`, `--delivery stdin\|add-dir` | Override summary settings. |
| `-j N`, `--jobs N` | Number of parallel `claude` workers (default 4). |
| `-y`, `--yes` | Don't ask before generating many summaries. |
| `--no-generate` | Show cached summaries only. |
| `--dry-run` | Classify only. Prints what would be summarized. |
| `--refresh` | Regenerate summaries even when they are cached. |
| `--any-fingerprint` | Accept cached summaries made with other prompt or model settings. |
| `--stream` | When piped, print rows as they finish instead of in listing order. |
| `--json` | One JSON object per entry (JSON Lines). |
| `--color auto\|always\|never`, `--plain` | Output styling. `--plain` turns off the rich live table. |
| `--config PATH`, `--no-config`, `--cache PATH` | Choose or turn off configuration files, or use a different cache. |
| `-v` | More logging (repeatable). |

## Configuration

Configuration is read from these layers, outermost first:

1. built-in defaults
2. `~/.config/claus/config.toml` (honors `$XDG_CONFIG_HOME`)
3. every `.claus.toml` from `/` down to the file's directory
4. command-line options

For ordinary settings, the innermost layer wins. Filter rules from all layers
accumulate (see below).

By default, a `.claus.toml` in a directory may only change the **filter**, because it
might come from a repository you cloned. In the global config, set
`trust_dir_config = "all"` to also allow `[summary]` settings, or `"none"` to ignore
directory files. Directory files can never set the `claude` executable, extra
arguments, environment, concurrency, confirmation threshold or cache settings.

Example global `~/.config/claus/config.toml`:

```toml
[summary]
model = "haiku"
effort = "low"
max_length = 100
concurrency = 6

[filter]
rules = [
  "-ext:lock,min.js,map",     # never summarize lockfiles or minified files
  "+name:Makefile",
]

[cache]
retention_days = 60
```

Example `~/code/project/.claus.toml`:

```toml
[filter]
rules = [
  "-path:/fixtures/",         # skip test fixtures in this project
  "!-path:/generated/",       # and no subdirectory may re-include generated code
]
```

All options are listed in DESIGN §4.5.

## Filter rules

```
[!|?] (+|-) type:pattern
```

- `+` includes the file for summarization. `-` excludes it. A file no rule matches is
  excluded.
- Types:
  - `mime:text/*`: glob on the MIME type
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
`?+mime:application/json`, …) plus strong excludes for secret-like files (`!-name:.env`,
`!-name:*.pem`, `!-path:/.ssh/`, …). The privacy excludes can be turned off only in the
global config, with `builtin_privacy_rules = false`.

Use `claus explain FILE` to see why a file is or isn't summarized.

Filters decide only which files get **summaries**. Every file still appears in the
listing, as with `ls`.

## Cache

- Location: `~/.cache/claus/cache.sqlite` (honors `$XDG_CACHE_HOME`).
- Summaries are keyed by **content hash + settings fingerprint**. If you change the
  model or prompt, files are summarized again unless you pass `--any-fingerprint`.
- When a listed directory no longer contains a file, claus forgets that path. Its
  content (and summary) is kept for `retention_days` in case it reappears somewhere
  else.
- `claus gc` checks every known path, including ones in directories you no longer
  list.
