# Web / Mobile 共享调度

生成和执行分别建队列，使用 `argus/scripts/scheduler.py`。宿主负责调用冻结角色、接受回执、操作 Playwright/Appium 和停止进程；脚本负责持久化领取、依赖、并发上限、资源互斥及 attempt token。它不替代生成校验、人工评审、runner discovery 或 scripts 门禁。

## 规划

先按平台确认测试文件语言和 runner。Web 通常按独立 spec；Mobile 沿用现有 Appium 文件、页面封装及语言，不强制 `.spec.ts`。一个输出文件只有一个写入者，复杂文件可拆为独立文件后由主 Agent 合并。共享 fixture/Page Object/配置先由唯一任务生成并冻结，消费者通过 dependsOn 依赖它。公共任务不伪装成业务用例，不加入用例分母。

按场景数、步骤、预估 token、DB 断言复杂度、准备成本和历史同类耗时估计 estimatedSeconds；附估算依据到计划输入。默认用现有事实和轻量规则，不新增模型调用。确需语义依赖判断才批量复用 decision-router 的 Jev 题集及阈值。高优先级先行，同优先级先取预计较长的就绪任务。只有一个任务的小范围也有效；不要为了凑 5–6 组增加启动成本。短任务可在不冲突的前提下合并，空闲 worker 可复用，但必须传本次 token、范围和输入哈希，清除上次可变上下文。

影响探索和用例生成规划时，先识别可独立处理的业务域与依赖，再估计每组工作量、启动与交接成本以及宿主实际可用槽位和预算。影响探索的读法、互斥对象、6 分钟 partial 回执和 shell 上限以 prd-to-cases 的探索调度为准。用例生成与评审表分片的 shell 上限是 6，且不读仓库和 PRD 全文。探索回执和评审分片在进入合并前先按对应脚本核对产物存在且字节数大于 0，缺失或空文件用同一任务包重派。存在多个有意义的独立任务时，优先保留足够的就绪任务利用可用槽位；预计短任务的启动与交接成本抵消并行收益时可合并，强耦合任务按业务边界合并。复杂任务有五个独立任务且五个槽位可用时，可并行五组；小范围只有一个内聚任务时可单组。记录分组、合并依据、估计耗时和并发上限，不因凑满槽位拆散业务义务或压缩覆盖。

收回任务时记录实际开始、结束、等待和重派；无数据标 unknown。比较估计和实际完成时间，若某组明显拖住其他组，在后续任务或新计划中沿可独立验证的业务边界细分；已冻结计划按原变更和回执规则处理。没有同输入实测时不声称提速。

共享只读输入冻结一次；worker 只读本任务清单和必要的跨任务契约。相同哈希的规则、页面地图及设备信息不重复探索；来源、构建或设备状态变化才刷新。业务执行顺序、设备排队与生成依赖分别记录。

补齐或证据核验任务的派发包列明具体缺口、已知文件及工具的核实后绝对路径、必要输入、独占输出和完成条件。独立文件读取、校验和互不依赖的问题按适用工具一次批量提交，输出保留文件和失败项对应关系，失败只补查相关项；避免一文件一模型轮次。每个补齐任务以约 30 轮为检查点而非完成门槛：届时先保存已完成内容、未完成清单和证据到独占目录并向主 Agent 回报，未完成不得标通过，主 Agent 可按原范围继续或拆分。按预计工作量设置任务截止时间；宿主或请求工具支持超时参数时显式设置并受截止时间约束，不支持时由宿主按截止时间检查并取消，不能假定参数生效。超时或请求挂起保留已落盘进度与真实错误，确认旧 worker 停止后按原重派规则处理，不以缩小范围替代完成。

## 新 run 执行规划（必需）

先从冻结 runConfigPath 读取 `config.execution`。只有 `parallelEnabled=true` 允许并行；缺失或 false 固定执行上限 1，不因旧 workers=2 或 executionConcurrency 自动开启。启用后用户上限依次取 `maxConcurrency` > 旧 `executionConcurrency` > 旧 `workers`；均未填写则仅受实际资源限制。setup 新默认不再注入 workers，避免默认值覆盖动态结果；旧 workers 仅在用户明确启用后作为上限。生成并发不受此开关限制。

主 Agent 在每个新 run 的实际任务已知后、首次执行 claim 前运行：

```text
python3 <argus>/scripts/execution.py <draft-plan.json> <runConfigPath> <inventory.json> <queue/plan.json> --web-private <projectDir/web-private.json> --mobile-private <projectDir/mobile-private.json>
```

只传范围内存在的私有文件。输出使用独占创建，已有文件不可覆盖；历史 plan/state 按原格式恢复，不重新计算。规划结果随队列哈希冻结，记录 executionPlanning、executionConcurrency、bindings 和缺资源任务的 blockedReason。后续资源减少时只可少启动或阻塞，增加资源并提高上限需新 run；其他阶段的执行计划沿用本 run 已冻结上限，再受本阶段资源限制。命令不启动 runner、不检查真实设备或登录策略，也不代表任何权限/评审门禁通过。

`inventory.json` 由宿主依据真实探测生成，不能用猜测数字：

```json
{
  "hostSlots": ["machine:executor-1", "machine:executor-2"],
  "web": [{"id": "web-1", "ready": true, "resources": ["browser:machine:1"]}],
  "mobile": [{"id": "android-1", "ready": true, "authorized": true,
              "resources": ["device:actual-device", "port:machine:8200"]}]
}
```

hostSlots 是本机跨 run 共用的稳定执行槽位，不能按 run 重命名；所有宿主/平台共用同一个 registry。web 条目代表可创建独立 Context 的已验证槽位；mobile 条目必须匹配已授权物理设备矩阵，端口已探测空闲且包含实际 driver 所需全部端口，iOS 的独占 derivedDataPath 也登记资源。route id 只是安全引用；不得放密码、URL 凭据或认证状态。宿主启动前再次核对资源及权限；探测失败的 route 用 ready=false，缺槽位/设备/匹配账号的任务保持 blocked，不能跳过它来宣称全量通过。

执行任务使用以下补充字段（生成任务不使用）：

```json
{
  "requiresLogin": true,
  "accountRequirements": [{"role": "admin", "tenant": "tenant-a"}, {"role": "user", "tenant": "tenant-a"}],
  "isolationVerified": true,
  "routeIds": ["web-1"],
  "resources": ["business:environment:order-42"]
}
```

requiresLogin 缺省 true；公开页面/API 明确写 false，此时不受账号数量限制。accountRequirements 必须列完整角色/租户组合；planner 从私有池计算组合，整个组合一次性预约。routeIds 可省略表示所有本平台已验证路线；设备/浏览器矩阵任务必须限制为对应路线。业务先后使用 dependsOn，共享可变对象登记稳定且跨 run 相同的 business 资源名。isolationVerified 仅在数据可隔离性有证据时设 true；未知保持 false。需要主观语义判断时批量使用既有 Jev 路由；布尔字段不替代证据。

规划器以依赖就绪层和资源贪心匹配计算保守并发，取宿主槽位、用户上限、浏览器/设备、端口、账号组合和业务锁可容纳任务数的最小值；不宣称最优装箱。账号登录策略未知或业务隔离未知的任务领取全局串行锁，与其他执行任务互斥。无资源时上限保留 1，但绑定为空且不可领取。账号租约同时使用规范化 username 哈希和可选 canonical principalId 哈希，跨平台同名账号也保守互斥；别名对应同一实际账号时必须在两个本地文件填写相同 principalId，无法确认映射或登录策略时 loginPolicyVerified=false。

claim 返回的 `binding` 是实际预约的路线、账号引用和资源集合；只能按此 binding 启动一个 workers=1 的 runner。runner 直接调用 `execution.private_file` 和 `execution.resolve_accounts(data, binding)`，在内存解析凭据并核对账号身份未漂移；主 Agent 不读取/复制密码到任务消息。私有文件改名、池重排或身份不匹配必须新 run。计划内 dataNamespace 再拼接 worker/attempt token/caseId，隔离业务数据、trace、认证状态和输出；清理仅处理本任务登记的数据。绑定、账号哈希也是受保护元数据，不输出到公共报告。

主 Agent 负责启动、收回和补位多个脚本执行任务；scheduler 只是领取/互斥程序，不会自行创建子 Agent 或子进程。宿主无法并发启动时传一个实际 hostSlot，报告串行。启动失败后先证明没有遗留 worker/session，才 finish failed；无法证明停止时保留租约。历史 running 会话恢复前先核对其原资源租约与当前执行者，不能将未登记账号的旧 run 与新 run 混跑；先完成/停止旧会话再启用新规划。

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

platform 为 web/mobile/shared；kind 为 generation/execution；taskType 区分 case/script/shared-resource/db-prepare/db-assert/test 等实际工作。outputs 是相对本阶段最终目录的独占路径，公共文件也必须登记。generationConcurrency 取冻结设置、宿主可用槽位和预算上限的最小值；executionConcurrency 由上述新 run 资源规划器独立冻结。旧配置没有新字段时生成上限按实际宿主可用槽位确定，执行缺省 1。记录解析后的数值和来源，旧运行不原地迁移。

所有 run 使用同一个机器级 `--leases <Argus产物根>/resource-leases.json`；不能按 run、项目或平台另建 registry，否则无法跨运行互斥。state 路径在现有阶段目录内，调用者仅为主 Agent。plan 与 state/registry 使用不同文件；同一 state 始终使用同一 registry。claim 前核对 inputHash 对应的真实冻结输入仍一致。

```text
python3 <argus>/scripts/scheduler.py <plan> <state> claim --leases <registry> --worker <agentId>
python3 <argus>/scripts/scheduler.py <plan> <state> status --leases <registry>
python3 <argus>/scripts/scheduler.py <plan> <state> finish --leases <registry> --task <id> --token <token> --outcome completed --stopped --evidence <receipt>
python3 <argus>/scripts/scheduler.py <plan> <state> report --leases <registry>
```

1. claim 先持久化预约再返回任务；null 表示当前无可领任务，保留等待原因。宿主收到任务后立即启动/复用 worker，传 token、独占 attempt 目录及输入清单。claim 后宿主调用失败也要登记 failed，不丢失预约。
2. 任一 worker 完成即收取回执，确认停止写入，用已有 generation.py 验证内容、哈希及审批，再 finish。completed 指本任务产物/执行事实已接受，不表示业务测试 passed。失败则 outcome=failed，保留真实错误及证据。每次回收立即 claim 补位，不等待整批。
3. 用例阶段镜像 claim/finish 到现有 GenerationState 的 running/failed/attempts；生成回执仍走原契约。脚本产物沿用现有 generation.py 回执校验和合并契约，接受前确认当前 token 对应的 worker 已停止写入。共享文件任务单独验证其输出哈希，全部必需依赖接受后才开始消费者。最终对全部业务任务执行 discovery 和 scripts 门禁。
4. status 的 overdue 由宿主每次等待返回后检查；按最近 deadline 设置等待上限。超时先取消 worker/子进程、结束 Session，再 finish failed。超时本身不释放资源、不自动重派。设备断连、Session 关闭无法确认时保留 running 与租约并报告 blocked。
5. 仅 failed 且 retrySafe=true、未超 maxAttempts 时可显式 `retry --task <id> --token <旧token>`；后续 claim 产生新 token 和 attempt，迟到旧回执拒绝。生成重试先停止旧写入者，使用新 attempt 目录；业务执行默认 maxAttempts=1/retrySafe=false，只有诊断、幂等/补偿和定向重跑条件已验证才重试。已完成上游不修改；必须修改则新建计划并按原契约使传递下游失效。
6. 恢复先核对宿主、输入哈希、running worker 和真实 Session；不能因过期自动恢复 pending。队列采用原子写与跨 run 文件锁；崩溃可能留孤儿租约，核对 registry 的 runId/taskId/token、确证进程和 Session 已停止后，由协调者在相同 registry 锁内清理并留恢复证据。宁可 blocked，也不重复操作设备。

## 探索与脚本生成重叠

人工评审通过后，把页面探索与脚本生成放在同一阶段队列，复用 dependsOn，不新增并发配置。页面任务使用 kind=execution、taskType=exploration，计入 executionConcurrency；脚本仍为 kind=generation、taskType=script。宿主可用槽位不足时串行并记录实际方式，不声称已重叠。

协调者按探索计划合并页面任务并保留进入状态的 dependsOn；每个脚本任务依赖其全部用例涉及的页面任务及公共 fixture。页面任务返回独立回执，协调者用真实探针校验本任务全部必要元素，写入共享地图后才 finish completed。随后对待派发 spec 的完整 caseIds 执行局部 check，保存不可变地图快照、哈希和回执；claim 后启动 worker 前再次核对，失败则停止并登记 failed，不启动生成。全量 cases 与 requiredMatrix 始终不变。纯 API 脚本没有页面依赖。

同一浏览器会话或真机登记共享资源锁，页面有顺序依赖或后端数据无法隔离时串行；不同隔离会话才可并发。allow 检查、预算预约和事件合并由协调者串行处理，不能重复使用同一份剩余预算。页面完成即释放会话资源并补位，脚本生成不占设备。探索任务不计业务用例或通过数量。

全量执行仍等待无 caseIds 的 check、全部脚本发布、discovery 和 scripts 门禁。人工评审通过即接受执行。相关地图失效沿现有新计划规则重建受影响下游，保留无关且仍有效的产物。记录首个 spec 就绪时间、全量生成墙钟及探索/生成重叠区间；无真实时间记录不宣称提速。

## Web 执行

生成槽位与 Playwright workers 独立。若队列同时启动多个 runner，各 runner 的 workers 总和不能超过 executionConcurrency（最简单是每领取一个执行任务启动 workers=1）。每 worker 独立 Browser Context、认证、租户数据和输出目录。共享可变账号/业务资源不能隔离时登记 resources 使其串行；保留原登录态管理、trace、reporter 和 retries=0。

## Mobile 执行

生成不领取设备；定位器需要探针时，先用单独设备探索任务准备相关地图，依赖就绪的脚本读取固定快照，其他页面探索可继续。执行前按 mobile-e2e-test 探测并核对已授权真机，逐个矩阵任务绑定真实 deviceId；多台选择遵守既有授权规则。执行 resources 至少包含 `device:<真实deviceId>`，同一设备跨 run 互斥；不同设备且数据隔离时可并行。

根据实际安装的 driver 版本验证配置，并将占用的主机端口统一登记为 `port:<host>:<number>`，不能按 driver 名分别命名同一端口。Android 核对独立 udid/systemPort，WebView/录像使用时核对 chromedriverPort/mjpegServerPort。iOS 核对独立 udid/wdaLocalPort/derivedDataPath，录像使用时核对 mjpegServerPort，并验证签名和 provisioning。配置必须与租约一致；已被外部程序占用的端口不能使用。参考 [UiAutomator2](https://github.com/appium/appium-uiautomator2-driver) 与 [XCUITest](https://appium.github.io/appium-xcuitest-driver/latest/guides/parallel-tests/) 的对应版本文档。

Session ID、实际 capabilities、探测时间和驱动版本写入受保护执行证据。正常结束或异常路径都先采证据，再在 finally 中 delete session、清理登记资源，核对 Session 已结束后 finish 释放。共享后端账号/数据也要隔离；设备锁不能防止后端数据竞争。单设备最多一个执行会话，生成仍可多并发。registry 仅约束遵守本契约的 Argus 调用，不管理外部 Appium 客户端。

## DB 与计时

DB 断言代码可独立生成共享 helper；不为生成代码连接业务数据库。正式测试需要查库时，主 Agent 派发独立 SQL agent 执行 `kind=execution`、`platform=shared`、`taskType=db-assert` 任务，复用冻结 reasoning 角色；按 [数据库核验规则](mysql.md) 接收并合并回执。任务使用 `requiresLogin=false`（仅免除 Web/Mobile 账号绑定，数据库认证与授权仍必需），保留业务资源锁和实际隔离证据，计入冻结执行并发上限。后置核验 dependsOn 产生业务状态的任务，清理任务 dependsOn 核验任务；前置基线如为断言必需，则先派 SQL agent 采集，业务任务依赖其回执。普通 runner 不内联数据库断言，生成的 helper 仅供 SQL agent 调用。DB 环境准备不触发业务数据库探测；造数和清理由业务 API 负责。

每 attempt 关联 taskId、worker、platform、taskType、开始/结束、超时、失败/重试和 evidence。`finish --usage <json>` 可附实测 llmSeconds、totalTokens、toolSeconds、queueSeconds、dependencyWaitSeconds、resourceWaitSeconds、agentIdleSeconds；缺失保留 unknown。宿主另外记录分析、生成、环境准备、E2E 执行、结果分析/报告的墙钟区间及可用槽位变化，写 manifest.timing 和本次计时证据。阶段墙钟不能由并行任务耗时求和替代。设计交付从输入接收到最终文件发布另设总区间（manifest.timing 的 designTotal），保留失败与返工在内的实际墙钟；去掉作废轮次的重算值单列为反事实估计，不能当实测提速。已嵌入 input 的探测不再重复相加；阶段有重叠时按区间并集统计活动时间。设计交付至少记录探索、汇总、用例生成每轮、语义请求与低置信度处理、冻结、呈现、核对/发布的开始和结束，人工等待单独记录。复用 manifest.timing 和现有任务回执，首阶段开始即打点；只有 mtime 的历史数据标为产物时间边界估计，重叠区间按并集计算总墙钟，未知起点保留 unknown。

report 按平台与生成/执行分组，统计占用时间、完成数、失败/重试和已知成本。占用包含 LLM/工具等待，不等于实际推理工作时间。利用率用该平台实际分配槽位时间积分作分母、worker 占用区间作分子，跨平台共享槽位不得重复计算；没有槽位轨迹则 unknown。Appium 另算设备会话占用/授权可用设备时间，合理设备等待不能计为 AI 空转。不能从完成时间差推断 LLM、工具或依赖阻塞。

对照实验必须固定输入/用例覆盖、模型、runner、环境与质量门禁，两端分别跑基线和新队列，比较全过程墙钟、token、完成率与断言。1295s→约780s 仅是已有 Web 场景目标；无实测不得宣称达标，模拟调度测试只证明调度行为。
