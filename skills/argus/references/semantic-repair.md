# 低分语义增量修复

语义回执不是低分清单。`final < thresholds.noulYes` 的主体必须进入修复闭环：协调者先运行
`semantic_batch.py repair-plan`，再按计划把低分用例切成互斥分片，使用 generation Agent 逐片修复；主会话不直接改 `cases.json`。

## 分片规则

- 同一个 `caseId` 的 `cases`、`realization`、`ruleSupport` 低分项必须进入同一分片；一个分片最多 10 个用例，可按业务域继续拆分。
- `sources`、`impacts` 低分项进入独立 evidence 分片，不允许为了抬分修改无关用例。
- 每个分片只有一个写入者，输入只使用计划生成的 `input.json`，输出为 `result.json`；失败只重派同一分片的新 attempt。
- Agent 必须逐条判断“确有遗漏 / 语义误报”。确有遗漏时只修改所属用例，补齐 Given/When/Then、步骤绑定断言和规则来源；疑似误报时不得修改用例，必须填写独立证据、理由和复核 Agent 标识。
- 结果必须由 `semantic_batch.py repair-validate` 校验，再由协调者 `repair-assemble` 原子合并。越权 caseId、重复写入、空理由、无证据或伪造低分复核均 BLOCKED。

## 二次语义复核

合并后只对受影响主体执行增量 Jev，不接受 Agent 自报分数：

```bash
python3 semantic_batch.py RUN repair-subjects RUN/cases/semantic-repair-subjects.json
python3 semantic_batch.py RUN repair-prepare RUN/cases/semantic-repair-subjects.json
# 对 cases/semantic-repair-batch/ 下每个 input 执行 request/record
python3 semantic_batch.py RUN repair-assemble
```

`repair-prepare` 只生成受影响主体的新题批，未受影响主体复用旧回执；不能整套重跑，也不能把原低分直接标为通过。`repair-assemble` 会把真实 Jev 回执中的新分数写回 `postFinal`，并同步到 `semantic-review.json`。修复项必须有新的 `postFinal >= threshold`；误报项必须有独立证据、证据哈希和复核标识。任何未闭环低分都会阻断 `semantic` 门禁。

产物：`cases/semantic-repair/plan.json`、各分片 `result.json`、`manifest.json`、增量回执以及修复前后 case 哈希。报告需分别统计 repaired、falsePositive、blocked 和重派次数。
