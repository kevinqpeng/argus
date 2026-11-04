# 安装移动测试依赖

1. 确认已授权的平台。检查 Node.js、npm、Appium 和已安装驱动。按当前[官方安装说明](https://appium.io/docs/en/latest/quickstart/install/)及[驱动要求](https://appium.io/docs/en/latest/ecosystem/drivers/)核对版本兼容性；不要自动升级已有组件。
2. 缺少组件时，列出拟安装的版本、驱动、安装位置及用途。按 [阻塞问答](interaction.md) 询问“安装所列组件”或“暂不安装”。等待真实回答；已有同范围安装授权则直接继续。拒绝时将移动执行标 BLOCKED，保留已有配置。
3. 同意后安装缺少的组件。使用当前用户可写的 npm 安装位置，在仓库外执行；记录实际版本。Appium 命令为 `npm install -g appium@<已核对版本>`。Android 驱动使用 `appium driver install uiautomator2`；iOS 驱动使用 `appium driver install xcuitest`，仅在 macOS 安装。需要固定驱动版本时使用其安装 CLI 支持的版本参数。只安装所选平台的驱动。
4. Node.js/npm、Android SDK/JDK 或完整 Xcode 缺失时，列出额外安装项，取得对应授权后处理。权限不足或版本冲突时报告原因；不自动使用 sudo、不卸载已有工具。Xcode 登录、许可、设备信任和 WDA 签名需用户操作时，给出具体待办。
5. 执行 `appium --version`、`appium driver list --installed`，再执行所选驱动的 `appium driver doctor uiautomator2` 或 `appium driver doctor xcuitest`。保存退出码和脱敏输出。失败标 BLOCKED，不把安装成功当作环境就绪。

安装授权不包含设备重置、卸载 App 或清数据。工具检查通过仅证明依赖可用；真机、App、签名、账号和业务流程仍由 mobile-e2e-test 验证。
