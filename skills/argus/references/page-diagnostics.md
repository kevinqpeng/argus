# 页面探索与失败诊断

本规则适用于 Web UI、H5 和移动端；纯 API 用例跳过页面地图。探索涉及真实业务操作时，先通过共享契约的人工用例评审门禁及授权检查。

## 先规划，再按需探索

人工评审通过后、脚本 worker 派发前，协调者一次读取全部批准用例与必需矩阵，按下述契约生成 `evidence/exploration-plan.json`。平台路由在此时完成；Web、Android、iOS 分开规划，纯 API 跳过。UI worker 是现有执行职责，不新增进程或角色。

1. 将每个 UI 用例的每个 step 和 assertion 映射到页面状态、必要元素及矩阵。API/DB 断言或无元素的导航步骤写 `nonUIReason`，仍保留原索引；不能遗漏断言目标。动态需求写 `dynamic=true`，可在到达状态后补充元素，但仍必须关联原用例、受相同预算约束。
2. 运行 `python3 <argus>/scripts/exploration.py plan <私有输入.json>`，保存 stdout；退出 2 或 BLOCKED 停止探索，补齐映射或在授权范围调整预算。脚本按平台、页面、状态、scope 合并需求，校验所有动作/断言×矩阵均有去向，检测未知依赖和依赖环。
3. 按合并任务依赖顺序，只访问需要的页面状态。先查缓存候选，再用必要定位器探针验证；成功即复用，缺失才采集相关区域。相同稳定状态的结构证据仅取一次供多个元素使用。截图仅为明确视觉断言、歧义或必需证据采集。
4. 执行命令前调用 `allow`；结束后立即记录事件，包括失败。协调者独占写计划、地图和事件，worker 只返回独立回执；同一个浏览器页面或 Appium session 串行，禁止先并行检查余额再执行。预算是执行前检查加实际客户端超时，不靠事后统计代替限制。
5. 按 spec 分批就绪：每次地图更新后，对受影响 spec 运行 `check`，`caseIds` 必须等于该 spec 分配的全部用例，`cases` 仍传全部批准用例。检查覆盖这些用例的全部步骤、断言及必需矩阵；PASSED 才释放该脚本任务。无关页面未完成不阻塞已就绪 spec；返回 missing 时仅补充所列元素。动态目标也必须补齐后才能释放对应脚本，不能凭空生成定位器。
6. 脚本 worker 读取本任务不可变的地图快照及其哈希、批准用例、探索计划、check 回执和证据指针；缺信息交协调者，不自行遍历页面。协调者继续探索其他状态，按 [共享调度](scheduling.md#探索与脚本生成重叠) 及时补位。地图只增补无关条目不使已有快照失效；相关条目失效时停止受影响 worker，保留旧产物并按新计划重新校验、生成，不能修改已发布脚本。用例或预期变化仍回人工评审。
7. 执行前省略 `caseIds` 运行全量 `check`，保存 `scope=all` 的 PASSED 回执，再检查 scripts、discovery 与覆盖门禁。局部 PASSED 只允许生成对应脚本，不能代表全量就绪或业务通过。

### 工具输入契约

`exploration.py` 只使用标准库；支持 Python 直接导入或任意 runner/宿主通过 CLI 传 JSON，不改变现有脚本语言。所有输入/输出放 run 的受保护目录，不写入被测仓库。入口 `python3 <argus>/scripts/exploration.py <plan|check|allow|cache|recover|report> <input.json>`；退出 0 需继续检查具体结果，READY/PLANNED/cache reusable 均不表示业务通过。

`plan` 输入如下；示例只有一条动作和断言，实际传全部批准用例与完整矩阵。refs 使用原数组的零基索引；cases 原文不变，casesHash 与现有规范化 JSON 摘要一致。

```json
{
  "cases": [{"caseId":"C1","suite":"ui","steps":[{"action":"登录"}],"assertions":[{"expected":"欢迎提示"}]}],
  "request": {
    "runId":"run-1", "requiredMatrix":["desktop"], "matrixPlatforms":{"desktop":"web"},
    "requirements":[
      {"caseId":"C1","matrixId":"desktop","platform":"web","page":"login","state":"signed-out","scope":"role-tenant-frame-env","element":"submit","refs":[{"kind":"steps","index":0}],"screenshot":false},
      {"caseId":"C1","matrixId":"desktop","platform":"web","page":"home","state":"signed-in","scope":"role-tenant-frame-env","element":"welcome","refs":[{"kind":"assertions","index":0}],"screenshot":false}
    ], "budgets":{}
  }
}
```

`dependsOn` 为上游任务 ID（`digest({platform,page,state,scope})`）；多步登录、弹窗、滚动或导航前置必须写依赖与进入条件，不能把不同状态只按 URL 合并。scope 绑定角色/租户、设备/浏览器、视口/方向/语言、frame/native/WebView context，用非敏感标识或哈希。

- `check`：`{plan,cases,map,caseIds?}`；可选非空 caseIds 选择一个 spec 的全部用例，不缩减 cases 或矩阵，输出 scope=cases；省略时 scope=all。空、重复或未知 ID 拒绝。map 为本次校验的固定快照，结构为 `{schemaVersion:1,runId,entries:[...]}`，每条含 taskId、element、platform/page/state/scope、runId、targetHash、buildHash、environmentHash、stateFingerprint、locator、strategy、confidence、evidenceRef、current、probe。测试用例关联由 task.needs 保存。confidence 未实测写 unknown，不伪造模型概率。
- `cache`：`{entry,current,probe,budgets}`。current 保存本次运行的完整身份和新 observationId；probe 必须来自本次真实探针，含相同 observationId、runId、observedAt（Last Validated）、stateFingerprint、locatorHash、matches=1、usable=true。唯一性以外的可见/可操作/状态条件由平台探针按实际动作验证。旧回执不能用作新观察。
- `allow`：`{plan,events,operation}`；operation 含 taskId、category、elapsedMs、pageElapsedMs，开始一次页面探索置 visitStart=true；定位器重试加 locatorRetry=true 与 element；恢复加 caseId/matrixId。READY 返回 timeoutMs，平台调用 timeout 取自身上限与它的较小值。elapsedMs 是从分析开始累计的活跃探索墙钟，pageElapsedMs 是本次页面访问墙钟；暂停审批不计。恢复必须继承已用预算，不能通过重置时钟获得新预算。
- `recover`：`{error,evidence,used,budgets}`；used 是该 caseId+matrixId 已用恢复次数。支持 LocatorNotFound/LocatorAmbiguous/StaleElement/NavigationFailure/Timeout/AssertionFailure/AppiumSessionFailure/ApplicationError，结果见下方恢复规则。
- `report`：`{events:[...]}`；返回 web/android/ios 各自统计，汇入现有 run 报告。

## 页面地图

在 `workspacePath` 的受保护目录保存本 run 的 `page-map.json`，使用 `schemaVersion: 1`。首次探索前记录 runId、目标和应用构建版本；每个页面状态记录平台、页面标识、进入条件、角色/租户标识、设备或浏览器及版本、视口/方向/语言、native 或 WebView context、稳定定位器、可观察状态、页面转换和脱敏证据引用。缺失信息标 unknown，不推断定位器或页面行为；不保存账号、令牌或真实业务数据。

按冻结用例识别需要的页面和状态，首次探索后立即写入地图。生成脚本前先读取地图，多个用例共享同一已验证条目；未覆盖的页面或状态才探索。地图是定位线索，执行时仍须验证真实页面和业务断言，探索不能计为用例通过。

默认仅在同一 run 内复用；cache=false 时仍需当场探针，只是不查候选缓存。显式开启 crossRunCache 后，可从同一授权目标的旧地图读取候选，但每个需要的元素必须重新校验；不导入旧认证状态、业务数据或旧探针回执。目标、构建版本、设备/OS、浏览器/WebView、视口/方向/语言或角色/租户变化时，相关条目失效；页面结构或定位器与当前证据不符时，只失效该页面状态并定点重新探索。无法核实指纹时视为未验证。保留旧条目及证据引用供追溯，记录失效原因和替代条目。stateFingerprint 来自与任务相关的稳定结构/状态锚点，不为算哈希额外拉完整 DOM/XML；过滤时间戳、随机值及敏感业务内容。只缓存定位描述，不缓存 ElementHandle 或 Appium element ID。

## 预算和失败诊断

开始前在 manifest 的 `budgets` 中记录探索、诊断及修复的上限和已用数量。未给出更严格限制时，每个页面状态最多一次首次探索和一次因失效的定点重探索；每个 caseId+matrixId 最多一次有证据的脚本修复及修复后重跑。runner 重试和修复后重跑分别记录，遵守平台已有重试规则。预算耗尽时停止对应探索或修复，保留未执行项及原因，不缩减覆盖分母。

探索默认值集中在 `exploration.py:DEFAULTS`：最多 30 个页面状态、每状态 2 次、20 张探索截图、每次页面探索 120 秒、总活跃探索 600 秒、单定位器重试 1 次、每 case+matrix 恢复 1 次；cache=true、crossRunCache=false。通过冻结的 `execution.exploration` 覆盖，记录到计划和 manifest.budgets.exploration；现有更严格上限优先。超限保留 partial/BLOCKED 和未完成元素、用例、矩阵。必要失败证据不受“可选探索截图”限额裁剪，按独立诊断预算记录。

失败时保留每次原始 runner 输出、首次失败及平台规定的完整证据，按现有脱敏和留存规则处理。建立诊断摘要，关联 caseId、matrixId、attempt、失败步骤、原始错误、定位器、前置状态、相关页面节点或片段、截图及对应时间段日志/trace 的证据引用；缺失项标 `evidence_missing`。先读取摘要和相关片段，证据不足时再按需读取原始文件。摘要不得替代原始证据或改变断言结果。

先检查设备、会话、服务、数据和错误发生步骤，再定位页面或断言。环境、数据和产品失败不因失败本身而修改测试脚本。存在语义歧义时，按 decision-router 的 `failure` 题目提交脱敏证据；概率不达阈值或 unknown 时补充针对性证据，仍不明确则记录 unknown。Jev 分类不能替代复现或业务断言。

确定性错误直接用 recover：定位错误仅在已确认 correctState=true 且 locatorChanged=true 时走 local-exploration；导航/超时仅在 transientState=true 且 safeToRepeat=true 时有限恢复页面状态。超时本身不证明定位器失效。断言失败、应用错误、Appium session 故障直接报告并走原诊断流程；会话故障恢复后全部元素句柄失效，重验当前必要候选，禁止重放不幂等业务动作。任何恢复先消耗预算、保留首次失败；成功重跑仍为 flaky。

复用现有 decision-router failure 题目和概率门槛处理语义归因；缺证据不调用逐元素模型猜测。独立歧义批量提交，确定性指纹/唯一性/预算比较无需模型。模型调用、失败及退避均计 aiDecision，usage/成本不可得写 unknown。

仅当证据表明测试脚本有误时，修改与失败直接相关的定位器、等待或断言，保留原断言意图和原失败证据；记录修改依据并重跑失败用例及受影响的必要用例。修复后仍失败时停止自动修改，记录新旧错误、未解决原因及剩余范围。首次失败后重试成功仍为 flaky；不得通过删除、弱化断言或跳过用例获得通过。

记录探索、地图命中、重探索、诊断、修复及重跑次数和耗时，写入 run 事实与报告；缺失的计量标 unknown。与相同范围、环境和版本的基线比较，分别报告脚本准备、诊断和执行耗时，不将推测收益当实测结果。

每次操作事件保存 platform、taskId、category、durationMs、command（如 getPageSource/findElements/screenshot）、status 及证据引用；按实际需要附 visitStart、locatorRetry、element、caseId/matrixId、collected/used 元素名数组、cacheResult=hit/miss/invalid。计时用单调时钟覆盖真实调用（含失败），排他分类为 caseAnalysis/planning/navigation/structure/locator/screenshot/aiDecision/cacheLookup/recovery/other；嵌套命令只记叶子耗时，父级墙钟另记。Android UiAutomator2 与 iOS XCUITest/WDA 分平台记录。

统计结构读取/截图/重复访问次数、定位器采集与实际使用、缓存命中与失效比例、各命令耗时。脚本生成记录“引用”，真实 runner 执行时才记录 used。协调者另外测探索墙钟与 E2E 墙钟，从 coverage-check 取得通过率和有效覆盖；事件耗时相加不能冒充墙钟。未采集平台报告 unknown，不把零事件当零耗时或通过。历史 1073s/30.6%/6 页面/8 截图仅作参考，缺相同用例/环境/设备的复测时不报告提速百分比。
