---
name: performance-test
description: Runs scoped k6 API load and isolated sysbench MySQL benchmarks. Use only when load testing or database benchmarking is explicitly selected.
---

# 性能测试

## 输入与必读规则

读 [共享契约](../argus/references/contracts.md)。

输入：已授权主机/接口/数据库、测试窗口、最大并发/时长、数据与停止条件、业务 SLO。缺少范围和上限先补齐，不能默认对生产发压。

## 1. 核对工具与环境

- 核实 k6/sysbench 版本、来源、help 和真实输出格式。
- 记录脚本/数据/工具/环境指纹和服务端指标采集方式。
- 工具启动失败 error。未选模块 N/A。

## 2. 配置业务 API 负载

- k6 依业务选择 smoke/固定/阶梯/持续负载，真实认证、动态资源、checks、停顿与清理齐备。阈值来自业务确认，不将示例延迟当需求。
- HTTP 200 仍需业务检查。
- 范围包含重定向校验，第三方支付等真实依赖需明确授权。

## 3. 保存指标并核对结论依据

- 保存全部原始指标：完成/丢弃迭代、请求率、VU、分位延迟、业务/HTTP 错误、服务端 CPU/内存/连接池/DB。
- 分别映射阈值失败、业务 check 失败、工具错误。
- 负载机饱和、鉴权失败或缓存混入使结论不充分。

## 4. 准备隔离的 MySQL 基准

- sysbench 只用隔离测试 MySQL、专用账户/命名数据库。现场找实际 Lua 与 MySQL 驱动，确认表/行数、线程、时长和容量。
- 密码通过验证过的安全配置引用，若只能明文 argv 则阻断。

## 5. 运行基准并清理资源

- prepare 即记录资源，运行只读/读写基准保存 TPS/QPS/分位/错误及服务资源，cleanup 仅清单内对象并读回。
- 中断可恢复。取消停止子进程和清理，不影响业务表。

## 6. 核对基线可比较性

- 比较基线须同脚本/环境/数据规模/版本/负载模型。报告预热、重复次数、每次原始结果和可比较性。
- sysbench 不代替业务 SQL/API 负载。

## 完成条件

- 完成：隔离环境真实负载可复现，过严 SLO 能失败，目标失联报 error，数据可清理。
- 无法运行时交付脚本/计划并列未验收，不把方案生成当性能通过。
