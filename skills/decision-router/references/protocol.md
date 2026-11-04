# 协议、归一化与实测

内置 `scripts/jev.py` 使用官方 `POST https://api.typesafe.ai/v1/systemone`、Bearer `TYPESAFE_API_KEY`；文本使用 `jev-latest`，图片使用冻结的 `jev-1.14.0`。输入 JSON 形如 `{"state":"脱敏证据","questions":{"ui":"Does scenario S1 require observing UI behavior?"}}`。题目 ID 来自 questions.json；脚本补齐类型和 criteria，输出原始响应与归一化结果到仓库外私有路径。协议依据 [官方 OpenAPI](https://api.typesafe.ai/openapi.json)。

模型别名和协议可能更新；API 返回结构变化时阻断并对照官方 OpenAPI 修复适配。不能把 HTTP 200 或聊天文本当概率结果。

调用前核对数据范围和预算。固定无敏感合成样例验证 choice/score/noul 的正例、负例、不确定例，并验证 401/429/超时/HTML 200/缺字段/缺模型/NaN/越界/分布不合法的诊断。需新增实验预算且当前未授权时向用户说明数据与预算；已有授权不重问。

保存原始脱敏证据后按当前返回格式显式归一化，下面是 **内部检查器输入**，不是 API 请求或供应商原始响应：

```json
{
  "schemaVersion": 1,
  "questionSetVersion": "1.3.0",
  "answers": {
    "ui": {"noul": 0.9, "probabilityKind": "calibrated"},
    "failure": {
      "choice": "product",
      "probabilities": {"product": 0.85, "test": 0.05, "environment": 0.03, "data": 0.02, "flaky": 0.01, "unknown": 0.04},
      "probabilityKind": "calibrated"
    },
    "impact": {"score": 2.85, "probabilities": {"0": 0.01, "1": 0.02, "2": 0.08, "3": 0.89}, "probabilityKind": "calibrated"}
  }
}
```

calibrated 必须来自已验证的 Provider 契约；不能通过手填字段使聊天模型成为校准模型。score 归一化等级下标为 0..3，原始 Provider 若是其他区间要显式转换并保留原始值。choice 的 confidence（若提供）只表示分布集中度，不作正确性证明。

`decisions.py` 退出 0 为结构合法且无 uncertain 的文本判断，2 为需 host-analysis、vision-review 或 BLOCKED。它不调用模型、不绕过门禁、不证明语义正确性。score 高低使用集中定义的阈值两侧质量，双峰跨界升级。unknown 类仍需宿主继续调查。生产记录除 answers 外另保存 provider/model/protocol/inputHash/rawEvidenceRef/thresholds/route/fallbackReason。

## 图片请求与并行复核

图片功能依据 [官方图片协议](https://docs.typesafe.ai/images.md)，仍为 Dreamer-ready，格式、限制和准确率可能改变；[模型价格](https://docs.typesafe.ai/models.md)于 2026-11-04 核对为每百万输入 token 0.042 美元、输出免费，图片计入输入。未知 usage 保持 unknown。不得据此声称本项目已完成准确率或性能校准。

使用新 run 的冻结 `config.decision.visualModel=jev-1.14.0`，通过 `--run-config <runConfigPath>` 传入；历史快照不修改，缺少该字段的旧 run 不能发起图片判断。`state` 给出具体目标和验收条件；`images.screenshot` 是本机文件路径，仅脚本读取，发送时替换为随机引用和顶层 `assets` 的 base64 图片。一次请求可提交多个视觉问题；问题措辞和阈值在 questions.json，视觉问题的输入字符串仅表示选择该题，不能覆盖模板。不要混入纯文本问题。示例写入受保护 run 目录：

```json
{"caseId":"CASE-1","state":{"target":"登录按钮","acceptance":"键盘出现后按钮完整可见"},"images":{"screenshot":"/private/run/evidence/login.png"},"questions":{"visual_obscured":"登录按钮","visual_truncated":"登录按钮文字"}}
```

页面状态还需 `state.states` 中的 `first`、`second` 两个具体状态说明，同时提交 `visual_state` 与 `visual_state_applicable`；不适用、other 或不确定都需复核。脚本检查静态 PNG/JPEG、每边 280–1568、单图 16 MiB、JSON 请求 32 MiB；macOS 使用 sips 检查格式和尺寸，缺工具即拒绝。服务端处理后及展开后的大小、图像完整性和上下文限制仍由服务端检查。预处理应使用现有可靠图片工具，保留原图并在私有 run 保存派生图；小字和遮挡使用保留上下文的清晰局部截图，清晰度不足升级复核，不能仅缩小长截图。不得上传未授权图片或输出 base64、凭据及敏感页面。

每个视觉判断均返回 `vision-review` 和 `judgment`，无论概率高低都由冻结的 vision 对照原图及业务证据复核；与日志或业务断言冲突时交 bug_analysis。视觉判断本身不产生 passed。`visual_obscured`、`visual_truncated` 的 yes 表示异常；`visual_error_visible` 的 yes 表示目标提示可见。候选不适用或复核角色不可用时保持未解决；调用错误为 BLOCKED/error，不能转成 no。runner 和 coverage 门禁仅在全部业务及视觉必需断言有独立证据时决定结果。精确数值用 DOM、布局或业务断言；不使用 Box 自动点击。

输出记录请求及实际模型、图片和请求 SHA-256、耗时、usage、原始响应引用；宿主关联 case ID、题目版本、阈值、每题概率、路由、升级原因、每次重试和兜底调用，保留在受保护 run 目录。请求哈希包含随机图片引用，每次调用不同。重试按现有配置和预算执行，累计预处理、请求、重试和复核时间与成本；不得将单次模型耗时当作整体 E2E 加速。
