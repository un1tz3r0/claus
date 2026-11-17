"""Summary generation via the ``claude`` CLI (DESIGN §6)."""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import signal
import time
from dataclasses import dataclass
from typing import Any

from claus.config import summary_schema
from claus.hashing import read_head

_RATE_RE = re.compile(r"rate.?limit|overloaded|too many requests|\b429\b|\b529\b", re.I)
_FATAL_RE = re.compile(
    r"usage limit|limit reached|not logged in|please run /login|invalid api key|authentication|"
    r"credit balance",
    re.I,
)


class SummaryError(Exception):
    def __init__(self, message: str, *, retryable: bool = True, rate_limited: bool = False,
                 fatal: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable and not fatal
        self.rate_limited = rate_limited
        self.fatal = fatal


class ClaudeMissing(SummaryError):
    def __init__(self, path: str) -> None:
        super().__init__(f"claude executable not found: {path}", retryable=False, fatal=True)


class BudgetExceeded(SummaryError):
    def __init__(self, budget: float) -> None:
        super().__init__(f"run budget of ${budget:.4f} reached", retryable=False, fatal=True)


@dataclass
class Job:
    path: str
    name: str
    mime: str | None
    size: int
    hash: str
    fingerprint: str
    cfg: dict  # effective [summary] settings
    mtime_ns: int = 0


@dataclass
class Result:
    summary: str
    model: str | None = None
    cost_usd: float | None = None


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(template: str, values: dict[str, Any]) -> str:
    try:
        return template.format_map(_SafeDict(values))
    except (ValueError, IndexError, AttributeError):
        return template


def build_prompts(cfg: dict, job: Job, shown: int) -> tuple[str, str]:
    truncated = (
        f"Only the first {shown} of {job.size} bytes are shown." if shown < job.size else ""
    )
    values = {
        "name": job.name, "path": job.path, "mime": job.mime or "unknown", "size": job.size,
        "max_length": cfg["max_length"], "truncated": truncated,
    }
    template = cfg["prompt_add_dir"] if cfg["delivery"] == "add-dir" else cfg["prompt"]
    prompt = " ".join(render_template(template, values).split())
    system = render_template(cfg["system_prompt"], values)
    return prompt, system


def build_command(cfg: dict, prompt: str, system: str, add_dir: str | None) -> list[str]:
    # The prompt goes right after -p: later options such as --tools and --add-dir are
    # variadic and would swallow a trailing positional argument.
    cmd = [
        cfg["claude_path"], "-p", prompt,
        "--model", cfg["model"],
        "--effort", cfg["effort"],
        "--output-format", "json",
        "--json-schema", json.dumps(summary_schema(int(cfg["max_length"])), separators=(",", ":")),
        "--system-prompt", system,
        "--no-session-persistence",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
    ]
    if add_dir:
        cmd += ["--add-dir", add_dir, "--tools", "Read", "--allowedTools", "Read",
                "--permission-prompts", "none"]
    else:
        cmd += ["--tools", ""]
    cmd += [str(a) for a in cfg.get("extra_args", [])]
    return cmd


def postprocess(text: str, max_length: int) -> str:
    s = " ".join(str(text).split())
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'`":
        s = s[1:-1].strip()
    if max_length > 0 and len(s) > max_length:
        s = s[: max_length - 1].rstrip() + "…"
    return s


def classify_error(text: str, status: Any = None) -> SummaryError:
    msg = " ".join(str(text).split())[:300] or "unknown error"
    if _FATAL_RE.search(msg):
        return SummaryError(msg, fatal=True)
    rate = status in (429, 529, "429", "529") or bool(_RATE_RE.search(msg))
    return SummaryError(msg, rate_limited=rate)


def parse_result(obj: dict, max_length: int) -> Result:
    """Turn one ``claude --output-format json`` result object into a :class:`Result`."""
    if obj.get("is_error") or obj.get("subtype", "success") != "success":
        raise classify_error(obj.get("result") or obj.get("subtype") or "error", obj.get("api_error_status"))
    summary = None
    so = obj.get("structured_output")
    if isinstance(so, dict) and isinstance(so.get("summary"), str):
        summary = so["summary"]
    if summary is None:
        res = obj.get("result")
        if isinstance(res, str):
            try:
                parsed = json.loads(res)
                if isinstance(parsed, dict) and isinstance(parsed.get("summary"), str):
                    summary = parsed["summary"]
            except json.JSONDecodeError:
                summary = res
    if not summary or not summary.strip():
        raise SummaryError("empty summary in claude output")
    usage = obj.get("modelUsage")
    model = next(iter(usage)) if isinstance(usage, dict) and usage else None
    cost = obj.get("total_cost_usd")
    return Result(postprocess(summary, max_length), model, float(cost) if cost is not None else None)


def parse_output(stdout: bytes, stderr: bytes, returncode: int | None, max_length: int) -> Result:
    text = stdout.decode(errors="replace").strip()
    obj = None
    if text:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            # tolerate leading noise: take the last JSON object line
            for line in reversed(text.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        obj = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
    if not isinstance(obj, dict):
        err = stderr.decode(errors="replace").strip() or text or f"exit status {returncode}"
        raise classify_error(err.splitlines()[-1] if err else err)
    return parse_result(obj, max_length)


def default_env(cfg: dict) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    env.update({str(k): str(v) for k, v in cfg.get("env", {}).items()})
    return env


async def kill_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(proc.wait(), wait)
            return
        except asyncio.TimeoutError:
            continue


def read_content(job: Job) -> tuple[bytes, int]:
    """Read the head of the file; raise if it changed since it was classified."""
    st = os.stat(job.path)
    if job.mtime_ns and (st.st_mtime_ns != job.mtime_ns or st.st_size != job.size):
        raise SummaryError("file changed during run; skipped", retryable=False)
    data, _ = read_head(job.path, int(job.cfg["max_bytes"]))
    return data.decode("utf-8", errors="replace").encode("utf-8"), len(data)


class Summarizer:
    """Runs one-shot ``claude -p`` processes with retries, backoff and a run budget."""

    def __init__(self, workdir: str, budget_usd: float = 0.0, backoff_scale: float = 1.0) -> None:
        self.workdir = workdir
        self.backoff_scale = backoff_scale
        os.makedirs(workdir, exist_ok=True)
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.calls = 0
        self._pause_until = 0.0
        self.fatal: SummaryError | None = None

    def _account(self, result: Result) -> None:
        self.calls += 1
        if result.cost_usd:
            self.spent_usd += result.cost_usd

    def check_budget(self) -> None:
        if self.fatal:
            raise self.fatal
        if self.budget_usd > 0 and self.spent_usd >= self.budget_usd:
            self.fatal = BudgetExceeded(self.budget_usd)
            raise self.fatal

    async def _wait_pause(self) -> None:
        while (delay := self._pause_until - time.monotonic()) > 0:
            await asyncio.sleep(delay)

    def _pause(self, seconds: float) -> None:
        self._pause_until = max(self._pause_until, time.monotonic() + seconds)

    async def summarize(self, job: Job) -> Result:
        retries = int(job.cfg.get("retries", 2))
        attempt = 0
        while True:
            self.check_budget()
            await self._wait_pause()
            try:
                result = await self._attempt(job)
                self._account(result)
                return result
            except SummaryError as e:
                if e.fatal:
                    self.fatal = self.fatal or e
                    raise
                if not e.retryable or attempt >= retries:
                    raise
                delay = min(60.0, 2.0 ** attempt * (4.0 if e.rate_limited else 1.0)) * (0.5 + random.random())
                delay *= self.backoff_scale
                if e.rate_limited:
                    self._pause(delay)
                else:
                    await asyncio.sleep(delay)
                attempt += 1

    async def _attempt(self, job: Job) -> Result:
        cfg = job.cfg
        add_dir = None
        if cfg["delivery"] == "add-dir":
            data, shown = b"", job.size
            add_dir = os.path.dirname(job.path)
        else:
            data, shown = await asyncio.to_thread(read_content, job)
        prompt, system = build_prompts(cfg, job, shown)
        cmd = build_command(cfg, prompt, system, add_dir)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=self.workdir, env=default_env(cfg), start_new_session=True,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise ClaudeMissing(cfg["claude_path"]) from e
        except PermissionError as e:
            raise SummaryError(f"cannot run {cfg['claude_path']}: {e}", fatal=True) from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(data), float(cfg["timeout"]))
        except asyncio.TimeoutError:
            await kill_process(proc)
            raise SummaryError(f"timed out after {cfg['timeout']}s") from None
        except BaseException:
            await kill_process(proc)
            raise
        return parse_output(out, err, proc.returncode, int(cfg["max_length"]))
