# Bug 表格创建、写入与权限回退

内部配置保留 provider/method 两个字段，但常规 setup 界面直接提供“飞书表格 / 企业微信表格”，不让用户先理解或填写技术参数。表格动作和交付范围另用选择控件收集；已有高级通道和权限回退仍保留，不限制平台只能通过 CLI 使用。

setup 的常规在线入口优先以真实选择控件提供“飞书表格 / 企业微信表格”，分别生成 provider=feishu/wecom、method=cli；不要只用文字让用户说平台。新建/复用/导入、名称/链接、汇总与附件也走 [设置问答](../../setup-argus/references/interaction.md)。此入口不删除下面的高级通道：已有明确选择、用户主动要求或权限回退时仍可用其他已验证方式。

## 选择与配置

- delivery.mode：online（在线）、local（本地正式表格）、both（两者）。已有选择沿用；首次推荐已验证可用的在线通道，没有在线能力时推荐本地，不要求先安装某个平台 CLI。
- bugPublisher.tableAction：create（新建）、existing（现有）、import（生成文件供导入）。收集空间、功能名称或现有表链接，不让用户手写字段 ID。
- bugPublisher.provider/method 分开选择。provider 可为 wecom、feishu 或其他平台标识；method 为 auto/mcp/api/cli/browser/webhook。本地模式可设 provider=local、method=local。
- delivery.onFailure：ask（默认）、local（已接受无权限时本地交付并保留待同步）、stop（停止发布保留证据）。不因权限错误换身份、换目标或扩大授权。

设置只存本机私有配置。一次性回退在 manifest/publication 记录 requestedDelivery、acceptedDelivery、reason 与用户选择，不改冻结配置。both 要两边验证；只完成本地必须单列在线未完成，除非用户改为本地正式交付。

setup 阶段只按 [产物边界](../../setup-argus/SKILL.md#setup-产物边界) 探测，不执行本节的建表、导入或发布；正式表格在测试后的发布阶段创建。

## 通道与证据

| 方式 | 能力与完成条件 |
| --- | --- |
| 连接器/MCP | 复用宿主已连接的新建表、字段、记录、附件能力；按实际工具 Schema 和身份验证，创建/写入后回读。有连接器不等于支持全部动作 |
| 官方 API/SDK | 使用获授权接口，核实当前协议、密钥引用、字段、结果和回读；本包不提供猜测 URL 的通用包装器 |
| 已安装 CLI | 核对来源、版本、help、身份和结构化输出；缺 CLI 不阻止其他方式 |
| 登录态浏览器 | 当前用户有网页权限时创建/编辑/导入，保存后重新读取单元格/记录和链接；本人也无权限则停止，不绕过授权 |
| 表格 Webhook/自动化 | 必须实际绑定“写表记录”动作，有字段映射、目标和可验证落表回执；接受请求或机器人消息成功不算表格写入 |
| 本地 CSV | 配套脚本直接维护累计 Bug 表和运行汇总；BOM 中文、去重、并发锁、原子替换和读回，见下文 |
| 本地 Excel | 宿主有表格工具时创建/维护 .xlsx，按对应 skill 验证内容与布局；不可用则如实提供 CSV，不只改扩展名 |

auto 只在已验证能力中选路由，不表示五种宿主均预装全部适配器。优先复用足够完成任务的连接器；其他方式分别标可用、待配置或不支持。

企微场景若有 wecomcli-shared、wecomcli-smartsheet 等技能，按其实际指引检查身份和写入回退。应用 API/CLI 权限不足不等于网页用户必然无权限；替代通道仍需真实目标权限。

## 新建、复用、导入

Bug 明细列头及顺序固定为：序号、模块、标题描述、操作步骤、证据（截图）、期望、实际结果、优先级。序号按表内新增顺序显示 01、02，重复缺陷保留原序号；内部 Bug ID 和指纹继续用于追溯。标题简洁描述问题；操作步骤分行，包含复现所需前置条件。证据列仅放可打开的截图附件或图片链接，XML、日志等保留在内部证据中。优先级使用单选下拉 P0、P1、P2、P3，复用现有表时保留其已确认的选项和人工选择。列宽适度固定，长内容自动换行，行高随内容调整，避免横向拉得过宽；保存后检查实际展示。新建表格或工作簿名称使用 `YYYYMMDD-功能名称`，功能名称取本次测试范围的简短名称。指纹、运行/用例关联、环境和状态等保存在内部记录或测试汇总，不增添明细可见列。

测试汇总面向用户的列头及顺序固定为：测试功能概述、用例数、覆盖率、通过率、测试bug数、未执行或失败的用例、执行人、执行时间。每个 run 一行；功能概述写本次测试的功能、范围及 full/partial，不把工具初始化记录当业务测试结果。用例数为本次范围内计划执行的唯一用例数；一个用例在所有必需矩阵上均有最终 passed/failed 结果才算已执行，在所有矩阵上均 passed 才算通过；重试不重复计数，flaky、error、blocked、skipped、cancelled 均列入未执行或失败的用例并注明状态。覆盖率显示“已执行用例数 / 用例数 (百分比)”，通过率显示“通过用例数 / 已执行用例数 (百分比)”，如 `9 / 10 (90%)`；分母为 0 时显示 `0 / 0 (N/A)`，不得写 100%。测试bug数为本次运行确认的唯一产品缺陷数，按指纹去重，不累计其他 run；没有则写 0。用例数和测试bug数使用整数列，两项比率使用文本列，未执行或失败的用例使用多行文本列；该列列出所有未通过用例 ID 及状态，没有则写“无”。执行人使用平台单人选择器，取本次实际执行身份；执行时间使用日期时间列，取本次运行完成时间。runId、门禁和完整 JSON 事实留在内部回执，不增加可见列。

create：确认平台、授权空间和功能名称，创建 Bug 明细/测试汇总，按上述列头、顺序及字段类型配置两表；保存真实链接，首条写入后读回列头、顺序、字段类型、选项、比率数值与内容。

existing：读结构并匹配列/枚举/附件，保留人工状态与现有数据；按授权调整展示列及顺序，缺列或类型不符时先核对现有数据再调整或询问，不清空表或暗改类型。

import：先生成 CSV/Excel。可用浏览器且有权限时可完成导入并核对；否则交付文件并标 IMPORT_PENDING。文件生成不代表已导入；有远端记录/链接并读回才算在线完成。

## 无权限或能力不可用

区分未登录/认证失效、通道应用权限不足、目标无写权限、工具不支持、临时故障。权限错误不盲重试，不向未知路径散发业务数据。

默认用 AskUserQuestion/request_user_input 提供“本地正式交付”“换可用在线方式”“停止发布”。已选 onFailure=local 时直接保存本地，不重复问。切换后保留失败原因、来源目标和待同步数据。

需要用户选择时按 [阻塞问答规范](../../setup-argus/references/interaction.md) 立即暂停整个发布/测试流程，等回答后再切换通道；不能异步询问后自行生成本地表或假定用户同意。同步工具不可用时按阻塞问答规范检查异步工具；所有适用结构化工具均不可用时，在最终消息保留选项并结束本轮。

本地完成时在线仍为 NOT_CONFIRMED/pending，不写在线成功。用户继续要求 online/both 时任务部分完成；用户接受本地正式交付时本地可完成，补同步单列。后续只恢复发布、按指纹查询，不重跑业务测试或重复建行。

## 本地 CSV 脚本

先看 `python3 <本Skill>/scripts/local_table.py --help`，由 Agent 根据本 run 事实生成私有输入 JSON：

```sh
python3 <本Skill>/scripts/local_table.py --run-config <runConfigPath> --input <私有输入.json>
```

输入 schemaVersion=1、runId、bugs（共享 Bug 契约数组）、summary。summary 保留 status、scope、coverage、p0、counts、cleanup、limitations，并提供 featureOverview、caseCount、executedCount、passedCount、incompleteCases（每项含 caseId、status）、executor、executedAt（带时区的 ISO 时间）。三个计数来自冻结用例与最终运行结果，满足 `0 ≤ passedCount ≤ executedCount ≤ caseCount`；incompleteCases 恰好列出其余用例。零缺陷传空 bugs，仍有汇总。

输出本机项目目录下 tables/bugs.csv、tables/runs.csv，可用 Excel/Numbers 打开；bugs.csv 只含上述 8 列，runs.csv 只含上述 8 列。去重指纹、runId 和完整汇总事实分别放在同目录私有 bugs.meta.json、runs.meta.json。CSV 不保存下拉或布局；按表格工具生成并验证带优先级单选下拉、固定列宽和自动换行的 Excel 工作簿，CSV 仅作数据源，不能单独作为正式交付。单次输入重复指纹拒绝；跨 run 同指纹更新原行，保留创建时间、原编号和人工优先级等内部信息，累积关联运行。使用 UTF-8 BOM 和标准 CSV 转义，用户内容防公式注入。旧版或被改动的表头拒绝覆盖，保留并报告，迁移现有表须另行核对数据。

并发发布使用稳定锁，每个文件原子替换后读回；两个表之间中断不承诺事务原子性，可幂等重跑恢复。输出哈希和行数，并在本 run 写 local-publish.json，与远端回执分开。退出 0 表示 CSV 读回完成，2 表示阻断；Excel 生成并验证后才标记本地交付完成。

脚本允许 local/both 或 onFailure=local。用户一次性同意回退后可加 --fallback-approved，在 manifest 记录选择；不能擅自传此参数。脚本回执 CSV_READY 只证明数据源已读回，Excel 验证后才记录 LOCAL_COMPLETED，doctor 的 LOCAL_NOT_VERIFIED 不代表文件已生成。

本地路径不是共享 URL。跨人共享需另选导入/上传/在线通道并验权。选 Excel 时按可用工具维护正式工作簿，不能把用户编辑的 .xlsx 静默覆盖成 CSV。
