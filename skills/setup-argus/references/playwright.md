# Playwright 安装与验证

仅在本次范围包含 Web/H5 自动化时执行。使用 Argus 仓库外的 `workspacePath/harness`，不改被测仓库的依赖、配置、锁文件或浏览器数据。

1. 探测 `node --version`、`npm --version`、harness 中的 `@playwright/test` 版本，以及所选浏览器是否已安装。版本要求按所选 Playwright 版本的[官方安装说明](https://playwright.dev/docs/intro)核对；不要从项目配置继承执行配置。
2. 浏览器按本次授权矩阵选择；未指定时使用 Chromium。只安装缺少的 Playwright 包和所选浏览器，复用兼容的现有版本，不自动升级已有组件。没有可复用版本时，从官方文档确认兼容的稳定版本并记录实际版本。
3. 有缺项时列出版本、安装位置、所选浏览器和用途，按 [阻塞问答](interaction.md) 取得安装授权；已有同范围授权时继续。拒绝时将 Web 执行标记为 `BLOCKED`，并保留已存在的环境。
4. 授权后只在 `workspacePath/harness` 安装 `@playwright/test`，并通过 `npx playwright install <browser>` 安装缺失的所选浏览器。不要使用全局 npm 安装。操作系统依赖缺失时列明具体依赖；需要管理员权限的安装交由用户处理，不运行 `sudo`。
5. 用 `npx playwright --version` 和 `npx playwright install --list` 核对实际版本与浏览器，再在 harness 中启动并关闭所选浏览器做 smoke check。记录退出码和脱敏输出；任何一步失败都标记 `BLOCKED`，不能仅凭包安装成功标记 READY。

该检查只证明 Playwright 运行环境可用，不证明目标站点、账号或业务用例可执行。浏览器包名与安装参数以当前 Playwright CLI 的 `install --help` 为准。
