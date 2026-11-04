# ego 辅助引导

本集成仅支持 macOS，按用户指定范围限制，不以 ego 上游其他平台的支持情况扩大范围。ego-browser skill 依赖 Ego Lite 浏览器，安装 skill 文件不等于安装浏览器。

## 选择与平台门禁

1. 用宿主系统元信息或 Python `platform.system()` 检测当前执行设备；仅 `Darwin` 是 macOS。系统未知先补证据，不猜测。Web/both 才进入本分支。
2. 非 macOS 不提示 ego 选择、不检测或安装 Ego Lite，辅助能力为 UNSUPPORTED；未选择则 SKIPPED。保留已有偏好但报告本机不可执行，继续 Playwright。API-only/移动端直接 SKIPPED。
3. macOS 普通 setup 中，没有已明确的有效选择时，按 [阻塞问答](interaction.md) 提问：“是否使用 ego 辅助探索页面和排查失败？”选项：“仅 Playwright（推荐）”“Playwright + ego 辅助”。等待真实回答；已有选择不重复问。只读 doctor 不提问。
4. 选择仅 Playwright 保存 execution.webAssistant=none，跳过 Ego Lite 检查。选择辅助保存 ego，但不将该回答视为安装或升级授权。保存范围、最终生效来源与冻结遵循 [配置规范](config.md)。

## 检测、安装与验证

仅选择 ego 且为 macOS 时执行，逐项记录证据；不读取浏览器个人数据或凭据。

1. 查找当前可用 ego-browser skill，读取其 SKILL.md。缺失时报告 skill 依赖 BLOCKED，不把浏览器安装冒充 skill 安装。
2. 检查 `/Applications/ego lite.app`、`~/Applications/ego lite.app` 与 `command -v ego-browser`；CLI 缺失时检查 `~/.local/bin/ego-browser`，只为当前命令补 PATH。自定义应用路径按本机安装登记核实，证据不足先定位，避免重复安装。只存在 CLI 不能证明运行可用。
3. 确认应用未安装时，下一轮单独询问：“Ego Lite 未安装，是否现在安装？”选项：“安装 Ego Lite”“暂不安装”。等待实际回答；空回答保持等待，超时不算授权。选择安装后读取该 ego-browser skill 的 references/install.md 和 references/install-macos.md，使用其实际路径下 `scripts/install.sh` 安装；不复制下载地址、不自建安装器。
4. 选择暂不安装则将辅助能力标 BLOCKED（依赖缺失），继续可独立完成的 Playwright 流程，明确 ego 尚未启用；不更改用户的已存辅助偏好。用户取消整个 setup 时停止。
5. 应用存在但 CLI 缺失时，按安装指南启动应用并引导完成 onboarding，不直接重装。安装后同样等待用户确认已完成首次引导；不自动导入个人浏览器数据。引导待完成标 BLOCKED。
6. 用户确认 onboarding 后，执行 `ego-browser nodejs -e 'console.log("ego-browser ready")'`，再执行 `ego-browser skill`，检查退出码和完整指南结束标记。两项成功才标 READY；记录应用路径、实际指南版本与验证结果。旧版出现 Unknown command: skill 时，按上游指南单独询问升级授权，不能默认为同意。
7. 安装失败记录脱敏错误与待办，不循环重装、不宣称 READY；已安装后重跑先检查现状。只读 doctor 仅检查已有安装和 CLI/指南，不安装、升级、启动应用或运行 nodejs；未验证运行能力明确 found-not-verified。

此处安装问答是以后 setup 的执行规则；修改此技能本身不触发安装。未选 ego 不影响 Playwright，选择 ego 但不可用时报告辅助缺口，不把该缺口报告成产品用例失败。恢复时复核系统与依赖，不改已有 run 快照；切换偏好使用新 run。
