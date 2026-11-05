# 门禁输入格式

脚本接受一个聚合 JSON。ID 关联、状态、分母和哈希使用确定性检查；语义审查、真实 runner 发现和附件来源由宿主验证并保存原始证据。

公共字段：impactAnalysis（共享 schema 的 ImpactAnalysis）、impactHash、currentSourceHashes（按实际来源重读的 ID 到 SHA-256 映射），以及 schemaVersion=1、threshold（可省略，默认 .95）、runId、sourceHash、currentSourceHash、baselineHash、acs、scenarios、cases、requiredMatrix、discovered。对象定义见 [schemas](../../argus/references/schemas.json)。

- acs：RequirementAC 数组，sourceId 存在于 impactAnalysis.sources，sourceHash 与该来源一致，sourceRef 非空，reviewStatus=approved。
- scenarios：Scenario 数组；`baselineHash = coverage.digest(scenarios)`，数组顺序在冻结后保持不变。
- cases：TestCase 数组，额外 `semanticReview: "approved"` 表示已核查源要求及断言映射；审查证据存 decisions.json/基线记录。
- requiredMatrix：非空唯一字符串，如 `["chromium:buyer"]` 或 `["android:<deviceId>:<role>", "ios:<deviceId>:<role>"]`。移动端按每个明确授权的本地物理设备建立矩阵项；`both` 两端分别计数，不能用一端结果补足另一端。只包含业务确认必需的矩阵。
- discovered：`{caseId,matrixId,automationRef}` 数组，来自 runner 真实发现清单；automationRef 精确匹配 cases。每用例在每个必需矩阵组合均需发现。

执行阶段额外字段：

- scope：full 或 partial，partial 阻断全量结论。
- inputHash：执行前冻结输入指纹。
- expectedShards：唯一分片 ID 数组，例如 `["ui-1","api-1"]`。
- shards：`{shardId,inputHash,state:"completed",resultHash}`。
- results：TestResult 数组，额外含 matrixId、shardId；attempt 从 1 连续递增。resultHash 是该分片 results 子数组的 `coverage.digest(rows)`，顺序与合并结果一致。

当前影响分析输入示例由本技能 `tests/test_coverage.py` 中 `fixture()` 构造，覆盖来源、跨模块义务与必需项门禁；这是合成自测，不能用作业务测试通过证据。

impactHash 是 coverage.digest(impactAnalysis)；PRD sourceHash/currentSourceHash 保持原有语义。旧输入缺少分析会阻断，保留历史并新建 run 补齐。必需场景设计与执行须 100%，threshold 只影响进度指标，不放宽必需项。

出口字段：impactAnalysis（状态、哈希及来源/影响/义务处置统计）、schemaVersion、phase、status、gates、reasons、baselineHash、statusCounts。planned、executed 和 passed 分子分母分别报告；passed 是观察指标，功能错误通过独立阻断原因报告。无 P0 为 N/A，空 AC/场景为 BLOCKED。JSON 缺字段、未知状态、重复 ID、缺尝试、缺矩阵或不完整分片明确阻断。
