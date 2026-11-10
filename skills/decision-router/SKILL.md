---
name: decision-router
description: Routes semantic test judgments using typed Jev or verified Laya responses and probability thresholds. Use for scenario routing, assertion mapping, failure classification or semantic duplicate candidates.
---

# 类型决策

## 输入与必读规则

读 [共享契约](../argus/references/contracts.md)、[集中问题与阈值](references/questions.json) 和 [协议与执行](references/protocol.md)。平台选择另按 [平台路由规则](references/platform-routing.md) 执行。所有措辞、选项、rubric、阈值和版本只在 questions.json 维护。宿主做多步分析、代码、算术和工具执行；Jev 做判断。

## 1. 绑定问题与证据

- 变更影响阶段按 prd-to-cases 的影响分析契约使用 impact_relevance、obligation_equivalence、rule_support；绑定具体依赖路径或规则证据。缺证据先补齐；no 不自动排除，低置信度保留 unknown，高置信度不代替业务确认。
- 来源冻结前用 source_inventory 对照原文检查漏项、impact_evidence 检查逐维依赖证据。题目之间不读取彼此答案，独立问题批量提交；缺口解决前不能用较高的另一项概率放行。
- 恢复或运行中配置变更按 [配置变更路由](references/config-changes.md) 使用 run_config_change，判断业务影响；不以配置哈希或模型名称变化自动新建 run。
- 形式化验证适用性按 [形式化路由](references/formal-routing.md) 处理，选中后调用 Argus 子技能；不与平台路由互斥。
- 选当前步骤需要的题目。绑定明确 AC/场景/用例 ID 与可定位证据。
- 只发送已授权且脱敏的相关 state。
- 多个可同时为真的路由分别用 noul。
- 具体场景的问题 instructions 须包含主体/ID，不依靠题目 ID 传递含义。

## 2. 批量调用 Jev

- 内置 Jev 使用宿主安全环境中的 `TYPESAFE_API_KEY`。
- 用户只需填写该 Key，不配置 Provider、模型或端点。
- 先按 [文本输入契约](references/protocol.md) 从完整证据构造 subjects/evidenceHash，再将脱敏 state 和 `{实例ID:{questionId:模板ID,subject:具体主体及证据引用}}` 写入仓库外私有 JSON，同一模板可绑定多个主体，措辞与 criteria 保留集中定义。旧 `{题目ID:绑定主体后的 instructions}` 问题格式仍兼容；旧字符串 state 要迁移为结构化证据。执行 `python3 <本目录>/scripts/jev.py <input.json> --raw <raw.json> --output <normalized.json>`。
- 独立问题按协议的 maxBytes 合批，有依赖的分阶段。逐题保留概率和回执关联；已完成且缓存键一致的判断直接复用，补证据后仅重审变化主体及受影响判断。语义不确定交宿主核查，传输重试按下方错误规则处理。截图判断按 [图片协议与并行复核](references/protocol.md#图片请求与并行复核) 准备证据并传冻结 run 配置。

## 3. 处理调用前置条件缺失

- Key 缺失或 API 不可用时标 BLOCKED 并引导填写或检查 Key。
- 自定义 Provider 仅在用户明确要求时验证。

## 4. 归一化并校验响应

- 保存原始脱敏响应。按真实协议归一化，读 probabilities/confidence（noul 无需 confidence）。
- 使用 `python3 <本目录>/scripts/decisions.py <normalized.json>` 检查类型、选项集、有限概率、概率和、阈值。
- 输入结构见 protocol.md。
- 脚本是归一化检查器，不负责假造或解释 HTTP 协议。

## 5. 按概率与阈值决定后续处理

- choice 结合 top 与次位差。noul 中间带为 uncertain。score 检查阈值两侧概率和双峰。
- 文本判断退出 2 或 host-analysis 时由当前宿主进一步核查证据。视觉判断固定 vision-review，由冻结的 vision 复核；冲突交 bug_analysis，角色不可用时保持未解决。仍不明确写 needs_clarification。
- 高概率也不能豁免 P0、授权或断言证据。

## 6. 保存决策与缓存依据

- 传 --metrics RUN/metrics.jsonl 采集成功、失败及调用前拒绝。写 DecisionRecord：题目/阈值版本、provider/model/protocol、inputHash、rawEvidenceRef、normalized、thresholds、route、fallbackReason、usage（不可得写 unknown）。
- 缓存键必须含来源/PRD 版本、模型、协议、输入哈希和题目阈值版本。

## 处理请求错误

- 401、模型不存在、协议不兼容直接阻断。
- 429、超时、暂时服务故障按 Retry-After 或抖动退避最多配置重试次数。
- jev.py 不自动重试；只对 retryable=true 的传输故障由宿主按冻结预算重试，最多 2 次。输入或响应契约错误 retryable=false，先修对应输入或协议，不重复调用。
- 不得将请求错误当作 no。

## 完成条件

完成：每个语义判断有证据与概率或明确不确定状态。
