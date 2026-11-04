<div align="center">

# Argus

**让每一次验收，都有可追溯的依据。**

面向 AI Agent 的测试技能集，将需求、用例、执行结果和缺陷证据串联起来。

[快速开始](#快速开始) · [工作流程](#工作流程) · [技能一览](#技能一览) · [运行要求](#运行要求)

</div>

---

## Argus 能做什么

Argus 通过一组 [Agent Skills](https://agentskills.io/) 编排软件测试。提供完整 PRD、测试范围和授权后，Agent 根据冻结的需求与场景设计用例，执行测试，并交付包含覆盖率、结果和证据的报告。

- **PRD 验收**：提取验收条件，冻结测试场景，生成可供人工评审的用例。
- **端到端测试**：按范围执行 Web、H5、API 和本地移动端真机测试。
- **缺陷回归**：关联原 Bug、用例和证据，验证修复后的实际行为。
- **覆盖门禁**：分别检查计划与执行覆盖，保留 P0 和未覆盖场景的明细。
- **测试交付**：记录失败、证据和缺陷，输出结构化结果与测试报告。
- **专项测试**：按用户选择执行性能或安全测试。

## 快速开始

### 1. 安装技能

使用 [skills CLI](https://skills.sh/) 安装：

```bash
npx skills add kevinqpeng/argus
```

选择全部 10 个技能，再选择需要使用的 Agent。Argus 的技能通过相对路径引用彼此；请将它们安装在同一个技能目录中。

也可以查看可安装的技能：

```bash
npx skills add kevinqpeng/argus --list
```

### 2. 配置运行环境

调用 `setup-argus`，按照提示配置 Agent 模型、测试目标和结果交付方式。执行 Web 或移动端测试时，还需要配置对应工具和目标环境。

Jev 使用宿主安全环境中的 `TYPESAFE_API_KEY`。凭据、测试账号和运行数据应保存在私有工作区，不要提交到代码仓库。

### 3. 开始测试

在支持技能的 Agent 中调用 `argus`，并提供 PRD、测试范围和目标环境。例如：

```text
使用 argus 验收这份 PRD。测试范围包括 Web 和 API，
目标环境与测试账号使用我已配置的私有工作区。
```

验证已报告的缺陷修复时，提供原 Bug 记录或原测试运行结果：

```text
使用 argus 回归验证这些已修复的 Bug，关联原用例和证据，
并报告每个问题是否已验证修复。
```

各宿主的调用方式和配置位置见 [宿主适配说明](skills/setup-argus/references/hosts.md)。

## 工作流程

| 阶段 | 工作内容 |
| --- | --- |
| 1 | 接收 PRD 与测试授权 |
| 2 | 配置运行环境并冻结输入 |
| 3 | 生成场景和测试用例 |
| 4 | 检查计划覆盖门禁 |
| 5 | 执行测试并复现失败 |
| 6 | 记录缺陷与证据 |
| 7 | 检查执行覆盖门禁 |
| 8 | 生成报告并交付 |

Argus 为每次运行记录独立的 runId、配置和输入指纹。输入或配置发生变化时，流程会创建新的运行记录并保留历史。

计划阶段要求有效场景覆盖率至少为 **95%**，P0 场景达到 **100%**。执行阶段单独报告计划、执行和通过情况；工具失败、阻塞或证据不足不会被算作通过。具体规则见 [coverage-check](skills/coverage-check/SKILL.md)。

## 技能一览

`argus` 是主入口，按测试阶段读取其他技能。也可以根据任务直接调用相应技能。

| 技能 | 用途 |
| --- | --- |
| [argus](skills/argus/SKILL.md) | 编排 PRD 验收、缺陷回归和结果交付 |
| [setup-argus](skills/setup-argus/SKILL.md) | 配置模型、目标、工作区和缺陷交付方式 |
| [prd-to-cases](skills/prd-to-cases/SKILL.md) | 冻结验收场景，生成测试用例和矩阵 |
| [coverage-check](skills/coverage-check/SKILL.md) | 检查计划与执行覆盖率及 P0 门禁 |
| [decision-router](skills/decision-router/SKILL.md) | 使用 Jev 处理分类和语义判断 |
| [web-e2e-test](skills/web-e2e-test/SKILL.md) | 执行 Web、H5 和 API 测试 |
| [mobile-e2e-test](skills/mobile-e2e-test/SKILL.md) | 使用 Appium 执行 Android 和 iOS 本地真机测试 |
| [bug-reporter](skills/bug-reporter/SKILL.md) | 查重、记录缺陷并交付测试结果 |
| [performance-test](skills/performance-test/SKILL.md) | 执行选定的 API 压测和数据库基准测试 |
| [security-test](skills/security-test/SKILL.md) | 执行选定的安全测试 |

## 运行要求

- **Agent 宿主**：需要支持读取 Agent Skills；模型、子 Agent 和工具能力以实际宿主为准。
- **测试输入**：提供完整 PRD、明确的测试范围、目标环境和必要授权。
- **测试工具**：Web 测试依赖 Playwright；移动端真机测试依赖 Appium 和对应驱动。专项测试依赖所选工具。
- **语义判断**：需要 Jev 时，在安全环境中配置 `TYPESAFE_API_KEY`。
- **私有数据**：配置、凭据、测试数据和证据保存在仓库外的私有工作区。

若宿主能力、工具、授权或证据不足，Argus 会报告阻塞或部分完成。测试结论以实际执行记录为准。

## 项目结构

```text
skills/
  argus/              主流程与共享契约
  setup-argus/        环境与模型配置
  prd-to-cases/       场景与用例设计
  coverage-check/     覆盖门禁
  decision-router/    语义判断与路由
  web-e2e-test/       Web 和 API 测试
  mobile-e2e-test/    移动端真机测试
  bug-reporter/       缺陷记录与交付
  performance-test/   性能测试
  security-test/      安全测试
```

每个技能的入口为 `SKILL.md`；相关脚本和按需读取的规则位于各技能目录内。项目组织形式参考 [pstack](https://github.com/backnotprop/pstack)。
