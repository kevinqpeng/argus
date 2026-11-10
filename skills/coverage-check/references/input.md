# 门禁输入格式

脚本接受一个聚合 JSON。ID 关联、状态、分母和哈希使用确定性检查；语义审查、真实 runner 发现和附件来源由宿主验证并保存原始证据。

公共字段：impactAnalysis（共享 schema 的 ImpactAnalysis）、impactHash、currentSourceHashes（按实际来源重读的 ID 到 SHA-256 映射），以及 schemaVersion=1、threshold（可省略，默认 .95）、runId、sourceHash、currentSourceHash、baselineHash、acs、scenarios、cases、requiredMatrix、discovered。对象定义见 [schemas](../../argus/references/schemas.json)。

使用 `python3 <coverage-check>/scripts/prepare_input.py RUN SPEC.json OUTPUT.json --phase design|plan|execution` 组装输入，再运行 coverage.py；不在 run 内重写组装脚本。SPEC 包含 `sourceHash`（冻结的原始输入哈希）、`baselineHash`（冻结场景哈希）、`impactHash`（冻结影响分析哈希）、`sourcePath`（本次原始输入字节文件绝对路径）、`sourceFiles`（每个 sourceId 到实际来源文件绝对路径的映射）；多文件来源的 sourcePath 指向按共享契约生成并核验的规范化来源清单。来源来自冻结 Git 提交时，路径可改为 `{ "repo": "仓库绝对路径", "commit": "完整40位提交SHA", "path": "仓库相对路径" }`，脚本读取该提交的字节。原始输入和所有来源均独立重算哈希，不能从 impactAnalysis 复制当前值。sourceHash、baselineHash、impactHash 来自冻结记录，不以当前内容重新计算代替。

plan/execution 的 SPEC 另提供 `discoveryFile`，指向宿主从真实 runner discovery 生成的数组 JSON；execution 还提供 `executionFile`，其中含 scope、inputHash、expectedShards、shards、results 的真实执行事实。外部用例的 SPEC 显式提供 inputBasis；requiredMatrix 从本 run manifest 读取。设计阶段只用 cases.json 和空 discovered；后续阶段使用 execution-cases.json。输出冲突时使用新文件，已有输入不覆盖。准备工具不验证来源真实性、批准或 discovery 的真实性，宿主仍须核实并运行原门禁。

sourceUnits 必须保存原文 sourceExcerpt 与精确 evidenceRef。每个业务步骤须有绑定断言；仅 kind=setup/cleanup 且有 assertionExemption 的步骤可豁免。生成、设计、执行入口绑定和阶段门禁共用此检查。旧材料缺字段或有无断言业务步骤时保留原件，在新基线补齐并重新评审，不回写历史批准。结构检查不证明摘录真实或原文无遗漏，来源召回与具体依赖仍须按影响分析契约审查。

inputBasis 缺省为 `{kind:"prd"}`，保持必须包含 PRD 的原规则。外部用例任务显式传 `{kind:"cases", sourceIds:[实际用例来源ID], approvalRef:真实适用依据引用}`；来源必须存在且 kind=history，依据须由宿主核实。三阶段保持相同 inputBasis，并绑定 manifest.inputs 和 inputHash；输出保留该字段，结论限定为所提供用例基线，不能冒充完整 PRD 验收。其余来源、影响、审批和覆盖检查不变。

- acs：RequirementAC 数组，sourceId 存在于 impactAnalysis.sources，sourceHash 与该来源一致，sourceRef 非空，reviewStatus=approved。
- scenarios：Scenario 数组；`baselineHash = coverage.digest(scenarios)`，数组顺序在冻结后保持不变。
- cases：TestCase 数组，额外 `semanticReview: "approved"` 表示已核查源要求及断言映射；审查证据存 decisions.json/基线记录。
- requiredMatrix：非空唯一字符串，如 `["chromium:buyer"]` 或 `["android:<deviceId>:<role>", "ios:<deviceId>:<role>"]`。移动端按每个明确授权的本地物理设备建立矩阵项；`both` 两端分别计数，不能用一端结果补足另一端。只包含业务确认必需的矩阵。
- discovered：`{caseId,matrixId,automationRef}` 数组。design 阶段使用空数组，cases 的 automationRef 必须为安全相对路径的 `planned:文件::标题`。plan/execution 阶段来自 runner 真实发现清单，automationRef 精确匹配已发布 cases，且不能为 planned；每用例在每个必需矩阵组合均需发现。

执行阶段额外字段：

- scope：full 或 partial，partial 阻断全量结论。
- inputHash：执行前冻结输入指纹。
- expectedShards：唯一分片 ID 数组，例如 `["ui-1","api-1"]`。
- shards：`{shardId,inputHash,state:"completed",resultHash}`。
- results：TestResult 数组，额外含 matrixId、shardId；attempt 从 1 连续递增。resultHash 是该分片 results 子数组的 `coverage.digest(rows)`，顺序与合并结果一致。

当前影响分析输入示例由本技能 `tests/test_coverage.py` 中 `fixture()` 构造，覆盖来源、跨模块义务与必需项门禁；这是合成自测，不能用作业务测试通过证据。

impactHash 是 coverage.digest(impactAnalysis)；sourceHash/currentSourceHash 绑定原始输入，PRD 验收保持原语义，外部用例任务绑定用例文件或稳定来源清单并注明来源类型。旧输入缺少分析会阻断，保留历史并新建 run 补齐。design、plan、execution 均核对完整来源、语义和必需矩阵；必需场景设计与执行须 100%，threshold 只影响进度指标，不放宽必需项。

出口字段：impactAnalysis（状态、哈希及来源/影响/义务处置统计）、schemaVersion、phase、status、gates、reasons、baselineHash、statusCounts。planned、executed 和 passed 分子分母分别报告；passed 是观察指标，功能错误通过独立阻断原因报告。无 P0 为 N/A，空 AC/场景为 BLOCKED。JSON 缺字段、未知状态、重复 ID、缺尝试、缺矩阵或不完整分片明确阻断。
