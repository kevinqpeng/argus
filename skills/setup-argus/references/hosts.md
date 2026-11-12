# 宿主适配与发现

核对日期 2026-11-02；路径为已核对的候选，启动环境、配置覆盖和组织策略仍需当前宿主核实。agy 指 Google Antigravity；若用户指其他产品先澄清。

| 宿主 | 用户级安装入口 | 调用 | 默认模型发现 |
| --- | --- | --- | --- |
| Cursor | ~/.cursor/skills 或插件 | 在 Agent 中请求使用 argus | 当前会话公开的模型信息；无法确认时为 unknown |
| Codex | ~/.agents/skills；安装器另给 ~/.codex/skills 链接兼容旧版本 | `$argus` 或直接要求读取文件 | CODEX_HOME/config.toml 或 ~/.codex/config.toml，活动 profile；会话覆盖从宿主元信息读取 |
| pi | ~/.pi/agent/skills；亦支持 ~/.agents/skills | `/skill:argus`；修改后 `/reload` | PI_CODING_AGENT_DIR 或 ~/.pi/agent/settings.json 的 defaultProvider/defaultModel，项目 .pi/settings.json 及当前会话另核对 |
| Claude Code | ~/.claude/skills | `/argus` | settings.json 的 model，managed/project/local/env/启动覆盖按实际版本解析；具体会话模型通过宿主公开信息 |
| Grok CLI | ~/.grok/skills | 提及 argus 或直接读文件；原生快捷命令以本机 help 为准 | ~/.grok/config.toml 的 models.default；不要按 Codex model 字段猜测 |
| agy | ~/.gemini/config/skills；安装器另给旧 IDE ~/.gemini/antigravity/skills 链接 | `/argus` 或直接读文件；CLI 更新后 `/skills reload`/新会话 | 先用当前界面/会话公开模型；settings.json 仅候选，无法确认则 unknown，不解析私有 protobuf 状态 |

所有宿主都可使用无前缀的兜底指令：“读取 `<实际绝对路径>/argus/SKILL.md` 并遵循；PRD 是……，范围是……；Playwright 目标和账号按私有文件配置”。这证明显式读取兼容，不代表菜单原生发现已实测。

共享 Skill 目录只存指令/脚本。setup 直接读取当前 Agent 根下的 argus-model.json，不设人员分层，不改其原有全局模型/凭据配置，不把某宿主角色值注入所有宿主的全局指令。产物另见 [存储规则](storage.md)。

| 宿主 | Agent 根解析 | 模型与强度执行规则 |
| --- | --- | --- |
| Cursor | ~/.cursor；可用 --runtime-dir 覆盖 | 从当前会话和工具发现模型、推理强度及委派能力；无法确认时为 unknown |
| Codex | CODEX_HOME 或 ~/.codex | 配置默认可读 model/model_provider/model_reasoning_effort 与活动 profile；当前会话和工具 model/reasoning_effort 参数以实际元信息为准 |
| pi | PI_CODING_AGENT_DIR 或 ~/.pi/agent | 模型/推理档位从当前会话目录及工具参数发现；不能假设沿用 Codex 的字段或同一组档位。强度只随模型串的 `:档位` 后缀传递且要带 Provider 前缀，dispatch.py 拼好；会话模型未记录时该角色 BLOCKED |
| Claude Code | CLAUDE_CONFIG_DIR 或 ~/.claude | 模型 slug 用当前 Agent 工具接受的名称，强度按实际 effort 能力，不把完整 API ID 与工具别名混用 |
| Grok | GROK_HOME 或 ~/.grok | 默认模型读 models.default；本机发行文档支持 --reasoning-effort/--effort，具体菜单档位按模型发现，不能假设 session 总可解析 |
| agy | ~/.gemini/config，特殊安装用 --runtime-dir | 从本轮界面/工具发现模型和强度；没有显式设置能力时只继承实际会话或标不支持，不猜私有字段 |

例如 Codex 为 ~/.codex/argus-model.json，Grok 为 ~/.grok/argus-model.json，pi 为 ~/.pi/agent/argus-model.json，Claude 为 ~/.claude/argus-model.json，agy 为 ~/.gemini/config/argus-model.json；均尊重对应环境变量或显式根目录。项目设置统一在该文件 projects 字段中。问答见 [模型规范](models.md)。

`host_config.py` 保守读取选定宿主用户配置；不读取其余宿主、auth 文件、模型密钥库或动态 helper。白名单 model/provider 字符串与源哈希进入报告，endpoint 默认不输出。没有可用委派工具或严格绑定的模型无执行证据时 BLOCKED。若需自定义 Provider 元信息，只由当前宿主解析被选 Provider 的非敏感字段，记录 URL/协议的安全摘要和来源，禁止全文输出。

完整配置优先级必须按当前宿主官方规则解析；脚本默认用户级候选不冒充最终默认值。多 `--config` 代表调用方已按真实规则排好低到高顺序，不能自行把五种宿主混成一套优先级。会话实际值无法观察时 unknown。

资料：

- [Codex Skills](https://developers.openai.com/codex/skills)
- [pi 官方 Skills](https://pi.dev/docs/latest/skills)
- [Claude Code Skills](https://code.claude.com/docs/en/skills)
- [Antigravity Skills](https://antigravity.google/docs/skills)
- Grok：本机随发行版本附带的 `~/.grok/README.md` 的 Agent Skills 与 Configuration 章节。不同同名 Grok CLI 不假定兼容。
