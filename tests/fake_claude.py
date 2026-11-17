#!/usr/bin/env python3
"""Stand-in for the `claude` CLI used by the tests. Never contacts any service.

Environment:
  FAKE_CLAUDE_MODE   ok | error | fatal | ratelimit-once | slow | badjson   (default ok)
  FAKE_CLAUDE_LOG    file to append one JSON line per call (argv + stdin length)
  FAKE_CLAUDE_STATE  file used by ratelimit-once to remember the first call
  FAKE_CLAUDE_SLEEP  seconds to sleep in slow mode (default 5)
"""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
data = sys.stdin.read()
argv = sys.argv[1:]
if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
        f.write(json.dumps({"argv": argv, "stdin_len": len(data), "cwd": os.getcwd()}) + "\n")


def result(**kw):
    base = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2,
            "total_cost_usd": 0.0003, "modelUsage": {"claude-fake-1": {"costUSD": 0.0003}}}
    base.update(kw)
    print(json.dumps(base))


if mode == "slow":
    time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "5")))
if mode == "badjson":
    print("this is not json")
    print("fatal: something broke", file=sys.stderr)
    sys.exit(1)
if mode == "error":
    result(subtype="error_during_execution", is_error=True, result="boom")
    sys.exit(1)
if mode == "fatal":
    result(is_error=True, result="Claude AI usage limit reached|1700000000")
    sys.exit(1)
if mode == "ratelimit-once":
    state = os.environ["FAKE_CLAUDE_STATE"]
    if not os.path.exists(state):
        open(state, "w").close()
        result(is_error=True, result="API Error: 429 rate limited", api_error_status=429)
        sys.exit(1)

first = next((ln.strip() for ln in data.splitlines() if ln.strip()), "(empty)")
summary = "fake: " + first[:60]
result(result=json.dumps({"summary": summary}), structured_output={"summary": summary})
