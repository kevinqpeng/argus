# 形式化验证路由

在场景基线冻结前检查每组 AC 与其候选场景，包括尚未形成场景的状态规则，缺陷回归和恢复时重新核对涉及的状态、源码和输入指纹。此路由与 web/mobile/API 路由并行适用，不替换平台选择。

1. 用户明确指定形式化检查时选中该范围。明确排除时记录排除依据与风险，不将未检查表述为通过。
2. 已有证据明确涉及共享状态交错、重复副作用或跨步骤状态约束时直接选中，保存确定性依据；纯静态展示、单次无状态字段校验不需要 TLA+，保留常规边界测试。单纯算术、解析或内存安全问题使用真实代码的属性测试或适用验证器，不强制建状态模型。
3. 先核对是否有明确 AC 和足以判断状态行为的来源证据。只有“提高可靠性”之类目标、未知操作语义或缺关键证据时，直接补证据并保持 needs_clarification/BLOCKED；不能把模型给出的低概率解释为已证明不适用。其余需语义判断的候选按 [集中题目](questions.json) 的 `formal_verification` 批量询问 Jev。state 含场景 ID、AC、状态与操作主体、事件顺序、重复/失败行为及源码或协议定位。使用完整集中措辞，逐题绑定主体，不靠关键词或优先级直接推测。独立场景可通过宿主 jev_ask 的不同题目 ID 一次提交，共用该定义并分别保存 DecisionRecord；脚本入口按原协议使用固定题目 ID。
4. 使用 questions.json 的 noulYes/noulNo 阈值及 decisions.py：yes 调用 [Argus 形式化子技能](../../argus/skills/formal-verification/SKILL.md)，no 留证据并继续原流程，host-analysis 补充证据后重判。仍不确定记 needs_clarification 和 BLOCKED，不默认跳过。服务失败按 decision-router 保留 BLOCKED，不能当 no。
5. 路由完成后调用方记录 `manifest.gates.formalVerification` 的 status、reason 和 evidenceRef，未选中时 evidenceRef 指向适用性检查与排除记录。只要存在未决场景或所选检查未闭环就保持 BLOCKED；所有候选都有明确不适用或排除依据时为 N/A。选择后的完成条件由子技能定义。预算不足、工具缺失不能改为 N/A。原验收范围内义务被排除时保留原分母缺口，结论标 partial。

题目、阈值和版本只维护在 questions.json。路由判断不证明性质成立，不产生用例 passed，也不解除人工评审门禁。
