#!/usr/bin/env python3
"""Record start/end wall-clock time for each Argus stage in manifest.timing.

usage:
    phase_timer.py <runDir> start <phase> [--note TEXT]
    phase_timer.py <runDir> end <phase>
    phase_timer.py <runDir> status

`end` requires a matching `start`; both rewrite manifest.json atomically and keep
any other fields of the phase record. Exit 2 when the run or the phase record is
missing so a stage cannot silently report an invented duration.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

TIMING = "timing"


def now():
    return datetime.now().astimezone()


def read_manifest(run_dir):
    path = Path(run_dir) / "manifest.json"
    if not path.is_file():
        sys.exit(f"missing {path}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def write_manifest(path, manifest):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def start(run_dir, phase, note):
    path, manifest = read_manifest(run_dir)
    timing = manifest.setdefault(TIMING, {})
    record = dict(timing.get(phase) or {})
    record["startedAt"] = now().isoformat(timespec="seconds")
    if note:
        record["note"] = note
    timing[phase] = record
    write_manifest(path, manifest)
    print(f"{phase} startedAt={record['startedAt']}")


def end(run_dir, phase):
    path, manifest = read_manifest(run_dir)
    record = (manifest.get(TIMING) or {}).get(phase)
    if not record or not record.get("startedAt"):
        sys.exit(f"phase {phase} has no recorded start; run start first")
    started = datetime.fromisoformat(record["startedAt"])
    ended = now()
    record["endedAt"] = ended.isoformat(timespec="seconds")
    record["durationSec"] = round((ended - started).total_seconds(), 1)
    manifest[TIMING][phase] = record
    write_manifest(path, manifest)
    print(f"{phase} durationSec={record['durationSec']}")


def status(run_dir):
    manifest = read_manifest(run_dir)[1]
    timing = manifest.get(TIMING) or {}
    rows = []
    for phase, record in timing.items():
        started = record.get("startedAt")
        duration = record.get("durationSec")
        rows.append((phase, started, record.get("endedAt"), duration))
    width = max([len(r[0]) for r in rows] + [5])
    print(f"{'phase'.ljust(width)}  startedAt            durationSec")
    for phase, started, _ended, duration in rows:
        shown = duration if isinstance(duration, (int, float)) else "unknown"
        print(f"{phase.ljust(width)}  {str(started):20} {shown}")
    windows = [(r[1], r[2]) for r in rows if r[1] and r[2]]
    if windows:
        first = min(datetime.fromisoformat(w[0]) for w in windows)
        last = max(datetime.fromisoformat(w[1]) for w in windows)
        print(f"wall clock {first.isoformat(timespec='seconds')} -> "
              f"{last.isoformat(timespec='seconds')} = {round((last - first).total_seconds(), 1)}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("action", choices=("start", "end", "status"))
    parser.add_argument("phase", nargs="?")
    parser.add_argument("--note")
    args = parser.parse_args()
    if args.action == "status":
        status(args.run_dir)
        return
    if not args.phase:
        parser.error("phase is required for start/end")
    if args.action == "start":
        start(args.run_dir, args.phase, args.note)
    else:
        end(args.run_dir, args.phase)


if __name__ == "__main__":
    main()
