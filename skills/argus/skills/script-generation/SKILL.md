---
name: argus-script-generation
description: 可独立或由 Argus 调用，按批准用例和已验证地图生成、发布测试脚本及绑定入口；只生成脚本时交付后结束，支持恢复未完成任务。
---

# 脚本生成

## 输入与职责

读取 [共享契约](../../references/contracts.md)、[共享调度](../../references/scheduling.md) 和 [产物生命周期](../../references/lifecycle.md)。输入为批准用例、完整矩阵、冻结配置、平台语言与 runner，以及每个 UI spec 的地图快照、哈希、探索计划和 check 回执。纯 API 无页面依赖。

协调者负责队列、派发、回执校验、发布、discovery 和入口绑定；generation worker 仅实现分配文件，不访问业务目标或自行探索。沿用冻结角色及宿主能力，无法调用时 BLOCKED，不由主会话冒充。

## 分批生成与发布

独立调用按 [工作流组合](../../references/workflows.md) 补齐必要前置，优先复用有效用例和地图；外部用例先规范化并通过 design 与本轮评审。缺 UI 地图时仅在目标访问已授权范围内调用页面探索。只请求脚本时完成发布、discovery 和门禁后结束，不自动进入业务执行或 Bug 发布。

- 前置：`cases/cases.json` 全部 `reviewStatus=approved`；`phase_gate.py --phase review` 必须已 PASSED。未批准不得开始脚本，否则脚本对着会被改的用例写。
- 计划写在 `cases/scripts/plan.json`：`{schemaVersion, runId, approvedCasesHash, tasks:[{taskId, caseIds, specFile, notesFile}]}`。`approvedCasesHash` 绑定批准时的 cases.json 摘要；用例再变，计划失效。
- 切分：一个任务一个独立测试文件（`specFile` 唯一，不限制扩展名；在本阶段决定文件名，不依赖设计用例中的 planned 引用），任务数按规模与复杂度确定；公共 DB 断言代码可作为共享生成任务，环境准备与实际核验进入执行队列并保留依赖。任务里写明「只实现分配 caseIds，不改用例期望值」。
- 回执：每任务写 `cases/scripts/<taskId>/result.json`（`runId`、`planHash`、`taskId`、`specFileHash`、`caseIds`、`discoveredTitles`（caseId → 完整标题））；主 Agent 收齐回执并发布全部文件后，通过 lifecycle.py bind 生成 `cases/execution-cases.json`，再用 runner `--list` 复核发现标题逐字等于用例标题，保留原审批材料。
- 门禁：`phase_gate.py <runDir> --phase scripts` 要求 plan 存在、每任务回执齐全、`approvedCasesHash` 与当前 cases 一致、discovery 覆盖全部已批准用例（不得多也不得少）、`manifest.scripts.generatedAfter` 晚于人工批准时间。

- 按共享调度领取任务；公共 fixture/Page Object/DB helper 由唯一任务生成并冻结，消费者保留 dependsOn。生成代码不连接业务数据库；实际核验留在执行阶段。
- UI spec 派发前由 [页面探索子技能](../page-exploration/SKILL.md) 交付该 spec 全部 caseIds 的 PASSED 回执及固定快照；依赖就绪即生成，其他页面探索继续。生成并发与执行并发独立，生成不占设备会话。
- worker 的输入清单仅含本任务批准用例、必要业务契约、依赖和地图证据；给出墙钟及工具预算，沿用冻结生成预算，未配置时每任务默认 10 分钟、复杂组 15 分钟、shell 调用最多 6 次。缺定位信息返回探索协调者；用例本身有误返回 prd-to-cases 重新评审，不静默改期望值。
- 每个任务完成即按生命周期运行 publish，通过门禁后原子发布到 harness/tests；源 spec 和 result.json 保留。全部发布后 bind，再运行真实 runner discovery 和 scripts 门禁，按生命周期规定的命令顺序执行。
- 用例、输入或相关地图失效时停止受影响 worker，保留旧产物，按新计划重新校验和生成；无关且仍有效的产物复用。恢复先检查输入哈希、队列和真实 worker 状态，不能仅因超时重复派发。

## 完成条件

全部任务回执和发布哈希有效，execution-cases.json 仅绑定真实 automationRef、保留原批准用例，真实 discovery 精确覆盖批准用例与必需矩阵，scripts 门禁 PASSED。UI 还须页面探索全量 check 通过。按所选终点交付脚本或交回 Web/Mobile runner；生成或 discovery 成功不代表测试通过。人工评审通过即接受执行。

记录生成、等待、重试和发布的真实耗时及缺口；清理仅走生命周期入口，保留恢复材料。
