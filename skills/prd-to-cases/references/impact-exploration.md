# 按复杂度调度影响探索

由宿主执行本流程，不新增角色或后台服务。索引和探索 worker 派发冻结的 generation 角色；合并冲突与义务判定派发 reasoning。影响探索在场景冻结前进行，与后续用例生成分开，两者可以共用 generation 绑定。项目源码、文档及既有用例只读，不执行业务测试或数据库核验。

## 1. 先写决策表

场景冻结前先把组合分母定下来，再决定派发方式。决策表 `cases/decision-table.json` 声明决定本轮行为的维度及取值（如驳回操作人角色、驳回后路径、重新审批方式、实例发起时开关），把它们的笛卡尔积作为分母逐格裁定：

```text
{"dimensions": [{"name": "重新审批方式", "values": ["驳回后重新发起", "驳回后从当前节点继续审批"]}],
 "rows": [{"values": {"重新审批方式": "驳回后重新发起"},
           "status": "covered",
           "scenarios": ["SCN-01"],
           "expected": "驳回弹窗出现两种方式，选重新发起后重新走流程",
           "sideEffects": ["原实例置为取消，新实例重新生成任务"],
           "notExpectedToChange": ["历史审批记录仍可查看"],
           "illegalTransitions": ["取消后不能再从当前节点继续审批"]},
          {"values": {"重新审批方式": "驳回后从当前节点继续审批"},
           "status": "out_of_scope",
           "reason": "本轮只改重新发起路径，从当前节点继续审批沿用基线行为，没有规则变化"}],
 "defaults": [{"values": {"实例发起时开关": "关闭"},
               "status": "out_of_scope",
               "reason": "关闭时不出现重新审批方式选择，与基线一致，本轮不覆盖"}]}
```

- 只登记来源真正裁定过的组合。PRD 没写、但可以由维度级事实推出的（如开关关闭时行为不变），写进 `defaults` 继承到所有未被显式登记的格子。
- 继承不到、来源也没有规则时不要猜：先找 `defaults`，仍推不出就 `status: unknown` 并写 `risk`。`risk: high`（会改变本轮验收结论）进待确认事项；`medium`/`low` 必须写 `assumption`，由 `decision_table.py expand` 连同未规定原因落进 `cases/assumptions.md`，不占评审预算。判断标准只有一条——它会不会改变本轮验收结论。
- `covered` 必填 `scenarios`（一条以上）和 `expected`（页面上能看到的判定结果）；`not_applicable`/`out_of_scope`/`unknown` 的理由 20 字以上，说清为什么这一格不受影响，不能只写「不适用」。
- 同一格被两条规则同时命中且有不同结论时视为未裁定，脚本阻断，不能按书写顺序取后一条。
- 组合数上限 60 格；超出时减少取值或拆矩阵，不靠删维度绕开。

派发前跑一次分母门禁，不通过就不派发：

```text
python3 <prd-to-cases>/scripts/decision_table.py check <runDir>
```

用例合并后由同一张表展开组合矩阵，矩阵是决策表的投影，不手改：

```text
python3 <prd-to-cases>/scripts/decision_table.py expand <runDir>
```

`coverage_check.py` 会核对矩阵是否就是决策表的展开；两张表各写一套结论时阻断。决策表登记过的 `unknown` 有风险和合并口径，不算矩阵缺口；没登记的 `unknown` 仍然阻断。

## 2. 决定探索方式

PRD 输入时，主 Agent 先核对来源采集完成的初步来源清单和变更对象；已有用例输入按外部用例接入建立来源清单。核对冻结的 main@SHA，所有 worker 从同一提交读取源码与契约。检查以下可定位证据，而非按文件数量或需求标题估计复杂度：

- 变更对象有跨业务模块或服务的调用、读写、事件或契约消费者。
- 涉及消息、回调、定时任务，或多个写入者对共享状态进行并发、重试与补偿。
- 多份适用业务规则或接口契约存在冲突，需要分别核查实现和预期。

任一项成立，且至少存在两个可基于当前快照独立读取证据的任务时，启用并行探索。均不成立时单 Agent；依赖证据不足时先继续发现，保留 unknown，不能把未发现当作简单或不相关。复杂但暂不可拆分时先串行追踪至可拆分边界。单 Agent 仍完整执行六维检查，发现上述条件后重新路由。

该规则是调度条件，不给业务结论打分。依赖相关性、规则支持和义务等价等语义判断沿用 decision-router 的集中题目与概率阈值，独立问题批量提交；证据不足先补证据，不以多数票决定事实。

PRD 快照冻结后，来源清单和代码索引并行。采集只做快照和 sourceUnits；索引只依赖 PRD 原文里的对象、方法和契约名，不等 source_inventory 结束。索引墙钟 3 分钟，到点交出已命中的 `文件:行号` 和未命中符号。被测检出含 `.codegraph/` 时，索引用一次 `codegraph_explore`：`query` 写这条链上的符号，`projectPath` 指含索引的检出。一次调用拿调用路径和带行号的源码，收成不超过 3 行的窗口。没有 `.codegraph/` 时做一次限定目录检索。先看 `rg --version`，输出含 ripgrep 才用 `rg`；否则用 `grep -n`。有的宿主把 `rg` 接成别的 grep。探索任务在该对象的行号写入任务书之后才派发。清单缺口仍阻断最终冻结。索引和探索派发前运行 `python3 <argus>/scripts/dispatch.py <runConfigPath> generation`，只传它打印的非 null 参数。generation 绑定为宿主已发现的快速模型，effort 取该模型关闭思考的档位；目录里没有关闭档时用它列出的最低档。不在任务书里另写模型名。已派发的 worker 保持原模型。

## 3. 派发只读任务

主 Agent 在本 run 的 `cases/exploration.md` 记录方式、依据、来源指纹和任务清单。按业务对象或依赖链拆分；接口调用、数据消费者、异步链路、契约与权限可以成为任务边界，六个维度仍由主 Agent 逐对象核对。每个任务明确任务 ID、起点、负责范围、来源定位、跨任务交接对象和完成条件。所有已知依赖都有负责人；主 Agent 负责尚未归属的边界。

按[共享调度的规划规则](../../argus/references/scheduling.md#规划)平衡独立业务边界、预计工作量和启动成本，并在 exploration.md 记录分组依据、槽位和预算。并发数取就绪任务数、宿主可用槽位和冻结预算允许数的最小值；共享只读输入允许并行。单槽位按相同任务清单串行调度并记录实际方式。配置角色无法调用时遵循模型规范保持 BLOCKED，不能由主会话冒充。

每次派发记录宿主 Agent 标识、任务 ID、attempt、输入指纹、状态与独占回执路径 `cases/exploration/<taskId>/<attempt>.json`。worker 只写该回执，读取完整变更对象索引及相关来源，不修改共享计划、最终影响分析或评审材料。回执逐条包含来源定位、依赖路径、六维归属、当前行为、候选义务、未知项、检索边界与停止依据；业务预期附权威依据，缺失则待确认。边界外发现作为交接项返回，不能以不属本任务为由丢弃。

主 Agent 在派发前把索引写进任务书。每个对象写已经核对的 4 到 6 个 `文件:起始-结束` 窗口，窗口跨度不超过 3 行。没有行号的对象先留在索引任务，不派给探索 worker。新发现由主 Agent 汇入索引，记录覆盖边界与未搜索区域，不作为完整性证明。

探索任务按互斥的 objectId 切分，每任务 4 到 5 个对象。同一对象和同一调用链只进一个任务；跨任务的边写成 handoff，不在两个任务里各查一遍。`plan.json` 的每个 task 写 `objectIds`。任务书只传本任务的窗口、对象和来源定位，不继承完整探索历史。

计划写好后，按任务生成任务包，不手写契约字段：

```text
python3 <prd-to-cases>/scripts/generation.py packet --task <taskId> --plan <plan.json> --baseline <基线工作树> --run <runDir> --prd <相对 PRD> --source-id <ID> --out cases/exploration/packets/<taskId>.json
```

packet 从 `assemble_baseline.py` 与 `merge_impact_candidates.py` 的同一份常量写出必填字段、ID 规则、`evidenceRef` 的分号字符串形状、`emptyFields`（含 reviewRef、approvalRef 留空的理由）、`copyVerbatim.sources`、窗口原文和一份最小回执。worker 把回执写到 `packet.output`，然后原样运行 `packet.checkCommand`。失败只改这份回执。`emptyFields` 已冻结，留空合法，不再询问主 Agent。`windows[].text` 是不超过 3 行的原文，`evidenceRef` 使用其中的字面量。全部 worker 返回后运行 `python3 <prd-to-cases>/scripts/merge_impact_candidates.py --stat --plan <plan.json> --run <runDir>`。缺失或 0 字节用同一 packet 重派，再运行合并命令。

worker 只读这份任务包。已经附上原文的窗口不再执行 `sed`。shell 不超过 25 次。每条 check 最多 2 条证据，每条不超过 3 行；这两条已经说明受影响或不受影响后停止，不再逐个调用者翻文件。点名窗口里没有该符号时，允许一次限定目录的检索（`rg --version` 含 ripgrep 才用 `rg`，否则 `grep -n`）；仍没有则写入 handoff。不从 `/` 或 `$HOME` 递归搜索，禁止 `git show <SHA>:<path>`。路径不存在时回报具体缺口，由主 Agent 核对后补发，不能猜测路径或忽略依赖。`sources` 原样复制 `copyVerbatim.sources`。

墙钟 6 分钟。到点把已完成的对象写入回执，`status` 为 `partial`，并列出未完成的 objectId 与 dimension。主 Agent 只补这些缺口。partial 不能当作探索完成。

结构化回执顶层包含 `schemaVersion: 1`、`taskId`、正整数 `attempt`、与派发一致的 `inputHash`、`status`（`complete` 或 `partial`），以及 `sources`、`sourceUnits`、`objects`、`checks`、`obligations`、`handoffs` 六个数组（无项填空数组）。字段按 [回执模板](exploration-receipt.template.json) 填写，不改键名。标识带本任务前缀：`sourceUnits` 为 `<taskId>-U<n>`，`checks` 为 `<taskId>-C<n>`，`obligations` 为 `<taskId>-OB-<n>`，`handoffs` 为 `<taskId>-H<n>`，n 从 1 开始。`objects.objectId` 只用计划分配给本任务的 objectId。`disposition` 只能是 `covered`、`not_applicable`、`unknown`、`out_of_scope`。kind 为 prd 的 `sourceRef` 写 `run://` 加本 run 相对路径。候选可以有待确认字段，不能标成已批准。`checks.dimension` 只能是 `data`、`inflight`、`lifecycle`、`permission`、`side_effect`、`ordering`。可测性、夹具和执行成本写入 handoffs，不写入 checks，也不要使用 `kind` 或 `result` 字段。每个 handoff 包含唯一 `handoffId`、生产者、接收任务、依赖路径、证据定位和未决问题；无接收任务或未闭环保留未知。证据内容只写一次，其他项引用可定位的来源或回执，不复制长段源码。

主 Agent 写 `cases/exploration/plan.json`，包含 `schemaVersion: 1`、绑定冻结来源与任务输入的 `inputHash` 和 `tasks: [{taskId, attempt, objectIds, windows: [{objectId, refs}]}]`，每次重派更新当前 attempt。基线六维是分母下限，允许用可选 `extraDimensions: [{key, question}]` 追加本需求特有的维度（如 `compat` 存量兼容、`idempotency` 重试幂等）：`key` 不能与基线重名，`question` 写清它回答什么，10 字以上。分母只许增不许减——`plan` 减维、改名或覆盖基线都会被拒绝，`phase_gate` 也拒绝少于基线六维的 `impact-analysis.json`。`windows` 每题对象写 4 到 6 个已核对过的 `文件:起始-结束`，每段不超过 3 行；`merge_impact_candidates.py` 与 `phase_gate.py --phase dispatch` 都拒绝缺窗口、条数越界和跨度超 3 行的计划。收到回执立即运行 `python3 <prd-to-cases>/scripts/merge_impact_candidates.py <plan.json> <当前回执...>`，将输出保存为候选汇总；未收到全部回执时允许 BLOCKED 的部分结果供局部整理。程序检查当前任务、attempt、输入指纹、数组/ID、`dimension`、证据条数和行跨度、任务 `objectIds`、索引窗口以及 partial 状态，按同 ID 且内容完全相同合并并保留任务映射；不同内容保留全部版本并阻断，不能按 ID、文本相似度或程序输出判定业务等价。它还核对每个声明对象的六维（加计划的扩展维度）结论：有 check 的格子若结论是 `not_applicable`/`out_of_scope`，理由必须 20 字以上且说清为什么该维度不受影响，否则 BLOCKED。空格不再逐格阻断，而是登记成具名缺口：缺格率不超过三分之一时输出 `gaps`（每项 `GAP-<objectId>-<dimension>`），超过三分之一仍 BLOCKED，理由是先查完对象再登记。同一对象可写一条 `dimensions: [..]` 合并豁免无关联维度，但 `data`、`permission` 不许合并、一次最多 3 维、理由同样要写清，因为它们才是产生用例的维度。它不验证完整 schema、证据真实性、交接闭环或语义，也不生成最终基线；主 Agent 核验这些事项。证据真实性由 `python3 <prd-to-cases>/scripts/verify_evidence.py <merged.json> --baseline <基线工作树> --run <runDir>` 打开每条 `文件:行` 核对路径与行号，结果写 `cases/exploration/evidence-check.json`，`failed` 必须为 0。不要运行覆盖脚本。历史 Markdown 回执按原流程恢复，不转换成虚假的新回执。

收到一项结果即核对证据并处理新边界，同时整理其 sourceUnits/checks/obligations 候选，关键路径有足够证据时即可派发独立复核；最终冻结仍等待全部回执、交接闭环和跨任务复核。exploration.md 保留调度、证据索引、交接处理和未决项，细节引用独占回执，避免再完整改写全部探索结论。失败重派前停止旧 worker，增加 attempt 并使用新回执路径；迟到旧结果不合并。预算不足时保留未完成任务和缺口，不减少原范围。恢复时先核对真实 Agent 状态、来源指纹和回执，避免重复派发；来源变化按影响分析规则新建 run。

## 4. 合并与复核

主 Agent 独占最终 `impact-analysis.json`，完成以下检查才进入义务确认和场景冻结：

1. 所有任务完成且回执对应当前输入；逐项核对初始来源、变更对象和六维检查。若某对象的每个维度都是 `not_applicable`/`out_of_scope`，工具只给提示 `可能不是本次变更对象`，不阻断 —— 这类对象通常应当合并或删除。`cases/impact-analysis.json` 的六维（含扩展维度，顶层 `dimensions` 记录本次实际分母）结论由 `matrix_errors` 校验：有结论的格子理由必须成立，空格率超过三分之一视为探索未完成；其余空格由 `cases/exploration/impact-gaps.json` 逐条登记（`GAP-<objectId>-<dimension>` 加待确认问法），`cases/exploration/impact-matrix.json` 记录本次格数、各处置计数、每格 checkId 与 `gaps`。`phase_gate.py --phase dispatch`、`--phase cases`、`--phase presentation` 复核同一矩阵；`--phase presentation` 还要求每个 `gapId` 以 `impact-gaps.json:<gapId>` 出现在待确认事项的 `sourceRefs` 里——可以不查，但不能不登记。空格不许用“其他对象已覆盖”抵消。新增依赖交给已有负责人或建立后续任务，继续到有证据的停止边界。
2. 合并实际依赖和候选义务，保留所有证据引用；同条件、同结果与副作用才可合并义务。冲突回到原始代码或权威契约核查，未解决记 unknown。
3. 对跨任务交接逐条核对生产者、消费者、字段或事件语义及完整路径。任何无人接收的交接或缺回执均为未完成，不用其他任务完成率抵消。
4. 并行探索涉及关键数据、权限或多写入者交错时，由非原作者的 reasoning worker 独立复核该路径与停止依据；主 Agent 汇总差异，缺复核证据保持未知。单 Agent 分支由主 Agent 核对同样边界并如实记录未做独立复核。

在探索记录中保存任务到最终 sourceUnits/checks/obligations 的映射和未完成项。`cases/exploration.md` 的六维核对写成对象×维度的矩阵表：每格填 checkId、带理由的 NA 或 `GAP-<objectId>-<dimension>`，不能只写六行维度汇总；登记为 GAP 的格子必须在待确认事项里有对应条目。将完成的探索记录及回执以 observation 来源登记到影响分析，记录原始字节哈希并由 evidenceRef/reviewRef 引用；它们仅是探索过程证据，不能代替业务批准。未完成任务、未闭合交接和未解决冲突必须进入 sourceUnits 或 checks，disposition=unknown，不能当成已覆盖或完整验收。冻结后不改回执；修改按输入变化处理。unknown 检查和独立复核留在记录里，不为了少一轮派发删掉。

报告实际串行/并行方式、任务完成与缺口、分工/探索/合并/复核的耗时和调用成本。完整墙钟时间包含全部阶段；没有同输入对照实测，不报告提速比例或准确率提升。宿主负责核查未登记来源和依赖。
