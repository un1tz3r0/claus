# Installing claus

> **Status:** pre-alpha. These are the planned install steps. Nothing has been
> published to PyPI yet.

## Requirements

| Requirement | Why | Notes |
|---|---|---|
| Python ≥ 3.11 | runtime | Uses `tomllib` and `asyncio.TaskGroup`. |
| [`claude`](https://claude.com/claude-code) CLI, logged in | generating summaries | Subscription (OAuth) or `ANTHROPIC_API_KEY` both work. Without it, claus still lists files and shows cached summaries. |
| `libmagic` (e.g. Debian/Ubuntu `libmagic1`) | MIME detection with `python-magic` | Optional. Without it, claus runs the `file` command. |
| `file` command | MIME detection fallback | Preinstalled on most Linux and macOS systems. |

## Install from GitHub

With [uv](https://docs.astral.sh/uv/) (recommended):

```sh
uv tool install 'claus[all] @ git+https://github.com/un1tz3r0/claus'
```

With pipx:

```sh
pipx install 'claus[all] @ git+https://github.com/un1tz3r0/claus'
```

### Optional extras

| Extra | Adds |
|---|---|
| `magic` | `python-magic`: in-process libmagic bindings. Faster than starting `file`. |
| `rich` | `rich`: a live-updating table on terminals. |
| `all` | Both of the above. |

Installing without extras gives a fully working tool with plain output that runs the
`file` command.

## Check the installation

```sh
claus --version
claude --version          # must be on PATH, or set summary.claude_path
claus --dry-run .         # shows what would be summarized without calling claude
```

## Development setup

```sh
git clone https://github.com/un1tz3r0/claus
cd claus
uv sync --all-extras      # creates .venv with dev dependencies
uv run pytest             # tests use a fake claude, so they cost nothing
uv run claus .
```
