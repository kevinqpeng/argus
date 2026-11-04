# 平台路由

Argus 在执行前选择 `web`、`mobile`、`both` 或 `insufficient_evidence`。用户明确的平台范围和直接调用的平台技能是硬约束；Jev 不能扩大授权范围。显式范围与项目证据冲突时，列出冲突并请求澄清。纯 API 场景继续走 Web Skill 的 API 路径，不启动手机。

读取项目入口、Android/iOS 工程、可执行产物和用户指定场景作为脱敏证据。两个平台工程同时存在，不等于用户授权同时测试。一次 Jev 请求批量提交两个独立判断：平台选择，以及当选择 mobile/both 时的 Android/iOS 目标。平台问题选项包含 `web`、`mobile`、`both`、`insufficient_evidence`；设备问题包含 `android`、`ios`、`both`、`insufficient_evidence`。

在 [`questions.json`](questions.json) 集中维护题目与待校准阈值。只有最高概率至少 0.8 且领先次高至少 0.2 才接受 choice；否则补充项目证据或澄清。无用户回复且信息仍不足时标 `needs_clarification` 并暂停执行。Jev 缺 Key、API 故障或协议异常时标 BLOCKED，不用主模型代答。显式范围通过确定性校验，不交给模型覆盖。

为每次决定保存 questionSetVersion、脱敏证据摘要、原始和归一化响应、概率、confidence、inputHash、阈值、最终路由和理由。冻结路由与设备矩阵；恢复时校验项目、App、目标及设备指纹。`both` 分开执行和报告每个平台，汇总不能掩盖单端阻断。
