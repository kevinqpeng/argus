---
name: coverage-check
description: Retired. Human approval of the case workbook releases execution. Do not recreate a coverage script.
---

# 覆盖检查已取消

人工评审通过，即接受按批准用例执行。不要运行或重写 coverage.py、prepare_input.py，也不要写 coverage-plan.json 或 coverage.json。

执行入口以 `phase_gate.py --phase scripts` 为准：已批准用例都有真实 discovery。执行事实写 results.json，结束时跑 `--phase execution`。只回归部分问题时报告 partial，结论来自本次结果。
