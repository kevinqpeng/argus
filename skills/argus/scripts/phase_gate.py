#!/usr/bin/env python3
"""Argus 阶段门禁检查：核对过程证据是否存在且自洽。

用法：
    python3 phase_gate.py <runDir> --phase target|dispatch|cases|execution|report

退出码：0 = PASSED，2 = BLOCKED，1 = 参数或文件读取错误。
只读检查，不写任何文件；不改动 run 快照。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prd-to-cases/scripts"))
from generation import digest, evaluate, file_hash, safe_path
from case_contract import validate_steps

LOCAL_HOSTS = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?")
URL_HOST = re.compile(r"https?://([^/\s\"'`)]+)")
DELIVERY_NAME = re.compile(r'.+用例(?:\(V\d+(?:\.\d+)+\))?\.xlsx')
STAGES = ("input", "targetProbe", "exploration", "generation", "planGate", "caseReview", "execution", "diagnosis", "publish")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} JSON 解析失败：{exc}")


def stringify(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Gate:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.gaps: list[str] = []
        self.manifest = read_json(run_dir / "manifest.json")

    def need(self, condition: bool, message: str) -> bool:
        if not condition:
            self.gaps.append(message)
        return condition

    def need_file(self, rel: str, why: str) -> bool:
        return self.need((self.run_dir / rel).is_file(), f"缺少 {rel}（{why}）")

    # --- 门禁一：目标冻结 ---
    def target(self) -> None:
        if not self.need(self.manifest is not None, "缺少 manifest.json（阶段 1 未冻结运行身份）"):
            return
        target = self.manifest.get("target") or {}
        self.need(bool(target.get("baseUrl")), "manifest.target.baseUrl 为空（目标未冻结）")
        self.need(
            target.get("source") == "web-private.json",
            f"manifest.target.source 必须为 web-private.json，实际 {target.get('source')!r}",
        )
        self.need(bool(target.get("hostHash")), "manifest.target.hostHash 缺失（未记录目标主机指纹）")
        self.need(bool(target.get("probedAt")), "manifest.target.probedAt 缺失（未记录目标探测时间）")
        self.need(bool(target.get("probeEvidence")), "manifest.target.probeEvidence 缺失（无可核验的存活探测）")
        self.need(bool(self.manifest.get("budgets")), "manifest.budgets 缺失（探索/诊断/修复预算未记录）")
        self._scan_local_targets()

    def _scan_local_targets(self) -> None:
        harness = self.run_dir / "harness"
        if not harness.is_dir():
            return
        offenders = []
        for path in sorted(harness.rglob("*")):
            if path.suffix not in {".js", ".mjs", ".cjs", ".ts", ".json"} or not path.is_file():
                continue
            if any(part in {"node_modules", "test-results", "playwright-report"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits = sorted(set(LOCAL_HOSTS.findall(text)))
            if hits:
                offenders.append(f"{path.relative_to(self.run_dir)} -> {', '.join(hits)}")
        if offenders:
            self.gaps.append("harness 指向本地自建环境（禁止，目标须来自 web-private.json）：" + "; ".join(offenders[:5]))

    # --- 门禁二：并行调度 ---
    def dispatch(self) -> None:
        if not self.need(self.manifest is not None, "缺少 manifest.json"):
            return
        generation = self.manifest.get("generation") or {}
        self.need(bool(generation), "manifest.generation 缺失（未记录生成计划与结果）")
        self.need_file("cases/exploration.md", "影响探索方式、判定依据与任务清单未记录")
        self._exploration_windows()
        self._impact_completeness()
        plan_path = generation.get("planRef") or generation.get("planPath") or "cases/generation/plan.json"
        state_path = generation.get("stateRef") or "cases/generation/state.json"
        plan = read_json(safe_path(self.run_dir, plan_path))
        if not self.need(plan is not None, f"缺少生成计划 {plan_path}"):
            return
        tasks = plan.get("tasks") or []
        self.need(bool(tasks), f"{plan_path} 没有任务（未派发子 Agent）")
        self.need_file(state_path, "调度状态未落盘")
        generation_root = (self.run_dir / plan_path).parent
        try:
            safe_path(self.run_dir, plan_path)
            state = read_json(safe_path(self.run_dir, state_path))
            self.need(plan.get("runId") == self.manifest.get("runId"), "生成计划 runId 不匹配")
            result = evaluate(plan, state, generation_root)
            self.need(result["status"] == "PASSED", "生成契约未通过：" + result["status"])
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            self.need(False, "生成契约错误：" + str(exc))
        merged = generation.get("mergedFrom") or []
        if merged:
            cases = read_json(self.run_dir / "cases/cases.json") or []
            case_ids = {item.get("caseId") for item in cases} if isinstance(cases, list) else set()
            planned = {cid for task in tasks for cid in (task.get("caseIds") or [])}
            if planned and case_ids:
                lost = sorted(planned - case_ids)
                self.need(not lost, "合并用例缺少计划分配的 caseIds：" + ", ".join(lost[:8]))

    # --- 门禁三：页面地图与用例可执行性 ---
    def cases(self) -> None:
        self._impact_completeness()
        cases = read_json(self.run_dir / "cases/cases.json")
        if not self.need(isinstance(cases, list) and bool(cases), "缺少 cases/cases.json 或为空"):
            return
        bad = []
        for item in cases:
            case_id = item.get("caseId") or "<no-id>"
            if item.get("reviewStatus") != "approved":
                bad.append(f"{case_id}: reviewStatus={item.get('reviewStatus')!r}（需 approved，人工评审批准后转）")
            if not item.get("automationRef"):
                bad.append(f"{case_id}: automationRef")
            try:
                validate_steps(item.get("steps"), item.get("assertions"))
            except ValueError as exc:
                bad.append(f"{case_id}: {exc}")
        self.need(not bad, "用例字段不完整：" + ", ".join(bad[:10]))
        if any(item.get("suite") == "ui" for item in cases):
            self.need_file("evidence/page-map.json", "UI 用例缺少本 run 页面地图")
        self.need_file("cases/discovered.txt", "缺少 runner discovery 结果")

    def _exploration_windows(self) -> None:
        """派发前每个对象必须有 4-6 个可解析的行号窗口，否则六维只能靠常识填。"""
        from merge_impact_candidates import window_errors
        plan = read_json(self.run_dir / "cases/exploration/plan.json")
        if not self.need(isinstance(plan, dict), "缺少 cases/exploration/plan.json（索引窗口未落盘）"):
            return
        for gap in window_errors(plan):
            self.gaps.append("探索计划缺索引窗口：" + gap)

    def _impact_completeness(self) -> None:
        """六维矩阵和证据真实性必须在用例前有机器结论。"""
        from merge_impact_candidates import matrix_errors
        try:
            impact = read_json(self.run_dir / "cases/impact-analysis.json")
        except ValueError as exc:
            self.gaps.append("cases/impact-analysis.json 无法解析：" + str(exc))
            impact = None
        if not isinstance(impact, dict):
            self.gaps.append("缺少 cases/impact-analysis.json（影响分析未冻结）")
        else:
            objects = [row.get("objectId") for row in impact.get("objects") or [] if isinstance(row, dict)]
            for gap in matrix_errors(objects, [row for row in impact.get("checks") or [] if isinstance(row, dict)]):
                self.gaps.append("影响分析未结论：" + gap)
        evidence = read_json(self.run_dir / "cases/exploration/evidence-check.json")
        if not self.need(isinstance(evidence, dict), "缺少 cases/exploration/evidence-check.json（先运行 verify_evidence.py）"):
            return
        self.need(evidence.get("status") == "PASSED",
                  f"探索证据未通过真实性校验：{evidence.get('failed')} 条无效证据")
        failures = [item for item in evidence.get("failures") or [] if isinstance(item, dict)]
        for item in failures[:10]:
            self.gaps.append(f"证据真实性：{item.get('id')} {item.get('reason')}（{item.get('evidenceRef')}）")

    def _semantic_review(self, _cases: list) -> None:
        """语义审查必须由 decision-router 的 Jev 回执证明，不接受散文自述。"""
        review = read_json(self.run_dir / "cases/semantic-review.json")
        if not self.need(isinstance(review, dict) and bool(review), "缺少 cases/semantic-review.json（Jev 语义审查回执）"):
            return
        self.need(bool(review.get("model")), "semantic-review 缺少 model 标识")
        definitions = read_json(Path(__file__).resolve().parents[2] / "decision-router/references/questions.json")
        self.need(review.get("questionSetVersion") == definitions["questionSetVersion"], "语义题集版本不匹配")
        hashes = semantic_subject_hashes(self.run_dir)
        self.need(review.get("inputHash") == digest(hashes), "语义审查输入已变化或缺少绑定")
        pass_at = definitions["thresholds"]["noulYes"]
        for group, expected in hashes.items():
            verdicts = review.get(group, {})
            if not self.need(isinstance(verdicts, dict) and set(verdicts) == set(expected),
                             "语义审查主体不完整：" + group):
                continue
            for ident, verdict in verdicts.items():
                label = group + "/" + ident
                self.need(verdict.get("subjectHash") == expected[ident], "语义主体已变化：" + label)
                score = verdict.get("final")
                valid = type(score) in (int, float) and 0 <= score <= 1
                self.need(valid, "语义概率无效：" + label)
                self.need(valid and (score >= pass_at or bool(verdict.get("acceptedReason"))),
                          "语义审查低分未处理：" + label)
                ref = verdict.get("evidenceRef")
                if self.need(isinstance(ref, str) and bool(ref), "缺少语义回执：" + label):
                    path = safe_path(self.run_dir, ref)
                    self.need(path.is_file() and verdict.get("evidenceHash") == file_hash(path),
                              "语义回执不存在或已变化：" + label)

    def semantic(self) -> None:
        """Design review accepts draft cases without requiring scripts or a browser."""
        cases = read_json(self.run_dir / "cases/cases.json")
        if not self.need(isinstance(cases, list) and bool(cases), "缺少设计用例"):
            return
        for case in cases:
            validate_steps(case.get("steps"), case.get("assertions"))
        self._semantic_review(cases)

    def presentation(self) -> None:
        """评审表发布前验证固定提示词、输入输出哈希及业务文案检查。"""
        from render_review_table import load_run, presentation_issues

        if not self.need(isinstance(self.manifest, dict), '缺少 manifest.json'):
            return
        round_id = str((self.manifest.get('presentation') or {}).get('round', '1'))
        if not re.fullmatch(r'[A-Za-z0-9_-]+', round_id):
            raise ValueError('展示轮次无效')
        base = f'cases/presentation/{round_id}'
        receipt = read_json(safe_path(self.run_dir, f'{base}/receipt.json'))
        if not self.need(isinstance(receipt, dict), '缺少展示任务 receipt.json'):
            return
        for key in ('agentId', 'model', 'effort', 'isolation'):
            self.need(isinstance(receipt.get(key), str) and bool(receipt[key].strip()), f'展示回执缺少 {key}')
        from render_review_table import SLICE_CAP, prompt_text, slice_case_ids
        cases = read_json(self.run_dir / 'cases/cases.json')
        expected = slice_case_ids(cases) if isinstance(cases, list) and cases else []
        if len(expected) > 1 or 'shards' in receipt:
            plan = read_json(safe_path(self.run_dir, f'{base}/plan.json')) or {}
            planned = [row.get('caseIds') for row in plan.get('slices') or []]
            self.need(planned == expected, '评审表分片计划与脚本容量不一致，先运行 render_review_table.py --plan-slices')
            shards = receipt.get('shards')
            groups = [row.get('caseIds') for row in shards] if isinstance(shards, list) and all(isinstance(row, dict) for row in shards) else []
            valid = all(isinstance(group, list) and group and len(group) <= SLICE_CAP for group in groups)
            if valid:
                for group in groups:
                    original = next((chunk for chunk in expected if group[0] in chunk), [])
                    if not original or group != original[original.index(group[0]):original.index(group[0]) + len(group)]:
                        valid = False
                        break
                valid = valid and [item for group in groups for item in group] == [item for chunk in expected for item in chunk]
            if self.need(valid,
                         '评审表应按计划逐片派发，单次产出超过一片容量'):
                for row in shards:
                    self.need(row.get('status') == 'completed' and isinstance(row.get('agentId'), str) and bool(row['agentId'].strip()),
                              '评审表分片缺少完成回执')
        prompt_hash = hashlib.sha256(prompt_text().encode('utf-8')).hexdigest()
        self.need(receipt.get('promptHash') == prompt_hash, '固定提示词哈希不一致')
        required = [f'cases/{name}.json' for name in ('cases', 'scenarios', 'acs', 'impact-analysis')]
        required += [f'{base}/{name}' for name in ('content.json', 'cases.xlsx', 'cases.md', 'mapping.json')]
        hashes = receipt.get('artifactHashes') or {}
        for ref in required:
            path = safe_path(self.run_dir, ref)
            self.need(path.is_file() and hashes.get(ref) == file_hash(path), f'展示材料缺失或哈希不一致：{ref}')
        content = read_json(safe_path(self.run_dir, f'{base}/content.json'))
        if content is not None:
            for issue in presentation_issues(content, load_run(self.run_dir)):
                self.gaps.append('评审文案待处理：' + issue)

    def delivery(self) -> None:
        """命名交付物必须已发布，且与内部 cases.xlsx 同字节（publish_review.py 之后才满足）。"""
        named = []
        deliverables = self.run_dir / 'deliverables'
        if deliverables.is_dir():
            named = sorted(path for path in deliverables.glob('*.xlsx')
                           if DELIVERY_NAME.fullmatch(path.name))
        if self.need(len(named) == 1,
                     'deliverables/ 应有且只有一个「功能名称用例(V版本).xlsx」：运行 prd-to-cases/scripts/publish_review.py 发布并清理旧版本'):
            internal = safe_path(self.run_dir, 'cases/cases.xlsx')
            self.need(internal.is_file() and file_hash(named[0]) == file_hash(internal),
                      '命名交付物与 cases/cases.xlsx 不一致，重新运行 publish_review.py')

    def presentation_delivery(self) -> None:
        """展示阶段的完整门禁：评审材料绑定 + 命名交付物已发布。"""
        self.presentation()
        self.delivery()

    def publish(self, task_id=None) -> None:
        """Validate immutable approval, ownership and bytes before any publication."""
        from lifecycle import publication_inputs
        try:
            plan = read_json(safe_path(self.run_dir, "cases/scripts/plan.json"))
            tasks = [task_id] if task_id else [t["taskId"] for t in plan["tasks"]]
            self.need(bool(tasks), "脚本计划为空")
            for tid in tasks:
                self.publication = publication_inputs(self.run_dir, tid)
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            self.need(False, "发布契约错误：" + str(exc))

    def review(self) -> None:
        from lifecycle import approved_cases
        try:
            approved_cases(self.run_dir, self.manifest)
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            self.need(False, "审批契约错误：" + str(exc))

    def scripts(self) -> None:
        """脚本必须晚于人工评审批准，且只对着批准后的用例写。"""
        if not self.need(self.manifest is not None, "缺少 manifest.json"):
            return
        self.publish()
        try:
            from lifecycle import execution_cases
            expected = execution_cases(self.run_dir)
            self.need(read_json(self.run_dir / "cases/execution-cases.json") == expected,
                      "执行用例未绑定已发布脚本，请运行 lifecycle.py bind")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.need(False, "已发布脚本校验失败：" + str(exc))
        cases = read_json(self.run_dir / "cases/cases.json") or []
        if not self.need(isinstance(cases, list) and bool(cases), "缺少 cases/cases.json"):
            return
        not_approved = [item.get("caseId") for item in cases if item.get("reviewStatus") != "approved"]
        self.need(not not_approved, "用例未批准不得生成脚本：" + ", ".join(not_approved[:8]))
        plan_path = (self.manifest.get("scripts") or {}).get("planRef") or "cases/scripts/plan.json"
        plan = read_json(safe_path(self.run_dir, plan_path))
        if not self.need(isinstance(plan, dict) and plan.get("tasks"), f"缺少脚本计划 {plan_path}"):
            return
        from hashlib import sha256

        approved_hash = sha256(
            json.dumps(cases, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        self.need(
            plan.get("approvedCasesHash") == approved_hash,
            "脚本计划的 approvedCasesHash 与当前用例不一致（用例已被修改，需重出计划）",
        )
        missing = [t.get("taskId") for t in plan["tasks"] if not (self.run_dir / "cases/scripts" / str(t.get("taskId")) / "result.json").is_file()]
        self.need(not missing, "脚本任务缺少 result.json 回执：" + ", ".join(map(str, missing[:8])))
        approved_at = (self.manifest.get("timing", {}).get("review") or {}).get("approvedAt")
        generated_after = (self.manifest.get("scripts") or {}).get("generatedAfter")
        self.need(bool(approved_at) and bool(generated_after), "缺少评审批准时间或脚本生成时间")
        if approved_at and generated_after:
            self.need(str(generated_after) >= str(approved_at), "脚本生成时间早于人工批准时间")
        discovered = read_json(self.run_dir / "cases/discovered.txt") or []
        bound = read_json(self.run_dir / "cases/execution-cases.json") or []
        refs = {c["caseId"]: c["automationRef"] for c in bound}
        self.need(all(item.get("automationRef") == refs.get(item.get("caseId"))
                      for item in discovered if isinstance(item, dict)), "runner 入口与发布脚本不一致")
        covered = {item.get("caseId") for item in discovered if isinstance(item, dict)}
        uncovered = [item.get("caseId") for item in cases if item.get("caseId") not in covered]
        extra = sorted(covered - {item.get("caseId") for item in cases})
        self.need(not uncovered, "runner 未发现已批准用例的入口：" + ", ".join(map(str, uncovered[:8])))
        self.need(not extra, "runner 发现多余用例：" + ", ".join(map(str, extra[:8])))

    # --- 门禁四：执行与重试纪律 ---
    def execution(self) -> None:
        if not self.need(self.manifest is not None, "缺少 manifest.json"):
            return
        execution = self.manifest.get("execution") or {}
        self.need(
            execution.get("retries") == 0,
            f"manifest.execution.retries 必须为 0，实际 {execution.get('retries')!r}",
        )
        self.need(bool((self.manifest.get("execution") or {}).get("webAssistant")), "manifest.execution.webAssistant 判定缺失")
        results = read_json(self.run_dir / "results.json")
        if not self.need(results is not None, "缺少 results.json（执行事实未落盘）"):
            return
        items = results if isinstance(results, list) else results.get("results") or []
        missing = []
        for item in items:
            status = item.get("status")
            case_id = item.get("caseId") or "<no-id>"
            if status in {"failed", "error", "flaky"}:
                ref = item.get("diagnosisRef")
                if not ref:
                    missing.append(f"{case_id}: 无 diagnosisRef")
                elif not (self.run_dir / ref).is_file():
                    missing.append(f"{case_id}: diagnosisRef 文件不存在 {ref}")
            for attempt in item.get("attempts") or []:
                if not attempt.get("resultHash"):
                    missing.append(f"{case_id}: attempt 缺 resultHash")
        self.need(not missing, "执行事实缺口：" + "; ".join(missing[:10]))
        self.need_file("evidence/playwright-results.json", "缺少 runner 原始输出")

    # --- 门禁五：计时与报告 ---
    def report(self) -> None:
        if not self.need(self.manifest is not None, "缺少 manifest.json"):
            return
        timing = self.manifest.get("timing") or {}
        absent = [stage for stage in STAGES if stage not in timing]
        self.need(not absent, "manifest.timing 缺少阶段：" + ", ".join(absent))
        report = self.run_dir / "report.md"
        if self.need(report.is_file(), "缺少 report.md"):
            text = report.read_text(encoding="utf-8", errors="ignore")
            for token in ("计划", "执行", "通过", "门禁", "耗时"):
                self.need(token in text, f"report.md 缺少「{token}」信息")


PHASES = {
    "target": "target",
    "dispatch": "dispatch",
    "cases": "cases",
    "semantic": "semantic",
    "presentation": "presentation_delivery",
    "scripts": "scripts",
    "publish": "publish",
    "review": "review",
    "execution": "execution",
    "report": "report",
}


SEMANTIC_GROUPS = {
    "sources": "source_inventory",
    "impacts": "impact_evidence",
    "cases": "mapping",
    "realization": "scenario_realization",
    "ruleSupport": "rule_support",
}
JUDGED_SOURCE_KINDS = {"prd", "history", "contract", "business_rule"}
SLICE_SKIP = {"reviewRef", "reviewStatus", "approvalRef", "semanticReview", "automationRef"}


def _strip_slice(value):
    if isinstance(value, list):
        return [_strip_slice(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_slice(item) for key, item in value.items() if key not in SLICE_SKIP}
    return value


def semantic_documents(run_dir):
    docs = {name: read_json(run_dir / "cases" / f"{name}.json")
            for name in ("impact-analysis", "acs", "scenarios", "cases")}
    if any(value is None for value in docs.values()):
        raise ValueError("missing semantic review inputs")
    if not isinstance(docs["impact-analysis"], dict) or not all(isinstance(docs[name], list) for name in ("acs", "scenarios", "cases")):
        raise ValueError("missing semantic review inputs")
    return docs


def source_content(run_dir, source):
    content = source.get("sourceContent") if isinstance(source, dict) else None
    if isinstance(content, str) and content.strip():
        return content
    source_id = source.get("sourceId") if isinstance(source, dict) else None
    path = run_dir / "cases" / "source-collection" / f"{source_id}.txt"
    if source_id and path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def semantic_document_hash(run_dir):
    """Whole-document binding for batch plans that predate subjectHash."""
    docs = semantic_documents(run_dir)
    legacy = dict(docs)
    legacy["cases"] = [{key: value for key, value in case.items() if key not in SLICE_SKIP}
                       for case in docs["cases"] if isinstance(case, dict)]
    return digest(legacy)


def semantic_subject_hashes(run_dir):
    """Hash only the business slice each semantic subject is judged on."""
    docs = semantic_documents(run_dir)
    impact = docs["impact-analysis"]
    ac_by_id = {row.get("acId"): row for row in docs["acs"] if isinstance(row, dict) and row.get("acId")}
    scenario_by_id = {row.get("scenarioId"): row for row in docs["scenarios"] if isinstance(row, dict) and row.get("scenarioId")}
    units = [row for row in impact.get("sourceUnits") or [] if isinstance(row, dict)]
    obligations = [row for row in impact.get("obligations") or [] if isinstance(row, dict)]
    objects = {row.get("objectId"): row for row in impact.get("objects") or [] if isinstance(row, dict) and row.get("objectId")}
    hashes = {group: {} for group in SEMANTIC_GROUPS}
    for source in impact["sources"]:
        if not isinstance(source, dict) or source.get("kind") not in JUDGED_SOURCE_KINDS:
            continue
        sid = source["sourceId"]
        hashes["sources"][sid] = digest({
            "source": _strip_slice(source),
            "content": source_content(run_dir, source),
            "units": [_strip_slice(row) for row in units if sid in (row.get("sourceIds") or [])],
            "obligations": [_strip_slice(row) for row in obligations if sid in (row.get("sourceIds") or [])],
        })
    for check in impact["checks"]:
        if not isinstance(check, dict):
            continue
        obligation_ids = set(check.get("obligationIds") or [])
        hashes["impacts"][check["checkId"]] = digest({
            "check": _strip_slice(check),
            "change": _strip_slice(objects.get(check.get("objectId"))),
            "obligations": [_strip_slice(row) for row in obligations if row.get("obligationId") in obligation_ids],
        })
    for case in docs["cases"]:
        if not isinstance(case, dict):
            continue
        ac_ids = set()
        linked = []
        for scenario_id in case.get("scenarioIds") or []:
            scenario = scenario_by_id.get(scenario_id)
            if not isinstance(scenario, dict):
                continue
            linked.append(_strip_slice(scenario))
            ac_ids.update(scenario.get("acIds") or [])
        bound = {row.get("obligationId") for row in obligations if set(row.get("acIds") or []) & ac_ids}
        piece = digest({
            "case": _strip_slice(case),
            "scenarios": linked,
            "acs": [_strip_slice(ac_by_id[ac_id]) for ac_id in sorted(ac_ids) if ac_id in ac_by_id],
            "obligations": [_strip_slice(row) for row in obligations if row.get("obligationId") in bound],
            "units": [_strip_slice(row) for row in units if set(row.get("obligationIds") or []) & bound],
        })
        for group in ("cases", "realization", "ruleSupport"):
            hashes[group][case["caseId"]] = piece
    return hashes


def semantic_input_hash(run_dir):
    return digest(semantic_subject_hashes(run_dir))


def semantic_contract():
    definitions = read_json(Path(__file__).resolve().parents[2] / "decision-router/references/questions.json")
    contract = definitions["textStateContract"]
    return {
        "questionSetVersion": definitions["questionSetVersion"],
        "noulYes": definitions["thresholds"]["noulYes"],
        "maxBytes": contract["maxBytes"],
        "groups": SEMANTIC_GROUPS,
        "evidence": {
            "mapping": contract["mappingRequired"],
            "reviewRequired": contract["reviewRequired"],
            "ruleSupport": "非空 evidence，包含该用例关联义务的 text、expected 和验收条件",
        },
        "inputHash": "各主体判定切片的哈希。reviewStatus、semanticReview、automationRef、reviewRef、approvalRef 不计入",
        "verdict": ["final", "evidenceRef", "evidenceHash", "subjectHash", "acceptedReason"],
        "batch": "prepare 按 build_request 的完整请求拆批，单批必须小于 maxBytes。只重跑 subjectHash 失配的批次。assemble 在 final、evidenceRef、evidenceHash 未变时保留 acceptedReason",
        "order": "用例合并后做一轮语义审查，只补原文已经写明的遗漏并重判新增主体。人工评审通过即接受执行",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Argus 阶段门禁检查")
    parser.add_argument("run_dir", nargs="?", type=Path, help="run 目录")
    parser.add_argument("--phase", required=True, choices=sorted(PHASES), help="要检查的阶段门禁")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--contract", action="store_true", help="打印 semantic 门禁契约后退出")
    args = parser.parse_args()
    if args.contract:
        if args.phase != "semantic":
            print("contract 只用于 semantic", file=sys.stderr)
            return 1
        print(json.dumps(semantic_contract(), ensure_ascii=False, indent=2))
        return 0
    if args.run_dir is None:
        parser.error("run_dir is required unless --contract is set")

    run_dir = args.run_dir.expanduser().resolve()
    if not run_dir.is_dir():
        print(f"run 目录不存在：{run_dir}", file=sys.stderr)
        return 1

    try:
        gate = Gate(run_dir)
        getattr(gate, PHASES[args.phase])()
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        print(json.dumps({"phase": args.phase, "status": "BLOCKED", "gaps": [str(exc)]}, ensure_ascii=False))
        return 2

    status = "PASSED" if not gate.gaps else "BLOCKED"
    if args.json:
        print(json.dumps({"phase": args.phase, "status": status, "gaps": gate.gaps}, ensure_ascii=False, indent=2))
    else:
        print(f"[{args.phase}] {status}")
        for gap in gate.gaps:
            print(f"  - {gap}")
    return 0 if status == "PASSED" else 2


if __name__ == "__main__":
    sys.exit(main())
