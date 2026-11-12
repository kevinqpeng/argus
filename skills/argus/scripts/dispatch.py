#!/usr/bin/env python3
"""Print host spawn parameters for one frozen role. Null means omit that argument."""
import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

ROLES = ("reasoning", "generation", "exploration", "bug_analysis", "vision")
INHERIT = {"inherit-parent", "auto", "host.default"}
SUFFIX_HOSTS = {"pi"}  # hosts that take the thinking level only as a model suffix
BINDING_KEYS = {"mode", "model", "provider", "effort"}
ADJUSTMENT_KEYS = {"revision", "requestedAt", "requestRef", "baseConfigHash", "previousRevision",
                   "fieldDiff", "overrides", "decisionRef", "capabilityEvidenceRef",
                   "effectiveFrom"}
EVIDENCE_KEYS = ("requestedAt", "requestRef", "decisionRef", "capabilityEvidenceRef",
                 "effectiveFrom")


def host_id(value):
    if (not isinstance(value, str) or value == "unknown" or "://" in value
            or not re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,160}", value)):
        return None
    return value


def qualify(model, provider):
    """Registry ids that carry a level suffix must name the provider."""
    if model and provider and not model.startswith(provider + "/"):
        return f"{provider}/{model}"
    return model


def adjusted_config(snapshot, raw, path):
    """Layer verified per-run adjustments on the frozen snapshot.

    Returns (config, revision, rejection). Adjustments live next to the snapshot and are
    applied in revision order over the frozen config; the first unverifiable entry ends the
    chain, so the call keeps using the last revision that did verify.
    """
    document = path.parent / "model-adjustments.json"
    if not document.is_file():
        return snapshot["config"], 0, None
    try:
        data = json.loads(document.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return snapshot["config"], 0, f"model-adjustments.json unreadable: {exc}"
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(data, dict) or data.get("schemaVersion") != 1:
        return snapshot["config"], 0, "model-adjustments.json is not schemaVersion 1"
    if data.get("runId") != snapshot.get("runId"):
        return snapshot["config"], 0, "model-adjustments.json belongs to a different run"
    entries = data.get("adjustments")
    if not isinstance(entries, list):
        return snapshot["config"], 0, "model-adjustments.json has no adjustments list"
    config = copy.deepcopy(snapshot["config"])
    revision = 0
    for entry in entries:
        problem = adjustment_problem(entry, revision, digest, config)
        if problem:
            return config, revision, f"revision {revision + 1} not applied: {problem}"
        overrides = entry["overrides"]
        for role, binding in overrides.get("roles", {}).items():
            config["roles"][role] = dict(binding)
        if "defaultEffort" in overrides:
            config["defaultEffort"] = overrides["defaultEffort"]
        revision = entry["revision"]
    return config, revision, None


def adjustment_problem(entry, applied, digest, config):
    if not isinstance(entry, dict):
        return "entry is not an object"
    if set(entry) - ADJUSTMENT_KEYS:
        return "unknown fields " + ", ".join(sorted(set(entry) - ADJUSTMENT_KEYS))
    if entry.get("revision") != applied + 1 or entry.get("previousRevision") != applied:
        return "revision chain is not consecutive"
    if entry.get("baseConfigHash") != digest:
        return "baseConfigHash does not match the frozen snapshot"
    for key in EVIDENCE_KEYS:
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            return "missing " + key
    overrides = entry.get("overrides")
    if not isinstance(overrides, dict) or set(overrides) - {"roles", "defaultEffort"}:
        return "overrides may only change roles and defaultEffort"
    roles = overrides.get("roles", {})
    if not isinstance(roles, dict):
        return "overrides.roles is not an object"
    for role, binding in roles.items():
        if role not in config["roles"]:
            return f"{role} is not a frozen role"
        if not isinstance(binding, dict) or set(binding) - BINDING_KEYS:
            return f"{role} binding has unknown fields"
    if "defaultEffort" in overrides and host_id(overrides["defaultEffort"]) is None and \
            overrides["defaultEffort"] not in {"session", "model-default"}:
        return "defaultEffort is not a host effort id"
    return None


def resolve_role(snapshot, role, config=None):
    if role not in ROLES:
        raise ValueError("unknown role")
    config = config if config is not None else snapshot["config"]
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
    if effort is not None and snapshot.get("host") in SUFFIX_HOSTS:
        base = qualify(model or host_id(session.get("model")),
                       host_id(binding.get("provider")) or host_id(session.get("provider")))
        if base is None:
            raise ValueError("session model is unknown; record it with --session-model at run init")
        return {"role": role, "modelParameter": f"{base}:{effort}", "effortParameter": None,
                "resolvedEffort": effort, "agentType": None}
    return {"role": role, "modelParameter": model, "effortParameter": effort,
            "resolvedEffort": effort, "agentType": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Frozen run argus-model.json")
    parser.add_argument("role", choices=ROLES)
    args = parser.parse_args()
    try:
        raw = args.config.read_bytes()
        snapshot = json.loads(raw)
        config, revision, rejection = adjusted_config(snapshot, raw, args.config)
        card = resolve_role(snapshot, args.role, config)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    card["configRevision"] = revision
    if rejection:
        card["adjustmentRejection"] = rejection
    print(json.dumps(card, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
