---
name: coverage-check
description: Computes frozen-scenario design and execution coverage with 95 percent and strict P0 gates. Use to validate test mappings, merged runs, retries, matrices or partial scope.
---

# 覆盖门禁

## 输入与必读规则

读 [共享契约](../argus/references/contracts.md) 和 [输入格式](references/input.md)。

输入：冻结 AC/场景/用例、PRD 哈希、runner 发现清单；执行阶段另需 runId、矩阵、全部 attempts、分片及哈希。

## 1. 核对来源与审查证据

- 宿主核对 PRD 内容哈希、冻结场景范围、实际源码断言及 runner discovery。semanticReview 必须有审查证据，不能只凭模型标签。
- 脚本只能验证提交的结构和对应关系，无法证明来源文件是真实业务事实。

## 2. 运行计划门禁

- 运行 `python3 <本目录>/scripts/coverage.py <input.json> --phase plan`。
- stdout 为 JSON，退出 0 表示门禁通过，2 表示阻断。
- 未知格式拒绝。
- 可将 stdout 保存为 coverage-plan.json。不要覆盖输入。

## 3. 运行执行门禁

- 计划通过后执行测试并合并分片，完整保留 attempt。
- 运行同一脚本 `--phase execution`，保存 coverage.json。
- partial 无法获得 full 通过。缺分片/重复/哈希不符阻断。

## 4. 分别报告覆盖与通过情况

- 报告 planned/executed/passed 分开。P0 计划与执行通过分别展示，缺口列 ID。
- 95% 按原始分数计算，不按四舍五入百分比。
- P0 无跳过、错误、阻塞、取消或 flaky。
- 非 P0 失败也阻断功能结论。

## 完成条件

- 完成执行：场景的全部必需用例在全部 requiredMatrix 组合均有 passed/failed 业务断言结果。
- 先失败后通过归 flaky，默认不计完成和通过。
- 所有映射用例均视为必需。
- 可选用例需单独经审查调整范围。
- 空场景阻断，无 P0 为 N/A。
- 门禁输出不代表外部工具、性能、安全或发布已验收。
- 由主 Skill 汇总。
- 形式化模型状态、迁移、反例和 TLC 通过不计入业务覆盖或 attempts。由模型导出的真实 ui/api 用例按原规则计数；主 Skill 单独检查 formalVerification 门禁，不能仅凭本脚本通过宣称形式化验证或全量验收通过。
