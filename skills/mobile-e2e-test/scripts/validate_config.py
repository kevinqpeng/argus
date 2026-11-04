#!/usr/bin/env python3
"""Validate Argus mobile-private.json without printing its contents."""
import json
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit


def validate(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("config must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("config permissions must be 0600")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    if not {"schemaVersion", "targets", "service"} <= set(data) or set(data) - {"schemaVersion", "targets", "service", "accounts"}:
        raise ValueError("config needs schemaVersion, targets, service, and optional accounts")
    if data.get("schemaVersion") != 1 or not isinstance(data.get("targets"), list) or not data["targets"]:
        raise ValueError("schemaVersion 1 and non-empty targets are required")
    seen = set()
    for target in data["targets"]:
        if not isinstance(target, dict) or set(target) - {"platform", "deviceId", "appId", "appPath", "credentialsRef"}:
            raise ValueError("target contains unsupported fields")
        if target.get("platform") not in ("android", "ios"):
            raise ValueError("each target needs platform android or ios")
        if any(not isinstance(target.get(key), str) or not target[key].strip() for key in ("deviceId", "appId", "appPath")):
            raise ValueError("each target needs deviceId, appId, and appPath")
        key = target["platform"], target["deviceId"]
        if key in seen:
            raise ValueError("duplicate platform and deviceId")
        seen.add(key)
        if not Path(target["appPath"]).is_absolute():
            raise ValueError("appPath must be absolute")
        if not Path(target["appPath"]).exists():
            raise ValueError("appPath does not exist")
        reference = target.get("credentialsRef")
        if reference is not None and (not isinstance(reference, str) or not reference.startswith("env:") or not reference[4:].isidentifier()):
            raise ValueError("credentialsRef must name an environment variable")
    accounts = data.get("accounts", {})
    if not isinstance(accounts, dict):
        raise ValueError("accounts must be an object")
    for name, account in accounts.items():
        if not name or not isinstance(account, dict) or set(account) - {"username", "password", "tenant"}:
            raise ValueError("invalid account fields")
        if any(not isinstance(account.get(key), str) or not account[key].strip() for key in ("username", "password")):
            raise ValueError("each account needs username and password")
        if "tenant" in account and not isinstance(account["tenant"], str):
            raise ValueError("account tenant must be a string")
    service = data.get("service")
    if not isinstance(service, dict) or set(service) != {"baseUrl"} or not isinstance(service["baseUrl"], str):
        raise ValueError("service.baseUrl is required")
    url = urlsplit(service["baseUrl"])
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("service.baseUrl must be an HTTP URL")


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: validate_config.py <mobile-private.json>")
        validate(Path(sys.argv[1]))
        print("mobile config valid")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        raise SystemExit(2)
