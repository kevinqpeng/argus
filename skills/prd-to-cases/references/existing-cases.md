# 外部用例接入

适用于用户提供 Excel、CSV、Markdown 或 JSON 用例，要求整理、生成脚本或执行测试。完整 Argus run 优先验证后复用；只有用例文件时走本分支。接入复用现有生成、语义审查和覆盖工具，不把人工用例伪装成完整 PRD。

已有 run 的在线评审修改或最终附件交回，先按 [Argus 评审交回](../../argus/references/case-review.md) 保留原基线和评审轮次，再复用下方转换规则；不自动切换 inputBasis 或另起无关联任务。

## 1. 保存来源和限定结论

原文件只读，在私有 run 保存原始副本和 SHA-256；表格用宿主可用表格读取工具，Markdown/JSON 完整解析。记录工作表/行号或段落、原用例 ID、版本、用户明确选中的范围及预期适用依据。工具无法读取、合并单元格含义不明或缺列时明确列出缺口，不猜测内容。

来源登记为现有 `history` 类型；随附需求、业务规则分别按其实际类型登记。兼容字段 sourceHash/currentSourceHash 绑定本次输入文件或稳定来源清单的哈希，报告注明来源为外部用例。用户明确将所附用例作为本轮测试依据时保存该真实指令，作为适用依据；单纯上传文件、历史“通过”状态或模型判断不能代替预期有效性与本轮人工评审。

在覆盖聚合输入设置 `inputBasis={kind:"cases", sourceIds:[实际用例来源ID], approvalRef:真实适用依据引用}`，各 sourceId 必须对应 history 来源；写入 manifest.inputs 并纳入 inputHash，design/plan/execution 原样传递。未指定 inputBasis 的旧任务仍按 PRD 验收校验，不能自动切换模式绕过缺失 PRD。此依据确认只表示本轮按这些用例测试，不解除人工用例评审。

## 2. 建立映射，不重写业务预期

| 输入内容 | 规范化处理 |
| --- | --- |
| 原用例 ID、模块、标题 | 保留原值和 sourceRef；唯一 ID 直接复用，缺失或冲突时分配稳定 caseId，记录逐行对应关系 |
| 前置条件、测试数据 | 保留到 fixtures/场景前置条件，凭据改为私有引用 |
| 操作步骤 | 顺序不变，分配唯一 stepId；不静默合并或遗漏步骤 |
| 预期结果 | 原意不变，绑定对应 stepId；“正常”“正确”等无可观察含义的预期标 needs_clarification |
| 优先级、平台、角色 | 明确值复用；必要值缺失时基于证据补齐并展示依据，无法确定的必需矩阵先澄清 |
| 历史执行结果、审批 | 保留为来源备注，不作为本次结果或审批 |

从明确的预期提取 requirements、AC、scenarios 与 obligations，每项均追溯原行和确认依据。按现有影响分析核对依赖与六维检查；无源码明确证据边界，不虚构 PRD 或“不适用”。发现外部用例未覆盖的已知相关义务时保留缺口；未授权扩展的范围保持 partial，不擅自增加用例或把遗漏排除后宣称全量通过。

sourceUnits 保留该行的 sourceExcerpt 原文和单元格定位。无断言的业务步骤标 needs_clarification，不静默删掉或标为准备步骤；仅真实准备/清理按共享步骤契约记录豁免。交回新增用例与原需求冲突时同时保留两份依据，不能因为原表写“通过”就替换产品规则。

## 3. 规范化与校验

由现有 generation 子 Agent 按[用例生成契约](parallel-generation.md)执行转换任务，输入增加原始用例及逐行映射，输出仍为 cases.json + notes.md。taskId、caseId、归属、分片哈希和 dispatch 门禁不变。worker 只做字段规范化，不重新设计已有步骤/预期；补充建议单列，得到范围与规则确认后才进入新版基线。

协调者合并后在 `cases/import-map.json` 保存 `{schemaVersion:1, sources:[{sourceId, sourceHash}], rows:[{sourceRef, originalCaseId, caseId, disposition, reason}]}`。每条原始用例有且只有一条处置记录，选中行与 caseId 一一对应；排除必须有用户范围依据。核对原行的前置、步骤顺序和预期未丢失，转换后没有未解释的新用例。映射和原始来源纳入生成 inputArtifacts、inputHash 及人工评审 artifactHashes；不覆盖历史文件。

按 decision-router 对原用例预期 → AC/场景 → 规范化步骤断言做语义审查；沿用概率阈值、低置信度处理和真实回执。随后运行 dispatch 与 `coverage.py --phase design`，生成与核对同一组 JSON、Markdown、Excel 评审材料；原 ID 与 caseId 对照保存在内部映射，待确认项以业务白话展示。

## 4. 交接

只整理用例时交付后结束。用户请求执行或脚本生成时，将本 run 完整材料交给对应技能，按共享人工门禁审批本轮版本，再探索、生成和执行。审批对象必须绑定实际转换后的材料；“这是已评审用例”只有附带可核验、覆盖本轮哈希与矩阵的审批记录才可复用，否则仍需本轮评审。

完成表示选中用例转换无遗漏、预期来源可追溯、design 与 dispatch 通过且材料一致；不表示全量 PRD 覆盖或真实业务通过。未知规则和缺失证据照常阻断，不能为导入开设门禁豁免。
