# Argus

Argus 是一组 Agent Skills，用于从 PRD 生成测试场景、执行验收与回归测试，并交付可追溯的结果和缺陷。

## 安装

```bash
npx skills add kevinqpeng/argus
```

选择全部 Argus 技能及需要使用的 Agent。技能之间通过相对路径共享规则，安装时请保留完整的 `skills/` 目录结构；单独安装一个技能可能无法找到其依赖。

## 使用

使用 `setup-argus` 配置运行环境，再使用 `argus` 开始 PRD 验收或缺陷修复回归。提供 PRD、目标环境和测试授权；缺失配置会在运行中提示。

| 技能 | 用途 |
| --- | --- |
| [argus](skills/argus/SKILL.md) | 编排验收和缺陷回归 |
| [setup-argus](skills/setup-argus/SKILL.md) | 配置模型、目标和运行工作区 |
| [prd-to-cases](skills/prd-to-cases/SKILL.md) | 冻结场景并生成用例 |
| [coverage-check](skills/coverage-check/SKILL.md) | 检查计划和执行覆盖率 |
| [decision-router](skills/decision-router/SKILL.md) | 使用 Jev 路由语义判断 |
| [web-e2e-test](skills/web-e2e-test/SKILL.md) | 执行 Web 和 API 测试 |
| [mobile-e2e-test](skills/mobile-e2e-test/SKILL.md) | 执行移动端真机测试 |
| [bug-reporter](skills/bug-reporter/SKILL.md) | 记录缺陷并交付结果 |
| [performance-test](skills/performance-test/SKILL.md) | 执行已授权的性能测试 |
| [security-test](skills/security-test/SKILL.md) | 执行已授权的安全测试 |

运行配置、凭据、测试数据和证据应保存在仓库外的私有工作区。各能力依赖实际宿主、工具和授权；详见对应技能。

项目组织形式参考 [pstack](https://github.com/backnotprop/pstack)。
