# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

Each entry may include a **Deviations** subsection. It records places where the
implementation differs from the original request or from `DESIGN.md`, and why.

## [Unreleased]

### Added
- Packaging: `pyproject.toml` (hatchling, `src/` layout), extras `magic`, `rich`
  and `all`, a `dev` dependency group (pytest, pytest-asyncio), a `claus` console
  script, and `uv.lock`.
- Package skeleton: `claus/__init__.py` (version), `__main__.py`, and a placeholder
  `cli.py` that supports only `--version`.
- Project documents: `DESIGN.md`, `TODO.md`, `CHANGELOG.md`, `README.md`,
  `INSTALL.md`, `USAGE.md`, and `LICENSE` (MIT).
- A git repository and a public GitHub remote.

### Deviations (from the original request)
- **File delivery: stdin instead of `claude --file`.** `--file` takes
  `file_id:path` specs for files already uploaded to the Anthropic Files API, so it
  cannot send local files. The default is to pipe the contents on stdin with all tools
  disabled. The requested `--add-dir` approach is available as `delivery = "add-dir"`.
  (DESIGN §2, §6.1)
- **`--bare` is not used.** It ignores OAuth/subscription logins. Features are turned
  off with separate flags instead (DESIGN §6.2).
- **Wider default MIME filter.** The default includes `text/*` plus common text-like
  `application/*` types (JSON, XML, JS, scripts, TOML, YAML, SQL), because libmagic
  does not report these as `text/*`. All defaults are weak (`?`) rules that any
  config can replace (DESIGN §4.4).
- **Restricted directory config.** For safety, per-directory `.claus.toml` files
  can set only filters by default (DESIGN §4.2).
- **Built-in privacy exclusions.** Strong exclusions for secret-like files
  (`.env`, keys, `.ssh/`, …) can be turned off only in the global config
  (DESIGN §4.4).
