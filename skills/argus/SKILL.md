---
name: argus
description: Orchestrates full PRD acceptance or selected test stages, including requirement-to-cases, execution of supplied cases, bug regression and cross-agent resume, with shared quality gates.
---

# AI 测试主流程

## 输入与必读规则

先读 [共享契约](references/contracts.md)。本文件负责阶段顺序；各阶段细节在对应 Skill，同一 run 已读且未变化的规则无需重复加载。阶段命令以门禁和对应契约里的命令为准；脚本源码不是阶段输入。相对路径从本 SKILL.md 解析。

输入：PRD 或已有用例/运行材料、测试范围、已有配置与授权。

先按 [独立调用与工作流组合](references/workflows.md) 确定入口和交付终点。仅生成用例、执行外部用例、只探索/生成脚本等请求走对应分支；未限定阶段的完整 PRD 验收继续下方 1–8。子技能共享本 run 和门禁，不重复初始化或生成有效产物。

Web 与移动端目标配置按 [私有工作区](../setup-argus/references/storage.md) 在本机文件保存。

过程要求由 [阶段门禁](references/gates.md) 强制执行；每个阶段结束前运行 `python3 <本目录>/scripts/phase_gate.py <runDir> --phase <target|dispatch|cases|execution|report>`，BLOCKED 时停在该阶段。

移动端先自动探测补齐，只引导用户补充无法确定的必填项。

完整验收的目标是完成设计、执行、复现和表格交付；阶段任务按组合契约的完成边界交付。

完整验收仅生成脚本不算完成；用户明确只要脚本时，脚本门禁和交付通过即该阶段完成，业务测试标未执行。

每个阶段完成、等待用户及交接前，按[恢复材料落盘](references/resume.md#恢复材料落盘)保存检查点，使其他模型或 Agent 可从文件继续原 run。

用户要求验证已报告问题的修复时，先执行下方「缺陷修复回归」分支；普通 PRD 验收仍按第 1–8 阶段执行。

用户要求恢复、继续上次测试或查找未完成任务时，先执行[引导式恢复](references/resume.md)，找到并确定历史任务后再进入对应阶段；此入口先于 setup 和新 run 创建，不要求用户记住或输入 runId。

## 缺陷修复回归

涉及状态、并发、重试或已有模型的 Bug，先按 [形式化路由](../decision-router/references/formal-routing.md) 核对修复版本与模型漂移。新发现的边界义务按输入变更新建基线并评审，保留原 Bug、原用例和原基线指标；未授权扩展的关联场景报告为范围外缺口。

1. 读取原 Bug 表、原 run 的 manifest、冻结 PRD/场景/用例、执行结果和证据。确定用户指定的 Bug；未指定时收集本次待回归范围。逐条关联原 Bug ID 或稳定指纹、原 run、caseIds、复现步骤、预期结果及首次失败证据；缺少关键关联或断言时标记该问题 blocked，说明缺口。
2. 记录修复后的实际应用/服务或代码版本、环境及目标，按 [新 run 复用入口](../setup-argus/SKILL.md#新-run-复用入口) 创建新 runId 并冻结配置和输入指纹，保留原 run。原 PRD、影响分析及全部来源指纹和场景未变时复用原冻结基线与用例；旧 run 缺少影响分析时先补分析，按输入变化处理；输入变化按现行规则重新冻结并保留原始关联。冻结本次选中的 Bug、关联用例和必需矩阵；额外关联场景仅按用户指定范围加入。
3. 先完成第 4 阶段及第 4.1 阶段的人工评审门禁，再按第 5 阶段执行原复现路径及关联用例的真实业务断言，并留存原始输出和必要证据。每个 Bug 的所有关联用例在必需矩阵均 passed 且原预期得到验证，才标「已验证修复」；原问题仍复现标「未修复」；error、blocked、skipped、cancelled、flaky、缺断言或证据不足标「未确认」，记录原因。出现不同问题时另行分类、查重和记录，保留原 Bug 的回归结论。
4. 只回归选中 Bug 时报告 partial，不把子集结果当全量验收通过。生成回归报告，逐项列原 Bug 标识、原 run 和用例、修复版本与环境、实际结果、结论、证据及新 runId；结果来自本次结构化执行事实，保留历史。
5. 按 bug-reporter 和已选交付策略将回归结论与证据关联原记录并读回。保留人工信息和原报告；状态仅在用户授权的更新范围内、现有状态枚举与结论明确对应且通道支持时更新。否则在报告注明待同步及原因，不宣称表格已更新。原问题复现复用原记录，避免重复建 Bug。写入本次测试汇总并报告发布状态；失败只恢复发布，不重跑业务测试。

## 1. 登记输入与运行身份

- 登记 PRD 来源，按[影响分析规则](../prd-to-cases/references/impact-analysis.md#冻结生产稳定业务基线)冻结被测项目的 main@SHA；完成 run 配置后，在第 3 阶段按来源采集路由读取 PRD 并登记来源指纹与缺口。
- 没有源码项目也可测试，记录应用或服务版本、PRD 哈希、目标与预算，不要求用户提供项目路径。
- 按 [新 run 复用入口](../setup-argus/SKILL.md#新-run-复用入口) 读取已有项目配置，取得唯一 runId 和仓库外 workspacePath，再写 manifest；只有首次或缺项才做相应 setup。
- 设计阶段不探测测试站或要求账号就绪。
- 禁止为测试自行启动或复刻被测环境（本地后端、本地前端 dev server、容器、反向代理、mock 服务）。项目内文档、运维手册、项目 Playwright 配置、`.env` 只能用于了解业务，不能作为目标来源或端口依据。目标不可达按 error 处理并等待恢复，不降级到自建环境。

## 2. 确认并冻结配置

- 消费第 1 阶段已冻结的 runConfigPath，不再次调用 setup 或冻结快照。模型、Jev 和交付选择沿用已有配置；缺项在新 run 复用入口定向补齐。
- 提出选择即暂停整个主流程，收到实际回答才继续。沿用 setup 的结构化问答工具选择和等待规则。
- 模型角色按冻结配置及[已验证的追加调整](../decision-router/references/config-changes.md)调用。派发前运行 `python3 <本目录>/scripts/dispatch.py <runConfigPath> <role>`，只传它打印的非 null 参数；`agentType` 为 null 时不传宿主的 agent 类型。宿主工具当场返回的错误就是该次派发的结束，记下错误后用同一张卡重派。
- 能力缺失或策略不允许时 BLOCKED，不能由主会话冒充。映射与强度见 [模型规范](../setup-argus/references/models.md)。

## 3. 冻结场景并生成用例

- 读 [prd-to-cases](../prd-to-cases/SKILL.md)，先完成多来源与跨模块影响分析、明确测试义务及未知规则，再提取 AC、冻结场景、生成用例与矩阵及供人工评审的 `cases/cases.xlsx` 和追踪用的 `cases/cases.md`。核对表格、Markdown 与 JSON 一致。
- 影响探索按 prd-to-cases 的复杂度规则采用单 Agent 或 reasoning 子 Agent 并行，完成跨任务依赖合并与复核后才冻结场景；此调度独立于后续用例生成。
- 此阶段只设计和生成用例，不判断或执行 MySQL 核验；在执行每条用例前再判断是否需要数据库配合。页面地图和 runner discovery 在第 4.2 阶段生成；设计覆盖使用 planned 入口，不为评审交付提前构造地图、安装 runner 或执行目标探测。
- 用例与脚本按 [共享调度](references/scheduling.md) 规划任务并动态领取，分两阶段、不混批：本阶段**只生成用例**（按 prd-to-cases 的分组契约分配场景，产出 cases.json + notes.md），脚本等人工评审批准后按批准用例分批生成（见 4.2）。主 Agent 负责依赖核查、宿主调度、失败重派和最终合并；子 Agent 写完分片文件后运行 `generation.py --seal` 生成 result.json。生成完整性未通过时交付缺口，全部生成任务完成后才能进入评审表。
- 调度证据是产物的一部分：影响探索写 `cases/exploration.md`（方式判定、依据、任务与回执），生成写 `cases/generation/plan.json`、`state.json` 和每个任务 `result.json`。主会话直接写出 `cases.json` 而无任何任务回执时，产物无效，按 [门禁二](references/gates.md#门禁二并行调度阶段-3进入阶段-4-前必须-passed) BLOCKED。宿主无子 Agent 能力或冻结角色不可调用时保持 BLOCKED，主会话不能冒充。
- 基线冻结前按 [形式化路由](../decision-router/references/formal-routing.md) 按需调用 [TLA+ 子技能](skills/formal-verification/SKILL.md)，把模型反例和边界义务交回 prd-to-cases。形式化检查是用例设计分析，真实回放仍走第 4.1 与第 5 阶段。
- 合并后按 prd-to-cases 第 5 节对照原文补上已明确的遗漏用例及断言。然后按 [评审表生成契约](../prd-to-cases/references/review-table.md) 出评审表并运行 `phase_gate.py --phase presentation`。主表给人员评审：不写申请单号、与本例结论无关的造数标签，也不写接口、字段和请求体；这些留在 cases.json。预期只写要核对的变化，同一不变量只写一次。缺陷探测型用例按 PRD 写断言，不按实测行为写。
- 范围内业务义务预期未明确、影响分析缺口或未授权关联范围写入待确认事项并阻断全量执行；评审表照常交付。不通过删除义务提高覆盖。

## 4. 交给人工评审

补齐已在第 3 阶段完成，这里直接进入人工评审。评审通过即接受执行。

## 4.1. 等待人工用例评审

- 执行 [共享契约的人工用例评审门禁](references/contracts.md#人工用例评审门禁)，交付本轮评审材料与范围、必需矩阵。默认暂停，等待人员明确评审通过。
- 在线共享表格修改、附件交回或提交最终评审版时，先按 [评审交回](references/case-review.md) 拉取指定版本、映射变更并绑定真实审批，再运行 `phase_gate.py --phase review`。不能继续使用交回前的本地用例。
- 门禁为 PASSED，或冻结配置明确关闭且 N/A 的授权证据通过检查，才进入验证流程。人员要求 Agent 修改业务内容时，回到第 3 阶段更新并重新评审。

## 4.2. 按批准用例生成脚本

主 Agent 在首次探索或业务执行前，按 [资源规划契约](references/scheduling.md#新-run-执行规划必需) 运行 execution.py 冻结 executionConcurrency 与 bindings；未显式启用固定串行。按 claim 的 binding 启动、收回并补位 runner，资源及停止确认由宿主验证；保留现有人工评审和权限门禁。

- 按平台路由完成 Web/Mobile 的隔离与授权检查；UI 探索按[轻量校验](references/page-diagnostics.md#探索前轻量校验)核实环境，再调用 [页面探索子技能](skills/page-exploration/SKILL.md) 规划并采集批准用例所需地图；纯 API 跳过页面探索。
- 调用 [脚本生成子技能](skills/script-generation/SKILL.md)，按已就绪 spec 分批生成、发布、discovery 并绑定执行入口。探索与生成按共享队列重叠，不等待全量地图才派发。
- 主流程只检查交接结果：review、scripts、真实 discovery 及 UI 全量地图 check 通过后进入执行。未完成项保留阻断，用例变更回第 3 阶段重新评审。门禁见 [门禁三·六](references/gates.md#门禁三六脚本滞后评审阶段-42-必须-passed)。

## 5. 选择平台并执行测试

- 按 [平台路由](../decision-router/references/platform-routing.md) 判定 web/mobile/both。明确平台范围和直接调用技能优先于 Jev。
- 纯 API 走现有接口执行路径。
- Jev 低置信度时补证据或澄清，Jev 不可用则 BLOCKED。具体截图条件可按 decision-router 批量并行比对，当前所有视觉结果均须冻结的 vision 复核，冲突由 bug_analysis 核查。
- 生成和执行并发独立冻结，按 [共享调度](references/scheduling.md) 领取执行任务并管理资源。
- 按路由读取 [web-e2e-test](../web-e2e-test/SKILL.md) 或 [mobile-e2e-test](../mobile-e2e-test/SKILL.md)，分别执行隔离的 Playwright 或 Appium 流程。
- UI 执行复用第 4.2 阶段本 run 页面地图，使用前校验当前状态和必要定位器；定位器来源必须是地图条目或当场探针。状态变化只补充关联元素，按 exploration.py allow 检查预算并记录分平台事件。`manifest.execution.retries` 冻结为 0：首次失败即停，保留首次失败证据并出诊断包，再定向重跑该用例，禁止整文件重跑。
- Web 的冻结配置选择 ego 时，按 web-e2e-test 的辅助流程使用；把适用性判定与理由写入 `manifest.execution.webAssistant`，受保护页面固定由隔离 Playwright 执行。Playwright 仍负责真实断言与通过结果。
- 执行每条用例前，按用例的验收条件判断是否需要 MySQL 配合；需要时由主 Agent 按 [数据库核验规则](references/mysql.md) 调度独立 SQL agent，冻结本用例查询与断言并汇总核验回执。生成场景和用例时不做数据库核验。
- 移动端只支持 Android/iOS 本地真机，本期不接云真机。

## 6. 记录并交付 Bug

- 读 [bug-reporter](../bug-reporter/SKILL.md)，分类、查重、写入并读回所选表格。新建 Bug 列表名称遵循下方交付命名规则；复用已有表时保留其名称和历史。产品修复须另有授权。
- 发布失败按已选策略处理，只恢复发布。

## 7. 执行已选非功能测试

范围包含负载或安全时，分别读 [performance-test](../performance-test/SKILL.md)、[security-test](../security-test/SKILL.md) 并执行。未选 N/A，已选且不可执行 BLOCKED。

## 8. 检查执行门禁并交付

- 先跑 `phase_gate.py --phase execution`，按下文报告要求交付；`manifest.timing` 记录各阶段墙钟后运行 `--phase report`。按 [人工用例评审门禁](references/contracts.md#人工用例评审门禁) 在独立结果表同步执行结果，保留冻结的评审材料。
- 完成并核对交付后，清理本 run 已确认不再使用的临时浏览器 profile、认证状态和缓存；先停止相关进程，保留报告、恢复材料、表格引用的证据及正式交付物。使用 [产物生命周期](references/lifecycle.md) 的清理入口并记录结果；源 spec、回执与已发布文件均为恢复材料，不清理。
- 汇总 `manifest.gates.formalVerification` 与独立模型结果；所选形式化检查或真实回放未闭环时保持 BLOCKED，业务覆盖达标不能覆盖该缺口。
- 有源码仓库时核对 Git 无本次新增改动，并核对清理结果及表格读回。online/local/both 的完成条件沿用 bug-reporter，未满足接受范围保持部分完成。

## 恢复

按[引导式恢复](references/resume.md)完成只读发现、工具选择和恢复校验。任务选择只确定恢复对象，不代表用例评审通过或扩大执行授权。

## 报告

面向人员的用例 Excel 按 [用例评审表规则](../prd-to-cases/SKILL.md#7-输出人工评审表) 命名为 `功能名称用例(V版本号).xlsx`，版本取本轮实际冻结需求文档的最新版本；无法确认版本时使用 `功能名称用例.xlsx`，不因版本不明暂停交付。执行结果表、Bug 列表、测试汇总表和测试报告仍使用 `YYYYMMDD-功能名称-类型`。内部固定路径和已有累计表保持原样；交付人员时只展示命名副本并核对内容。

report.md 从同一 JSON 事实生成，包含：

- 来源单元处置、影响检查处置、义务明确程度及 impactHash；未知、排除、检索边界和关联模块。已登记项处理完整不代表未知依赖不存在。
- full/partial 范围、版本、时间、环境；计划/执行/通过分子分母、missingIds、P0 计划与执行。
- passed/failed/error/blocked/skipped/flaky/cancelled；非功能范围与结论。
- 失败复现、根因事实或假设、真实证据与 Bug 链接。
- 模型调用预算、已知/未知成本与降级；清理、发布、恢复状态。
- 页面探索、地图复用、失败诊断和脚本修复的预算、次数及耗时；未采集的指标标 unknown。
- 可用表格路径/链接、测试结论及分别的本地/在线状态；附件必须存在。
- 人工用例评审开关、门禁状态、审批依据及绑定的输入指纹；等待评审时交付用例，不报告验证完成。
- 形式化路由依据与概率、门禁状态、模型来源与界限、性质检查和故障校准证据、反例、关联真实用例及回放缺口；未采用时说明理由，模型状态数不计入业务覆盖。
- 用例 JSON、人工评审 Excel 与追踪 Markdown 的路径；复用原冻结用例时也在本次 run 生成或复制并核对对应的评审表。执行结果表另列路径及同步状态。

工具失败、未运行或缺证据不能报告通过；本地回退不能冒充在线成功。
