# Installing claus

## Requirements

| Requirement | Why | Notes |
|---|---|---|
| Python ≥ 3.11 on Linux or macOS | runtime | Uses `tomllib` and `asyncio.TaskGroup`, and POSIX process groups and user/group lookup. Windows is not supported. |
| [`claude`](https://claude.com/claude-code) CLI, logged in | generating summaries | Subscription (OAuth) or `ANTHROPIC_API_KEY` both work. Without it, claus still lists files and shows cached summaries, with one warning. |
| `libmagic` (e.g. Debian/Ubuntu `libmagic1`, macOS `brew install libmagic`) | MIME detection with `python-magic` | Optional. Without it, claus runs the `file` command. |
| `file` command | MIME detection fallback | Preinstalled on most Linux and macOS systems. Without both libmagic and `file`, only name and path rules can include files. |

claus is not on PyPI yet. Install it from GitHub.

## Install

With [uv](https://docs.astral.sh/uv/) (recommended):

```sh
uv tool install 'claus[all] @ git+https://github.com/un1tz3r0/claus'
```

With pipx:

```sh
pipx install 'claus[all] @ git+https://github.com/un1tz3r0/claus'
```

To upgrade: `uv tool upgrade claus`, or `pipx upgrade claus`.

### Optional extras

| Extra | Adds |
|---|---|
| `magic` | `python-magic`: in-process libmagic bindings. Faster than starting `file`. (The official `file-magic` bindings work too, if you install them yourself.) |
| `rich` | `rich`: the live-updating display on terminals. |
| `all` | Both of the above. |

Installing without extras gives a fully working tool with plain output that runs the
`file` command.

## Check the installation

```sh
claus --version
claude --version          # must be on PATH, or set summary.claude_path in the global config
claus config --paths      # where the config and cache files live
claus --dry-run .         # shows what would be summarized without calling claude
claus -v -h .             # -v also reports the MIME backend, the cache path and the cost
```

## Development setup

```sh
git clone https://github.com/un1tz3r0/claus
cd claus
uv sync --all-extras                        # creates .venv with dev dependencies
uv run pytest                               # tests use a fake claude, so they cost nothing
CLAUS_LIVE=1 uv run pytest tests/test_live.py   # one real claude call (a fraction of a cent)
uv run claus .
```
