#!/usr/bin/env python3
"""Load one Argus configuration per agent and keep run outputs outside repositories."""
import argparse
import hashlib
import json
import os
import re
import shutil
import tomllib
import tempfile
import uuid
import fcntl
from datetime import datetime, timezone
from pathlib import Path

PATHS = {
    "codex": (".codex/config.toml",),
    "pi": (".pi/agent/settings.json",),
    "claude": (".claude/settings.json",),
    "grok": (".grok/config.toml",),
    "agy": (".gemini/settings.json",),
}
RUNTIME_DIRS = {
    "codex": ("CODEX_HOME", ".codex"),
    "pi": ("PI_CODING_AGENT_DIR", ".pi/agent"),
    "claude": ("CLAUDE_CONFIG_DIR", ".claude"),
    "grok": ("GROK_HOME", ".grok"),
    "agy": (None, ".gemini/config"),
}
RUNTIME_KEYS = {"roles", "decision", "execution", "defaultEffort"}
PROJECT_KEYS = {"prdPath", "baseUrlEnv", "allowedHosts", "coverage", "execution", "bugPublisher", "delivery"}


def workspace_paths(project, host, runtime_dir=None, state_dir=None, project_id=None):
    project = project.resolve()
    root = (state_dir or Path(os.environ.get("ARGUS_HOME") or
            str(Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "argus"))).expanduser().resolve()
    if root.is_relative_to(project) or any((parent / ".git").exists() for parent in [root, *root.parents]):
        raise ValueError("personal state must be outside project and Git repositories")
    if project_id is not None:
        if not re.fullmatch(r"[0-9a-f]{24}", project_id):
            raise ValueError("invalid project ID")
        existing = root / "projects" / project_id
        if existing.is_symlink() or not existing.is_dir():
            raise ValueError("existing project directory not found")
    else:
        project_id = hashlib.sha256(str(project).encode()).hexdigest()[:24]
    native_dir = runtime_directory(host, runtime_dir) if host != "unknown" or runtime_dir else None
    config_path = native_dir / "argus-model.json" if native_dir else None
    if config_path:
        resolved = config_path.resolve()
        if resolved.is_relative_to(project) or any((parent / ".git").exists() for parent in [resolved, *resolved.parents]):
            raise ValueError("agent setup configuration must be outside Git repositories")
    return {"stateRoot": root, "projectId": project_id, "projectDir": root / "projects" / project_id,
            "configPath": config_path}


def project_settings(data):
    if set(data) - PROJECT_KEYS - {"schemaVersion"}:
        raise ValueError("personal project settings contain unknown fields")
    if "execution" in data and (not isinstance(data["execution"], dict) or
            type(data["execution"].get("requireHumanCaseReview", True)) is not bool):
        raise ValueError("execution.requireHumanCaseReview must be a boolean")
    if data.get("execution", {}).get("webAssistant", "none") not in ("none", "ego"):
        raise ValueError("execution.webAssistant must be none or ego")
    delivery = data.get("delivery", {})
    if (not isinstance(delivery, dict) or delivery.get("mode", "online") not in {"online", "local", "both"}
            or delivery.get("requiresReadback", True) is not True
            or delivery.get("onFailure", "ask") not in {"ask", "local", "stop"}):
        raise ValueError("delivery requires readback and a valid fallback policy")
    publisher = data.get("bugPublisher", {})
    if (not isinstance(publisher, dict)
            or publisher.get("method", "auto") not in {"auto", "mcp", "api", "cli", "browser", "webhook", "local"}
            or publisher.get("tableAction", "existing") not in {"create", "existing", "import"}):
        raise ValueError("invalid bug table method or action")
    if publisher.get("method") == "local" and delivery.get("mode", "online") in {"online", "both"}:
        raise ValueError("local-only publisher cannot complete online delivery")
    return data


def update_sheet(path, overrides, validate):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = validate(merge(validate(load_json(path)), overrides))
        if not path.exists() or load_json(path) != result:
            write_json(path, result)
    return result


def agent_settings(data):
    if data.get("schemaVersion") != 1:
        raise ValueError("unsupported Argus config version")
    runtime = runtime_overrides({k: v for k, v in data.items() if k != "projects"})
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise ValueError("invalid project settings map")
    for project_id, settings in projects.items():
        if not re.fullmatch(r"[0-9a-f]{24}", project_id) or not isinstance(settings, dict) or settings.get("schemaVersion") != 1:
            raise ValueError("invalid project entry")
        project_settings(settings)
    return {**runtime, "projects": projects}


def runtime_directory(host, explicit=None):
    if explicit:
        return explicit.expanduser().resolve()
    if host not in RUNTIME_DIRS:
        raise ValueError("runtime identity is required")
    env, relative = RUNTIME_DIRS[host]
    return Path(os.environ[env]).expanduser().resolve() if env and os.environ.get(env) else Path.home() / relative


def load_json(path):
    if not path.is_file():
        return {"schemaVersion": 1}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("schemaVersion") != 1:
        raise ValueError("unsupported config")
    return data


def runtime_overrides(data):
    if set(data) - RUNTIME_KEYS - {"schemaVersion"}:
        raise ValueError("runtime sheet contains project or unknown fields")
    if any(not isinstance(data[k], dict) for k in RUNTIME_KEYS - {"defaultEffort"} if k in data):
        raise ValueError("invalid runtime override")
    if type(data.get("execution", {}).get("requireHumanCaseReview", True)) is not bool:
        raise ValueError("execution.requireHumanCaseReview must be a boolean")
    if data.get("execution", {}).get("webAssistant", "none") not in ("none", "ego"):
        raise ValueError("execution.webAssistant must be none or ego")
    if "defaultEffort" in data:
        effort_value(data["defaultEffort"])
    result = dict(data)
    if "roles" in data:
        result["roles"] = dict(data["roles"])
    for role, binding in data.get("roles", {}).items():
        if role not in {"reasoning", "generation", "bug_analysis", "vision"} or not isinstance(binding, dict):
            raise ValueError("invalid role binding")
        binding = dict(binding)
        if binding.get("mode") == "host":
            binding["mode"] = "delegate"
            binding.pop("inherit", None)
        if binding.get("model") in {"inherit-parent", "auto", "host.default"}:
            binding["model"] = "inherit-parent"
            binding.pop("inherit", None)
            binding.pop("provider", None)
        if binding.get("mode") not in {"delegate", "api"} or "inherit" in binding:
            raise ValueError("invalid role binding")
        if binding["mode"] == "delegate" and not binding.get("model"):
            binding["model"] = "inherit-parent"
        result["roles"][role] = binding
        if "effort" in binding:
            effort_value(binding["effort"])
    return result


def effort_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value):
        raise ValueError("invalid reasoning effort")
    return value


def merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            # A role binding is replaced as a unit so api mode never retains host inheritance.
            result[key] = {**result[key], **value} if key == "roles" else merge(result[key], value)
        else:
            result[key] = value
    return result


def write_json(path, data, immutable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if load_json(path) != data:
                    raise FileExistsError("run config belongs to a different execution; use a new runId")
        else:
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def configure(project, host, runtime_dir=None, overrides=None, initialize=False, run_id=None, session=None,
              state_dir=None, project_overrides=None, model_catalog=None, project_id=None):
    if run_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        raise ValueError("invalid runId")
    paths = workspace_paths(project, host, runtime_dir, state_dir, project_id)
    runtime_path = paths["configPath"]
    if initialize and runtime_path is None:
        raise ValueError("select a runtime before initializing")
    overrides = runtime_overrides(overrides or {"schemaVersion": 1})
    current = agent_settings(load_json(runtime_path)) if runtime_path else {"schemaVersion": 1, "projects": {}}
    if model_catalog is not None:
        catalog = model_catalog_entries(model_catalog)
        for binding in overrides.get("roles", {}).values():
            model = binding.get("model")
            if model and model != "inherit-parent" and model not in catalog:
                raise ValueError("selected model was not discovered")
            if model and model != "inherit-parent" and binding.get("effort", "session") not in {"session", "model-default"} and binding["effort"] not in catalog[model]["efforts"]:
                raise ValueError("selected effort is unsupported for this model")
        if overrides.get("defaultEffort") not in {None, "session", "model-default"}:
            if not any(overrides["defaultEffort"] in entry["efforts"] for entry in catalog.values()):
                raise ValueError("default effort was not discovered")
            preview = merge(merge(initial_config(), {k: v for k, v in current.items() if k != "projects"}), overrides)
            for binding in preview["roles"].values():
                level = binding.get("effort", preview["defaultEffort"])
                model = binding.get("model", "inherit-parent")
                if model == "inherit-parent":
                    model = (session or {}).get("model", "unknown")
                if level not in {"session", "model-default"} and (model not in catalog or level not in catalog[model]["efforts"]):
                    raise ValueError("default effort is not verified for every assigned model")
    project_overrides = project_settings(project_overrides or {"schemaVersion": 1})
    if initialize:
        current = update_sheet(runtime_path, {**overrides, "projects": {paths["projectId"]: project_overrides}}, agent_settings)
    else:
        current = agent_settings(merge(current, {**overrides, "projects": {paths["projectId"]: project_overrides}}))
    runtime = {k: v for k, v in current.items() if k != "projects"}
    common = current["projects"][paths["projectId"]]
    effective = merge(merge(initial_config(), common), runtime)
    effective.pop("host", None)
    project_settings({"schemaVersion": 1, "delivery": effective["delivery"], "bugPublisher": effective["bugPublisher"]})
    sources = {"configPath": str(runtime_path) if runtime_path else None,
               "projectId": paths["projectId"],
               "projectDir": str(paths["projectDir"]), "stateRoot": str(paths["stateRoot"])}
    snapshot = None
    if initialize or run_id is not None:
        run_id = run_id or uuid.uuid4().hex
        snapshot = paths["projectDir"] / "runs" / run_id / "argus-model.json"
        write_json(snapshot, {"schemaVersion": 1, "runId": run_id, "host": host,
                              "config": effective, "sources": sources, "sessionEffective": session or {}}, immutable=True)
    return effective, sources, runtime_path, snapshot, run_id


def public(value):
    # Model identifiers only; endpoints may contain credentials, queries or tenant secrets.
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,160}", value) and "://" not in value else "unknown"


def codex_session(runtime_dir):
    thread = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID", "")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", thread):
        return {}
    sessions = runtime_dir / "sessions"
    matches = list(sessions.glob(f"*/*/*/rollout-*-{thread}.jsonl"))
    if len(matches) != 1:
        return {}
    result, verified = {}, False
    try:
        with matches[0].open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload", {})
                if event.get("type") == "session_meta":
                    verified = payload.get("id") == thread
                elif event.get("type") == "turn_context":
                    result = {"model": public(payload.get("model")),
                              "effort": public(payload.get("effort"))}
    except (OSError, UnicodeError, AttributeError):
        return {}
    return result if verified else {}


def inspect_config(host, paths, profile=None):
    records = []
    for path in paths:
        path = Path(path).expanduser()
        if not path.is_file():
            continue
        raw = path.read_bytes()
        before = hashlib.sha256(raw).hexdigest()
        record = {"source": str(path), "sha256": before, "status": "declared", "model": "unknown", "provider": "unknown", "effort": "unknown"}
        try:
            obj = tomllib.loads(raw.decode()) if path.suffix == ".toml" else json.loads(raw)
            if host == "codex":
                active = profile or obj.get("profile")
                layer = obj.get("profiles", {}).get(active, {}) if active else {}
                record["model"] = public(layer.get("model", obj.get("model")))
                record["provider"] = public(layer.get("model_provider", obj.get("model_provider")))
                record["effort"] = public(layer.get("model_reasoning_effort", obj.get("model_reasoning_effort")))
                record["profile"] = public(active)
            elif host == "grok":
                record["model"] = public(obj.get("models", {}).get("default"))
            elif host == "pi":
                record["model"] = public(obj.get("defaultModel"))
                record["provider"] = public(obj.get("defaultProvider"))
            elif host == "claude":
                record["model"] = public(obj.get("model"))
            elif host == "agy":
                value = obj.get("model", {})
                record["model"] = public(value.get("name") if isinstance(value, dict) else value)
                record["status"] = "candidate-unverified-for-installed-version"
        except (ValueError, TypeError, AttributeError, UnicodeError):
            record["status"] = "unreadable-format"
        record["unchanged"] = hashlib.sha256(path.read_bytes()).hexdigest() == before
        records.append(record)
    return records


def initial_config():
    config = {"schemaVersion": 1, "host": {"detect": "auto"},
              "defaultEffort": "session",
              "roles": {r: {"mode": "delegate", "model": "inherit-parent"} for r in ["reasoning", "generation", "bug_analysis", "vision"]},
              "decision": {"activeProvider": "jev", "transport": "api", "verification": "required", "visualModel": "jev-1.14.0", "maxCallsPerRun": 100, "maxRetries": 2, "timeoutMs": 10000},
              "coverage": {"scenarioThreshold": 0.95, "p0Threshold": 1, "failOnFlaky": True},
              "execution": {"workers": 2, "retries": 1, "requireHumanCaseReview": True},
              "delivery": {"mode": "online", "requiresReadback": True},
              "bugPublisher": {"provider": "none", "enabled": False}}
    config["roles"]["vision"]["requires"] = ["image-input"]
    return config


def resolve_declared(records):
    result = {"status": "declared", "model": "unknown", "provider": "unknown", "effort": "unknown", "sources": {}}
    for record in records:
        for field in ["model", "provider", "effort"]:
            if record.get(field, "unknown") != "unknown":
                result[field] = record[field]
                result["sources"][field] = record["source"]
    return result


def publication_status(config):
    if config["delivery"]["mode"] == "local":
        return "LOCAL_NOT_VERIFIED"
    publisher = config["bugPublisher"]
    configured = (publisher.get("enabled") and publisher.get("provider") not in {None, "none", "local"}
                  and (publisher.get("targetEnv") or publisher.get("targetUrl")))
    return "NOT_VERIFIED" if configured else "PENDING_TARGET"


def model_catalog_entries(data):
    if data.get("schemaVersion") != 1 or not isinstance(data.get("models"), list):
        raise ValueError("invalid model catalog")
    entries = {}
    for entry in data["models"]:
        name = entry["id"]
        if public(name) == "unknown" or name in entries:
            raise ValueError("invalid or duplicate model catalog ID")
        efforts = entry.get("efforts", [])
        modes = entry.get("modes", [])
        capabilities = entry.get("capabilities", [])
        if (not isinstance(efforts, list) or any(effort_value(v) in {"session", "model-default"} for v in efforts)
                or not isinstance(modes, list) or set(modes) - {"host", "delegate", "api"}
                or not isinstance(capabilities, list) or any(not isinstance(v, str) for v in capabilities)):
            raise ValueError("invalid model capabilities")
        entries[name] = {"id": name, "provider": public(entry.get("provider")), "efforts": efforts,
                         "modes": modes, "capabilities": capabilities}
    return entries


def model_mapping(config, session, catalog=None, explicit_roles=()):
    entries = model_catalog_entries(catalog) if catalog is not None else {}
    descriptions = {"reasoning": "PRD 理解与复杂推理", "generation": "测试场景与代码生成",
                    "bug_analysis": "失败复现与根因分析", "vision": "截图与视觉检查"}
    rows = []
    for role, description in descriptions.items():
        binding = config["roles"][role]
        mode = binding["mode"]
        configured = binding.get("model", "inherit-parent")
        model = public(session.get("model")) if configured in {"inherit-parent", "auto", "host.default"} else public(configured)
        requested = binding.get("effort", config["defaultEffort"])
        effort = public(session.get("effort")) if requested == "session" else requested
        issues = []
        entry = entries.get(model)
        if not entry or mode not in entry["modes"]:
            issues.append("execution-mode-unverified")
        if catalog is not None and model != "unknown" and entry is None:
            issues.append("model-not-in-catalog")
        if entry and requested not in {"session", "model-default"} and requested not in entry["efforts"]:
            issues.append("effort-unsupported")
        if requested == "session" and effort == "unknown":
            issues.append("session-effort-unavailable")
        if role == "vision" and not (entry and "image-input" in entry["capabilities"]):
            issues.append("image-input-unverified")
        rows.append({"role": role, "purpose": description, "configuredModel": configured,
                     "resolvedModel": model, "provider": public(binding.get("provider", entry["provider"] if entry else session.get("provider") if configured == "inherit-parent" else None)),
                     "mode": mode, "requestedEffort": requested, "resolvedEffort": effort,
                     "source": "runtime-role-override" if role in explicit_roles else "skill-default",
                     "issues": issues, "executionStatus": "NOT_INVOKED"})
    decision = config["decision"]
    provider = decision.get("activeProvider", "jev")
    rows.append({"role": "decision", "purpose": "类型判断与概率路由", "configuredModel": decision.get("providers", {}).get(provider, {}).get("model", "unknown"),
                 "resolvedModel": "unknown", "provider": provider, "mode": decision.get("transport", "api"),
                 "requestedEffort": "N/A", "resolvedEffort": "N/A", "source": "decision-provider",
                 "issues": ["provider-call-unverified"], "executionStatus": "NOT_INVOKED"})
    return rows


def mapping_markdown(report):
    lines = [f"当前宿主：{report['host']}；默认推理强度：{report['defaultEffort']}",
             f"配置文件：{report['configPath']}",
             f"会话最后记录：模型 {report.get('sessionEffective', {}).get('model', 'unknown')}，强度 {report.get('sessionEffective', {}).get('effort', 'unknown')}（来源：{report.get('sessionEffective', {}).get('source', 'unknown')}）",
             "", "| 角色 | 职责 | 配置模型 | 解析后的目标模型 | 推理强度（配置 → 解析） | 执行方式 | 来源 | 待处理 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["modelMapping"]:
        values = [row["role"], row["purpose"], row["configuredModel"], row["resolvedModel"],
                  row["requestedEffort"] + " → " + row["resolvedEffort"], row["mode"], row["source"],
                  ", ".join(row["issues"]) or "配置已解析；未执行验证"]
        lines.append("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values) + " |")
    lines.extend(["", "可用模型：" + (", ".join(report["detectedModels"]) or "尚未由宿主提供，不能猜测"),
                  "推理强度按各模型已发现能力提供选项；session 无法解析时先明确选择，不假装已切换。"])
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", choices=[*PATHS, "unknown"], default="unknown", help="Use session metadata, not model-name guesses")
    p.add_argument("--config", type=Path, action="append", help="Explicit config source(s), in documented low-to-high precedence")
    p.add_argument("--profile")
    p.add_argument("--session-model", default="unknown")
    p.add_argument("--session-provider", default="unknown")
    p.add_argument("--session-effort", default="unknown", help="Observed current-turn effort; do not substitute a config default")
    p.add_argument("--model-catalog", type=Path, help="Nonsecret model IDs, per-model efforts/modes/capabilities discovered from this host")
    p.add_argument("--format", choices=["json", "markdown"], default="json")
    p.add_argument("--project", type=Path, default=Path.cwd())
    p.add_argument("--project-id", help="Reuse an existing private project ID when the source path is unavailable")
    p.add_argument("--runtime-dir", type=Path, help="Native agent home; setup settings go in argus-model.json")
    p.add_argument("--state-dir", type=Path, help="Private state root outside all Git repositories; defaults to ARGUS_HOME or XDG state/argus")
    p.add_argument("--project-overrides", type=Path, help="Private project targets, coverage and bug publisher settings as JSON")
    p.add_argument("--overrides", type=Path, help="JSON containing roles/defaultEffort/decision/execution overrides")
    p.add_argument("--run-id", help="Freeze this execution's immutable config; omitted with --init creates a UUID")
    p.add_argument("--init", action="store_true", help="Initialize this agent's argus-model.json; repository stays untouched")
    args = p.parse_args()
    stage = "host-config-discovery"
    try:
        paths = args.config or [Path.home() / x for x in PATHS.get(args.host, ())]
        if args.host == "codex" and not args.config and os.environ.get("CODEX_HOME"):
            paths = [Path(os.environ["CODEX_HOME"]) / "config.toml"]
        if args.host == "pi" and not args.config and os.environ.get("PI_CODING_AGENT_DIR"):
            paths = [Path(os.environ["PI_CODING_AGENT_DIR"]) / "settings.json"]
        if args.host in {"claude", "grok"} and not args.config:
            paths = [runtime_directory(args.host) / ("config.toml" if args.host == "grok" else "settings.json")]
        if args.runtime_dir and not args.config:
            paths = [args.runtime_dir / ("config.toml" if args.host in {"codex", "grok"} else "settings.json")]
        records = inspect_config(args.host, paths, args.profile)
        discovered = codex_session(runtime_directory("codex", args.runtime_dir)) if args.host == "codex" else {}
        session = {"model": public(args.session_model), "provider": public(args.session_provider), "effort": public(args.session_effort), "source": "caller-supplied-session-metadata"}
        session["sources"] = {}
        for field in ("model", "effort"):
            if session[field] != "unknown":
                session["sources"][field] = "caller-supplied-session-metadata"
            elif discovered.get(field, "unknown") != "unknown":
                session[field] = discovered[field]
                session["sources"][field] = "codex-last-recorded-turn-context"
        if session["sources"] and all(v == "codex-last-recorded-turn-context" for v in session["sources"].values()):
            session["source"] = "codex-last-recorded-turn-context"
        if any(path and not path.is_file() for path in [args.overrides, args.project_overrides, args.model_catalog]):
            raise FileNotFoundError("override file missing")
        catalog = load_json(args.model_catalog) if args.model_catalog else None
        stage = "workspace-and-argus-config"
        config, sources, runtime_path, snapshot, run_id = configure(
            args.project, args.host, args.runtime_dir,
            load_json(args.overrides) if args.overrides else None, args.init, args.run_id, session,
            args.state_dir, load_json(args.project_overrides) if args.project_overrides else None, catalog, args.project_id)
        stage = "report-generation"
        delivery_status = publication_status(config)
        report = {"schemaVersion": 1, "host": args.host, "testedAt": datetime.now(timezone.utc).isoformat(),
                  "configuredSources": records,
                  "configuredDefault": resolve_declared(records) if args.config and records else {"status": "candidate", "reason": "host must resolve managed/project/env/session precedence", "candidates": records},
                  "sessionEffective": session,
                  "configSources": sources, "configPath": str(runtime_path) if runtime_path else None,
                  "projectId": sources["projectId"],
                  "workspacePath": str(Path(snapshot).parent) if snapshot else None,
                  "storagePolicy": "private-external", "deliveryStatus": delivery_status,
                  "runId": run_id, "runConfigPath": str(snapshot) if snapshot else None,
                  "roles": {k: {"mode": v.get("mode"), "inherit": v.get("inherit"), "model": public(v.get("model")),
                                "provider": public(v.get("provider")), "status": "requires-host-verification"} for k, v in config["roles"].items()},
                  "executables": {k: {"path": shutil.which(k), "status": "found-not-verified" if shutil.which(k) else "not-found"} for k in ["node", "npx", "k6", "sysbench", "docker"]},
                  "integrationStatus": "NOT_VERIFIED", "configWritten": str(runtime_path) if args.init else None}
        explicit_roles = merge({k: v for k, v in agent_settings(load_json(runtime_path)).items() if k != "projects"} if runtime_path else {},
                               load_json(args.overrides) if args.overrides else {}).get("roles", {})
        report.update({"defaultEffort": config["defaultEffort"], "modelMapping": model_mapping(config, session, catalog, explicit_roles),
                       "detectedModels": list(model_catalog_entries(catalog)) if catalog is not None else []})
        print(mapping_markdown(report) if args.format == "markdown" else json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, OSError) as exc:
        # Parser exception text may embed secret input; report the type only.
        safe_reasons = {
            "personal state must be outside project and Git repositories",
            "agent setup configuration must be outside Git repositories",
            "invalid project ID", "existing project directory not found",
            "invalid runId", "override file missing", "unsupported Argus config version",
        }
        reason = str(exc) if str(exc) in safe_reasons else "inspect the indicated stage and local configuration"
        print(json.dumps({"schemaVersion": 1, "status": "BLOCKED", "stage": stage,
                          "errorType": type(exc).__name__, "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
