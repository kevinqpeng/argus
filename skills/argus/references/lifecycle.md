# 产物生命周期与测量

## 状态与路径

生成用例使用 plan.phase="cases"：Worker 返回 reviewStatus=semanticReview=draft，automationRef 为 planned:<specFile>::<完整标题>。generation.py 是本地契约唯一实现；每个 Worker 结束写入后立即检查，全部通过再 --merge。draft 错误、缺引用或归属错误均 BLOCKED，不猜测修复。JSON 规范化不产生审批。旧计划无 phase 时保留旧版实际文件引用契约。

脚本生命周期是 Draft（Worker 独占目录中的源文件）→ Validated（publish 门禁通过）→ Published（原子写入且 published.json 绑定哈希）。Validated 是当次对真实文件的检查结果，不缓存成可陈旧的布尔值；发布在同一把 run 锁内重新检查。Published 恢复时核验内容，异内容拒绝覆盖。

- 冻结配置是 run/argus-model.json，runId 与 manifest 一致，原始字节哈希必须匹配初始化记录的 manifest.runConfigHash。
- 人工门禁按 [评审证据契约](case-review.md#本地证据契约) 校验真实来源回执、审批材料、用例/矩阵和状态；关闭评审也必须提供绑定本轮配置的授权证据。旧裸 approvalRef 或任意 reason 不再足以通过。
- 脚本计划沿用 cases/scripts/plan.json，specFile 是相对 harness/tests 的安全路径，各任务及文件唯一。源文件为 cases/scripts/<taskId>/<specFile>。回执增加 runId、planHash、taskId；discoveredTitles 是 caseId → 标题。回执是本地契约证据，不能替代真实 runner discovery。
- 已批准 cases/cases.json 保留。bind 生成 execution-cases.json；执行、runner 和结果映射使用这份派生用例，计划及人工审批仍绑定原文件。两份用例只允许 automationRef 不同。

## 命令顺序

所有命令使用实际 Skill 脚本绝对路径，RUN 指本次私有 run。

```sh
python3 phase_gate.py RUN --phase review
# 生成每个任务的源 spec 与 result.json 后，由协调者执行：
python3 lifecycle.py RUN publish TASK
# 所有脚本已发布后：
python3 lifecycle.py RUN bind
# 用真实 runner --list 写 cases/discovered.txt，再执行：
python3 phase_gate.py RUN --phase scripts
```

publish 内部调用 phase_gate.publish；也可单独 `--phase publish` 做全量预检。脚本文件必须先发布才能 discovery/E2E；测试报告或在线表格的发布仍在执行后。两者生命周期不同。

原子性按单个 spec 文件保证。发布采用独立临时副本 + 不覆盖的原子链接，不把 Worker 源文件硬链接到正式目标。中断在文件落盘与回执之间时，重复命令核验同内容后补齐回执。不同 task 发布失败不回滚成功 task，也不要求重新生成它。计划、输入或依赖哈希变化仍使关联阶段失效。

## 清理

Worker 的缓存只放 RUN/tmp/<taskId>/<attempt>/。协调者确认实际进程停止后，在该目录写 owner.json：`{"runId":"...","taskId":"...","attempt":1,"state":"stopped"}`，再执行：

```sh
python3 lifecycle.py RUN cleanup TASK --attempt 1
```

清理只接受脚本计划中的 taskId、正整数 attempt、精确所有权和 stopped 标记；拒绝符号链接。目录已不存在时幂等成功。源文件、回执、harness、报告与证据均不在清理范围；浏览器认证资源只有明确置于此任务临时目录、停止进程且确认不再用于恢复后才可清理。其他位置的资源保留并报告。

发布与清理共用 run 锁，事件写 lifecycle.jsonl，计时写 metrics.jsonl；保留锁文件以免并发创建不同锁 inode。普通文件权限不是恶意 Worker 的沙箱：宿主必须落实独占目录及停止写入，绕过入口执行 rm 不受该程序保护。

## 统计与恢复

generation.py --metrics RUN/metrics.jsonl 自动记录 validation；lifecycle.py 自动记录 publish/cleanup；jev.py --metrics RUN/metrics.jsonl 记录实际网络调用数、失败、调用前拒绝、耗时、usage 和契约错误重试数（内部不重试）。默认日志在各命令输出目录，编排时显式传统一日志路径。

宿主生成和 Playwright 执行是外部阶段，用包装器记录其真实进程耗时：

```sh
python3 metrics.py --stage generation RUN/metrics.jsonl -- <生成命令及参数>
python3 metrics.py --stage playwright RUN/metrics.jsonl -- <runner命令及参数>
python3 metrics.py RUN/metrics.jsonl
```

宿主子 Agent 无可包装命令时，在调度开始/完成的真实时间记录同结构事件。每个操作只选一个计时边界，避免父子耗时重复计入；并发事件时长求和不是总墙钟。manifest.timing 保留全流程起止时间。统计缺失为 unknown，不补零；Token 只有 provider 返回 total_tokens 时求和，缺任一调用用量则总量 unknown。宿主重试须另记 contractErrorRetries，内部零次不代表外部零次。

648 秒仅是用户提供的历史收尾返工基线。本地测试耗时不能与它计算提升比例；需要同一输入、模型、目标、质量门禁的真实运行，单独记录返工区间后再比较。
