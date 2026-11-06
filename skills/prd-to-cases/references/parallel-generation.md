# 用例与脚本生成（两阶段、分批）

本流程由主 Agent 调度宿主子 Agent；`scripts/generation.py` 只校验和汇总，不创建模型进程。沿用冻结的 generation 角色及模型/强度配置。同 run 的 worker 共用 runId，各自写独立目录；独立测试运行仍各自创建 runId。单组也使用相同校验契约。

交付分两阶段，不混批：

| 阶段 | 时机 | 切分粒度 | 任务数 | 产出 |
|---|---|---|---|---|
| A 用例 | 语义审查与人工评审前 | AC 域 / 场景组 | ≥ 5 | cases.json、notes.md |
| B 脚本 | 人工评审批准后 | **spec 文件级**，DB 核验单列 | ≥ 5 | 一个任务一个 spec + notes.md |

理由：脚本依赖用例的期望值与断言。用例改一轮、脚本白写一轮；先批准用例再写脚本，脚本永远对应最新用例，且人工评审等待期不用干等脚本。两阶段分开还让最慢 worker 从「多 spec + DB 核验」降为「一个 spec」，并行利用率接近 100%。

## 1. 准备和判断依赖

主 Agent 先完成来源与影响义务核查、完整 PRD/AC 理解、形式化路由和场景冻结；准备公共 harness、fixture 接口、认证与数据隔离约定。公共文件由主 Agent 唯一维护。worker 读取完整基线索引、相关来源及跨组业务规则，只生成分配范围。

仅设计或外部用例规范化时，共享 harness/fixture 仅为声明式接口与隔离约定，不创建运行环境、获取账号或安装 runner。外部用例输入按 [接入规则](existing-cases.md) 保留来源、步骤和预期，仍使用本流程的真实任务回执与校验。

按业务内聚性提出候选组，核对所有跨组输入/输出、共享 fixture、状态和全局约束；跨模块依赖也必须纳入，不能把“没有候选边”当作独立证据。阶段 A 按 AC/场景内聚性、步骤复杂度与依赖切分；任务数同时考虑上下文和模型调用成本，不以文件数或 AC 数平均分配。
显式文件读写关系由主 Agent 直接确定；需要语义判断的候选关系按 decision-router 批量提交 `generation_dependency` 和 `generation_conflict`，正反向依赖分开提问。使用 `{questionId, subject}` 绑定具体任务与证据；记录 DecisionRecord、概率、题集版本及宿主最终处理依据。

- 生成依赖：A 需要消费 B 尚未冻结的接口或产物，记 A.dependsOn 包含 B。已冻结接口可以消除生成等待。
- 生成冲突：多个任务修改同一产物。优先把该产物交给主 Agent 先生成并冻结，或合并为同组；仅加执行顺序不能解决文件所有权冲突。
- 业务先后与执行资源竞争单独保留在用例前置条件和执行计划中，不能直接充当生成依赖。业务并发仍在单条用例内真实安排。
- 使用 questions.json 的集中阈值；不确定交 reasoning/主 Agent 核查，仍不明确则合并相关组或保持 blocked，不能按 no 调度。Jev 不可用遵循 decision-router 的阻断规则。

每个场景由唯一组负责，跨场景用例涉及的场景归入同组。主 Agent 分配稳定 caseId；发现必须新增场景、用例 ID、输出文件或变更公共契约时停止相关生成，重审并创建新计划版本，不能让 worker 扩大范围。冻结前合并循环依赖组或修正错误依赖；脚本拒绝环和未知依赖。

## 2. 冻结生成计划

在私有 run 的 `cases/generation/` 保存 plan.json、state.json、冻结输入副本和各组产物。共享 JSON 定义见 [schemas.json](../../argus/references/schemas.json) 的 GenerationPlan、GenerationState、GenerationResult。

plan.json 包含 schemaVersion、phase="cases"、runId、inputHash、baselineHash、完整 scenarioIds、inputArtifacts 和 tasks。inputArtifacts 是相对此目录的冻结输入文件路径 → 原始字节 SHA-256，至少包含来源清单、impact-analysis.json、AC/场景、run 配置、共享 harness/fixture 契约和分组决策记录。主 Agent 核对副本与本 run 源文件一致、scenarioIds 与冻结基线完全相等。inputHash 绑定这些输入、代码/目标指纹和必需矩阵；生成时还没有最终用例内容，用计划哈希另行绑定分配，不能冒用执行 inputHash。

每项 task 为 `{taskId, scenarioIds, caseIds, dependsOn, outputFiles}`。outputFiles 是 worker 本次目录内的相对文件路径，不能包含 result.json、绝对路径、上级路径或符号链接。公共输入目录与 taskId 目录不重叠。计划哈希是 generation.digest(plan)，算法与 coverage.digest 一致。冻结后不原地改计划；新版本用新 generation 目录并保存旧版。

state.json 仅由主 Agent 写：`{schemaVersion:1, planHash, attempts:{任务ID:正整数}, running:[], failed:[]}`。首次 attempt=1；running 是已派发未确认结束的任务，failed 是明确失败且待处理的任务。状态应原子落盘，恢复前核对宿主真实 Agent 状态，不能只因本地状态过旧就重复派发。

## 3. 调度与恢复

按 [共享调度](../../argus/references/scheduling.md) 使用持久队列领取任务；下述 generation.py 保留产物校验职责。任务粒度及数量根据场景复杂度、依赖与启动成本确定。

运行 `python3 <本技能>/scripts/generation.py <plan.json> <state.json> --root <generation目录>`。退出 0 仅表示检查有效；查看 status，PENDING 不表示生成完成。退出 2 为 BLOCKED。ready 是依赖已完成且不在 running/failed 的任务；按宿主当前可用子 Agent 槽位、冻结预算和限流取一批，一次派发。单个槽位按相同 ready 顺序串行；配置的角色无法调用则 BLOCKED，主会话不能冒充。

每次派发先持久化 running，再把 planHash、任务范围、完整上下文指针、依赖结果哈希、独占目录 `任务ID/attempt/`、验收要求交给 worker。生成期间只操作私有文件；不连接业务目标执行测试。worker 只写该目录，只读共享输入和上游已完成产物，不写 manifest、state.json、最终 cases.json、Excel 或 Markdown。

主 Agent 等待任一任务结束即收取回执，确认 worker 已停止写入后移出 running，再校验并启动新就绪任务，无需等待整批最慢任务。失败记录原因并加入 failed；预算允许才重派。重派先停止旧 worker，将该任务及已消费旧结果的全部传递下游任务 attempt 加一、清除对应 running/failed 后持久化；下游重新等待。旧目录保留，迟到的旧回执不参与合并。恢复不修改已完成产物。

## 3.1 worker 输入封闭与工具预算

- 派发必须给**封闭输入清单**：只列本任务要读的绝对路径（PRD、冻结 AC/场景、契约、必要的源码文件），并明示「不得自行搜索其他目录」。实测过：一个 worker 从 `$HOME` 起递归 `find` 会烧掉 30 分钟并连带整批超时。
- 明确禁止项写进任务：不递归 `find`/`grep -r` 于仓库根或 home、不读其他 run 目录、不跑 `playwright`/`mvn`/`npm`、不连业务目标。生成阶段的产物只需要读 PRD 与冻结基线。
- 每个任务给墙钟预算（默认 10 分钟，复杂组 15 分钟）与 bash 调用次数上限（默认 6 次）；超预算即中止该任务并按失败重派，不让最慢任务拖住整批。
- 预算与已用值记入 `manifest.budgets`，报告用真实耗时判定是否需要再切分。

## 4. 回执和合并

worker 完成后最后写 `任务ID/attempt/result.json`，字段为 schemaVersion、runId、planHash、taskId、attempt、state="completed"、dependencyHashes、files、cases、resultHash。dependencyHashes 精确绑定所有直接上游 taskId → 已接受 resultHash；files 是 outputFiles 路径 → 文件 SHA-256；resultHash 是去除自身字段后整个回执的 generation.digest。每组 cases 必须完整包含分配的 caseIds，场景关联不能越组，reviewStatus 和 semanticReview 均为 draft。

阶段 A 没有脚本，automationRef 写声明式入口 `planned:<spec文件名>::<用例标题>`：只承诺脚本阶段会落这个文件名与标题，不声称文件已存在。阶段 B 生成脚本后，主 Agent 跑 runner discovery，在独立的 execution-cases.json 中把 automationRef 绑定为真实入口 `文件::标题`，保留已审批 cases.json；文件存在只说明入口存在，业务断言仍由语义审查与执行验证。

脚本检查全部分片、输入/输出哈希、依赖、文件所有权、唯一 ID、场景集合、步骤断言及入口文件。所有任务完整才给生成 status=PASSED。用 `--merge <新的输出路径>` 写合并的 draft 用例数组；输出相同字节时幂等成功，异内容拒绝覆盖；只规范化 JSON 格式与排序，不自动补写或改写 draft、审批状态和关键引用。缺少 phase 的旧计划仍按旧版文件入口契约校验，迁移必须创建新计划。该状态仅代表生成结构完整，不代表覆盖或业务测试通过。

主 Agent 先停止所有 worker 写入，再运行合并；复核产物、批量语义映射（Jev）和 design 覆盖检查，最后统一生成 cases.json、cases.md、cases.xlsx 供人工评审。此阶段仅校验声明式入口，不运行或伪造 runner discovery；真实发现和 plan 门禁在脚本发布后执行。已审批文件保留；修订生成新材料并重新评审。漏片即生成未完成，即使缺失仅占非 P0 的 5% 以内也不能跳过。部分产物可交付诊断，但不能合并成全量通过。

## 5. 汇总

manifest.generation 由主 Agent 记录计划路径/哈希、状态路径、任务与重派结果、产物哈希及未完成任务。报告区分生成完整性、计划覆盖和执行门禁，保留现有人工评审规则。分别记录分组/Jev、生成、等待、重试、合并与评审耗时、模型调用和未知成本；比较提速时用同一冻结输入与质量门禁下的全流程墙钟时间。

借鉴 Arena 的统一输入、独立目录和最终验证；各 worker 负责互补分片，不重复生成全量候选，也不因一个 worker 失败而静默缩小范围。

分批回报：阶段 B 每完成一个 spec 就校验并入 `harness/tests/`，不必等整批；人工评审期间只冻结用例，不阻塞评审结论的收集。

## 6. 调度证据即产物

主会话直接写出 `cases/cases.json`、没有任何 `result.json` 回执时，本次生成视为未调度，用例无效，不能进入计划覆盖门禁。宿主无子 Agent 能力、冻结 generation 角色不可调用或预算不足时保持 BLOCKED，主会话不能冒充 worker，也不能用"单 Agent 也写完了"代替调度证据。

单组分支同样要有 `plan.json`、`state.json` 和 `result.json`，便于核对是谁写的、写了什么。阶段 3 结束前运行 `python3 <argus>/scripts/phase_gate.py <runDir> --phase dispatch`，BLOCKED 时停在该阶段并把缺口写入 `manifest.generation`。

## 7. 脚本阶段（人工评审批准后）

评审门禁通过后交给 [脚本生成子技能](../../argus/skills/script-generation/SKILL.md)，由其负责脚本计划、分批生成、发布、discovery 和执行入口绑定。本文件只负责用例生成。

## 8. 用例分片校验

每个用例回执完成后立即运行 generation.py，确定性契约错误只修对应分片，不重新生成已校验通过的独立分片。脚本发布和临时文件清理由脚本生成子技能按产物生命周期执行。
