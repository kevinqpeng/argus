#!/usr/bin/env python3
"""Stamp wall-clock start and end on manifest.timing.

  timing.py <runDir> stamp --stage exploration --event start
  timing.py <runDir> stamp --stage exploration --event end
  timing.py <runDir> show

Stage names are the report gate's stages, plus presentation for the design
delivery. The first start is kept. End requires a start and records the latest
end. The command does not invent durations for stages that have not run.
"""
import argparse
import fcntl
import json
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase_gate import STAGES  # noqa: E402

ALLOWED = STAGES + ("presentation",)


def now():
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def stamp(path, stage, event):
    if stage not in ALLOWED:
        raise ValueError("未知阶段：" + stage)
    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        manifest = json.loads(handle.read() or "{}")
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json 必须是对象")
        timing = manifest.setdefault("timing", {})
        if not isinstance(timing, dict):
            raise ValueError("manifest.timing 必须是对象")
        slot = timing.setdefault(stage, {})
        if not isinstance(slot, dict):
            raise ValueError(f"manifest.timing.{stage} 必须是对象")
        if event == "start":
            slot.setdefault("startedAt", now())
        else:
            if not slot.get("startedAt"):
                raise ValueError(f"{stage} 还没有开始时间")
            slot["endedAt"] = now()
        handle.seek(0)
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        handle.truncate()
        return slot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("command", choices=("stamp", "show", "run"))
    parser.add_argument("--stage")
    parser.add_argument("--event", choices=("start", "end"))
    parser.add_argument("--exec", dest="argv", nargs=argparse.REMAINDER,
                        help="run 模式执行的实际命令；记录真实起止及退出状态")
    args = parser.parse_args()
    path = args.run.resolve() / "manifest.json"
    if not path.is_file():
        print(json.dumps({"status": "BLOCKED", "reason": "缺少 manifest.json"}, ensure_ascii=False))
        return 2
    try:
        if args.command == "run":
            if not args.stage or not args.argv:
                parser.error("run requires --stage and --exec COMMAND")
            stamp(path, args.stage, "start")
            started = time.monotonic()
            status = "ERROR"
            try:
                result = subprocess.run(args.argv, check=False)
                status = "PASSED" if result.returncode == 0 else "FAILED"
                return result.returncode
            finally:
                stamp(path, args.stage, "end")
                from metrics import record
                record(args.run / "timing.metrics.jsonl", args.stage, started, status)
        if args.command == "show":
            manifest = json.loads(path.read_text(encoding="utf-8"))
            print(json.dumps(manifest.get("timing") or {}, ensure_ascii=False, indent=2))
            return 0
        if not args.stage or not args.event:
            parser.error("stamp 需要 --stage 和 --event")
        slot = stamp(path, args.stage, args.event)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "PASSED", "stage": args.stage, **slot}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
