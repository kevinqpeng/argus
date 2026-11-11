#!/usr/bin/env python3
"""Open every evidenceRef in the merged receipts against the frozen baseline.

Fabricated paths and out-of-range lines cannot survive this check.

  verify_evidence.py <merged.json|cases/impact-analysis.json> --baseline <worktree> --run <runDir>
  verify_evidence.py cases/impact-analysis.json --baseline <worktree> --out <file.json>
"""
import argparse
import json
import re
import sys
from pathlib import Path

CODE_REF = re.compile(r"(.+):(\d+)(?:-(\d+))?$")
GROUPS = (("checks", "checkId"), ("sourceUnits", "unitId"))


def rows_of(document):
    candidates = document.get("candidates")
    if isinstance(candidates, dict):
        for group, key in GROUPS:
            for _ident, variants in (candidates.get(group) or {}).items():
                for variant in variants if isinstance(variants, list) else []:
                    value = variant.get("value") if isinstance(variant, dict) else None
                    if isinstance(value, dict):
                        yield group, value
        return
    for group, _key in GROUPS:
        for row in document.get(group) or []:
            if isinstance(row, dict):
                yield group, row


def failure(group, row, ref, reason):
    ident = row.get("checkId") or row.get("unitId") or "<no-id>"
    return {"group": group, "id": ident, "evidenceRef": ref, "reason": reason}


def check_ref(part, roots):
    match = CODE_REF.fullmatch(part)
    if match is None:
        return "skipped", "没有行号，无法核对窗口"
    relative = match.group(1).strip().removeprefix("run://")
    start = int(match.group(2))
    end = int(match.group(3) or match.group(2))
    path = None
    for root in roots:
        candidate = (root / relative).resolve()
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        return "failed", f"文件不存在：{relative}"
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if start < 1 or end < start or end > len(lines):
        return "failed", f"行号越界：文件只有 {len(lines)} 行"
    if not any(lines[index].strip() for index in range(start - 1, end)):
        return "failed", "窗口为空行"
    return "verified", ""


def verify(document, baseline, run=None):
    roots = [baseline] + ([run] if run else [])
    total = verified = 0
    failures, skipped = [], []
    for group, row in rows_of(document):
        ref = row.get("evidenceRef")
        if not isinstance(ref, str) or not ref.strip():
            total += 1
            failures.append(failure(group, row, ref, "缺少 evidenceRef"))
            continue
        for part in (chunk.strip() for chunk in ref.split(";")):
            if not part:
                continue
            total += 1
            status, reason = check_ref(part, roots)
            if status == "verified":
                verified += 1
            elif status == "failed":
                failures.append(failure(group, row, part, reason))
            else:
                skipped.append(failure(group, row, part, reason))
    return {"schemaVersion": 1, "status": "PASSED" if not failures else "FAILED",
            "baseline": str(baseline), "total": total, "verified": verified,
            "failed": len(failures), "skipped": len(skipped),
            "failures": failures, "skippedRefs": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--baseline", type=Path, required=True, help="冻结的基线工作树")
    parser.add_argument("--run", type=Path, help="run 目录，用于 run:// 相对引用")
    parser.add_argument("--out", type=Path, help="默认 <run>/cases/exploration/evidence-check.json")
    args = parser.parse_args()
    try:
        document = json.loads(args.document.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("证据文档必须是 JSON 对象")
        if not args.baseline.is_dir():
            raise ValueError(f"基线工作树不存在：{args.baseline}")
        result = verify(document, args.baseline.resolve(), args.run.resolve() if args.run else None)
        target = args.out or (args.run / "cases/exploration/evidence-check.json" if args.run else None)
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    summary = {key: result[key] for key in ("status", "total", "verified", "failed", "skipped")}
    print(json.dumps(summary, ensure_ascii=False))
    for item in result["failures"][:20]:
        print(f"  {item['group']} {item['id']}: {item['reason']} ({item['evidenceRef']})", file=sys.stderr)
    return 0 if result["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
