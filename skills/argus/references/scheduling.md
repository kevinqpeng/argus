# Web / Mobile 共享调度

生成和执行分别建队列，使用 `argus/scripts/scheduler.py`。宿主负责调用冻结角色、接受回执、操作 Playwright/Appium 和停止进程；脚本负责持久化领取、依赖、并发上限、资源互斥及 attempt token。它不替代生成校验、人工评审、runner discovery 或覆盖门禁。

## 规划

先按平台确认测试文件语言和 runner。Web 通常按独立 spec；Mobile 沿用现有 Appium 文件、页面封装及语言，不强制 `.spec.ts`。一个输出文件只有一个写入者，复杂文件可拆为独立文件后由主 Agent 合并。共享 fixture/Page Object/配置先由唯一任务生成并冻结，消费者通过 dependsOn 依赖它。公共任务不伪装成业务用例，不加入用例分母。

按场景数、步骤、预估 token、DB 断言复杂度、准备成本和历史同类耗时估计 estimatedSeconds；附估算依据到计划输入。默认用现有事实和轻量规则，不新增模型调用。确需语义依赖判断才批量复用 decision-router 的 Jev 题集及阈值。高优先级先行，同优先级先取预计较长的就绪任务。只有一个任务的小范围也有效；不要为了凑 5–6 组增加启动成本。短任务可在不冲突的前提下合并，空闲 worker 可复用，但必须传本次 token、范围和输入哈希，清除上次可变上下文。

共享只读输入冻结一次；worker 只读本任务清单和必要的跨任务契约。相同哈希的规则、页面地图及设备信息不重复探索；来源、构建或设备状态变化才刷新。业务执行顺序、设备排队与生成依赖分别记录。

## 队列契约与调用

每阶段在私有 run 中新增 `queue/plan.json` 和 `queue/state.json`，不覆盖旧 GenerationPlan。plan 的 schemaVersion=1，包含 runId、inputHash（绑定本阶段冻结输入）、generationConcurrency、executionConcurrency、tasks：

```json
{
  "taskId": "mobile-login", "platform": "mobile", "kind": "generation",
  "taskType": "script", "dependsOn": [], "priority": 0,
  "estimatedSeconds": 240, "timeoutSeconds": 600,
  "maxAttempts": 2, "retrySafe": true,
  "outputs": ["mobile/tests/test_login.py"], "resources": []
}
```

platform 为 web/mobile/shared；kind 为 generation/execution；taskType 区分 case/script/shared-resource/db-prepare/db-assert/test 等实际工作。outputs 是相对本阶段最终目录的独占路径，公共文件也必须登记。generationConcurrency 取冻结设置、宿主可用槽位和预算上限的最小值；executionConcurrency 独立冻结，旧 execution.workers 仅作为执行并发的兼容来源。旧配置没有新字段时生成上限按实际宿主可用槽位确定，执行缺省 1。记录解析后的数值和来源，旧运行不原地迁移。

所有 run 使用同一个机器级 `--leases <Argus产物根>/resource-leases.json`；不能按 run、项目或平台另建 registry，否则无法跨运行互斥。state 路径在现有阶段目录内，调用者仅为主 Agent。plan 与 state/registry 使用不同文件；同一 state 始终使用同一 registry。claim 前核对 inputHash 对应的真实冻结输入仍一致。

```text
python3 <argus>/scripts/scheduler.py <plan> <state> claim --leases <registry> --worker <agentId>
python3 <argus>/scripts/scheduler.py <plan> <state> status --leases <registry>
python3 <argus>/scripts/scheduler.py <plan> <state> finish --leases <registry> --task <id> --token <token> --outcome completed --stopped --evidence <receipt>
python3 <argus>/scripts/scheduler.py <plan> <state> report --leases <registry>
```

1. claim 先持久化预约再返回任务；null 表示当前无可领任务，保留等待原因。宿主收到任务后立即启动/复用 worker，传 token、独占 attempt 目录及输入清单。claim 后宿主调用失败也要登记 failed，不丢失预约。
2. 任一 worker 完成即收取回执，确认停止写入，用已有 generation.py 验证内容、哈希及审批，再 finish。completed 指本任务产物/执行事实已接受，不表示业务测试 passed。失败则 outcome=failed，保留真实错误及证据。每次回收立即 claim 补位，不等待整批。
3. 用例阶段镜像 claim/finish 到现有 GenerationState 的 running/failed/attempts；生成回执仍走原契约。脚本产物沿用现有 generation.py 回执校验和合并契约，接受前确认当前 token 对应的 worker 已停止写入。共享文件任务单独验证其输出哈希，全部必需依赖接受后才开始消费者。最终对全部业务任务执行 discovery 和 coverage 门禁。
4. status 的 overdue 由宿主每次等待返回后检查；按最近 deadline 设置等待上限。超时先取消 worker/子进程、结束 Session，再 finish failed。超时本身不释放资源、不自动重派。设备断连、Session 关闭无法确认时保留 running 与租约并报告 blocked。
5. 仅 failed 且 retrySafe=true、未超 maxAttempts 时可显式 `retry --task <id> --token <旧token>`；后续 claim 产生新 token 和 attempt，迟到旧回执拒绝。生成重试先停止旧写入者，使用新 attempt 目录；业务执行默认 maxAttempts=1/retrySafe=false，只有诊断、幂等/补偿和定向重跑条件已验证才重试。已完成上游不修改；必须修改则新建计划并按原契约使传递下游失效。
6. 恢复先核对宿主、输入哈希、running worker 和真实 Session；不能因过期自动恢复 pending。队列采用原子写与跨 run 文件锁；崩溃可能留孤儿租约，核对 registry 的 runId/taskId/token、确证进程和 Session 已停止后，由协调者在相同 registry 锁内清理并留恢复证据。宁可 blocked，也不重复操作设备。

## Web 执行

生成槽位与 Playwright workers 独立。若队列同时启动多个 runner，各 runner 的 workers 总和不能超过 executionConcurrency（最简单是每领取一个执行任务启动 workers=1）。每 worker 独立 Browser Context、认证、租户数据和输出目录。共享可变账号/业务资源不能隔离时登记 resources 使其串行；保留原登录态管理、trace、reporter 和 retries=0。

## Mobile 执行

生成不领取设备；定位器需要探针时，先用单独设备探索任务冻结页面地图，后续生成只读地图。执行前按 mobile-e2e-test 探测并核对已授权真机，逐个矩阵任务绑定真实 deviceId；多台选择遵守既有授权规则。执行 resources 至少包含 `device:<真实deviceId>`，同一设备跨 run 互斥；不同设备且数据隔离时可并行。

根据实际安装的 driver 版本验证配置，并将占用的主机端口统一登记为 `port:<host>:<number>`，不能按 driver 名分别命名同一端口。Android 核对独立 udid/systemPort，WebView/录像使用时核对 chromedriverPort/mjpegServerPort。iOS 核对独立 udid/wdaLocalPort/derivedDataPath，录像使用时核对 mjpegServerPort，并验证签名和 provisioning。配置必须与租约一致；已被外部程序占用的端口不能使用。参考 [UiAutomator2](https://github.com/appium/appium-uiautomator2-driver) 与 [XCUITest](https://appium.github.io/appium-xcuitest-driver/latest/guides/parallel-tests/) 的对应版本文档。

Session ID、实际 capabilities、探测时间和驱动版本写入受保护执行证据。正常结束或异常路径都先采证据，再在 finally 中 delete session、清理登记资源，核对 Session 已结束后 finish 释放。共享后端账号/数据也要隔离；设备锁不能防止后端数据竞争。单设备最多一个执行会话，生成仍可多并发。registry 仅约束遵守本契约的 Argus 调用，不管理外部 Appium 客户端。

## DB 与计时

DB 断言代码可独立生成共享 helper；不为生成代码连接业务数据库。DB 环境准备按所需权限及数据隔离独立执行，实际 DB 断言依赖产生业务状态的执行任务，或与该用例保持同一执行任务。只读 DB 核验沿用 mysql.md，不能用单独队列绕过审批或权限。

每 attempt 关联 taskId、worker、platform、taskType、开始/结束、超时、失败/重试和 evidence。`finish --usage <json>` 可附实测 llmSeconds、totalTokens、toolSeconds、queueSeconds、dependencyWaitSeconds、resourceWaitSeconds、agentIdleSeconds；缺失保留 unknown。宿主另外记录分析、生成、环境准备、E2E 执行、结果分析/报告的墙钟区间及可用槽位变化，写 manifest.timing 和本次计时证据。阶段墙钟不能由并行任务耗时求和替代。

report 按平台与生成/执行分组，统计占用时间、完成数、失败/重试和已知成本。占用包含 LLM/工具等待，不等于实际推理工作时间。利用率用该平台实际分配槽位时间积分作分母、worker 占用区间作分子，跨平台共享槽位不得重复计算；没有槽位轨迹则 unknown。Appium 另算设备会话占用/授权可用设备时间，合理设备等待不能计为 AI 空转。不能从完成时间差推断 LLM、工具或依赖阻塞。

对照实验必须固定输入/用例覆盖、模型、runner、环境与质量门禁，两端分别跑基线和新队列，比较全过程墙钟、token、完成率与断言。1295s→约780s 仅是已有 Web 场景目标；无实测不得宣称达标，模拟调度测试只证明调度行为。
