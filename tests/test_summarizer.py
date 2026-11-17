import json

import pytest

from claus.config import DEFAULTS
from claus.summarizer import (Job, Summarizer, SummaryError, build_command, build_prompts,
                              classify_error, parse_output, postprocess)
from tests.conftest import FAKE


def cfg(**kw):
    c = dict(DEFAULTS["summary"])
    c.update(kw)
    return c


def test_postprocess():
    assert postprocess('  "hello\n world"  ', 100) == "hello world"
    assert postprocess("x" * 50, 10) == "x" * 9 + "…"


def test_build_command_stdin_and_add_dir():
    cmd = build_command(cfg(), "PROMPT", "SYS", None)
    assert cmd[:3] == ["claude", "-p", "PROMPT"]
    for flag in ("--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert json.loads(cmd[cmd.index("--json-schema") + 1])["required"] == ["summary"]
    cmd = build_command(cfg(delivery="add-dir"), "P", "S", "/some/dir")
    assert cmd[cmd.index("--add-dir") + 1] == "/some/dir"
    assert cmd[cmd.index("--tools") + 1] == "Read"


def test_prompts_and_truncation_note():
    job = Job(path="/x/a.py", name="a.py", mime="text/x-python", size=100_000, hash="h",
              fingerprint="f", cfg=cfg())
    prompt, system = build_prompts(job.cfg, job, 65536)
    assert "a.py" in prompt and "text/x-python" in prompt and "65536 of 100000" in prompt
    assert "120 characters" in system
    # unknown placeholders and stray braces survive
    c = cfg(prompt="{name} {nope} {")
    assert build_prompts(c, job, 10)[0] == "{name} {nope} {"
    c = cfg(prompt="{name} {nope}")
    assert build_prompts(c, job, 10)[0] == "a.py {nope}"


def test_parse_output_variants():
    ok = {"subtype": "success", "is_error": False, "structured_output": {"summary": "S"},
          "total_cost_usd": 0.5, "modelUsage": {"claude-x": {}}}
    r = parse_output(json.dumps(ok).encode(), b"", 0, 100)
    assert (r.summary, r.model, r.cost_usd) == ("S", "claude-x", 0.5)
    fallback = {"subtype": "success", "result": json.dumps({"summary": "R"})}
    assert parse_output(json.dumps(fallback).encode(), b"", 0, 100).summary == "R"
    noisy = b"warning: blah\n" + json.dumps(ok).encode()
    assert parse_output(noisy, b"", 0, 100).summary == "S"
    with pytest.raises(SummaryError):
        parse_output(b"garbage", b"oops", 1, 100)
    err = {"subtype": "success", "is_error": True, "result": "API Error: 529 overloaded"}
    with pytest.raises(SummaryError) as ei:
        parse_output(json.dumps(err).encode(), b"", 1, 100)
    assert ei.value.rate_limited


def test_classify_error():
    assert classify_error("Claude AI usage limit reached").fatal
    assert classify_error("Not logged in · Please run /login").fatal
    assert classify_error("rate_limit_error").rate_limited
    e = classify_error("something odd")
    assert e.retryable and not e.fatal and not e.rate_limited


def _job(tmp_path, **kw):
    f = tmp_path / "f.txt"
    f.write_text("first line\nsecond\n")
    c = cfg(**{"claude_path": FAKE, **kw})
    return Job(path=str(f), name="f.txt", mime="text/plain", size=f.stat().st_size, hash="h",
               fingerprint="fp", cfg=c, mtime_ns=f.stat().st_mtime_ns)


async def test_summarizer_ok_and_budget(tmp_path):
    s = Summarizer(str(tmp_path / "wd"), budget_usd=0.0005)
    job = _job(tmp_path)
    r = await s.summarize(job)
    assert r.summary == "fake: first line"
    await s.summarize(job)
    with pytest.raises(SummaryError) as ei:
        await s.summarize(job)
    assert ei.value.fatal and "budget" in ei.value.message


async def test_summarizer_retries_rate_limit(tmp_path):
    state = tmp_path / "state"
    s = Summarizer(str(tmp_path / "wd"), backoff_scale=0.001)
    job = _job(tmp_path, env={"FAKE_CLAUDE_MODE": "ratelimit-once", "FAKE_CLAUDE_STATE": str(state)})
    r = await s.summarize(job)
    assert r.summary.startswith("fake:") and state.exists()


async def test_summarizer_timeout_and_missing(tmp_path):
    s = Summarizer(str(tmp_path / "wd"), backoff_scale=0.001)
    job = _job(tmp_path, timeout=0.5, retries=0,
               env={"FAKE_CLAUDE_MODE": "slow", "FAKE_CLAUDE_SLEEP": "30"})
    with pytest.raises(SummaryError) as ei:
        await s.summarize(job)
    assert "timed out" in ei.value.message
    job = _job(tmp_path, claude_path=str(tmp_path / "no-such-claude"))
    with pytest.raises(SummaryError) as ei:
        await Summarizer(str(tmp_path / "wd")).summarize(job)
    assert ei.value.fatal


async def test_file_changed_during_run(tmp_path):
    job = _job(tmp_path)
    (tmp_path / "f.txt").write_text("changed content, different size\n")
    with pytest.raises(SummaryError) as ei:
        await Summarizer(str(tmp_path / "wd")).summarize(job)
    assert "changed" in ei.value.message
