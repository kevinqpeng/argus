# 阶段门禁与反绕过

独立阶段与组合任务按 [工作流组合](workflows.md) 选择必需门禁。设计阶段使用 dispatch + coverage.py --phase design，不调用要求地图和 discovery 的 cases 门禁；脚本发布后仍须 review、scripts、真实 discovery 的 plan 和适用的全量地图 check。design 不替代执行就绪或人工审批；完整工作流保持下列门禁。

本文件把主流程中易被跳过的要求变成可执行检查。门禁只核对过程证据是否存在且自洽，不判断业务结论。

命令：`python3 <本目录>/../scripts/phase_gate.py <runDir> --phase <target|dispatch|semantic|cases|execution|report>`
退出 0 = PASSED，退出 2 = BLOCKED，输出缺口清单。任一阶段 BLOCKED 时停在该阶段，不进入下一阶段，不能用内容完整代替过程完整。

## 门禁一：目标冻结（评审门禁放行后，首次 Web 探索或业务执行前必须 PASSED）

目标只能来自本项目 `projectDir/web-private.json` 的 `baseUrl` 与 `accounts`。

- 必须实际探测运行中的目标（HTTP 状态、构建版本或健康接口），把结果写入 `manifest.target`：`baseUrl`、`hostHash`（主机名或 IP 的 SHA-256 前 12 位）、`source`、`probedAt`、`probeEvidence`。
- `source` 只允许 `web-private.json`。项目内文档、项目 Playwright 配置、`.env`、docker-compose、本地运维手册、源码默认端口可以作为了解业务的输入，**不能**作为目标来源。
- 禁止为测试自行启动、复刻或搭建被测环境：本地后端、本地前端开发服务器、容器编排、反向代理、mock 服务都不允许。理由是本地环境与目标版本/数据不一致，且让"目标"从配置消失。目标不可达时按 error/BLOCKED 处理，等待人员恢复，不降级到自建环境。
- `baseUrl` 为空、账号缺失或只有一项而用例需要多角色时 BLOCKED，只报告文件绝对路径与缺失字段名。
- harness 中不得出现 `localhost`、`127.0.0.1`、`0.0.0.0` 作为目标主机；端口探索由 `baseUrl` 决定，不由项目运维文档决定。

## 门禁二：并行调度（阶段 3，进入阶段 4 前必须 PASSED）

影响探索与用例生成由宿主子 Agent 完成，主会话只写计划、调度状态和最终材料。

- 影响探索：按 [调度规则](../../prd-to-cases/references/impact-exploration.md) 判定单 Agent 或并行，判定依据与任务清单写 `cases/exploration.md`。启用并行时每个任务有独占回执 `cases/exploration/<taskId>/<attempt>.md`。
- 用例生成：按 [并行生成流程](../../prd-to-cases/references/parallel-generation.md) 写 `cases/generation/plan.json`、`state.json`，每个任务完成后写 `<taskId>/<attempt>/result.json`，最终 `cases/cases.json` 只能由合并结果产生。
- 主会话直接写出 `cases.json` 而没有任何任务回执时，视为未调度，产物无效，不能进入阶段 4。
- 宿主无子 Agent 能力、冻结角色不可调用或预算不足时 BLOCKED，主会话不能冒充子 Agent，也不能用"单 Agent 也做完了"代替调度证据。
- 单 Agent 分支（探索复杂度判定为简单）必须写入判定依据与所检查的证据，不能不判定直接单干。

## 门禁三：页面地图（阶段 5 UI 执行前必须 PASSED）

- UI 用例执行前必须取得 exploration.py check 不带 caseIds 的全量 PASSED 回执（scope=all）；分批生成的局部回执不能替代。存在本 run 的 `evidence/page-map.json`（结构见 [页面探索与失败诊断](page-diagnostics.md)），覆盖每条 UI 用例实际进入的页面状态。
- 按[探索异常诊断](page-diagnostics.md#探索异常与归因)记录阻断的 caseId/矩阵和原因。可安全取得全部所需地图的 spec 继续按原 check 放行；阻断项保留在全量门禁分母内。
- 探索先于脚本：新页面状态先由探针采集定位器与可观察状态，再写 spec。禁止把未验证的定位器直接写进断言。
- 判定为已知页面的条目直接命中地图，不重复探索；同一页面状态最多一次首次探索加一次失效定点重探索，超出预算记录原因并停止。
- 断言模态框/浮层关闭后不要用"DOM 数量为 0"（AntD、vxe 等组件关闭后节点常驻），用可见性断言；表格可能是 `vxe`，操作列可能在固定列镜像表里，定位前先看地图的定位器来源。
- `execution.webAssistant=ego` 时按 [Playwright 执行](../../web-e2e-test/SKILL.md#ego-辅助按需) 判定适用性，并把判定与理由写入 `manifest.execution.webAssistant`：`applicable`/`not_applicable` + `reason`。受保护页面固定由隔离 Playwright 执行，ego 不参与断言。

## 门禁三·五：语义审查（阶段 4.1 人工评审前必须 PASSED）

- 用例合并后、送人工评审前，运行 `phase_gate.py RUN --phase semantic`。此阶段接受 draft 用例，不依赖页面地图、脚本、账号或 runner。旧 `cases` 阶段继续复核同一语义证据。
- `cases/semantic-review.json` 保存 `model`、当前 `questionSetVersion` 和 `inputHash=phase_gate.semantic_input_hash(Path(RUN))`，绑定 impact-analysis、AC、场景和用例业务内容。仅用例的评审标签与后续 automationRef 绑定不影响该指纹。
- 回执包含五组键值映射：`sources`（所有 prd/history/contract/business_rule 的 sourceId，source_inventory）、`impacts`（全部 checkId，impact_evidence）、`cases`（全部 caseId，mapping）、`realization`（全部 caseId，scenario_realization）、`ruleSupport`（全部 caseId，rule_support）。各组完整，不以另一组的高分替代。一个来源分段或一用例对应多项 AC 时，在原始回执保留每项判断，汇总 final 取最小概率，逐项解决缺口。
- 每项为 `{final:实际noul概率, evidenceRef:run内回执路径, evidenceHash:文件SHA256}`；路径指向可核对题目、完整证据、实际模型和概率的 decision-router 回执。低于 questions.json 的 noulYes 时补证据/改用例复查，或记录 `acceptedReason` 及其实际核查依据到回执。缺回执、文件变化、输入/题集版本变化、非法概率、未处理低分均 BLOCKED；不能降低本地 pass 值放行。
- state 里放完整原始依据、fixtures、步骤和断言，不截断。空内容、部分办结、时间边界等条件要有保证触发的样本；步骤豁免也进入语义核查。
- 用 `argus/scripts/semantic_batch.py` 构造 Jev 请求和汇总回执，不为每次 run 编写组装脚本。先将实际证据写成 JSON 数组，每项为 `{group,id,subject,evidence}`：group 分别为 sources、impacts、cases、realization、ruleSupport，id 为对应 sourceId、checkId 或 caseId；subject 明确本题主体，evidence 符合 decision-router 的文本输入契约。每个来源段、每个关联 AC 需各自提交记录；程序按同一 group/id 的最低概率汇总，不能只给每个 ID 一条摘要代替逐项判断。来源原文、影响依赖证据和断言依据由宿主核对并完整提供，不从案例生成推测。
- `python3 <argus>/scripts/semantic_batch.py RUN prepare SUBJECTS.json` 按字节预算生成 `cases/semantic-batch/NNNN.input.json` 和计划。对每批执行 `python3 <argus>/scripts/semantic_batch.py RUN request NNNN`，把返回的 questions 和 state 原样传给 `jev_ask` MCP；将实际返回的 model、answers 和 usage 原样保存为 `cases/semantic-batch/NNNN.raw.json`，运行 `python3 <argus>/scripts/semantic_batch.py RUN record NNNN` 生成规范化回执。宿主须保存真实派发与返回的关联证据，文件哈希不能证明调用发生。最后执行 `python3 <argus>/scripts/semantic_batch.py RUN assemble`。脚本核对输入、题集、原始响应、模型和概率，缺项阻断，不自动接受低分。再按原契约逐项补证据复审或记录有依据的 acceptedReason，运行语义门禁；变更输入时使用新 run 或保存旧批次后重新准备。

- 缺陷探测型用例（预期按 PRD 失败）也要按 PRD 写断言，不能改成「断言实测行为」——那样用例永远不失败，也就抓不到缺陷。

## 门禁三·六：脚本滞后评审（阶段 4.2 必须 PASSED）

- 脚本只能在人工评审批准后生成。`phase_gate.py --phase scripts` 校验：所有用例 `reviewStatus=approved`、`cases/scripts/plan.json` 存在、每任务 `result.json` 齐全、`approvedCasesHash` 与当前 cases 一致、`manifest.scripts.generatedAfter` 不早于 `manifest.timing.review.approvedAt`。
- 切分为 spec 文件级（一任务一 spec，≥5 任务，DB/接口核验单列），避免最慢 worker 拖住整批；worker 只实现分配 caseIds，不改用例期望值。
- runner discovery 的标题集合必须与已批准用例逐字一致：多一条、少一条都 BLOCKED。automationRef 在独立 execution-cases.json 绑定真实入口，保留审批材料。
- 用例在脚本生成后被修改，`approvedCasesHash` 失配即失效：重出脚本计划、重新 discovery，不许沿用旧脚本。

## 门禁四：重试纪律（阶段 5 每次失败后）

- runner 重试次数冻结为 0：`retries=0`。首次失败即停，先出诊断包，不盲目重跑。
- 诊断包写入 `evidence/diagnosis/<caseId>-<attempt>.md`，含原始错误、失败步骤、定位器或接口、前置数据、截图/trace/日志引用。
- 修复后只重跑该用例及其直接受影响用例，命令带 `-g` 或文件名+行号定位；禁止重跑整个规格文件、禁止"多跑一遍看看"。
- 每个 caseId 最多一次有证据的脚本修复与修复后重跑；仍失败停止自动修改，记录新旧错误。
- 失败项写入 `results.json` 时必须有 `diagnosisRef`；无诊断的重试不计入 attempt，也不计分子。
- 预算写在 `manifest.budgets`：探索、诊断、修复各自上限与已用次数。

## 门禁五：计时与报告（阶段 8）

- `manifest.timing` 按阶段记录墙钟与等待时间：输入、目标探测、影响探索、用例生成、计划门禁、人工评审等待、执行、诊断与修复、发布。
- 报告必须给出计划/执行/通过分子分母、失败处置、门禁状态、阶段耗时与最慢环节；无法采集标 `unknown`，不估算。
- 出现"某阶段耗时显著高于同类阶段"时，报告写明原因归类：调度未并行、每用例重复冷启动、失败重试、真实业务等待、真实诊断。

## 反模式速查

| 现象 | 说明 | 处理 |
|---|---|---|
| 自己起前后端跑测试 | 目标不是冻结配置里的环境 | 停服务，改回 `baseUrl` |
| 主会话直接产出 cases.json | 未调度子 Agent | 无效，重做调度 |
| 失败后整文件重跑 | 重试替代诊断 | 出诊断包，定向重跑 |
| 定位器未验证就写断言 | 跳过页面地图 | 先探针再脚本 |
| 断言浮层 `toHaveCount(0)` | 组件关闭后节点常驻 | 改可见性断言 |
| 每用例新开浏览器与登录 | 冷启动重复计费 | 复用角色级 context |
| 报告只说"已通过" | 无分母、无耗时、无门禁 | 按门禁五补齐 |

## 发布、清理与输入完整性硬门禁

- `dispatch` 运行 generation.evaluate 校验当前 attempt、哈希、draft、引用及依赖；仅存在 result.json 不算通过。
- 脚本生成前用 `--phase review` 检查冻结配置与人工审批。脚本产出后用 `--phase publish` 校验本地契约，由 lifecycle.py 在锁内再次执行相同门禁并原子发布；执行前 `--phase scripts` 检查已发布哈希与真实 discovery。
- 具体路径、状态、清理所有权、复用及统计要求统一见 [产物生命周期](lifecycle.md)。已批准的 cases.json 保持不可变。
- Jev 文本输入先经过 jev.py 的 subjects/evidenceHash 与 mapping 字段检查；直接 MCP 调用也先运行相同 build_request 检查，发送其输出的完整 state。不得以工具输出预览或截断 JSON 组装 state；不完整输入 fail fast，返回 retryable=false。
