---
name: bug-reporter
description: Records reproducible bugs in user-selected online or local tables using connectors, APIs, CLIs, browsers or verified table webhooks. Use for bug-table creation, failure triage, deduplication or permission fallback.
---

# Bug 记录与交付

## 输入与必读规则

读 [共享契约](../argus/references/contracts.md)、[私有工作区](../setup-argus/references/storage.md)、[表格通道与回退](references/channels.md)。

输入：失败 attempts、复现证据、来源版本和本机选择的目标/方式。配置、表格和恢复记录均在仓库外；本地表格是正式交付选项，不仅是在线失败的 JSON 缓存。

## 1. 分类并复现失败

- 读 decision-router 分类，再由宿主复现。
- 产品缺陷建立 Bug。环境/数据/测试问题和 unknown 单独保留，不能将全部失败发布成产品缺陷。
- severity 表示影响，priority 表示安排。

## 2. 记录缺陷信息与指纹

- Bug 内部记录项目/模块、版本/环境、AC/场景/用例、前置/复现、预期/实际、首次/最近时间、频率、证据和根因状态。面向用户的 Bug 表只展示 [表格通道与回退](references/channels.md) 指定的 12 列。
- 指纹为项目+环境类别+场景/接口+归一化错误/断言的 SHA-256，不含 runId 或动态订单号。语义近似只作候选，核查后合并。

## 3. 复用或收集交付选择

- 已有选择直接复用。缺项按 [设置问答](../setup-argus/references/interaction.md) 收集目标、操作和回退方式。
- 本地交付不依赖 CLI 或云端权限。

## 4. 准备目标表格

- 新建在授权空间创建 Bug 明细与测试汇总，回读结构和真实链接。现有表读列/枚举/人员/附件并保留人工信息。
- 导入先生成本地表，再执行导入或标 IMPORT_PENDING。
- 创建、保存与读回前不宣称成功。

## 5. 串行写入并读回

- 按目标+指纹单写入者串行查重、增改、上传附件、回读。
- CLI 用参数数组及受保护输入，API 按真实协议，浏览器用本人真实权限。
- 超时先查再重试。Webhook 接受请求/机器人消息不算落表。
- 保存目标、内容哈希、时间和记录回执，面向用户输出可打开链接。

## 6. 按所选策略处理权限失败

- 权限失败按 onFailure=ask/local/stop 处理。默认问答选择本地正式交付、换可用在线方式或停止。
- 替代通道不扩大授权，保留私有证据与远端待处理状态，不丢 Bug、不重跑测试。

## 7. 生成并验证本地表格

- 本地 CSV 调用配套 local_table.py，以指纹维护累计 Bug 表、以 runId 维护汇总。
- 要求下拉选择时，按通道规范生成并验证 Excel 工作簿，CSV 单独不能完成该要求。所选本地交付物回读及字段验证通过后才标 LOCAL_COMPLETED。
- Excel 按可用表格工具生成验证，不伪造扩展名。
- 保留人工状态/优先级。表头变更先报告，不清空重建。

## 8. 写入每次测试的汇总

- 每次测试都按 [表格通道与回退](references/channels.md) 的 8 列模板写所选目标的测试汇总。完整 JSON/报告仍保留范围/版本、覆盖/P0、状态计数、未测项、Bug 链接和清理。
- 零 Bug 不造缺陷行。
- 在线写远端汇总，本地写 runs 表。证据真实可访问，私有路径不当共享 URL。

## 完成条件

- 完成：按用户接受的范围验证表格、汇总、证据与回执。
- online 需在线回读，local 可独立正式完成，both 需两边完成。
- 回退本地时单列在线未同步。
- 仍要求在线的任务保持部分完成，不能把 LOCAL_COMPLETED 当作在线成功。
- 后续仅恢复发布并查重。
