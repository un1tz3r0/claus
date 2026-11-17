from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from claus.cli import main

FAKE = str(Path(__file__).with_name("fake_claude.py"))


@dataclass
class Env:
    root: Path  # tree to list
    home: Path  # config / cache / logs live here
    config: Path
    cache: Path
    log: Path

    def write_config(self, text: str = "", **env: str) -> None:
        env = {"FAKE_CLAUDE_LOG": str(self.log), **env}
        env_toml = ", ".join(f'{k} = {json.dumps(v)}' for k, v in env.items())
        self.config.write_text(
            f'[summary]\nclaude_path = {json.dumps(FAKE)}\nenv = {{ {env_toml} }}\n' + text
        )

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run(self, capsys, *args: str) -> tuple[int, str, str]:
        code = main([*args, "--config", str(self.config), "--cache", str(self.cache)])
        out, err = capsys.readouterr()
        return code, out, err

    def ls_json(self, capsys, *args: str) -> tuple[int, dict[str, dict], str]:
        code, out, err = self.run(capsys, "--json", *args)
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
        return code, {r["path"]: r for r in rows}, err


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch) -> None:
    """Never read the real ~/.config/claus or write the real ~/.cache/claus."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> Env:
    root = tmp_path / "tree"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    monkeypatch.chdir(root)
    e = Env(root, home, home / "config.toml", home / "cache.sqlite", home / "calls.log")
    e.write_config()
    return e


def write(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)
    return path


@pytest.fixture
def tree(env: Env) -> Env:
    write(env.root / "notes.txt", "shopping list\nmilk\n")
    write(env.root / "script.py", "print('hello')\n")
    write(env.root / "data.json", '{"a": 1}\n')
    write(env.root / "image.bin", bytes(range(256)) * 4)
    write(env.root / "empty.txt", "")
    write(env.root / ".env", "SECRET=1\n")
    write(env.root / "sub" / "deep.md", "# Deep\n")
    os.utime(env.root / "notes.txt", (1_700_000_000, 1_700_000_000))
    return env
