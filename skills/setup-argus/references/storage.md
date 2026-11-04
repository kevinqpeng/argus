# 本机配置与工作区

按本机单用户使用，不设置 testerId、用户 profile 或人员选择问题。被测仓库只读：不生成 .argus、探测脚本、个人配置或报告，不为本机浏览器/CDP/凭据修改项目 Playwright 配置、fixture、依赖或锁文件。Argus 的 Playwright 执行配置始终独立创建；项目配置仅供只读了解页面和业务断言，不导入或继承。

## 每个 Agent 一份配置

直接保存到当前 Agent 根的 argus-model.json：Codex 为 ${CODEX_HOME:-~/.codex}/argus-model.json，Grok 为 ${GROK_HOME:-~/.grok}/argus-model.json；pi、Claude、agy 的根目录按 [宿主规范](hosts.md) 解析。--runtime-dir 可显式适配安装位置。

该文件顶层保存 roles、defaultEffort、decision、execution 和 projects。projects.<projectId> 保存该项目的环境、目标、覆盖、发布及交付设置；不同项目不会互相覆盖，但不再单独生成项目配置文件。每次 Skill 启动显式读取此文件，不改 Agent 原有 config.toml/settings.json/AGENTS.md。

## 运行产物

根目录：--state-dir > ARGUS_HOME > ${XDG_STATE_HOME:-~/.local/state}/argus。配置与产物都必须在 Git 仓库外，不能用 .gitignore 隐藏污染。

```text
<Agent根>/argus-model.json
<产物根>/projects/<projectId>/
  web-private.json
  mobile-private.json
  mysql-private.json
  runs/<runId>/
    argus-model.json
    manifest.json
    capabilities.json
    doctor.md
    cases/
    harness/
    evidence/
    results.json
    coverage.json
    bugs.json
    publish.json
    report.md
  tables/bugs.csv
  tables/runs.csv
```

只创建实际需要的文件。projectId 为 `--project` 指定目录的真实绝对路径的 SHA-256 前 24 位；未指定时脚本使用当前工作目录，此目录只用于隔离配置，不要求是源码或 Git 仓库。源码路径已知时可传入，换目录视为独立项目；无需向用户强制索取项目路径。每次 runId 唯一，跨 Agent 同项目共享本机累计表时使用发布锁，各 run 的快照和证据独立。配置文件 0600，工作区按敏感资料管理。

`projectDir/web-private.json` 是该项目的 Argus 专用本机配置，用户自行在此填写，不通过聊天收集值。需要执行 Playwright 时，Agent 可在文件不存在时创建权限 0600 的空白模板，并告知脚本返回的 projectDir 绝对路径；已有文件不得覆盖。默认只需填写测试地址、用户名和密码：

```json
{
  "baseUrl": "",
  "accounts": {
    "default": {
      "username": "",
      "password": ""
    }
  }
}
```

## MySQL 项目配置

MySQL 与 Web、Mobile 共用同一个 Argus projectDir，使用 `projectDir/mysql-private.json`，由现有 projectId 隔离项目。执行时使用 host_config.py 返回的 projectDir；已有 run、Web 或 Mobile 测试上下文的 projectId 直接复用。用户指定已有测试项目或配置时复用其 projectId；上下文无法唯一确定且存在多个候选时展示可辨认的非敏感目标并询问选择，不索取源码或项目目录。不从当前目录变化推断要切换已有测试项目，也不跨项目读取或复用凭据。

每次普通 `setup-argus` 都检查当前 Argus projectDir 中的 MySQL 配置；不存在时创建权限 0600 的空模板，已有文件不覆盖。模板：`{"host":"","port":3306,"database":"","username":"","password":"","tls":{"enabled":true,"caFile":""}}`。用户自行在本机填写连接信息；只报告绝对路径和缺失字段名，不在聊天索取或回显凭据，不从项目配置推断。按实际环境设置 TLS，启用时验证服务器身份；caFile 在需要指定 CA 时填写。执行进程读取配置，不复制到 run 快照、脚本、日志或证据。缺少配置只表示 MySQL 尚未就绪，不阻断不需要数据库的用例。

配置完整时，在 setup 中用当前宿主提供的 MySQL 工具验证连接；没有专用工具时用已安装的 MySQL 客户端或私有环境驱动执行 `SELECT 1`。该查询只验证连通性，不修改数据库。必要依赖不可用时报告缺项；配置不完整为 `NOT_CONFIGURED`，连接失败为 `ERROR`，仅实际连接与查询成功才标 `READY`。连通性成功不证明账号没有写权限；执行需要数据库核验的用例前，由数据库侧确认账号仅有目标表或视图所需 `SELECT`，不授予写入、DDL 或存储过程执行权限。setup 的连接检查不代表后续用例都要查库。

旧独立 MySQL 配置不自动读取或合并；若用户指出已有文件，先确认其所属 Argus 项目和目标，再迁移到对应 projectDir。不覆盖已存在文件，不回显内容，保留原文件直至用户明确要求移除。

`projectDir/mobile-private.json` 保存移动端私有配置，仅在 mobile 路由时需要。格式见 [MobilePrivateConfig](../../argus/references/schemas.json)。先执行下述探测与补齐，再运行移动端配置校验；不要把空白模板直接交给用户填写。

### 移动端自动识别与缺项引导

1. 先按脚本返回的 projectDir 读取已有 `mobile-private.json`，逐项对照本次范围和用户已明确提供的信息，再只读探测本机连接设备。配置中的有效值直接复用；不得把应用名称当作额外必填项。无源码的会话工作目录变化会产生不同 projectId；当前目录没有配置时，先只读枚举本机 Argus 项目目录中的移动配置，按本次已授权设备和应用标识匹配，不输出账号或密码。唯一匹配时通过 `host_config.py --project-id <已有ID>` 复用该配置；多个匹配时比较已知目标和设备证据，仍有歧义只展示非敏感区别并询问选择，不要求源码路径；无匹配才引导补充缺项。若用户指出已有配置或先前测试项目，使用对应工作区，不得因当前目录不同而忽略它或自行合并其他项目的凭据。Android 使用 `adb devices -l`，仅考虑状态为 `device` 的真机；通过显式 `adb -s <serial>` 查询系统版本、已安装应用及版本。iOS 使用已安装 Xcode 的设备工具，先查看其 help，枚举已配对真机并核对连接、信任状态和 UDID；不能用模拟器代替。工具缺失、未授权、离线或未信任时，给出对应的连接、解锁、授权或安装指引，完成后重新探测。
2. 用户指定的设备优先。没有指定且只有一台符合所选平台的可用真机时，自动填写 `platform`、`deviceId`。多台时展示型号与可区分的脱敏标识，让用户选择；不默认取第一台。设备连接只支持识别，不代表允许安装、重置或清除数据。
3. 已配置 `appId` 是目标应用标识；在指定设备上核对安装状态和版本，通过后直接使用，不再询问应用名称或重新确认身份。仅当 `appId` 缺失、无法对应已授权目标、或与用户明确指定的目标发生冲突时，结合设备安装信息及可用的源码配置排查；无法唯一确定时只询问缺失或冲突项。不能凭包名关键词猜测。系统版本和应用版本留在受保护的探测证据中，不向配置添加 schema 未定义字段。
4. `appPath` 优先复用已有有效路径或用户提供的路径，有源码项目时再检查明确的构建产物目录。仅当本机安装包存在、包标识与目标一致、版本满足本次测试要求且候选唯一时自动填入绝对路径。设备上的 `codePath` 不是本机安装包路径。当前校验器仍要求安装包；缺失时解释此要求，引导提供本机 APK/IPA 路径或生成产物，不自行构建、下载或安装。
5. `service.baseUrl` 仅复用已授权的测试环境配置或用户明确指定的地址。源码默认值、手机网络地址不能证明服务目标已授权。无法确定时引导用户在私有文件填写完整测试服务 URL，并说明用于业务 API 验证。需要登录时，在 `mobile-private.json` 的 `accounts` 中按 Web 的角色/租户账号格式由用户自行填写用户名和密码；只提供文件绝对路径和缺失字段名，不通过聊天收集或输出凭据。无需登录时使用空对象。执行进程读取该文件并在内存中使用账号，不自动复用 Web 账号。已有 `credentialsRef` 保留以兼容旧配置，新配置使用 `accounts`。
6. 将唯一且有证据的结果写入私有文件。新文件使用 0600 权限；已有文件仅补空字段，保留其他平台、目标与用户设置。已填值与实时探测冲突时列出冲突并引导选择，不静默覆盖。写前核对文件未被其他会话修改，原子保存并读回校验；内容不复制进快照、脚本、报告或日志。
7. 对剩余必填项一次性给出简短清单，每项包含字段名、用途、无法自动确定的原因、获取方式及填写位置。非敏感选择可在聊天中回答并由 Agent 写入；敏感值留在本机安全环境。已有有效配置和已识别字段不重复询问；仅报告缺失、无效或与本次明确指令冲突的具体字段，未选平台的字段不阻断。缺项期间继续独立的只读准备，依赖这些字段的执行等待补齐。补齐后重新验证连接、应用与配置，只有校验通过才进入测试。

公开页面无需账号时可将 `accounts` 改成 `{}`；多角色时按角色增加账号项，可加入 `tenant`。默认只允许访问 `baseUrl` 的精确主机；确需跨主机跳转时才添加 `allowedHosts` 数组，并仍须校验目标授权。文件只供本机执行进程读取，不复制进 run 快照、脚本、报告或证据，不打印内容。运行前只检查文件是仓库外普通文件、权限为 0600、字段完整且目标在授权范围；Web 配置缺失时给出文件路径和缺失字段名，等用户自行补齐；移动配置遵循上面的探测与引导流程。

脚本返回 configPath、projectId、workspacePath、runConfigPath；使用返回值，不从 cwd 拼目录。configPath 是唯一持久配置，runConfigPath 是只读运行快照。后续模型设置不改变已有快照，同 ID 异内容不可覆盖。

## 只读执行

运行前记录 Git 状态；在 workspacePath/harness 创建本 run 专用的 Playwright 配置与测试脚本，显式设置目标、账号引用、outputDir、reporter、trace、认证状态、缓存和浏览器 profile。执行进程从 `projectDir/web-private.json` 读取目标及账号，内存中传给 Playwright；不加载项目 .env、storageState、globalSetup 或共享浏览器会话。需要依赖就在外部 harness 安装，不通过 symlink 把输出回流到项目。运行后核对 Git 与生成目录，保留用户已有改动。

## 迁移

Web 配置旧名为 `playwright-private.json`。发现旧文件且 `web-private.json` 不存在时，先核对旧文件是仓库外权限 0600 的普通文件，再原子重命名并保留内容和权限。两者同时存在时不覆盖、不合并凭据；告知用户文件冲突并等待选择。

旧命名和人员目录只按明确迁移操作处理，不保留为并行活动配置。核实当前 Agent 的旧模型/项目设置，合入 argus-model.json；已有新配置优先，项目设置合并到 projects，不丢其他项目。旧工作区整体迁出人员分层，历史证据与冻结内容保留；旧快照不能当新 Argus 可执行配置，继续测试应新建 run 并引用历史来源。

删除旧路径前核对备份、源文件未变化和目标内容一致，不读取或复制其他 Agent 的凭据。不宽泛回滚项目改动，不清空 skills 根目录。

## Bug 交付

按 [通道规范](../../bug-reporter/references/channels.md) 选择 online/local/both、新建/复用/导入、工具和权限回退。所有方式都需 Bug 与每次运行汇总读回；本地是正式选项，online/both 未满足时不冒充成功。失败只补发布，不重跑业务。CSV/Excel 和证据在本机工作区，不能把本地路径当他人可访问的在线 URL。
