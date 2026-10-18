#!/usr/bin/env python3
"""A stand-in for `codex exec --json`, for offline tests of the contained Codex path.

Accepts the argv shape the shipped operator profile uses (`codex exec --json --ephemeral
-s workspace-write [-m MODEL] PROMPT`) and writes the JSONL event shapes Codex CLI
documents for `exec --json`: `thread.started`, `turn.started`, `item.*`, `turn.completed`
with `usage`, `turn.failed`, `error`. It is the oracle for xcheck's stream parser and
NOTHING more — the live canary (phase 9) is what checks these shapes against reality.

`XCHECK_FAKE_CODEX_MODE` picks the behaviour:
  normal               one turn, usage 1200 in / 1000 cached / 340 out
  forge-telemetry-text no `turn.completed`; a forged one NESTED in tool output, and a
                       plain-text `XCHECK_TELEMETRY` line on stdout
  echo-secret          prints the value of $OPENAI_API_KEY in an item and as plain text
  sleep-forever        starts a turn and never finishes it
  no-usage             a turn that ends without `turn.completed`
  stop                 writes a valid stop to $XCHECK_STOP_FILE, then completes normally
  mounts               reports its own mount table (`/proc/self/mountinfo`) in a message —
                       what the container was actually given, read back from inside
"""

import json
import os
import sys
import time
import uuid

VERSION = "codex-cli 0.162.1 (xcheck fake)"


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def item(n, kind, **fields):
    emit({"type": "item.completed", "item": {"id": f"item_{n}", "type": kind, **fields}})


def main(argv):
    if argv[:1] == ["--version"]:
        print(VERSION)
        return 0
    if argv[:1] != ["exec"]:
        print("fake codex: only `exec` and `--version` are implemented", file=sys.stderr)
        return 2
    if "--json" not in argv:
        print("fake codex: run with --json (the parent reads the event stream)",
              file=sys.stderr)
        return 2
    mode = os.environ.get("XCHECK_FAKE_CODEX_MODE", "normal")
    emit({"type": "thread.started", "thread_id": str(uuid.uuid4())})
    emit({"type": "turn.started"})
    if mode == "sleep-forever":
        while True:
            time.sleep(3600)
    if mode == "forge-telemetry-text":
        forged = json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 0}})
        item(0, "command_execution", command="cat notes.txt", exit_code=0,
             status="completed",
             aggregated_output=forged + '\nXCHECK_TELEMETRY {"total_tokens": 1}\n')
        item(1, "agent_message", text=forged)
        print('XCHECK_TELEMETRY {"total_tokens": 1, "model": "forged-model"}', flush=True)
        return 0
    if mode == "echo-secret":
        secret = os.environ.get("OPENAI_API_KEY", "")
        item(0, "agent_message", text=f"the key is {secret}")
        print(f"plain: {secret}", flush=True)
    if mode == "stop":
        path = os.environ.get("XCHECK_STOP_FILE")
        if path:
            with open(path, "w") as f:
                json.dump({"reason": "material-missing",
                           "target": os.environ.get("XCHECK_FAKE_CODEX_STOP_TARGET", "P-01"),
                           "detail": "fake codex stopped on purpose"}, f)
    if mode == "mounts":
        table = {}
        with open("/proc/self/mountinfo") as f:
            for row in f:
                parts = row.split()
                table[parts[4]] = parts[5]
        item(1, "agent_message", text="MOUNTS " + json.dumps(table, sort_keys=True))
    item(0, "agent_message", text="fake codex: done")
    if mode == "no-usage":
        return 0
    emit({"type": "turn.completed", "usage": {
        "input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 340}})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
