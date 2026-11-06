#!/usr/bin/env python3
"""Measure host-owned stages and aggregate observed costs; missing data stays unknown."""
import argparse
import fcntl
import json
import subprocess
import time
from pathlib import Path

STAGES = ("analysis", "generation", "environment", "validation", "playwright", "appium", "report", "publish", "cleanup", "jev")


def record(path, stage, started, status, **fields):
    event = {"stage": stage, "recordedAt": time.time(), "durationMs": round((time.monotonic() - started) * 1000, 3),
             "status": status, **fields}
    with path.open("a") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
    return event


def summarize(path):
    events = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    stages = {}
    for stage in STAGES:
        rows = [e for e in events if e["stage"] == stage]
        stages[stage] = {"count": len(rows), "durationMs": round(sum(e["durationMs"] for e in rows), 3) if rows else "unknown"}
    jev = [e for e in events if e["stage"] == "jev"]
    usages = [e.get("usage", {}) for e in jev if e.get("calls", 0)]
    stages["jev"].update(calls=sum(e.get("calls", 0) for e in jev),
                          failures=sum(e.get("calls", 0) for e in jev if e["status"] != "PASSED"),
                          preflightRejected=sum(not e.get("calls", 0) for e in jev),
                          totalTokens=sum(u["total_tokens"] for u in usages) if usages and all(
                              isinstance(u, dict) and type(u.get("total_tokens")) is int for u in usages) else "unknown")
    if not jev:
        stages["jev"].update(calls="unknown", failures="unknown", preflightRejected="unknown")
    return {"stages": stages, "contractErrorRetries": sum(e.get("contractErrorRetries", 0) for e in events)
            if any("contractErrorRetries" in e for e in events) else "unknown",
            "historicalReworkSeconds": 648, "comparableReworkSeconds": "unknown",
            "note": "Observed event sums, not end-to-end wall time; host stages must be measured explicitly."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.stage:
        print(json.dumps(summarize(args.log), ensure_ascii=False, indent=2))
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a measured command is required")
    started = time.monotonic()
    try:
        result = subprocess.run(command)
        record(args.log, args.stage, started, "PASSED" if result.returncode == 0 else "FAILED")
        return result.returncode
    except OSError:
        record(args.log, args.stage, started, "FAILED")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
