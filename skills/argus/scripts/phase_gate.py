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
        cases = read_json(self.run_dir / "cases/cases.json")
        if not self.need(isinstance(cases, list) and bool(cases), "缺少 cases/cases.json 或为空"):
            return
        bad = []
        for item in cases:
            case_id = item.get("caseId") or "<no-id>"
            if item.get("reviewStatus") != "approved":
                bad.append(f"{case_id}: reviewStatus={item.get('reviewStatus')!r}（需 approved，人工评审批准后转）")
            if item.get("semanticReview") != "approved":
                bad.append(f"{case_id}: semanticReview={item.get('semanticReview')!r}")
            if not item.get("automationRef"):
                bad.append(f"{case_id}: automationRef")
            try:
                validate_steps(item.get("steps"), item.get("assertions"))
            except ValueError as exc:
                bad.append(f"{case_id}: {exc}")
        self.need(not bad, "用例字段不完整：" + ", ".join(bad[:10]))
        if any(item.get("suite") == "ui" for item in cases):
            self.need_file("evidence/page-map.json", "UI 用例缺少本 run 页面地图")
        self._semantic_review(cases)
        self.need_file("cases/discovered.txt", "缺少 runner discovery 结果")
        coverage = read_json(self.run_dir / "coverage-plan.json") or read_json(self.run_dir / "cases/coverage-plan.json")
        self.need(coverage is not None, "缺少计划覆盖输出 coverage-plan.json")

    def _semantic_review(self, cases: list) -> None:
        """语义审查必须由 decision-router 的 Jev 回执证明，不接受散文自述。"""
        review = read_json(self.run_dir / "cases/semantic-review.json")
        if not self.need(isinstance(review, dict) and bool(review), "缺少 cases/semantic-review.json（Jev 语义审查回执）"):
            return
        self.need(bool(review.get("model")), "semantic-review 缺少 model 标识")
        definitions = read_json(Path(__file__).resolve().parents[2] / "decision-router/references/questions.json")
        self.need(review.get("questionSetVersion") == definitions["questionSetVersion"], "语义题集版本不匹配")
        self.need(review.get("inputHash") == semantic_input_hash(self.run_dir), "语义审查输入已变化或缺少绑定")
        impact = read_json(self.run_dir / "cases/impact-analysis.json")
        case_ids = {c["caseId"] for c in cases}
        groups = {
            "sources": {s["sourceId"] for s in impact["sources"] if s["kind"] in {"prd", "history", "contract", "business_rule"}},
            "impacts": {c["checkId"] for c in impact["checks"]},
            "cases": case_ids, "realization": case_ids, "ruleSupport": case_ids,
        }
        pass_at = definitions["thresholds"]["noulYes"]
        for group, expected in groups.items():
            verdicts = review.get(group, {})
            if not self.need(isinstance(verdicts, dict) and set(verdicts) == expected,
                             "语义审查主体不完整：" + group):
                continue
            for ident, verdict in verdicts.items():
                label = group + "/" + ident
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
        contract = (Path(__file__).resolve().parents[2] / 'prd-to-cases/references/review-table.md').read_text(encoding='utf-8')
        prompt = contract.split('## 固定提示词（原样派发）\n', 1)[1].split('\n## 主 Agent 验收', 1)[0].strip()
        prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        self.need(receipt.get('promptHash') == prompt_hash, '固定提示词哈希不一致')
        required = [f'cases/{name}.json' for name in ('cases', 'scenarios', 'acs', 'impact-analysis', 'semantic-review')]
        required += [f'{base}/{name}' for name in ('content.json', 'cases.xlsx', 'cases.md', 'mapping.json', 'quality-review.json')]
        hashes = receipt.get('artifactHashes') or {}
        for ref in required:
            path = safe_path(self.run_dir, ref)
            self.need(path.is_file() and hashes.get(ref) == file_hash(path), f'展示材料缺失或哈希不一致：{ref}')
        content = read_json(safe_path(self.run_dir, f'{base}/content.json'))
        if content is not None:
            for issue in presentation_issues(content, load_run(self.run_dir)):
                self.gaps.append('评审文案待处理：' + issue)
        quality = read_json(safe_path(self.run_dir, f'{base}/quality-review.json'))
        if self.need(isinstance(quality, dict) and quality.get('status') == 'PASSED' and
                     isinstance(quality.get('evidence'), list) and bool(quality['evidence']),
                     '缺少展示语义与可读性核查证据'):
            self.need(quality.get('contentHash') == hashes.get(f'{base}/content.json'), '展示文案与质量审查版本不一致')
            for item in quality['evidence']:
                path = safe_path(self.run_dir, item['ref'])
                self.need(path.is_file() and item.get('sha256') == file_hash(path), '展示质量核查证据缺失或已变化')
            if content is not None:
                subjects = {f"case:{row['caseId']}": ('case_readability',
                            {k: row[k] for k in ('group', 'title', 'fixtures', 'steps', 'expected')})
                            for row in content['cases']}
                subjects.update({f'note:{i}': ('note_readability',
                                 {k: note[k] for k in ('category', 'question', 'action')})
                                 for i, note in enumerate(content.get('notes') or [], 1)})
                checks = quality.get('readability') or {}
                if self.need(isinstance(checks, dict) and set(checks) == set(subjects), '展示可读性核查未覆盖每条用例及待确认事项'):
                    definitions = read_json(Path(__file__).resolve().parents[2] / 'decision-router/references/questions.json')
                    quality_questions = read_json(Path(__file__).resolve().parents[2] / 'prd-to-cases/references/presentation-quality.json')
                    threshold = definitions['thresholds']['noulYes']
                    for subject, (question, display) in subjects.items():
                        check = checks[subject]
                        self.need(check.get('questionId') == question, f'展示可读性题目不匹配：{subject}')
                        input_path = safe_path(self.run_dir, check['inputRef'])
                        output_path = safe_path(self.run_dir, check['resultRef'])
                        valid_files = (input_path.is_file() and check.get('inputHash') == file_hash(input_path) and
                                       output_path.is_file() and check.get('resultHash') == file_hash(output_path))
                        if not self.need(valid_files, f'展示可读性回执缺失或已变化：{subject}'):
                            continue
                        inputs = read_json(input_path)
                        result = read_json(output_path)
                        self.need(inputs.get('display') == display and bool(inputs.get('source')), f'展示可读性输入不匹配：{subject}')
                        self.need(inputs.get('question') == quality_questions['questions'][question], f'展示可读性问题定义不匹配：{subject}')
                        answer = result.get('answers', {}).get(check['answerId'], {})
                        probability = answer.get('noul')
                        self.need(answer.get('probabilityKind') == 'calibrated' and
                                  type(probability) in (int, float) and threshold <= probability <= 1,
                                  f'展示可读性未通过或概率无效：{subject}')

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
    "presentation": "presentation",
    "scripts": "scripts",
    "publish": "publish",
    "review": "review",
    "execution": "execution",
    "report": "report",
}


def semantic_input_hash(run_dir):
    inputs = {name: read_json(run_dir / "cases" / (name + ".json"))
              for name in ("impact-analysis", "acs", "scenarios", "cases")}
    if any(value is None for value in inputs.values()):
        raise ValueError("missing semantic review inputs")
    # Review flags and later script binding do not change the approved business meaning.
    inputs["cases"] = [{k: v for k, v in case.items()
                        if k not in {"reviewStatus", "semanticReview", "automationRef"}}
                       for case in inputs["cases"]]
    return digest(inputs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Argus 阶段门禁检查")
    parser.add_argument("run_dir", type=Path, help="run 目录")
    parser.add_argument("--phase", required=True, choices=sorted(PHASES), help="要检查的阶段门禁")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

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
