#!/usr/bin/env python3
"""Turn a source-unit TSV into SourceUnit JSON. The model does not hand-write the JSON."""
import argparse
import csv
import io
import json
from pathlib import Path

HEADERS = ("unitId", "sourceIds", "sourceExcerpt", "evidenceRef",
           "disposition", "obligationIds", "reason", "reviewRef")
DISPOSITIONS = {"covered", "not_applicable", "unknown", "out_of_scope"}


def split_ids(value):
    return [part.strip() for part in value.split("|") if part.strip()]


def convert(text):
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames != list(HEADERS):
        raise ValueError("TSV header must be " + ",".join(HEADERS))
    units = []
    seen = set()
    for row in reader:
        unit_id = (row.get("unitId") or "").strip()
        if not unit_id or unit_id in seen:
            raise ValueError(f"missing or duplicate unitId: {unit_id}")
        seen.add(unit_id)
        disposition = (row.get("disposition") or "").strip()
        excerpt = row.get("sourceExcerpt") or ""
        if disposition not in DISPOSITIONS or not excerpt.strip() or not (row.get("evidenceRef") or "").strip():
            raise ValueError(f"invalid unit: {unit_id}")
        source_ids = split_ids(row.get("sourceIds") or "")
        if not source_ids or not (row.get("reason") or "").strip():
            raise ValueError(f"invalid unit: {unit_id}")
        units.append({
            "schemaVersion": 1,
            "unitId": unit_id,
            "sourceIds": source_ids,
            "sourceExcerpt": excerpt,
            "evidenceRef": row["evidenceRef"].strip(),
            "disposition": disposition,
            "obligationIds": split_ids(row.get("obligationIds") or ""),
            "reason": row["reason"].strip(),
            "reviewRef": (row.get("reviewRef") or "").strip(),
        })
    if not units:
        raise ValueError("no source units")
    return units


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tsv", type=Path)
    parser.add_argument("-o", type=Path, required=True)
    args = parser.parse_args()
    try:
        units = convert(args.tsv.read_text(encoding="utf-8"))
        encoded = (json.dumps(units, ensure_ascii=False, indent=2) + "\n").encode()
        args.o.parent.mkdir(parents=True, exist_ok=True)
        if args.o.exists() and args.o.read_bytes() != encoded:
            raise ValueError(f"已有不同内容，禁止覆盖: {args.o}")
        if not args.o.exists():
            args.o.write_bytes(encoded)
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f"BLOCKED: {exc}\n")
    print(json.dumps({"count": len(units), "path": str(args.o)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
