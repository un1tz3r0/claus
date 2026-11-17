# claus — design

`claus` is an `ls -l`-like directory lister. It adds a one-line, AI-generated summary
for every file whose type passes a configurable filter. Summaries come from the
`claude` CLI in non-interactive mode and are cached in SQLite by content hash, so a
file's contents are summarized only once, even after it is moved or copied.

Status: **implemented in v0.1.0**. This document describes the code as built.
When the implementation differs from the design, the difference goes in
`CHANGELOG.md` under *Deviations*, and this document is updated to match.

---

## 1. Goals and non-goals

**Goals**

- Output that looks like `ls -l` (mode, links, owner, group, size, mtime, name), plus
  a summary column.
- Summaries only for files that pass the filter rules. By default these are text MIME
  types, detected with libmagic.
- A filter configured with layered dotfiles: built-in → global → per-directory tree
  → command line. Rules can have priority modifiers.
- A persistent cache. A summary is generated once for each *content* and settings
  fingerprint. The cache tracks moved, copied and removed files, and removed contents
  are kept for a configurable time.
- Asyncio throughout. Blocking work (libmagic, hashing, SQLite) runs in thread
  pools. Summary generation uses a fixed-size pool of worker coroutines that run
  `claude` subprocesses.
- Responsive output. On a TTY, rows appear at once and summaries fill in as they
  arrive.
- Works with the standard library alone. `python-magic` and `rich` are optional and
  improve the result.

**Non-goals (v0)**

- Full `ls` flag compatibility. Only a useful subset is planned (see §9).
- Summaries for binary files, images or PDFs.
- Watching directories, or running as a daemon.
- Calling the Anthropic API directly. All model access goes through the `claude`
  CLI, so the user's existing login (subscription or API key) works unchanged.

---

## 2. Findings that shaped this design

These were checked against `claude` 2.1.326 on 2026-11-17.

| Topic | Finding | Consequence |
|---|---|---|
| `--file` | Takes `file_id:relative_path` specs that point to files already uploaded to the Anthropic Files API. It does **not** upload local files. | Not used. The file contents are sent on **stdin** instead (§6). |
| `--bare` | Reads only `ANTHROPIC_API_KEY`, never OAuth. The user logs in with a subscription. | Not used. Features are turned off with separate flags (§6.2). |
| `--json-schema` | Works with `-p --output-format json`. The parsed object is returned in the result's `structured_output` field (`result` holds the same thing as a string). The call takes 2 turns internally. | The schema is `{"summary": string}`. Read `structured_output` and fall back to parsing `result`. |
| `--effort low` + `--model haiku` | Accepted. The alias resolved to `claude-haiku-5-5`. | These are the defaults. |
| Latency | One call took ~8.5 s wall time: ~2.5 s of API time plus ~6 s of CLI start-up. Cost was ~$0.0003. | Concurrency matters. Default is 4 workers. A persistent `stream-json` worker could remove the start-up cost later (§12). |
| libmagic MIME | JSON, JS and some XML/YAML/scripts are reported as `application/*`, not `text/*`. | The default include list adds them (§4.4). |
| Summary length | With the limit only in the system prompt, Haiku often overshot 120 characters. Repeating it in the user prompt and in the schema field's `description` brought most summaries under the limit. | The limit appears in all three places (§6.2, §6.3). Longer output is still truncated with `…`. |

---

## 3. Architecture

```
                       ┌─────────────┐
 argv ──► cli ──► config/rules ──┐   │ renderer    │◄── row updates (index, field, value)
                                 ▼   │ rich Live / │
 ┌────────┐ entries ┌────────────┐   │ plain / json│
 │scanner ├────────►│ classifier │   └─────────────┘
 │(scandir│         │  stat      │          ▲
 │ thread)│         │  path-cache│──────────┤ (cached summary: immediately)
 └────────┘         │  mime      │          │
                    │  rules     │          │
                    │  hash      │   ┌──────┴──────┐
                    │  sum-cache │──►│ gen queue   │──► N summary workers ──► claude -p
                    └────────────┘   │ (dedup by   │        │
                         │           │ hash+fprint)│        ▼
                         ▼           └─────────────┘   cache writes
                    ┌─────────┐
                    │ DB actor│  (one thread, one sqlite3 connection, WAL)
                    └─────────┘
```

### 3.1 Modules (`src/claus/`)

| Module | Responsibility |
|---|---|
| `cli.py` | argparse, subcommand dispatch, exit codes, the event loop, signal handling. |
| `model.py` | `Entry`, `Block` and `Status`, shared by the pipeline and the renderers. |
| `scan.py` | `scandir` in the thread pool, sorting, hidden files, recursion (`-R`, `--depth`, `skip_dirs`). |
| `config.py` | TOML loading (`tomllib`), layering, trust levels, defaults, the settings fingerprint. |
| `rules.py` | Parsing and evaluating filter rules (§4). |
| `mime.py` | MIME detection with `python-magic`, or the `file` subprocess as a fallback. |
| `hashing.py` | Streamed BLAKE2b-256 of file contents in the thread pool. |
| `cache.py` | SQLite schema, migrations, the DB actor, garbage collection (§5). |
| `summarizer.py` | Building the `claude` command, running it, parsing output, retries and backoff (§6). |
| `pipeline.py` | Scanner → classifier → generation queue → renderer. Owns the queues, workers, in-flight dedup and the confirmation gate (§7). |
| `lsfmt.py` | `ls -l` column formatting: mode string, owner/group cache, sizes, times. |
| `render/common.py`, `render/plain.py` (plain + JSON Lines), `render/rich_live.py` | Output back ends (§8). |

### 3.2 Concurrency model

- **Event loop**: a single asyncio loop orchestrates everything.
- **I/O thread pool** (`ThreadPoolExecutor`, default `min(32, cpu*2)`): `os.scandir`,
  `os.stat`, libmagic calls (ctypes releases the GIL) and hashing (`hashlib`
  releases the GIL for large updates, and file reads release it too).
- **DB actor**: one dedicated thread that owns the single `sqlite3` connection.
  Requests are submitted as callables and awaited as futures. SQLite runs in WAL mode
  with `busy_timeout`, so several concurrent `claus` processes are safe. Each write
  operation is its own short `BEGIN IMMEDIATE` transaction.
- **Generation workers**: `concurrency` coroutines (default 4) take jobs from an
  `asyncio.Queue` and each runs one `claude` subprocess at a time
  (`asyncio.create_subprocess_exec`).
- **In-flight dedup**: `dict[(hash, fingerprint)] → Future`. Duplicate files in one
  run share a single generation.
- **Global backoff**: on a rate-limit or overload error, an `asyncio.Event` pauses
  every worker for an exponential backoff period.
- **Cancellation**: Ctrl-C, SIGTERM and SIGHUP cancel the main task. The workers are
  cancelled, and each running `claude` gets SIGTERM, then SIGKILL, sent to its process
  group (children are started in their own session). Finished summaries are already
  committed. The exit status is 130.

---

## 4. Configuration and filter rules

### 4.1 Files and layers

Layers from **outermost to innermost**:

1. **built-in**: the defaults compiled into `claus`.
2. **global**: `$XDG_CONFIG_HOME/claus/config.toml` (default
   `~/.config/claus/config.toml`). Overridden with `--config PATH` or turned off with
   `--no-config`.
3. **directory**: every `.claus.toml` from the filesystem root down to the file's
   directory. Outer directories come before inner ones. `--no-config` ignores these
   files as well as the global one.
4. **command line**: `--include`, `--exclude`, `--rule`, and option flags.

Scalar settings: the innermost layer that sets a key wins.
Filter rules: rules from all layers accumulate and are resolved as described in §4.3.

### 4.2 Trust

A `.claus.toml` might come from a cloned repository, and nobody has checked it. If a
directory file could set the executable or arguments, it could run arbitrary code, and
if it could raise the confirmation threshold, it could spend money without asking.
Directory-layer keys are therefore restricted by the global setting
`trust_dir_config`:

| Value | Directory files may set |
|---|---|
| `"none"` | Nothing. Directory files are ignored. |
| `"filters"` *(default)* | The `[filter]` table only. |
| `"all"` | `[filter]` and `[summary]` keys `model`, `effort`, `max_length`, `max_bytes`, `delivery`, `system_prompt`, `prompt`, `prompt_add_dir`. |

Every other key is **never** settable from a directory file, notably `claude_path`,
`extra_args`, `env`, `concurrency`, `confirm_threshold`, `timeout`, `retries`,
`budget_usd`, `[cache]`, `[listing]`, `trust_dir_config` and `builtin_privacy_rules`.
A forbidden key produces a warning and is ignored.

### 4.3 Rule syntax and semantics

```
[!|?] (+|-) type:pattern
```

| Part | Meaning |
|---|---|
| `!` | **strong**: when several strong rules match, the outermost one wins |
| *(none)* | **normal**: when several normal rules match, the innermost (or last) one wins, gitignore-style |
| `?` | **weak**: when several weak rules match, the innermost (or last) one wins; weak rules apply only when no stronger rule matches |
| `+` / `-` | include / exclude the file from summarization |
| `mime:` | glob on the MIME type, e.g. `mime:text/*` |
| `name:` | glob on the base name (`fnmatch`, case-sensitive), e.g. `name:*.min.js` |
| `iname:` | the same as `name:`, but case-insensitive |
| `ext:` | shorthand for one or more extensions: `ext:py,md` ≡ `name:*.py` + `name:*.md` |
| `path:` | literal **substring** of the absolute path, e.g. `path:/vendor/` |

**Resolution** for one file:

1. Collect every rule whose pattern matches the file.
2. Keep only the rules of the highest strength present (`!` > normal > `?`).
3. Among those rules:
   - strong: the rule from the outermost layer wins. Within one layer, the first
     listed wins.
   - normal or weak: the rule from the innermost layer wins. Within one layer, the
     last listed wins.
4. The winner's sign decides. If no rule matches, the file is **excluded**.

The result:

- A global `!` rule cannot be overridden by anything below it.
- A directory's `!` rule overrides everything in its subdirectories.
- `?` rules are fallbacks that any ordinary rule replaces.

MIME detection is skipped when the decision does not depend on it. For example, a
matching strong `name:` rule settles the result before the MIME type is needed.
Formally, the decision is settled without the MIME type when no unknown MIME rule
could both outrank the current winner and have the opposite sign.

**Symlinks** must pass the rules twice: once under the link's own name and path, and
once under the real path of the target. A link named `notes.txt` that points into
`~/.ssh/` is therefore excluded by `!-path:/.ssh/`.

`claus explain PATH` prints every matching rule (with its layer and source file), the
strength filtering, and the winner.

### 4.4 Built-in defaults

```toml
[filter]
rules = [
  # what counts as text (weak, so any config can replace it)
  "?+mime:text/*",
  "?+mime:application/json",
  "?+mime:application/xml",
  "?+mime:application/javascript",
  "?+mime:application/x-*script",   # x-shellscript, x-javascript, ...
  "?+mime:application/toml",
  "?+mime:application/yaml",
  "?+mime:application/x-yaml",
  "?+mime:application/sql",
  "?+mime:application/x-ndjson",
  # privacy: strong and outermost, so no ordinary rule can include these files
  "!-name:.env", "!-name:.env.*", "!-name:*.pem", "!-name:*.key",
  "!-name:id_rsa*", "!-name:id_ed25519*", "!-name:id_ecdsa*", "!-name:id_dsa*",
  "!-iname:*secret*", "!-iname:*credential*", "!-name:.netrc",
  "!-name:.pgpass", "!-name:*.kdbx", "!-path:/.git/", "!-path:/.ssh/",
  "!-path:/.gnupg/",
]
max_size = "16MiB"       # larger files are never hashed or summarized
skip_empty = true
```

The privacy rules can be turned off **only** in the global config, with
`builtin_privacy_rules = false`. Because they are strong and in the outermost layer,
they cannot be overridden any other way.

### 4.5 Full option reference (global `config.toml`)

```toml
trust_dir_config = "filters"         # none | filters | all
builtin_privacy_rules = true

[summary]
model = "haiku"
effort = "low"                       # low | medium | high | xhigh | max
max_length = 120                     # characters; longer output is truncated
max_bytes = 65536                    # bytes of content sent; the rest is truncated
delivery = "stdin"                   # stdin | add-dir
timeout = 120                        # seconds per claude call
retries = 2                          # extra attempts after a failed call
concurrency = 4
confirm_threshold = 25               # new generations allowed before asking; -1 = never ask
budget_usd = 0                       # stop generating after spending this much per run; 0 = no limit
system_prompt = "..."                # see §6.3
prompt = "..."                       # see §6.3; template for delivery = "stdin"
prompt_add_dir = "..."               # see §6.3; template for delivery = "add-dir"
claude_path = "claude"
extra_args = []
env = {}                             # extra environment variables for claude

[filter]
rules = []                           # appended after the built-in rules
max_size = "16MiB"
skip_empty = true

[cache]
path = "~/.cache/claus/cache.sqlite" # honors $XDG_CACHE_HOME
retention_days = 30                  # how long orphaned contents are kept

[listing]
skip_dirs = [".git", "node_modules", "__pycache__", ".venv", ".tox"]  # for -R
time_style = "ls"                    # ls | iso | relative
```

### 4.6 Settings fingerprint

`fingerprint = blake2b(model, effort, system_prompt, prompt, schema, max_length,
max_bytes, delivery)` (16 hex digits), computed for each file from its effective
config. `prompt` is `prompt_add_dir` when `delivery = "add-dir"`. A cached
summary is used only when the fingerprint matches, so changing the prompt or model
leads to regeneration. `--any-fingerprint` accepts the newest summary for the content
under any fingerprint, and generates only when none exists.

---

## 5. Cache (SQLite)

### 5.1 Schema (v1)

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);          -- schema_version

CREATE TABLE contents (                 -- one row per distinct file content
  hash        TEXT PRIMARY KEY,         -- blake2b-256 hex
  size        INTEGER NOT NULL,
  mime        TEXT,
  first_seen  REAL NOT NULL,
  orphaned_at REAL                      -- NULL while at least one path refers to it
);

CREATE TABLE summaries (
  hash        TEXT NOT NULL REFERENCES contents(hash) ON DELETE CASCADE,
  fingerprint TEXT NOT NULL,
  summary     TEXT NOT NULL,
  model       TEXT,                     -- resolved model id from the claude response
  cost_usd    REAL,
  created_at  REAL NOT NULL,
  PRIMARY KEY (hash, fingerprint)
);

CREATE TABLE paths (                    -- last known state of each seen path
  path      TEXT PRIMARY KEY,           -- absolute, symlinks not resolved
  parent    TEXT NOT NULL,              -- dirname(path), for removal detection
  dev INTEGER, ino INTEGER, size INTEGER, mtime_ns INTEGER,
  hash      TEXT REFERENCES contents(hash),
  last_seen REAL NOT NULL
);
CREATE INDEX paths_parent ON paths(parent);
CREATE INDEX paths_hash   ON paths(hash);

CREATE TABLE events (                   -- history: new/changed/moved/copied/removed
  ts REAL NOT NULL, kind TEXT NOT NULL,
  path TEXT NOT NULL, other_path TEXT, hash TEXT
);
CREATE INDEX events_path ON events(path);
```

### 5.2 Lookup flow (one regular file)

1. `stat` the file. If a `paths` row exists and `(dev, ino, size, mtime_ns)` match,
   the file is **unchanged**: reuse its `hash` and the `contents.mime` without reading
   the file.
2. Otherwise, apply the cheap rules (name, path) and the size limit. If the result is
   still undecided, or it is an include, detect the MIME type and resolve the rules.
3. If the file is included and has no hash yet, hash it.
4. Upsert the `paths` row and the `contents` row, setting `orphaned_at = NULL`.
   Record events:
   - **changed**: the path existed with a different hash.
   - **copied**: the hash is already referenced by another path that still exists.
   - **moved**: the hash is referenced by a path that no longer exists. That old
     `paths` row is deleted.
   - **new**: none of the above.
5. Look up `summaries(hash, fingerprint)`. If found, show it. If not, queue a
   generation job.

Hashing happens only for **included** files. Excluded files are only stat'ed (and
MIME-detected if a rule needs it), and do not get `paths` rows.

### 5.3 Removal, orphaning and garbage collection

- **During a listing**: after every file in a directory has been classified, the
  `paths` rows with `parent = dir` are reconciled:
  - The file is gone from the directory: the row is deleted and a **removed** event is
    logged.
  - The file is present, but this scan classified it as excluded (for example, after
    the rules changed): the row is deleted silently.
  - The file is present but was not classified (for example, a hidden file in a
    listing without `-a`): the row is kept.

  Any `contents` row left with no paths gets `orphaned_at = now`.
- **`claus gc`**: stat every `paths` row and delete the rows for missing files, as
  above. Then purge `contents` (and, by cascade, `summaries`) where
  `orphaned_at < now - retention_days`. Also purge `events` older than
  `retention_days * 4`. Finish with `VACUUM` when `--vacuum` is given.
- **Opportunistic GC**: at most once a day (tracked in `meta`), a normal run purges
  expired orphans. It does not stat the paths.

---

## 6. Summary generation

### 6.1 Delivery modes

- **`stdin`** *(default)*: the file contents (the first `max_bytes`, decoded as
  UTF-8 with `errors="replace"`) are piped to `claude` on stdin. The prompt argument
  carries the instructions. `--tools ""` means the model has **no** tools and no
  filesystem access, and the call takes a single model step plus the structured-output
  step.
- **`add-dir`**: `--add-dir <parent> --tools Read --allowedTools Read
  --permission-prompts none`. The prompt names the absolute path. This costs one more
  turn and grants read access to the parent directory. It is useful for files whose
  `max_bytes` head is not representative, since the model can page through the file.

### 6.2 Command line

```
claude -p <prompt>
  --model <model> --effort <effort>
  --output-format json --json-schema <schema>
  --system-prompt <system_prompt>
  --tools ""                      # stdin mode
  --no-session-persistence
  --setting-sources ""            # no user/project/local settings, hooks, plugins
  --strict-mcp-config             # no MCP servers
  --disable-slash-commands        # no skills/commands
  [extra_args...]
```

- `cwd` is an empty private directory under the cache dir (`…/claus/workdir`), so no
  `CLAUDE.md` or project settings are found.
- `env` is the inherited environment plus `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`
  (unless already set) plus `config.env`.
- stdout is parsed as one JSON object. The summary comes from
  `structured_output.summary`, with `json.loads(result)["summary"]` as a fallback.
- Errors are detected from `is_error`, `subtype != "success"`, `api_error_status`, a
  non-zero exit code, or a timeout.

Schema (`N` is `max_length`):
```json
{"type":"object","properties":{"summary":{"type":"string",
  "description":"One line, plain text, at most N characters."}},
 "required":["summary"],"additionalProperties":false}
```

Post-processing: collapse whitespace, strip one pair of surrounding quotes, and
truncate to `max_length` with `…`.

### 6.3 Default prompts (templates)

Placeholders: `{name}`, `{path}`, `{mime}`, `{size}`, `{max_length}`, `{truncated}`.

- **system**: "You write one-line summaries of files for a directory listing. Describe
  what the file is, does, or contains, as specifically as possible. Plain text, never
  more than {max_length} characters; aim for about two thirds of that. Do not start
  with 'This file', and do not repeat the file name."
- **prompt (stdin)**: "Summarize the file provided on stdin. File name (hint only, the
  file may be renamed): {name}. MIME type: {mime}. {truncated} The summary must fit in
  {max_length} characters."
- **prompt (add-dir)**: "Read and summarize the file at {path}. MIME type: {mime}. The
  summary must fit in {max_length} characters."

`{truncated}` expands to "Only the first N of M bytes are shown." or to nothing.
Unknown placeholders are left as they are. A template that `str.format` cannot parse
(for example, one with a stray `{`) is used verbatim. The
summary should describe the content only, because it is shared by every path with the
same hash.

### 6.4 Failure handling

- **Retries**: up to `retries` times with exponential backoff and jitter, for
  timeouts, non-zero exits and API errors.
- **Rate limits and overload** (429/529, or matching error text): trigger the global
  backoff described in §3.2.
- **Failures are not cached.** The row shows `! <short error>`, and the exit status
  becomes 1.
- **Fatal errors** stop all generation for the rest of the run. One warning is shown,
  and the remaining files are marked *skipped*. Fatal errors are:
  - `claude` not found
  - a usage limit reached, or not being logged in
  - the run budget (`budget_usd`) used up
- **File changed** between classification and generation: the file is skipped and not
  cached under a stale hash.

---

## 7. Pipeline and confirmation gate

1. The **scanner** lists each directory with `scandir` in a thread and sorts the
   entries (§9). Every entry is sent to the renderer at once, with its `ls -l`
   columns, under a stable row index.
2. **Classifier** tasks (bounded by a semaphore, default 64) run §5.2 for each
   regular file and emit `summary=<cached>`, `summary=<pending>` or `summary=<none>`.
3. **Generation jobs** go into the queue. A **confirmation gate** lets the first
   `confirm_threshold` jobs through at once. If more are needed, the remaining jobs
   are held until the scan finishes. Then the user is asked once: "N more summaries
   needed (M already running). Continue? [y/N]". On a TTY the rich Live display
   pauses for the prompt.
   - `-y/--yes` skips the prompt.
   - `--no-generate` sends no jobs at all and shows only cached summaries.
   - With no TTY on stdin and no `-y`, held jobs are skipped with a warning, so
     scripts never hang.
4. **Workers** generate summaries, write them to the cache through the DB actor, and
   send updates to the renderer.
5. The run ends when the scan, the classifiers and the queue have all drained.

`--dry-run` stops after classification and writes nothing to the cache. It shows
which files would be summarized and why (the winning rule), and prints the number of
files on stderr.

---

## 8. Output

| Mode | When | Behavior |
|---|---|---|
| **rich live** | stdout is a TTY, `rich` is installed, no `--plain`/`--stream` | Rows are printed permanently, in order, as soon as they and every row above them are final. Rows after that point are shown in a live region at the bottom, with `·` for pending, a spinner while generating, and a footer with progress. Lines are truncated to the terminal width. |
| **plain tty** | stdout is a TTY, and `rich` is missing or `--plain` is given | Progress counter on stderr, then plain aligned output when done, truncated to the terminal width. |
| **plain ordered** | stdout is not a TTY *(default)* | Waits, then prints complete rows in listing order. Summaries are never truncated. |
| **plain stream** | `--stream` | Prints each row as soon as it is final, so the order may differ. Rows show the path (`dir/name`) instead of the name, with no headers. |
| **jsonl** | `--json` | One JSON object per entry: all the stat fields, mime, hash, summary, status. Ordered unless `--stream`. |

Row format: `<mode> <nlink> <owner> <group> <size> <mtime> <name>[ -> target]  <summary>`.
Within a block, summaries line up in a column after the longest name (padding is
capped at 40 columns). Under `--color=auto|always|never` (`auto` honors `NO_COLOR`),
names are colored by type (directory, symlink, executable), summaries are dimmed and
errors are red. Control characters in names are shown as `?`, like `ls -q`.

---

## 9. Command-line interface (v0)

```
claus [ls] [OPTIONS] [PATH...]                       # default command
claus explain [--include/--exclude/--rule R] PATH... # rule trace
claus gc [--vacuum] [--dry-run] [--retention-days D] # cache maintenance
claus info [--json] PATH...                          # cache record: hash, other paths, events, summaries
claus config [--paths] [DIR]                         # effective merged config (+ source files)
```

Every command also accepts `--config PATH`, `--no-config`, `--cache PATH`, `-v`,
`--help` and `--version`. `-h` means human-readable sizes, as in `ls`, so help is
`--help` only.

A first argument equal to a subcommand name is treated as that subcommand. To list a
file named `gc`, use `claus ls gc` or `claus ./gc`.

`ls` options:

| Option | Meaning |
|---|---|
| `-a` / `-A` | include hidden entries (with / without `.` and `..`) |
| `-R`, `--depth N` | recurse, optionally to depth N; `skip_dirs` is honored |
| `-h` | human-readable sizes |
| `-t`, `-S`, `-r` | sort by mtime, by size, reverse |
| `--time-style ls\|iso\|relative` | timestamp format |
| `--include RULE`, `--exclude RULE`, `--rule RULE` | command-line rules (`--include X` ≡ `--rule +X`) |
| `--model`, `--effort`, `--max-length`, `--max-bytes`, `--prompt`, `--system-prompt`, `--delivery` | summary settings (`--prompt` sets both prompt templates) |
| `-j/--jobs N` | concurrency |
| `--budget USD`, `--confirm-threshold N` | spending limits for this run |
| `-y/--yes`, `--no-generate`, `--dry-run` | generation control |
| `--any-fingerprint`, `--refresh` | cache control (`--refresh` regenerates even when cached) |
| `--stream`, `--json`, `--color`, `--plain` | output |
| `--config PATH`, `--no-config`, `--cache PATH`, `-v/--verbose` | misc |

Exit codes follow `ls`: 0 ok, 1 minor problems (some summaries failed, unreadable
files), 2 serious trouble (bad arguments, inaccessible path argument).

---

## 10. Packaging and dependencies

- Python ≥ 3.11 (`tomllib`, `asyncio.TaskGroup`). `src/` layout, `pyproject.toml`
  built with hatchling, entry point `claus = claus.cli:main`, `uv.lock` committed.
- No required dependencies. Optional extras:
  - `claus[magic]` → `python-magic`, which needs the `libmagic1` system library.
  - `claus[rich]` → `rich`.
  - `claus[all]` → both.
- Both `magic` modules are supported: `python-magic` (one handle per pool thread,
  since libmagic handles are not thread-safe) and the official `file-magic`.
- Fallbacks: MIME detection runs `file -b -L --mime-type -- a b c` through
  `asyncio.create_subprocess_exec`, batching concurrent requests (up to 64 paths, or
  whatever arrives within 5 ms). Without `rich`, output is plain (§8).
- `textual` is not used in v0. A TUI browser is a later option.

## 11. Testing

- `pytest` and `pytest-asyncio`.
- A **fake `claude`** script (`tests/fake_claude.py`, selected with `claude_path`)
  that returns canned JSON, errors, fatal usage-limit errors, slow responses and rate
  limits, so tests never spend money. An autouse fixture points `XDG_CONFIG_HOME` and
  `XDG_CACHE_HOME` at temporary directories.
- Unit tests: rule resolution (strength, layer order, ties), config layering and
  trust, `lsfmt`, cache events (move, copy, remove, orphan expiry, with a fake clock).
- Integration tests: a tmp tree → `claus --json` → assertions. One opt-in live test,
  `CLAUS_LIVE=1`, against the real `claude`.

## 12. Future work

- **Persistent workers**: keep `concurrency` long-lived `claude -p --input-format
  stream-json` processes and feed files as successive turns. This removes the ~6 s
  start-up per file. Context growth is a risk: restart each process after N files or
  run `/clear`.
- **Batching**: several small files per call, with an array schema.
- A `textual` TUI with navigation and a regenerate key.
- `--watch` mode.
- ~~Per-run budget caps~~: done in v0.1.0 (`budget_usd`, `--budget`). It is
  enforced by claus between calls, using the cost each `claude` result reports.

---

## 13. Development workflow

Every change to the codebase also updates, in the same commit:

- `CHANGELOG.md`: what changed, plus any **deviation** from this design, and why.
- `TODO.md`: tick, add or re-scope tasks.
- `README.md`, `INSTALL.md`, `USAGE.md`: whenever user-visible behavior, install
  steps or options change.
- `DESIGN.md`: when the design itself changes. Mark sections *(open)* or
  *(superseded)* as needed.
