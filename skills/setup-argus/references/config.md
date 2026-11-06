# 配置与模型角色

## 单文件配置

先读 [存储规则](storage.md) 与 [模型和强度](models.md)。本机单用户使用，当前 Agent 根只有一份 argus-model.json，不创建人员 profile 或额外项目配置文件。

顶层字段 schemaVersion、roles、defaultEffort、decision、execution、projects。模型角色与强度作用于当前 Agent；projects.<projectId> 保存环境/覆盖/表格/交付等项目设置。Playwright 目标及账号以 [web-private.json](storage.md) 为执行来源，不写入该模型配置或运行快照；已有项目环境字段不能覆盖它。两类选择通过同一个锁读取、合并、校验、原子写入，保留其他项目和未修改角色。

本次 --overrides 覆盖顶层角色/强度等，--project-overrides 覆盖当前 projects 条目；加 --init 才持久化，否则只影响本次运行快照。优先级保持：本次模型选择 > 当前 Agent 模型配置 > 本次项目选择 > 当前项目条目 > Skill 默认。

configPath 是唯一配置文件位置，runConfigPath 是产物目录的不可变 argus-model.json 快照。当前 Agent 原有全局设置与业务项目只读；JSON 记录选择不表示主会话已切换。继承别名规范化为 mode=delegate、model=inherit-parent，角色条目整体替换。

## 人工用例评审开关

`execution.requireHumanCaseReview` 默认 `true`，旧配置缺少字段时同样开启。普通 setup 对尚未明确选择的用户按 [阻塞问答](interaction.md#问答顺序) 引导选择，推荐“是”；等待实际回答后才保存。开启时生成用例后按 [人工用例评审门禁](../../argus/references/contracts.md#人工用例评审门禁) 等待人员批准。

用户确认选择后，Agent 通过现有 `--overrides` 或 `--project-overrides` 写入 `{"schemaVersion":1,"execution":{"requireHumanCaseReview":true}}`；选择“否”时写 `false`。加 `--init` 保存后续默认值，不加只用于新 run。沿用现有配置优先级，展示最终生效值与来源；运行时顶层 execution 覆盖项目同名字段，若覆盖导致结果与用户选择冲突，先明确保存范围并解决冲突。

开关随 runConfigPath 冻结，运行中不可修改快照；更改开关须新建 runId。关闭后自动继续其余已授权流程，原有覆盖、语义映射和安全门禁仍须通过。

## Web 辅助选择

`execution.webAssistant` 仅接受 `none` 或 `ego`，缺省执行效果为 `none`。缺失字段与明确选择 none 不同：仅 macOS 的 Web/both setup 对缺失字段按 [ego 引导](ego.md) 提问；未回答不保存默认答案。通过现有 --project-overrides 保存项目偏好，沿用 execution 优先级并展示最终来源；若顶层覆盖与回答冲突，先澄清覆盖范围。配置选择随新 run 冻结，不代表依赖 READY 或安装授权。系统限制与能力状态按 ego 引导单独记录，不改写历史快照。

## 并发与恢复

不同 Agent 各有独立配置文件。同一 Agent 配置的并发更新以稳定锁在锁内读取、合并、原子替换，唯一临时文件结束清理。Python 3.11+、macOS/Linux POSIX 本地文件系统；不满足则阻断自动持久化。

每个独立会话使用唯一 runId；相同内容可复用快照，异内容不可覆盖。doctor、capabilities、cases、harness、evidence、Bug 和回执都写本 run。后续偏好变更不影响已运行快照；切换宿主或需要新配置时新建 run。仅协调者合并同 run 的分片。

```sh
python3 <skill>/scripts/host_config.py --host codex --project <仓库> --init
python3 <skill>/scripts/host_config.py --host pi --project <同一仓库> --init
python3 <skill>/scripts/host_config.py --host claude --project <仓库> --project-overrides <私有目标设置.json> --overrides <角色覆盖.json> --run-id <唯一ID>
```

实际用户通过结构化问答设置，Agent 负责生成上述参数与私有输入文件，不要求用户编写 JSON。--state-dir 只能指向仓库外；不再提供 --tester 或人员选择。

## 字段与验证

运行前由宿主补齐并校验以下字段；未知字段报错，不静默忽略。目标、账户和发布设置归属本机，不随 Git 共享。

| 字段 | 约束 |
| --- | --- |
| schemaVersion | 固定 1 |
| host | 只在运行快照元信息中记录当前宿主 |
| roles | reasoning/generation/bug_analysis/vision；mode 为 delegate/api |
| defaultEffort / roles.*.effort | 默认强度与角色覆盖；session/model-default 或该模型实际支持档位，不能把一个宿主的级别硬套到其他宿主；决策模型强度 N/A |
| delegate 模式 | 绑定 provider/model，先验证宿主模型委派 API 与授权；没有则 BLOCKED |
| api 模式 | provider/model/protocol、baseUrl 或 baseUrlEnv 恰一、endpointPath 或 endpointPathEnv 恰一、认证环境引用；不得同时 inherit |
| vision | requires 包含 image-input；必须实测或有可信宿主能力证据 |
| prdPath | 用户指定、完整可读的实际文件；不隐式换 PRD |
| baseUrlEnv | 合法环境变量名，运行时解析后校验 allowedHosts |
| allowedHosts | 项目设置中的精确主机白名单；Playwright 私有文件省略时只允许 baseUrl 主机，显式填写时重定向每跳校验 |
| decision.visualModel | 新 run 默认 jev-1.14.0；仅用于图片判断并冻结到快照，旧配置保持兼容 |
| decision | activeProvider=jev、transport=api 默认且内置；旧配置的 Jev MCP 映射在新 run 改用内置 API；verification 不等于 READY；maxCallsPerRun 正整数，timeoutMs 正整数，maxRetries 0..2 |
| decision.providers | 自定义时使用；activeProvider 要指向已验证条目，protocol/adapterVersion/model/targetFingerprint/概率类型变化后重验 |
| coverage | scenarioThreshold 0.95..1 仅用于进度指标；必需场景设计/执行固定 100%，p0Threshold 固定 1，failOnFlaky=true |
| execution | workers 正整数，retries 0..1，资源与数据隔离实测；requireHumanCaseReview 为布尔值，默认 true |
| bugPublisher | provider 与 method 分开；provider 可为企微、飞书、其他实际平台或 local，method=auto/mcp/api/cli/browser/webhook/local，tableAction=create/existing/import；默认未启用，所选方式需能力验证 |
| delivery | mode=online/local/both，requiresReadback=true；本地也是正式交付，both 两边均需完成；onFailure=ask/local/stop，默认 ask；本地专用通道不能充当 online/both |

环境变量名匹配 `[A-Za-z_][A-Za-z0-9_]*`。URL 拒绝用户信息、token 查询和非预期协议；执行目标限制在授权主机与路径。密钥不写 config，也不输出 env 值。headers 仅记录头名与 secretEnv 引用。

记录方式及回退以 [通道规范](../../bug-reporter/references/channels.md) 为准。method=auto 不是预置全部适配器；只在已验证能力中选择。目标可用 targetEnv 或非敏感 targetUrl 引用，Webhook 中含认证信息时只能使用安全引用。本地 CSV 输入和回执路径由 run 快照推导，不写 Git。记录方式变更需新 run 或本次 manifest 中的明确回退选择，不能覆盖冻结快照。

以下是传给 --project-overrides 的本地表格设置片段；Agent 会将它写入同一份 argus-model.json 的 projects.<projectId>，不是直接放到文件顶层：

```json
{
  "schemaVersion": 1,
  "delivery": {"mode": "local", "requiresReadback": true, "onFailure": "ask"},
  "bugPublisher": {"provider": "local", "method": "local", "tableAction": "create", "enabled": true}
}
```

在线示例可选择 method=mcp/api/cli/browser/webhook 或 auto，tableAction=create/existing/import，onFailure=ask/local/stop；只有真实能力/权限验收后可执行。both 选择的是在线通道，另由本地脚本维护表格；不能用 method=local 声称完成两者。配置成功和 LOCAL_NOT_VERIFIED/NOT_VERIFIED 都不是发布回执。

自定义 decision Provider 片段（示例不代表已启用适配）：

```json
{
  "name": "laya-candidate",
  "provider": "UNItoken",
  "model": "keyan/laya-typed-decisions",
  "protocol": "unknown",
  "baseUrlEnv": "LAYA_BASE_URL",
  "endpointPathEnv": "LAYA_ENDPOINT_PATH",
  "apiKeyEnv": "LAYA_API_KEY",
  "verification": "required"
}
```

默认 Jev 由 decision-router 内置 API 脚本执行，固定官方端点；文本使用 `jev-latest`，视觉使用冻结的 `decision.visualModel=jev-1.14.0`，从宿主安全环境读取 `TYPESAFE_API_KEY`；无需用户设置 decision Provider、模型或端点。自定义 Provider 须单独验证协议；聊天模型自述置信度不能冒充校准概率。

角色覆盖示例：generation 独立 API 可指定 mode=api、provider、model、protocol=openai-chat、baseUrlEnv、endpointPathEnv、apiKeyEnv。其他角色仍使用 inherit-parent 子 Agent，decision 仍为 Jev。委派使用宿主现有认证，不要求额外 API key。无法验证执行方式不能标已切换。

doctor 需列实际工具版本/targetFingerprint/testedAt/证据路径；只发现二进制、配置项或模型名不够。此包没有供应商专用 Laya、办公 CLI 或通用配置校验运行时；宿主按此表验证，未知协议 BLOCKED。


## 生成与执行并发

`execution.generationConcurrency` 与 `execution.executionConcurrency` 是可选正整数，独立保存并冻结；设置验证拒绝零、负数、布尔值及字符串。旧 `execution.workers` 保留，仅作为执行并发兼容来源。缺省、宿主槽位和设备资源约束及队列使用见 [共享调度](../../argus/references/scheduling.md)。
