---
name: setup-argus
description: Configures Argus model mappings, reasoning effort, tools and bug-recording channels. Use for setup-argus, Argus doctor, model changes or scoped reset.
---

# 初始化与诊断

## 输入与必读规则

先读 [共享契约](../argus/references/contracts.md) 和 [存储规则](references/storage.md)。

解析或保存配置时读 [配置规范](references/config.md)，收集选择时读 [阻塞问答](references/interaction.md)，模型阶段读 [角色映射与推理强度](references/models.md)，宿主发现时读 [适配说明](references/hosts.md)。

输入：可选的只读项目、本机、宿主元信息与已有选择。

恢复已有任务（含切换模型或跨 Agent 接管）先走 [Argus 恢复入口](../argus/references/resume.md)，直接读取原快照；已有 run 的配置调整先走[配置变更路由](../decision-router/references/config-changes.md)，不直接进入初始化；下面的冻结步骤仅用于已确定需要的新 run。

源码项目路径不是必填。

没有项目时使用当前会话工作目录作为工作区身份，不因缺少源码阻断测试。

配置保存当前 Agent 原生目录下独立的 argus 命名空间。

证据和 run 快照仍在仓库外个人工作区。

setup 不产生项目 Git 改动。

## setup 产物边界

- setup 只保存已确认的配置、所需私有配置模板、run 快照、必要诊断证据，以及经授权安装且后续执行需要的依赖。用例、执行结果、Bug 表、测试汇总和测试报告按 Argus 对应阶段生成；选择记录格式或目标不触发生成或发布。
- 本地能力探测的 CSV、Excel、输入、脚本及其他一次性文件放在本轮独立临时目录，验证后清理；失败或中断也清理。不要在项目目录、累计表目录或交付目录用正式名称创建探测文件。不得修改已有表格或历史产物。
- 在线通道的 setup 只读核对身份、目标、结构及可查询的权限；不创建、导入、修改或删除在线表、记录、附件。无法只读证明写入能力时标未验证，实际写入和读回留到测试后的发布阶段。
- 开始前记录本轮可能写入的目标及已有文件状态，结束时核对本轮新增文件：保留上面允许的必要产物，清理可确认属于本轮的一次性探测材料；不删除已有文件、共享依赖、历史证据或其他 run 的文件。清理失败报告残留位置和原因，不标为已清理。只读 doctor 不创建或修改任何文件。

仅配置或测试 MySQL 时，直接按 [MySQL 项目配置](references/storage.md#mysql-项目配置) 执行，跳过下面的完整 setup 步骤。

其他 setup 在收集选择前必须读取 [阻塞问答](references/interaction.md#工具选择)，调用本轮允许的真实问答工具，再等待实际回答。暂停的是 setup 的后续步骤，不是无条件结束当前轮；工具返回请求已接受不等于用户已回答或弹窗已显示。

## 1. 识别当前宿主

- 从当前会话识别宿主，区分配置默认模型和会话实际模型。
- 不要从模型名猜宿主。
- 先用宿主公开元信息。再按该版本规则选择非敏感配置来源和层级。

## 2. 只读发现配置

普通 setup 记录宿主发现、配置读取、各前置检查、问答等待、保存各阶段的单调时钟耗时，区分实际工作与等待用户的时间；仅报告实际测得的结果。提问前并发执行互不依赖的只读宿主发现、Jev 工具/Key 状态、MySQL 配置检查及已知范围的依赖探测；确定范围或路径后立即并发启动剩余独立检查，合并结果再展示。单次 setup 中相同输入的发现与验证结果复用，输入变化或安装后只重验受影响项；实际连接和 smoke 检查仍执行。普通 setup 必须独立检查 Jev、当前项目 MySQL，以及所选 Playwright 的前置条件；即使配置发现、问卷或保存失败，也先完成不依赖失败步骤的检查并展示实际结果。需要已创建 harness 的检查在具备路径后执行，暂不可执行时明确报告原因和后续操作，不以「稍后检查」代替可执行的检查。

- 先运行 `python3 <本目录>/scripts/host_config.py --help`。
- --host 选实际宿主，--runtime-dir/原生环境变量确定 Agent 根目录。只使用根目录的一份 argus-model.json，顶层保存模型设置，projects 保存各项目设置。
- 没有人员 profile 或 --tester。
- --state-dir/ARGUS_HOME 只控制产物根。
- 初次只读发现，不加 --init。仅在有源码仓库时记录 Git 状态。
- 发现返回 BLOCKED 时先依据 stage、reason 和本地脱敏证据排查，不能把未知错误解释为缺少应用名称或项目路径。
- 移动端配置查找及 `--project-id` 复用遵循存储规则。
- 在任何选择提问之前，按 [MySQL 项目配置](references/storage.md#mysql-项目配置) 检查当前项目 MySQL 配置并展示状态及缺项引导。普通 setup 缺文件时创建私有空模板；只读 doctor 不创建文件或连接数据库。已完整配置的连接验证按存储规则处理。

## 3. 核对配置来源与实际模型

- 脚本仅探测已知用户级候选，默认结果是 candidate，不是最终有效模型。
- 宿主核对 managed/user/project/profile/env/启动覆盖后，可按低到高顺序多次传 `--config`。把完整解析依据写入 doctor。
- `--profile` 适用 Codex。
- agy 的 JSON 候选需用已安装版本验证。无法读取的会话模型为 unknown。

## 4. 收集缺失选择

- 按阻塞问答的 [问题去重与恢复](references/interaction.md#问题去重与恢复) 合并所有来源、校验每轮候选和保存回答；再展示完整模型映射，优先调用允许的同步问答工具，逐轮收集尚缺选择；只有同步工具不适用时才使用异步或文本回退。
- 按 [阻塞问答](references/interaction.md#问答顺序) 引导选择用例是否需要人工评审，推荐“是”。
- Web 或 both 范围确定后，按 [ego 辅助引导](references/ego.md) 检测本机系统；仅 macOS 收集尚缺的辅助选择与后续安装选择。API-only、移动端和非 macOS 跳过该问卷。
- 按 [执行并行开关](references/config.md#生成与执行并发) 收集尚缺的并行选择；启用后引导本地配置专用角色/租户账号池，可选用户上限，不在聊天收集凭据。
- Bug 问卷由 `scripts/bug_questions.py` 和 `references/bug-questions.json` 驱动。面向用户显示表格平台名称，技术通道留在内部配置。

## 5. 校验并保存已确认配置

- 选择收到后校验模型、每模型强度、执行模式和图片能力。
- 按 [配置规范的开关规则](references/config.md#人工用例评审开关) 保存已确认的人工评审选择，并展示新 run 的最终生效值。
- --model-catalog 传宿主发现值，--session-effort 传本轮真实强度。--format markdown 可生成完整映射表。
- --overrides 传 roles/defaultEffort/decision/execution。--project-overrides 传目标等个人设置；二者接受私有 JSON 文件路径或不含敏感信息的内联 JSON。使用实际 schema（本地记录使用 `delivery.mode=local` 和 `bugPublisher.localFormat=csv`），不得使用 `delivery.channel` 或 `delivery.format`。
- --init 在当前 Agent 命名空间锁内保存，不改其原有 config.toml/settings.json。仅本次用临时覆盖和 --run-id。
- 选择待定就停在第 4 步，不提前保存。收到本轮回答后继续收集缺项；所有必需选择已确认时立即保存并继续独立检查。缺失检查或保存未完成本身不是结束理由。遇到覆盖文件缺失、参数或 schema 错误，依据实际错误修正并重试；无需用户提供的信息不得交由用户操作。仅当需要实际用户输入、授权、不可自行恢复的外部条件或明确取消时暂停，并给出具体阻塞和下一步。

## 6. 冻结本次运行快照

- 每个独立测试任务使用唯一 runId；恢复或接管同一任务保留原 runId。异内容不可覆盖同名快照。
- 运行中只读 runConfigPath；需保留的诊断和运行证据写仓库外 workspacePath，一次性探测材料按上面的产物边界处理。
- 旧名称/人员目录只做显式迁移，校验后移除旧活动入口，不并行保留两份配置。
- 历史证据不改写。

## 7. 检查 Jev 决策能力

- decision 使用 [decision-router](../decision-router/SKILL.md) 内置 Jev API。检查宿主安全环境中的 `TYPESAFE_API_KEY` 和 Jev 工具可用性；具备条件时执行一次最小实际调用验证，缺 Key 时引导用户设置。只报告状态，不输出 Key；调用失败或未验证不得标 READY。
- 无需用户配置决策 Provider、模型或端点。
- 自定义服务仅在用户明确要求时验证。

## 8. 验证所选工具与记录通道

- 每次普通 setup 都按 [MySQL 项目配置](references/storage.md#mysql-项目配置) 检查当前项目的配置并引导补齐；只配置状态，不因此认定测试用例需要查库。缺项不阻断非数据库用例。
- Web 范围已确定时，按 [Playwright 安装引导](references/playwright.md) 检查 Node.js/npm、隔离 harness 内的 Playwright 和所需浏览器；缺少组件时先取得安装授权，再安装并验证。Web/API-only 跳过移动依赖检查。
- 已选择 ego 辅助时，按 [ego 辅助引导](references/ego.md) 检查 Ego Lite、按明确授权安装并验证；doctor 只读报告，不弹选择或安装。
- 按范围探测其他测试工具与所选记录通道；Playwright 目标和账号文件按 [存储规则](references/storage.md) 指引用户自行填写，缺项只报字段名，不在聊天收集值。
- 仅在测试用例执行前判断该用例是否需要 MySQL；需要时按 [数据库核验规则](../argus/references/mysql.md) 调用可用的 MySQL 工具并执行受审查的只读核验。不需要时仅将该用例的数据库核验标为 N/A，用例本身继续执行。
- 本地 CSV 或 Excel 按产物边界在临时目录验证创建、写入与读回；检查目标目录权限时不创建或修改累计表及交付表。不要求企微/飞书授权。
- 在线按产物边界通过实际 MCP/API/CLI/浏览器/Webhook 只读验证；未能证明写权限时报告未验证。未选工具不阻断。
- k6/sysbench/ZAP 按需探测。
- 版本/help/样例与退出码是证据，found-not-verified 不能标 READY，不为探测执行配置内命令。

### 移动端依赖检查

- 移动端范围已确定时，先按 [存储规则](references/storage.md) 自动识别设备与应用、补齐私有配置并引导剩余必填项。再按 [Appium 安装引导](references/appium.md) 检查依赖。缺少组件先询问，用户同意后安装并验证。
- 只读 doctor 仅报告缺项。

## 9. 展示配置与诊断结果

- 展示更新后完整角色—模型—强度映射和各配置文件位置，doctor 区分默认/角色覆盖、请求/解析/实际值及执行缺口。
- 普通 setup、安装结束和只读 doctor 的结果均按下方格式展示；仅配置 MySQL 时只展示相关项。先用独立加粗行说明结论：有缺项为「⚠️ Argus 配置待补齐」，配置完整但未验证为「⚠️ Argus 配置已保存，能力待验证」，所选能力全部验证通过才为「✅ Argus 配置与验证完成」。doctor 使用「Argus 诊断结果」，不暗示本轮保存或安装。
- 紧接配置状态表，列为「配置项｜是否已配置｜验证状态｜下一步」，每项一行。覆盖当前范围的 Web/移动端、MySQL、Jev、所选工具与 Bug 记录通道，以及人工用例评审开关的生效值。缺项和失败项排在前面；未选项标「未选择／不适用」。用中文短句呈现，必要时括注原始状态码；配置存在、字段完整与实际验证成功分开表达，空模板不算已配置。
- setup 尚未执行测试时，已有的累计 `bugs.csv`、`runs.csv` 可标「历史内部累计表」；未生成的表格标「待测试后生成」，不创建或展示为已有文件。本 run 交付完成后展示回执中的命名交付副本。
- 路径另列「文件／用途｜绝对路径」表，包含实际 configPath、相关私有配置文件及已生成的 runConfigPath、workspacePath。使用本轮返回的真实路径，已有文件使用可点击链接；缺失文件注明「待创建」。每个路径独占一行，不把长路径、缺失字段和操作说明挤进同一单元格，不展示占位路径或凭据。
- 保留独立的「角色｜当前映射｜强度｜状态」完整模型表。最后用简短编号列表给出待办，引用路径表中的文件名，仅列缺失字段名和必要操作；Web 公开页面注明 `accounts` 可为 `{}`，MySQL 缺项注明只影响需要查库的用例，Jev Key 存在但未调用注明「Key 已配置，API 未验证」。没有待办时省略列表。
- 上述表格直接渲染为 Markdown，不放进代码块，不退回长段落。以下仅示范排版，实际行、状态和待办必须来自本轮检查，路径表和模型表按实际结果补充：

  **⚠️ Argus 配置待补齐**

  | 配置项 | 是否已配置 | 验证状态 | 下一步 |
  | --- | --- | --- | --- |
  | Web 目标与账号 | 待补齐 | 未验证 | 填写 `web-private.json` |
  | MySQL | 未配置（NOT_CONFIGURED） | 未连接 | 填写 `mysql-private.json`；仅查库用例需要 |
  | Jev | Key 已配置 | API 未验证 | 首次实际调用后更新状态 |

  1. 在 `web-private.json` 填写 `baseUrl` 和默认账号字段；公开页面可设 `accounts: {}`。
  2. 在 `mysql-private.json` 补齐 `host`、`database`、`username`、`password`；不影响无需查库的用例。

- 原生目录内本 Skill 的配置在每次启动时显式读取，不依赖把某个宿主的模型名注入所有宿主的全局指令。
- 交付/能力和 Git 检查沿用当前 run 规则。未调用验证不能报模型切换成功。

## 完成条件

- 完成：选中能力有真实证据或明确 BLOCKED/UNSUPPORTED，未选 SKIPPED。
- 宿主子 Agent 不要求另一份 API key。
- 自动发现不改宿主原生配置或共享项目文件，不读其他 Agent 凭据。
- reset/switch-model 仅更新当前运行时所选覆盖（使用相同锁与原子写入），已运行快照保持不变。
- 需重验后使用新 runId。
- API Key 仅保存环境变量名或安全引用。
