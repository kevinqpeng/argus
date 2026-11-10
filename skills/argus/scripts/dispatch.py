#!/usr/bin/env python3
"""Print host spawn parameters for one frozen role. Null means omit that argument."""
import argparse
import json
import re
import sys
from pathlib import Path

ROLES = ("reasoning", "generation", "bug_analysis", "vision")
INHERIT = {"inherit-parent", "auto", "host.default"}


def host_id(value):
    if (not isinstance(value, str) or value == "unknown" or "://" in value
            or not re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,160}", value)):
        return None
    return value


def resolve_role(snapshot, role):
    if role not in ROLES:
        raise ValueError("unknown role")
    config = snapshot["config"]
    binding = config["roles"][role]
    configured = binding.get("model", "inherit-parent")
    if configured in INHERIT:
        model = None
    else:
        model = host_id(configured)
        if model is None:
            raise ValueError("frozen model is not a host model id")
    requested = binding.get("effort", config.get("defaultEffort", "session"))
    session = snapshot.get("sessionEffective") or {}
    if requested in {"session", "model-default"}:
        effort = host_id(session.get("effort")) if requested == "session" else None
    else:
        effort = host_id(requested)
        if effort is None:
            raise ValueError("frozen effort is not a host effort id")
    return {"role": role, "modelParameter": model, "effortParameter": effort, "agentType": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Frozen run argus-model.json")
    parser.add_argument("role", choices=ROLES)
    args = parser.parse_args()
    try:
        card = resolve_role(json.loads(args.config.read_text(encoding="utf-8")), args.role)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(card, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
