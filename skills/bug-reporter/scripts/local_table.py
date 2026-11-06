#!/usr/bin/env python3
"""Upsert private local CSV bug/run tables; no network or workbook dependency."""
import argparse
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

BUG_COLUMNS = {"number": "序号", "module": "模块", "title": "标题描述",
               "reproduction": "操作步骤", "evidence": "证据（截图）",
               "expected": "期望", "actual": "实际结果", "priority": "优先级"}
RUN_COLUMNS = {"featureOverview": "测试功能概述", "caseCount": "用例数",
               "coverage": "覆盖率", "passRate": "通过率", "bugCount": "测试bug数",
               "incompleteCases": "未执行或失败的用例", "executor": "执行人",
               "executedAt": "执行时间"}


def cell(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    if text.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"} or text[:1] in {"\t", "\r", "\n"}:
        text = "'" + text
    return text


def read_bugs(path, metadata_path):
    if not path.exists() and not metadata_path.exists():
        return {}
    if not path.exists() or not metadata_path.exists():
        raise ValueError("bug table or private index is missing; preserve and review")
    records = json.loads(metadata_path.read_text())
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(BUG_COLUMNS.values()):
            raise ValueError("existing table header differs; preserve and review instead of overwriting")
        rows = list(reader)
    if len(rows) != len(records):
        raise ValueError("bug table and private index differ; preserve and review")
    result = {}
    for record, row in zip(records, rows):
        if None in row or any(value is None for value in row.values()):
            raise ValueError("malformed existing CSV row")
        if any(record[key] != row[BUG_COLUMNS[key]] for key in ("number",)):
            raise ValueError("bug rows were reordered or identifiers changed; preserve and review")
        fingerprint = record["fingerprint"]
        if not fingerprint or fingerprint in result:
            raise ValueError("duplicate or missing bug fingerprint")
        record.update({key: row[label] for key, label in BUG_COLUMNS.items()})
        result[fingerprint] = record
    return result


def write_bugs(path, metadata_path, rows):
    fd, temporary = tempfile.mkstemp(prefix="." + metadata_path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(list(rows.values()), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, metadata_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(BUG_COLUMNS.values()))
            writer.writeheader()
            for row in rows.values():
                writer.writerow({label: row[key] for key, label in BUG_COLUMNS.items()})
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if read_bugs(path, metadata_path) != rows:
        raise ValueError("bug table readback mismatch")


def read_runs(path, metadata_path):
    if not path.exists() and not metadata_path.exists():
        return {}
    if not path.exists() or not metadata_path.exists():
        raise ValueError("run table or private index is missing; preserve and review")
    records = json.loads(metadata_path.read_text())
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(RUN_COLUMNS.values()):
            raise ValueError("existing run table header differs; preserve and review instead of overwriting")
        rows = list(reader)
    if len(rows) != len(records):
        raise ValueError("run table and private index differ; preserve and review")
    result = {}
    for record, row in zip(records, rows):
        if None in row or any(value is None for value in row.values()):
            raise ValueError("malformed existing run row")
        if any(record[key] != row[RUN_COLUMNS[key]] for key in ("featureOverview", "executedAt")):
            raise ValueError("run rows were reordered or identifiers changed; preserve and review")
        run_id = record["runId"]
        if not run_id or run_id in result:
            raise ValueError("duplicate or missing run ID")
        record.update({key: row[label] for key, label in RUN_COLUMNS.items()})
        result[run_id] = record
    return result


def write_runs(path, metadata_path, rows):
    fd, temporary = tempfile.mkstemp(prefix="." + metadata_path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(list(rows.values()), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, metadata_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(RUN_COLUMNS.values()))
            writer.writeheader()
            for row in rows.values():
                writer.writerow({label: row[key] for key, label in RUN_COLUMNS.items()})
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if read_runs(path, metadata_path) != rows:
        raise ValueError("run table readback mismatch")


def ratio(numerator, denominator):
    if not denominator:
        return "0 / 0 (N/A)"
    percent = f"{100 * numerator / denominator:.1f}".rstrip("0").rstrip(".")
    return f"{numerator} / {denominator} ({percent}%)"


def delivery_csv(path, columns, rows):
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(columns.values()))
            writer.writeheader()
            writer.writerows({label: row[key] for key, label in columns.items()} for row in rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if list(csv.DictReader(stream)) != [{label: row[key] for key, label in columns.items()} for row in rows]:
            raise ValueError("delivery readback mismatch")


def publish(run_config, data, fallback_approved=False, *, delivery_date, feature_name):
    try:
        if not isinstance(delivery_date, str) or not re.fullmatch(r"[0-9]{8}", delivery_date):
            raise ValueError("invalid delivery date")
        datetime.strptime(delivery_date, "%Y%m%d")
    except (ValueError, TypeError):
        raise ValueError("invalid delivery date")
    if not isinstance(feature_name, str) or not feature_name.strip() or len(feature_name) > 100 or re.search(r"[/\\\x00-\x1f]", feature_name) or feature_name in {".", ".."}:
        raise ValueError("invalid delivery feature name")
    snapshot = json.loads(run_config.read_text())
    if snapshot.get("schemaVersion") != 1 or data.get("schemaVersion") != 1:
        raise ValueError("unsupported version")
    run_id = snapshot["runId"]
    if data["runId"] != run_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        raise ValueError("run mismatch")
    sources = snapshot["sources"]
    state = Path(sources["stateRoot"]).resolve()
    project_id = sources["projectId"]
    if not re.fullmatch(r"[\w][\w.-]{0,127}", project_id) or project_id in {".", ".."}:
        raise ValueError("invalid project scope")
    projects = (state / "projects").resolve()
    project = Path(sources["projectDir"]).resolve()
    marker = project / ".project-id"
    if project.parent != projects or (project.name != project_id and
            (not marker.is_file() or marker.read_text().strip() != project_id)):
        raise ValueError("invalid project scope")
    table_dir = (project / "tables").resolve()
    expected = (project / "runs" / run_id / "argus-model.json").resolve()
    if (run_config.resolve() != expected
            or not table_dir.is_relative_to(state) or not expected.is_relative_to(state)
            or any((parent / ".git").exists() for parent in [table_dir, *table_dir.parents, expected.parent])):
        raise ValueError("tables must stay in the private scope outside Git repositories")
    delivery = snapshot["config"]["delivery"]
    if delivery["mode"] not in {"local", "both"} and delivery.get("onFailure", "ask") != "local" and not fallback_approved:
        raise ValueError("local fallback has not been selected")
    summary = data["summary"]
    if not isinstance(summary, dict) or not {"status", "scope", "coverage", "p0", "counts", "cleanup", "limitations",
                                            "featureOverview", "caseCount", "executedCount", "passedCount",
                                            "incompleteCases", "executor", "executedAt"}.issubset(summary):
        raise ValueError("incomplete run summary")
    planned, executed, passed = (summary[key] for key in ("caseCount", "executedCount", "passedCount"))
    incomplete = summary["incompleteCases"]
    if (any(type(n) is not int for n in (planned, executed, passed)) or not 0 <= passed <= executed <= planned
            or not isinstance(summary["featureOverview"], str) or not summary["featureOverview"].strip()
            or not isinstance(summary["executor"], str) or (executed > 0 and not summary["executor"].strip())
            or not isinstance(summary["executedAt"], str)
            or not summary["executedAt"].strip() or not isinstance(incomplete, list)
            or len(incomplete) != planned - passed
            or any(not isinstance(item, dict) or not isinstance(item.get("caseId"), str)
                   or not item["caseId"] or not isinstance(item.get("status"), str)
                   or not item["status"] for item in incomplete)
            or len({item["caseId"] for item in incomplete}) != len(incomplete)):
        raise ValueError("invalid run case counts or incomplete case list")
    if datetime.fromisoformat(summary["executedAt"]).tzinfo is None:
        raise ValueError("execution time must include a timezone")
    bugs = data["bugs"]
    fingerprints = set()
    for bug in bugs:
        required = {"schemaVersion", "bugId", "fingerprint", "runId", "caseIds", "title", "severity", "status", "reproduction", "expected", "actual", "evidence", "analysis"}
        if (not required.issubset(bug) or bug["schemaVersion"] != 1 or bug["runId"] != run_id
                or not re.fullmatch(r"[0-9a-f]{64}", bug["fingerprint"]) or bug["fingerprint"] in fingerprints):
            raise ValueError("invalid or duplicate bug")
        if not isinstance(bug["caseIds"], list) or not bug["caseIds"] or any(not isinstance(cid, str) or not cid for cid in bug["caseIds"]):
            raise ValueError("bug case IDs must be a nonempty string list")
        fingerprints.add(bug["fingerprint"])
    now = datetime.now(timezone.utc).isoformat()
    table_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    bug_path, run_path = table_dir / "bugs.csv", table_dir / "runs.csv"
    metadata_path = table_dir / "bugs.meta.json"
    runs_metadata_path = table_dir / "runs.meta.json"
    with (table_dir / ".publish.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = read_bugs(bug_path, metadata_path)
        runs = read_runs(run_path, runs_metadata_path)
        for bug in bugs:
            previous = existing.get(bug["fingerprint"])
            item = dict(previous) if previous else {"fingerprint": bug["fingerprint"]}
            item.update({key: cell(bug[key]) for key in BUG_COLUMNS if key in bug})
            item["title"] = cell(bug["title"])
            item["reproduction"] = cell("\n".join(bug["reproduction"]))
            item["expected"] = cell(bug["expected"])
            evidence_paths = [e if isinstance(e, str) else e.get("path", "") if isinstance(e, dict) else "" for e in bug["evidence"]]
            screenshots = [e for e in evidence_paths if isinstance(e, str) and re.search(r"\.(png|jpe?g|webp)(?:[?#].*)?$", e, re.I)]
            item["rawEvidence"] = bug["evidence"]
            item["evidence"] = cell("\n".join(screenshots))
            item["actual"] = cell(bug["actual"])
            item["notes"] = cell(bug.get("notes", {"actual": bug["actual"], "analysis": bug["analysis"]}))
            item["createdAt"] = previous["createdAt"] if previous else cell(bug.get("createdAt", bug.get("firstSeenAt", now)))
            item["number"] = previous["number"] if previous else f"{len(existing) + 1:02d}"
            for key in BUG_COLUMNS:
                item.setdefault(key, "")
            run_ids = json.loads(previous["runIds"]) if previous else []
            item["runIds"] = cell(sorted(set(run_ids + [run_id])))
            case_ids = json.loads(previous["caseIds"]) if previous else []
            item["caseIds"] = cell(sorted(set(case_ids + bug["caseIds"])))
            item["firstSeenAt"] = previous["firstSeenAt"] if previous else cell(bug.get("firstSeenAt", now))
            item["lastSeenAt"] = cell(bug.get("lastSeenAt", now))
            if previous:
                # Preserve human triage and the original stable ID when a failure recurs.
                for key in ["bugId", "status", "priority", "assignee", "notes"]:
                    if previous.get(key):
                        item[key] = previous[key]
            else:
                item["bugId"] = cell(bug["bugId"])
            existing[bug["fingerprint"]] = item
        runs[run_id] = {"runId": run_id, "featureOverview": cell(summary["featureOverview"]),
                        "caseCount": str(planned), "coverage": ratio(executed, planned),
                        "passRate": ratio(passed, executed), "bugCount": str(len(bugs)),
                        "incompleteCases": cell("\n".join(f'{item["caseId"]}（{item["status"]}）' for item in incomplete) or "无"),
                        "executor": cell(summary["executor"]), "executedAt": cell(summary["executedAt"]),
                        "summary": summary}
        write_bugs(bug_path, metadata_path, existing)
        write_runs(run_path, runs_metadata_path, runs)
        delivery_dir = run_config.resolve().parent / "delivery"
        delivery_dir.mkdir(mode=0o700, exist_ok=True)
        bug_delivery = delivery_dir / f"{delivery_date}-{feature_name}-Bug列表.csv"
        summary_delivery = delivery_dir / f"{delivery_date}-{feature_name}-测试汇总表.csv"
        delivery_csv(bug_delivery, BUG_COLUMNS, [existing[bug["fingerprint"]] for bug in bugs])
        delivery_csv(summary_delivery, RUN_COLUMNS, [runs[run_id]])
        receipt = {"schemaVersion": 1, "runId": run_id, "status": "CSV_READY", "onlineStatus": "NOT_CONFIRMED",
                   "bugTable": str(bug_path), "runTable": str(run_path), "bugDelivery": str(bug_delivery), "summaryDelivery": str(summary_delivery), "bugRows": len(existing), "runRows": len(runs),
                   "hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [bug_path, run_path]}}
        receipt_path = run_config.resolve().parent / "local-publish.json"
        fd, temporary = tempfile.mkstemp(prefix=".local-publish.", dir=receipt_path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, receipt_path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-config", type=Path, required=True)
    p.add_argument("--input", type=Path, required=True, help="schemaVersion/runId/bugs/summary JSON")
    p.add_argument("--delivery-date", required=True, help="Frozen delivery date YYYYMMDD")
    p.add_argument("--feature-name", required=True, help="Frozen feature name for this run")
    p.add_argument("--fallback-approved", action="store_true", help="Use only after the user selected local fallback for this run")
    args = p.parse_args()
    try:
        result = publish(args.run_config, json.loads(args.input.read_text()), args.fallback_approved, delivery_date=args.delivery_date, feature_name=args.feature_name)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        print(json.dumps({"schemaVersion": 1, "status": "BLOCKED", "errorType": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
