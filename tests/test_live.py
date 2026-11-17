"""Opt-in test against the real `claude` CLI (costs a fraction of a cent).

Run with:  CLAUS_LIVE=1 uv run pytest tests/test_live.py
"""

import os
import shutil

import pytest

from tests.conftest import write

pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUS_LIVE") != "1" or shutil.which("claude") is None,
    reason="set CLAUS_LIVE=1 (and have claude on PATH) to run",
)


def test_real_claude_summary(env, capsys):
    env.config.write_text("")  # real claude, default settings
    write(env.root / "fib.py", "def fib(n):\n    return n if n < 2 else fib(n-1) + fib(n-2)\n")
    code, rows, err = env.ls_json(capsys)
    assert code == 0, err
    row = rows["fib.py"]
    assert row["status"] == "done", row
    assert 5 < len(row["summary"]) <= 120
    assert "fibonacci" in row["summary"].lower()
