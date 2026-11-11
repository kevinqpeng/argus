# 脚本速查

调用 prd-to-cases 的脚本前读这一页。命令顺序在 [SKILL](../SKILL.md) 和对应契约里。这里只记录会让脚本退出的格式，以及一行修法。

路径里的 `<本技能>` 是本 SKILL.md 所在目录。

## 命令

| 何时 | 命令 |
|---|---|
| 来源单元 | `python3 <本技能>/scripts/source_units.py <units.tsv> -o <runDir>/cases/sources/units.json` |
| 表行 | `python3 <本技能>/scripts/assemble_baseline.py rows --run <runDir> --prd <相对路径>` |
| 绑定 AC | `python3 <本技能>/scripts/assemble_baseline.py ac --run <runDir> --prd <相对路径> --source-id <ID> --bindings <tsv>` |
| 覆盖 | `python3 <本技能>/scripts/assemble_baseline.py cover --run <runDir> --prd <相对路径> --acs cases/acs.json` |
| 影响分析 | `python3 <本技能>/scripts/assemble_baseline.py impact --run <runDir> --prd <相对路径> --source-id <ID> --candidates <merged.json>` |
| 探索回执 | `python3 <本技能>/scripts/merge_impact_candidates.py <plan.json> <回执.json>...` |
| 证据真实性 | `python3 <本技能>/scripts/verify_evidence.py <merged.json> --baseline <基线工作树> --run <runDir>` |
| 用例正文 | `python3 <本技能>/scripts/emit_cases.py --tsv <taskId>.rows.tsv --ac-ids <本任务AC> --check`，通过后加 `--out <attempt>/cases.json` |
| 封印 | `python3 <本技能>/scripts/generation.py <plan.json> <state.json> --root <generation目录> --seal <taskId> --gaps '[]'` |
| 展示分片 | `python3 <本技能>/scripts/render_review_table.py <runDir> --plan-slices` |

`baselineHash` 和 `inputHash` 是 64 位小写 hex。基线哈希用 `generation.digest`：`sort_keys=True`、`ensure_ascii=False`、`separators=(",", ":")` 的 UTF-8 SHA-256。文件字节哈希不能填进 `baselineHash`。

## 表头

来源单元 TSV，制表符分隔：

`unitId	sourceIds	sourceExcerpt	evidenceRef	disposition	obligationIds	reason	reviewRef`

`sourceIds` 和 `obligationIds` 用 `|` 分隔。

AC 绑定 TSV，制表符分隔：

`rowRef	acId	requirementId	text	priority	scenarioId	title	expected`

一个 `acId` 只对应一个 `requirementId`、一个 `scenarioId`、一个 `priority`、一个 `title`、一个 `expected`、一份 `text`。多行只为盖住多条 `rowRef`。同一个 `requirementId` 的 `text` 逐字相同。

用例步骤 TSV 写在 generation 根目录的 `<taskId>.rows.tsv`。attempt 目录只放计划里的 outputFiles。一行一个步骤，同一 `caseId` 按顺序接成一条用例。制表符分隔：

`caseId	scenarioId	title	priority	suite	spec	fixtures	stepId	kind	action	expected	exemption`

`scenarioId` 和 `fixtures` 用 `|` 分隔。`suite` 是 `ui`、`api`、`performance`、`security`。`kind` 空着就是 `action`。`spec` 是脚本阶段的文件名，不含 `::`。`emit_cases.py` 写出 `planned:<spec>::<title>`，标题用 `title` 列。

先写这一条的各步骤并 `--check`。通过后再写本任务其余行，然后 `--out`。`action` 步骤写 `expected`。`setup` 或 `cleanup` 可以不写 `expected`，这时 `exemption` 说明它为什么不是待测行为。

## 报错

| 原文 | 修法 |
|---|---|
| `invalid baselineHash` | 改成 64 位小写 hex，用 `generation.digest` |
| `evidence span > 3 lines` | `文件:起-止` 含首尾最多 3 行；一条 check 最多 2 段，用 `;` 分隔 |
| `六维留空 24/36 超过三分之一` | 先把变更对象查完再登记空格。允许的空格会自动写成 `impact-gaps.json` 的 `GAP-<obj>-<dim>` |
| `六维结论不成立 … 理由不足` | `not_applicable`/`out_of_scope` 的理由不能只写“不适用/无”；写清该维度为何不被本次变更触及 |
| `impact-gaps.json 与实际六维空格不一致` | 手改过 `impact-analysis.json`。重跑 `assemble_baseline.py impact` 重新生成登记表 |
| `六维未核查项必须写进待确认事项：GAP-…` | 在 `content.json` 的 notes 里补一条，`sourceRefs` 写 `impact-gaps.json:<gapId>` |
| `data 不能和别的维度合并` / `一次最多 3 个` | `dimensions: [...]` 批量豁免不允许包含 `data`、`permission`，且每对象最多 3 维 |
| `缺少 windows` / `应有 4 到 6 个窗口` | `plan.json` 每个 task 按 objectId 写 `windows: [{objectId, refs}]`，每对象 4 到 6 个 `文件:起始-结束` |
| `窗口跨度超过 3 行` | 窗口最多 3 行；跨多段就拆成多个窗口 |
| `文件不存在` / `行号越界`（verify_evidence） | `evidenceRef` 的路径相对基线工作树，行号必须落在真实文件内；拼错或靠 PRD 反推都会被拦 |
| `同一 AC 的场景或优先级不一致` | 一个 acId 只保留一套 requirementId、scenarioId、priority、title、expected、text |
| `同一需求的正文不一致` | 同一个 requirementId 的 text 改成同一份原文 |
| `绑定表表头必须是` | 绑定表改成上面的 AC 表头，制表符、列序不变 |
| `TSV header must be unitId,sourceIds` | 来源单元改成上面的单元表头。报错用逗号连接列名，文件本身用制表符 |
| `TSV header must be caseId` | 用例步骤表改成上面的步骤表头 |
| `input overlaps worker directory` | inputArtifacts 放在 `frozen/` 或 `inputs/`，路径不以 taskId 开头 |
| `planned automationRef requires file::title` | 阶段 A 由 emit 写 `planned:<spec>::<标题>`；标题与 title 列相同 |
| `missing assertion or explained setup/cleanup exemption` | 给该 stepId 补 expected，或把 kind 改成 setup/cleanup 并写 exemption。整条用例没有断言时报 `missing assertions`，同样补上 expected |

`--check` 同时拦住常量前置（`status=已驳回` 这种赋值）、断言里的「记录保留」、以及没有基线步骤的增量断言。传入 `--ac-ids` 时，步骤里出现的 `AC-…` 必须属于本任务。

展示片的条数和 `lineBudgets` 以 `render_review_table.py --plan-slices` 写出的 plan.json 为准。代码定位方式在 [探索调度](impact-exploration.md)。
