---
name: coverage-check
description: Checks source and change-impact obligations, mandatory design and execution coverage, and strict P0 gates. Use to validate test mappings, merged runs, retries, matrices or partial scope.
---

# 覆盖门禁

## 输入与必读规则

读 [共享契约](../argus/references/contracts.md) 和 [输入格式](references/input.md)。

输入：impactAnalysis、impactHash、currentSourceHashes、冻结 AC/场景/用例、来源哈希、runId 和矩阵；plan 阶段需要 runner 发现清单，execution 另需全部 attempts、分片及哈希。直接调用只核查所选阶段，不生成用例或启动测试。

## 1. 核对来源与审查证据

- 先按 [影响分析契约](../prd-to-cases/references/impact-analysis.md) 核对独立来源单元清单、真实依赖和规则批准依据；脚本检查处置与映射，不能发现从未提交的来源或依赖。缺失、未知、越范围和指纹漂移保持 BLOCKED，旧 run 新建基线补齐。
- 宿主核对 PRD 内容哈希、冻结场景范围、实际源码断言及 runner discovery。semanticReview 必须有审查证据，不能只凭模型标签。
- 脚本只能验证提交的结构和对应关系，无法证明来源文件是真实业务事实。

## 2. 运行计划门禁

- 用例设计或导入完成后先运行一次；只有缺口修复或输入变更时重算，独立复杂业务缺口可派 Agent 并行分析，统计由脚本完成。design 的 draft 用例可计入设计覆盖，但必须语义审查 approved；plan/execution 仍要求人工批准的 approved 用例。运行 `python3 <本目录>/scripts/coverage.py <input.json> --phase design`；使用 planned 入口及空 discovered，不要求浏览器、目标或脚本。
- 脚本发布并 bind 后，使用 execution-cases.json 与真实 discovery 运行 `python3 <本目录>/scripts/coverage.py <input.json> --phase plan`。plan 和 execution 拒绝 planned 入口，不接受设计声明冒充发现结果。
- stdout 为 JSON，退出 0 表示门禁通过，2 表示阻断。
- 未知格式拒绝。
- stdout 分别保存为 coverage-design.json 或 coverage-plan.json。不要覆盖输入；design 通过不能代替执行前的 plan。

## 3. 运行执行门禁

- 计划通过后执行测试并合并分片，完整保留 attempt。
- 运行同一脚本 `--phase execution`，保存 coverage.json。
- partial 无法获得 full 通过。缺分片/重复/哈希不符阻断。

## 4. 分别报告覆盖与通过情况

- 报告 planned/executed/passed 分开。P0 计划与执行通过分别展示，缺口列 ID。
- planned/executed 的 95% 按原始分数计算，仅作进度指标；required_planned/required_executed 要求全部必需场景 100%，同一场景全部关联用例均有效。
- 单列 impactAnalysis.status 与 sources/impacts/obligations 的分子、分母和 missingIds；相关原因在 reasons 中。100% 仅指已登记集合处置完成。
- P0 无跳过、错误、阻塞、取消或 flaky。
- 非 P0 失败也阻断功能结论。

## 完成条件

- 完成执行：场景的全部必需用例在全部 requiredMatrix 组合均有 passed/failed 业务断言结果。
- 先失败后通过归 flaky，默认不计完成和通过。
- 所有映射用例均视为必需。
- 可选用例需单独经审查调整范围。
- 空场景阻断，无 P0 为 N/A。
- 门禁输出不代表外部工具、性能、安全或发布已验收。
- 由当前调用方汇总；独立调用交付覆盖结果及缺口即完成，不触发后续阶段。
- 形式化模型状态、迁移、反例和 TLC 通过不计入业务覆盖或 attempts。由模型导出的真实 ui/api 用例按原规则计数；主 Skill 单独检查 formalVerification 门禁，不能仅凭本脚本通过宣称形式化验证或全量验收通过。

两阶段脚本生成时，执行与执行覆盖使用 `cases/execution-cases.json` 中已发布入口；人工评审与原始计划仍绑定不可变的 `cases/cases.json`。先按 [产物生命周期](../argus/references/lifecycle.md) 完成 bind 和 scripts 门禁，不能手工改写审批文件来匹配 runner。
