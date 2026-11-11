# 按复杂度调度影响探索

由宿主执行本流程，不新增角色或后台服务。索引和探索 worker 派发冻结的 generation 角色；合并冲突与义务判定派发 reasoning。影响探索在场景冻结前进行，与后续用例生成分开，两者可以共用 generation 绑定。项目源码、文档及既有用例只读，不执行业务测试或数据库核验。

## 1. 决定探索方式

PRD 输入时，主 Agent 先核对来源采集完成的初步来源清单和变更对象；已有用例输入按外部用例接入建立来源清单。核对冻结的 main@SHA，所有 worker 从同一提交读取源码与契约。检查以下可定位证据，而非按文件数量或需求标题估计复杂度：

- 变更对象有跨业务模块或服务的调用、读写、事件或契约消费者。
- 涉及消息、回调、定时任务，或多个写入者对共享状态进行并发、重试与补偿。
- 多份适用业务规则或接口契约存在冲突，需要分别核查实现和预期。

任一项成立，且至少存在两个可基于当前快照独立读取证据的任务时，启用并行探索。均不成立时单 Agent；依赖证据不足时先继续发现，保留 unknown，不能把未发现当作简单或不相关。复杂但暂不可拆分时先串行追踪至可拆分边界。单 Agent 仍完整执行六维检查，发现上述条件后重新路由。

该规则是调度条件，不给业务结论打分。依赖相关性、规则支持和义务等价等语义判断沿用 decision-router 的集中题目与概率阈值，独立问题批量提交；证据不足先补证据，不以多数票决定事实。

PRD 快照冻结后，来源清单和代码索引并行。采集只做快照和 sourceUnits；索引只依赖 PRD 原文里的对象、方法和契约名，不等 source_inventory 结束。索引墙钟 3 分钟，到点交出已命中的 `文件:行号` 和未命中符号。被测检出含 `.codegraph/` 时，索引用一次 `codegraph_explore`：`query` 写这条链上的符号，`projectPath` 指含索引的检出。一次调用拿调用路径和带行号的源码，收成不超过 3 行的窗口。没有 `.codegraph/` 时做一次限定目录检索。先看 `rg --version`，输出含 ripgrep 才用 `rg`；否则用 `grep -n`。有的宿主把 `rg` 接成别的 grep。探索任务在该对象的行号写入任务书之后才派发。清单缺口仍阻断最终冻结。索引和探索派发前运行 `python3 <argus>/scripts/dispatch.py <runConfigPath> generation`，只传它打印的非 null 参数。generation 绑定为宿主已发现的快速模型，effort 取该模型关闭思考的档位；目录里没有关闭档时用它列出的最低档。不在任务书里另写模型名。已派发的 worker 保持原模型。

## 2. 派发只读任务

主 Agent 在本 run 的 `cases/exploration.md` 记录方式、依据、来源指纹和任务清单。按业务对象或依赖链拆分；接口调用、数据消费者、异步链路、契约与权限可以成为任务边界，六个维度仍由主 Agent 逐对象核对。每个任务明确任务 ID、起点、负责范围、来源定位、跨任务交接对象和完成条件。所有已知依赖都有负责人；主 Agent 负责尚未归属的边界。

按[共享调度的规划规则](../../argus/references/scheduling.md#规划)平衡独立业务边界、预计工作量和启动成本，并在 exploration.md 记录分组依据、槽位和预算。并发数取就绪任务数、宿主可用槽位和冻结预算允许数的最小值；共享只读输入允许并行。单槽位按相同任务清单串行调度并记录实际方式。配置角色无法调用时遵循模型规范保持 BLOCKED，不能由主会话冒充。

每次派发记录宿主 Agent 标识、任务 ID、attempt、输入指纹、状态与独占回执路径 `cases/exploration/<taskId>/<attempt>.json`。worker 只写该回执，读取完整变更对象索引及相关来源，不修改共享计划、最终影响分析或评审材料。回执逐条包含来源定位、依赖路径、六维归属、当前行为、候选义务、未知项、检索边界与停止依据；业务预期附权威依据，缺失则待确认。边界外发现作为交接项返回，不能以不属本任务为由丢弃。

主 Agent 在派发前把索引写进任务书。每个对象写已经核对的 4 到 6 个 `文件:起始-结束` 窗口，窗口跨度不超过 3 行。没有行号的对象先留在索引任务，不派给探索 worker。新发现由主 Agent 汇入索引，记录覆盖边界与未搜索区域，不作为完整性证明。

探索任务按互斥的 objectId 切分，每任务 4 到 5 个对象。同一对象和同一调用链只进一个任务；跨任务的边写成 handoff，不在两个任务里各查一遍。`plan.json` 的每个 task 写 `objectIds`。任务书只传本任务的窗口、对象和来源定位，不继承完整探索历史。

worker 只读任务书给出的基线工作树。一次 shell 用 `sed -n` 输出多个已点名窗口。shell 不超过 25 次。每条 check 最多 2 条证据，每条不超过 3 行；这两条已经说明受影响或不受影响后停止，不再逐个调用者翻文件。点名窗口里没有该符号时，允许一次限定目录的检索（`rg --version` 含 ripgrep 才用 `rg`，否则 `grep -n`）；仍没有则写入 handoff。不从 `/` 或 `$HOME` 递归搜索，禁止 `git show <SHA>:<path>`。路径不存在时回报具体缺口，由主 Agent 核对后补发，不能猜测路径或忽略依赖。

墙钟 6 分钟。到点把已完成的对象写入回执，`status` 为 `partial`，并列出未完成的 objectId 与 dimension。主 Agent 只补这些缺口。partial 不能当作探索完成。

结构化回执顶层包含 `schemaVersion: 1`、`taskId`、正整数 `attempt`、与派发一致的 `inputHash`、`status`（`complete` 或 `partial`），以及 `sources`、`sourceUnits`、`objects`、`checks`、`obligations`、`handoffs` 六个数组（无项填空数组）。字段按 [回执模板](exploration-receipt.template.json) 填写，不改键名。标识带本任务前缀：`sourceUnits` 为 `<taskId>-U<n>`，`checks` 为 `<taskId>-C<n>`，`obligations` 为 `<taskId>-OB-<n>`，`handoffs` 为 `<taskId>-H<n>`，n 从 1 开始。`objects.objectId` 只用计划分配给本任务的 objectId。`disposition` 只能是 `covered`、`not_applicable`、`unknown`、`out_of_scope`。kind 为 prd 的 `sourceRef` 写 `run://` 加本 run 相对路径。候选可以有待确认字段，不能标成已批准。`checks.dimension` 只能是 `data`、`inflight`、`lifecycle`、`permission`、`side_effect`、`ordering`。可测性、夹具和执行成本写入 handoffs，不写入 checks，也不要使用 `kind` 或 `result` 字段。每个 handoff 包含唯一 `handoffId`、生产者、接收任务、依赖路径、证据定位和未决问题；无接收任务或未闭环保留未知。证据内容只写一次，其他项引用可定位的来源或回执，不复制长段源码。

主 Agent 写 `cases/exploration/plan.json`，包含 `schemaVersion: 1`、绑定冻结来源与任务输入的 `inputHash` 和 `tasks: [{taskId, attempt, objectIds, windows: [{objectId, refs}]}]`，每次重派更新当前 attempt。`windows` 每题对象写 4 到 6 个已核对过的 `文件:起始-结束`，每段不超过 3 行；`merge_impact_candidates.py` 与 `phase_gate.py --phase dispatch` 都拒绝缺窗口、条数越界和跨度超 3 行的计划。收到回执立即运行 `python3 <prd-to-cases>/scripts/merge_impact_candidates.py <plan.json> <当前回执...>`，将输出保存为候选汇总；未收到全部回执时允许 BLOCKED 的部分结果供局部整理。程序检查当前任务、attempt、输入指纹、数组/ID、`dimension`、证据条数和行跨度、任务 `objectIds`、索引窗口以及 partial 状态，按同 ID 且内容完全相同合并并保留任务映射；不同内容保留全部版本并阻断，不能按 ID、文本相似度或程序输出判定业务等价。它还核对每个声明对象的六维结论：每个 `objectId × dimension` 要么有 check，要么写 `not_applicable`/`out_of_scope` 并给出 20 字以上、说清为什么该维度不受影响的理由，缺格直接 BLOCKED 并逐条列出 `OBJ × dimension`。它不验证完整 schema、证据真实性、交接闭环或语义，也不生成最终基线；主 Agent 核验这些事项。证据真实性由 `python3 <prd-to-cases>/scripts/verify_evidence.py <merged.json> --baseline <基线工作树> --run <runDir>` 打开每条 `文件:行` 核对路径与行号，结果写 `cases/exploration/evidence-check.json`，`failed` 必须为 0。不要运行覆盖脚本。历史 Markdown 回执按原流程恢复，不转换成虚假的新回执。

收到一项结果即核对证据并处理新边界，同时整理其 sourceUnits/checks/obligations 候选，关键路径有足够证据时即可派发独立复核；最终冻结仍等待全部回执、交接闭环和跨任务复核。exploration.md 保留调度、证据索引、交接处理和未决项，细节引用独占回执，避免再完整改写全部探索结论。失败重派前停止旧 worker，增加 attempt 并使用新回执路径；迟到旧结果不合并。预算不足时保留未完成任务和缺口，不减少原范围。恢复时先核对真实 Agent 状态、来源指纹和回执，避免重复派发；来源变化按影响分析规则新建 run。

## 3. 合并与复核

主 Agent 独占最终 `impact-analysis.json`，完成以下检查才进入义务确认和场景冻结：

1. 所有任务完成且回执对应当前输入；逐项核对初始来源、变更对象和六维检查。`cases/impact-analysis.json` 的六维结论由 `matrix_errors` 硬校验：每个对象六个维度都要有 check 或带理由的 `not_applicable`/`out_of_scope`，`cases/exploration/impact-matrix.json` 记录本次格数、各处置计数和每格 checkId，`phase_gate.py --phase dispatch` 与 `--phase cases` 复核同一矩阵。缺格不许用“其他对象已覆盖”抵消。新增依赖交给已有负责人或建立后续任务，继续到有证据的停止边界。
2. 合并实际依赖和候选义务，保留所有证据引用；同条件、同结果与副作用才可合并义务。冲突回到原始代码或权威契约核查，未解决记 unknown。
3. 对跨任务交接逐条核对生产者、消费者、字段或事件语义及完整路径。任何无人接收的交接或缺回执均为未完成，不用其他任务完成率抵消。
4. 并行探索涉及关键数据、权限或多写入者交错时，由非原作者的 reasoning worker 独立复核该路径与停止依据；主 Agent 汇总差异，缺复核证据保持未知。单 Agent 分支由主 Agent 核对同样边界并如实记录未做独立复核。

在探索记录中保存任务到最终 sourceUnits/checks/obligations 的映射和未完成项。`cases/exploration.md` 的六维核对写成对象×维度的矩阵表：每格填 checkId 或带理由的 NA，空格等于漏查，不能只写六行维度汇总。将完成的探索记录及回执以 observation 来源登记到影响分析，记录原始字节哈希并由 evidenceRef/reviewRef 引用；它们仅是探索过程证据，不能代替业务批准。未完成任务、未闭合交接和未解决冲突必须进入 sourceUnits 或 checks，disposition=unknown，不能当成已覆盖或完整验收。冻结后不改回执；修改按输入变化处理。unknown 检查和独立复核留在记录里，不为了少一轮派发删掉。

报告实际串行/并行方式、任务完成与缺口、分工/探索/合并/复核的耗时和调用成本。完整墙钟时间包含全部阶段；没有同输入对照实测，不报告提速比例或准确率提升。宿主负责核查未登记来源和依赖。
