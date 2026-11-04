---
name: security-test
description: Runs authorized ZAP passive or explicitly approved active scans and API authorization tests. Use when a bounded security test is selected in the test plan.
---

# 安全测试

## 输入与必读规则

读 [共享契约](../argus/references/contracts.md)。

输入：目标主机/路径/排除项、身份、有效窗口、请求上限和授权记录。默认 ZAP baseline 爬取+被动扫描，仍有真实流量。主动扫描必须单独有绑定目标的授权。

## 1. 冻结并落实扫描范围

- 冻结 allowedHosts/路径/排除项，校验重定向最终主机。
- 应用 allowlist 与网络出站限制，限制扫描器实际请求。只在报告里筛掉越界请求不算范围控制。
- 认证失败、范围控制不可落实保持 BLOCKED。

## 2. 核对 ZAP 版本与输出协议

- 固定 ZAP 本地版本或镜像摘要，按该版本 help/官方文档确认扫描和输出参数、退出码。
- 保存 policy、规则 ID、原始 JSON、HTML、stdout/stderr、退出码。未知格式 UNSUPPORTED，不能当零告警。

## 3. 验证认证与权限覆盖

- 结合 API 验证未登录/跨角色/跨租户/对象越权/重放/敏感字段。
- 真实预置问题与请求证据验证认证覆盖，不凭页面可打开推断全站已扫描。

## 4. 复核告警与证据

- 复核告警，严重度与置信度分开，保留可复现请求/响应及业务范围。
- Jev 只做辅助分类，宿主验证原因。未证实不写已确认漏洞或猜 CVSS。

## 5. 执行风险门禁

- 未豁免 critical/high 默认阻断。待复核高风险保持 needs_review 并阻断。
- 忽略项按规则/目标/理由/责任人/到期时间记录，到期失效。
- P0 越权同时满足 P0 门禁。

## 完成条件

- 完成：获授权隔离目标能发现预置问题、拒绝越界重定向、有原始证据。
- 扫描器无法启动、认证失败、覆盖不足不输出安全通过。
- 零告警仅表示本次范围和规则下未发现问题。
