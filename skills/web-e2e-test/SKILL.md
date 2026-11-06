---
name: web-e2e-test
description: Runs Argus web, H5, and API end-to-end tests with Playwright. Use when Argus routes to web or for direct Argus web testing.
---

# Playwright 执行

## 输入与必读规则

读 [共享契约](../argus/references/contracts.md)、[私有工作区](../setup-argus/references/storage.md)。

输入：计划门禁通过的基线、目标/账户/租户/版本、矩阵、预算及 workspacePath。测试脚本、适配层和所有输出只写本机仓库外目录；默认不产生任何项目 Git 改动。

## 1. 创建隔离的测试环境

- 只读发现项目页面/API 和业务规则。
- 执行前确认 [Playwright 安装引导](../setup-argus/references/playwright.md) 所需依赖状态为 READY；缺失时先按引导安装并验证，不在被测仓库安装依赖。
- 在 workspacePath/harness 创建本 run 专用 Playwright 配置、脚本和依赖。显式设置 outputDir、reporter、trace、认证状态、缓存与浏览器 profile。
- 运行时只指定这份配置，不导入项目 Playwright 配置、fixtures、globalSetup、环境文件、认证状态或浏览器会话。需要复用的断言逻辑复制到私有 harness 并审查其依赖。
- 记录源码版本，不修改项目配置/fixture/package.json/锁文件。
- 大批量用 runner，浏览器探索不算批量通过。

## ego 辅助（按需）

仅当冻结配置 execution.webAssistant=ego、本机为 macOS 且 [依赖检查](../setup-argus/references/ego.md) READY 时使用。调用前读取当前 ego-browser skill 并执行其 `ego-browser skill` 指引。未选时直接执行 Playwright。

- 陌生页面才做只读结构探索，输出页面结构、稳定语义定位线索和观察证据供 harness 使用；snapshot ref 不写入长期测试脚本。熟悉页面不重复探索。
- 遵守相同目标白名单与人工评审门禁；门禁前仅只读准备，业务验证和有副作用的交互等待评审通过。
- 不复用用户登录态或从 Ego 向 Playwright 导出 Cookie/认证。TaskSpace 不等于认证隔离；未验证专用测试身份与隔离前，ego 只可访问无需认证、无敏感会话的公开页面，受保护页面由隔离 Playwright 操作。
- 失败先检查 runner 输出与 trace，证据不足再用 ego 调查；调查结论由 Playwright 原路径重放验证。ego 的截图、动作回执和模型判断不计 passed，不替换 caseId/matrixId/attempt 或覆盖分母。
- 不在 ego nodejs 内导入 Playwright 或启动额外浏览器；二者独立执行，仅传递脱敏观察事实。ego 故障记录辅助能力缺口，继续可执行的 Playwright 测试，不跳过断言或改变原结果。

## 2. 核对私有配置与授权目标

- 按 [私有工作区](../setup-argus/references/storage.md) 使用 `projectDir/web-private.json`，由执行进程读取 base URL 及每个角色/租户的专用账号。测试范围从已授权的 Argus run 获取。
- 不从项目配置、环境变量文件或共享账号推断。
- 缺少必需字段只告知文件绝对路径与字段名，等待用户自行编辑。聊天和工具输出不索取或回显账号密码，未补齐则 blocked。
- 未配置 allowedHosts 时只允许 base URL 的精确主机。配置后按显式白名单校验 base URL 和每跳重定向，且均需在授权目标内。
- 确认真实登录角色、租户、服务健康、构建版本。
- 超出已授权目标停止。启动/网络故障 error。

## 3. 隔离认证与测试数据

- 每个角色/租户/worker 独立认证，可变资源按 runId+worker+caseId 隔离。
- 优先业务 API 创建 fixtures，创建成功即登记精确资源 ID。
- 测试级 fixture 管可变对象，worker 级只共享安全连接/只读资源。

## 4. 编写并审查业务断言

按 [共享调度](../argus/references/scheduling.md) 领取文件生成任务；公共 fixture/Page Object 只生成一次。执行并发单独控制，各 runner 的 Playwright workers 总和不超过冻结 executionConcurrency。

- 生成脚本前按 [页面探索与失败诊断](../argus/references/page-diagnostics.md) 建立和复用本 run 的页面地图；纯 API 用例跳过。
- UI 用 role/label/testid，API 验证 HTTP、业务码、契约和后置状态。
- 可组合 API 造数、UI 操作、API/授权只读 DB 核对。
- 每条用例执行前才判断是否需要 MySQL 配合；需要时按 [数据库核验规则](../argus/references/mysql.md) 调用可用 MySQL 工具。不需要时不连接数据库。
- 依据业务规则审查生成代码后执行。mock 依赖单独标注。

## 5. 冻结执行清单并分片

- 从 runner 真实发现数据生成 caseId+matrixId+automationRef 清单。
- 冻结用例、矩阵、预期分片、输入哈希，再按 suite/ID/标签/目录分片。资源无法隔离时串行。
- 默认最多重试一次，保留首次失败。

## 6. 保存每次尝试的证据

- teardown 前采集失败截图、trace、相关 console/network，API 保存脱敏请求/响应/correlation ID。
- 每个 attempt 记录时间、duration、环境、断言和 error。
- 缺截图明确 evidence_missing。API 截图 N/A。
- 原始 reporter 无法适配为契约时 UNSUPPORTED，不能空结果通过。
- 具体截图检查按 [图片协议](../decision-router/references/protocol.md#图片请求与并行复核) 准备清晰原图或局部图、批量判断并由冻结 vision 复核；业务状态仍由 runner、API 和必要 DB 断言。

## 7. 复现失败并核查根因

- 按 [页面探索与失败诊断](../argus/references/page-diagnostics.md) 选择证据、限制修复并记录用量。
- 原始错误 -> 前置数据 -> 调用链 -> 服务日志 -> 必要视觉分析。
- 读 decision-router 分类，再由宿主复现归因。
- 产品/测试/环境/数据/flaky/unknown 分开。未知根因是待证实假设。
- 仅在已有修复授权下修改产品。

## 8. 清理资源并检查执行门禁

- 按本 run 资源清单幂等清理并读回。
- 取消时停止派发/子进程，留结果和待清理项。
- 进程中断后继续精确清理，不能宽泛按前缀删数据。
- 分片写独立文件，核对完整性后 coverage-check 计算执行门禁。

## 完成条件

- 完成：正常链与异常/权限链有真实断言，失败证据完整，重试仍 flaky，矩阵完整且无污染。
- 检查 Git 状态与仓库生成文件未新增本次变化。
- 确认产品缺陷交 bug-reporter 在线记录，证据上传后可由协作者访问。
- 未执行/发布未完成明确阻断。
- 认证和敏感日志只保存在个人受保护目录。
