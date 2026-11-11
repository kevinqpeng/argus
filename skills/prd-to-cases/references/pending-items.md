# 待确认事项：先自查，再提问

待确认事项是评审成本的来源。一条能在代码、契约或既有测试里查到答案的问题，写进评审表就是让产品替 Agent 干活；一条真正需要业务裁决的问题被漏掉，评审就失去意义。本文件规定三分法、每条必填的自查证据，以及只有哪一类进评审表。

## 1. 三分法

生成 worker 与主 Agent 提出的每个疑问，先归类，再决定去向：

| 类型 `disposition` | 判定 | 去向 |
|---|---|---|
| `resolved_by_code` | 基线代码、配置项、接口契约、既有测试、流程定义里能查到明确答案 | **不进评审表**：结论写进断言或前置条件，证据写 `evidenceRef` |
| `assumption` | 查不到明文，但既有实现有稳定默认行为，且按它写不会改变业务意图 | **不进评审表**：写 `basis`（为什么这么假设）与 `falsifier`（什么现象会推翻它），落 `cases/assumptions.md` |
| `decision` | 只有产品意图、规则取舍、排期或未定契约才能回答，且不同答案会改断言 | **进评审表**「待确认事项」，写清 `blocking` 与 `impactCaseIds` |

决策顺序固定：先查（`resolved_by_code`）→ 查不到看是否可假设（`assumption`）→ 都不行才提问（`decision`）。跳过前两步直接提问视为未完成自查。

## 2. 每条必带自查证据

全部类型都要写 `searchedRefs`：至少一项，形如 `文件:起始-结束`（查过并给出依据），或 `检索无命中：关键词`（查了什么没找到）。空数组或只写「不确定」都会被 `notes_check.py` 阻断。

`decision` 类额外要求：`impactCaseIds` 非空且都是真实 caseId（一条决定会影响哪些用例），`blocking` 用布尔值标明「不裁决能否先写断言」。

## 3. 结构化落盘

生成阶段写 `cases/notes.json`（人读版 `notes.md` 同步保留）：

```json
{"schemaVersion": 1, "notes": [{
  "category": "驳回记录展示",
  "question": "驳回行的动作名称写「驳回至发起人」还是「驳回」？",
  "action": "请产品确认驳回动作的展示名称，确认后固定记录断言。",
  "sourceRefs": ["cases/generation/G4/2/notes.md"],
  "disposition": "resolved_by_code",
  "searchedRefs": ["yunfun-module-bpm/src/main/java/.../BpmCommentTypeEnum.java:12-20"],
  "evidenceRef": "yunfun-module-bpm/src/main/java/.../flow-record-list.vue:406-418",
  "conclusion": "流转记录按 comment 类型渲染，驳回动作沿用既有「驳回」文案，不新增独立名称。"
}]}
```

字段速查：

| 字段 | 适用 | 要求 |
|---|---|---|
| `category` `question` `action` | 全部 | 非空；问题写发生条件与具体疑问 |
| `sourceRefs` | 全部 | 非空，指向本 run 内的生成回执或影响分析 |
| `disposition` | 全部 | 三值之一；缺省按 `decision` 处理（兼容旧 run） |
| `searchedRefs` | 全部 | 非空，`文件:行` 或 `检索无命中：…` |
| `evidenceRef` `conclusion` | `resolved_by_code` | 证据定位与 ≥20 字结论 |
| `basis` `falsifier` | `assumption` | 假设依据与推翻条件，各 ≥20 字 / 非空 |
| `impactCaseIds` `blocking` | `decision` | 非空真实 caseId 列表；布尔 |

## 4. 呈现规则

- 评审表「待确认事项」只列 `decision`。`resolved_by_code` 的结论必须已经体现在断言或前置条件里，评审时无需回答。
- `assumption` 写 `cases/assumptions.md`，评审表末尾给一行汇总数量与文件路径；被推翻时改假设并重跑受影响用例。
- `resolved_by_code` 若结论尚不能落进断言（例如查到的就是「不涉及」），把它写进 `cases/cases.md` 的「已自查结论」附录，不进 Excel。
- 同一疑问在不同分片重复出现时合并成一条，保留全部 `sourceRefs`；合并后类型取更严格的一档。

## 5. 自检

合并之后跑一次，不逐片跑：

```
python3 <本技能>/scripts/notes_check.py <runDir> [--baseline <基线工作树>]
```

阻断条件：类型非法、`searchedRefs` 缺失、`resolved_by_code` 无证据或结论过短、`assumption` 缺依据、`decision` 缺影响用例或未标阻塞。通过后打印三类计数，主 Agent 把计数写进 `manifest.gates.pendingItems`。

## 6. 判定示例

同一需求的疑问按三分法分流（取自「审批驳回后重新发起」的实践）：

| 疑问 | 类型 | 理由 |
|---|---|---|
| 驳回记录的动作名称取自哪个枚举 | `resolved_by_code` | 代码里有渲染分支，查得到 |
| 加签分向前、向后，前端都提供吗 | `resolved_by_code` | 枚举与按钮入口都在基线里 |
| 开关关闭的在途实例驳回时按哪个时点判定 | `decision` | 涉及产品口径，不同答案改断言 |
| 多人节点待办是候选人池还是指派一人 | `decision` | 来源未写，两种答案对应不同断言 |
| 提示文案的停留时长 | `assumption` | 既有实现是浮层 3 秒，按它写不影响业务结论，被推翻再改 |

问题不在于问题多，而在于有多少问题本可以自己回答。三分法把「Agent 该做的功课」和「只有人能做的决定」分开，评审表只剩后者。
