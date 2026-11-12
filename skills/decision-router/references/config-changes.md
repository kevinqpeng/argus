# 原 run 配置变更

配置不同不等于业务基线失效。此入口用于用户要求把配置调整应用到已有 run；新 Agent 的默认偏好不会自动覆盖原任务。单纯切换会话、宿主或继承模型按恢复契约继续，无需为身份变化额外调用 Jev。

## 判断

1. 读取原快照、已生效调整、完整待应用差异及用户指令，比较实际生效值。相同值直接沿用。向 Jev 提供脱敏字段差异、业务输入/目标/范围/用例/断言/矩阵/门禁是否变化的证据、完成与未完成工作、能力验证和数据授权边界；缺失项明确标 unknown。模型更强不代表断言或历史结果自动改变，也不能用“只是优化”代替证据。
2. 使用 questions.json 的 `run_config_change` 原始措辞和选项，通过已有 subjects/evidenceHash 协议调用；独立变更批量询问，有关联的字段作为一组判断。保存 DecisionRecord，以 decisions.py 的 choiceTop、choiceMargin 检查完整概率分布。不得仅用最高选项或配置哈希决定重启。
3. `retain_run` 且能力与授权核验通过：沿用原 runId、检查点和全部仍有效结果。`business_impact`：宿主必须定位具体受影响的业务义务、门禁或证据并核实；仅在确有原契约规定的基线变更时进入对应变更流程，保留原 run 和历史关联，明确待执行范围。Jev 结论本身不能创建新 run、扩大授权或重跑已完成业务。
4. `insufficient_evidence`、host-analysis、低置信度、响应无效或工具不可用：保留原 run 和旧配置，补证据；仍不明则只阻塞依赖该调整的步骤。用户明确停用旧模型时不再调用它。不因不确定性重新初始化，也不清空结果或降低门禁。

## 无业务影响的模型调整落盘与调用

当前可直接应用的字段限定为 `roles` 下已有角色绑定与 `defaultEffort`；包括模型、推理强度及已验证的调用方式。新增 Provider/API 必须验证协议、能力与数据授权，凭据仅保留安全环境引用。其他字段即使 Jev 返回 retain_run，也先核对其专属配置契约和调用方支持，不能借模型调整覆盖执行参数、人工审批开关、决策阈值或目标。

唯一协调者在原 workspacePath 的 `model-adjustments.json` 保存 `schemaVersion: 1`、`runId` 和按顺序追加的 `adjustments`。每条包含 `revision`、`requestedAt`、`requestRef`、`baseConfigHash`（原快照字节哈希）、`previousRevision`、完整字段差异、`overrides`、`decisionRef`、`capabilityEvidenceRef` 与 `effectiveFrom`（后续调用起点）。仅在用户请求、完整分布判定和能力核验均通过后追加，原子保存并读回；不得覆盖已有条目或原 argus-model.json。未完成验证的候选只留决策证据，不写入生效列表。

恢复和每次角色调用前，核对 runId、原快照哈希、连续 revision、请求/决策/能力证据及 overrides 字段范围；失败只阻塞该调整。`argus/scripts/dispatch.py` 已实现这组核对：以原快照为底按 revision 应用 overrides，遇到第一处不通过的条目就停在上一版，并在 card 里给出 `configRevision` 与 `adjustmentRejection`；主 Agent 不再手工覆盖角色绑定。以原快照为底按 revision 应用 overrides，角色绑定整体替换，未调整角色保留原值。实际调用回执记录所用 revision、实际模型和强度。已在运行的 worker 保持派发时版本，完成后收回原回执；新调用才采用新版本，不为换模型重复派发。

原 runConfigPath、业务 inputHash、审批材料与已完成结果不因模型调整而改写；模型调用/决策缓存键必须包含实际模型、强度、题目版本和调整 revision，不能把旧模型响应冒充新调用。若后续生成改变用例、断言或其他审批材料，按实际产物变化重新执行对应评审门禁，不能用本次 retain_run 豁免。只发布失败仍只补发布。

完成条件：同一 run 从未完成检查点继续，调整与每次调用可追溯；或原 run 保持明确的待核验状态。任何分支都不以“配置变了”为由自动重启全量测试。
