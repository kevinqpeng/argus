# 模型映射与推理强度设置

这是 setup 的模型选择阶段，不是启动多 Agent。角色使用自动化测试职责，不复制其他工具的角色表；除非本任务已授权且宿主具备能力，不因设置角色而启动子 Agent。

## 发现当前能力

先从本宿主的模型目录或工具 model/effort 参数枚举取得可用模型、每模型推理档位、执行模式和图片能力。原生配置只说明已声明默认值，不证明本轮实际值或独立调用能力。只读选定字段，禁止输出配置原文或获取私有 OAuth 凭据。

模型列表必须是本次发现值，不硬编码某家的固定型号，也不把截图示例当当前权限。inherit-parent/auto 表示当前会话继承；持久化为 mode=delegate、model=inherit-parent，实际子 Agent 调用省略 model 参数。自定义 API 模型需另做协议和能力验证。

宿主可将发现结果规范化为私有 model-catalog.json，传 --model-catalog。示例仅为格式，名称替换为真实发现值：

```json
{
  "schemaVersion": 1,
  "models": [
    {"id": "detected-model-id", "provider": "detected-provider", "efforts": ["low", "high"], "modes": ["delegate"], "capabilities": ["image-input"]}
  ]
}
```

efforts/modes/capabilities 没有证据时留空，不能凭名字补齐。模型 ID 必须与实际宿主枚举一致；不同 Provider 用宿主真实接受的唯一 slug，不能私自发明别名。low/medium/high/xhigh/max/ultra、none/minimal/deep 等档位仅在该模型实际支持时提供。

## 先展示完整映射表

每次先读当前宿主、本机的 argus-model.json；存在就作为当前选择，不存在才用默认继承。显示可用模型列表、当前角色表、来源，以及旧位置可迁移配置。所有五个角色都要展示，不能只说“沿用默认”：

| 角色 | 职责 | 模型/来源 | 强度 |
| --- | --- | --- | --- |
| reasoning | PRD 理解与复杂推理 | 本宿主当前选择或当前会话 | 默认强度或角色覆盖 |
| generation | 测试场景/代码生成 | 本宿主当前选择或当前会话 | 默认强度或角色覆盖 |
| bug_analysis | 复现与根因分析 | 本宿主当前选择或当前会话 | 默认强度或角色覆盖 |
| vision | 截图与视觉检查 | 当前选择；需验证图片输入 | 默认强度或角色覆盖 |
| decision | 类型判断与概率路由 | 当前决策 Provider；默认 Jev | N/A；不套用 LLM 推理强度 |

展示实际模型名、执行模式、默认/角色覆盖的强度、解析后的值与缺口。无法观察写 unknown，不把继承别名当模型 ID；不支持的旧选择标“需重选”，不要默换成别的模型。

host_config.py 的 JSON 含 modelMapping/defaultEffort/detectedModels；--format markdown 可直接生成五行表。Codex 优先读取当前 CODEX_THREAD_ID 对应会话文件最后一条 turn_context 的 model/effort，并标明这是最后记录值；调用者传 --session-model/--session-effort 时优先使用明确传入的本轮值。其他宿主传入可观察的本轮元信息；不拿配置默认值冒充实际值。会话记录尚未更新、缺失或存在歧义时向宿主核实，不能把历史记录当成已验证的当前调用。未使用 --init/--run-id 时只读，不落盘。

## 阻塞问答

按 [交互规范](interaction.md) 选择可用的问答工具并等待实际回答，每轮最多三个独立问题：

1. 映射方案：保留上表/采用建议映射、全部继承当前会话、逐角色自定义。推荐方案必须来自已发现能力，保留明确已保存的选择；用户可输入额外模型，但验证可用后才能接受。
2. 默认推理强度：展示本次映射共同支持的档位，以及可执行的 session/model-default。high 只有在所选模型支持且符合用户偏好时才能推荐。session 表示读取当前轮真实强度；model-default 表示不传强度，由模型默认决定。
3. 是否逐角色覆盖强度：统一默认、指定角色。选择自定义后下一轮才问具体角色/模型/强度。模型选择会改变可用强度时，先等映射回答再问强度，不能批量制造相互依赖的问题。

不要问无关的其他插件开关。用户没回答就停，不生成“已保存”或继续主测试流程。

## 保存与执行

模型文件包含 schemaVersion、roles、defaultEffort、decision、execution。角色可有 effort，未设置就使用 defaultEffort；默认初值为 session。角色绑定整体替换，避免切换 host/api 后混入旧 inherit。示例：

```json
{
  "schemaVersion": 1,
  "defaultEffort": "high",
  "roles": {
    "reasoning": {"mode": "delegate", "model": "inherit-parent"},
    "generation": {"mode": "delegate", "model": "detected-model-id", "effort": "xhigh"}
  }
}
```

示例需匹配实际发现档位才能使用。API 角色按 Provider 实际 effort 参数编码；delegate 按宿主工具参数编码，不把通用字段名盲发给每个模型。宿主不支持指定模型/强度或当前策略禁止委派时，该角色标 BLOCKED；不得悄悄在主会话使用其他模型。

视觉 Jev 判断是 decision 的并行比对能力，模型版本冻结在 decision.visualModel；保留 vision 和 bug_analysis 角色负责复核和归因。

恢复时沿用原冻结角色策略：`inherit-parent` 和 `session` 在当前轮重新解析，明确指定的模型与强度保持原绑定，除非用户要求的调整已通过配置变更路由。原快照的 `sessionEffective` 仅为创建时记录；当前实际调用写入新的回执或 doctor.md，不覆盖快照。仅更换协调者模型或宿主不新建 run；能力不足按 [恢复契约](../../argus/references/resume.md) 阻塞对应步骤。

每次调用按角色读取冻结配置，并按[配置变更路由](../../decision-router/references/config-changes.md)应用原 run 中已验证的 model-adjustments.json 记录；无记录则使用原快照。delegate 使用宿主子 Agent 工具，明确模型传入其 model 参数；inherit-parent/auto 省略 model 参数。Codex 使用 spawn_agent 的 model 和 reasoning_effort，Claude 使用 Agent 支持的对应参数；其他宿主只用其已验证的等价调用。具体模型的 session 强度需使用当前轮可靠元数据并显式传入，不能用模型默认值代替；仅有最后记录而无法确认当前轮时，先阻塞询问固定强度或 model-default。API 按实际协议传参。

每次调用前按实际 model/effort 能力复验。报告分别记录请求值、解析值和实际调用值；modelMapping.executionStatus=NOT_INVOKED 不能表述为执行通过。最终展示保存位置和完整更新后映射表，再进入后续已授权步骤。
