---
name: mobile-e2e-test
description: Runs Argus end-to-end tests on authorized local Android and iOS physical devices with Appium. Use when Argus routes to mobile or both, or the user directly requests Argus mobile testing.
---

# Mobile E2E

## Inputs and required references

Read the [Argus contract](../argus/references/contracts.md), [private storage rules](../setup-argus/references/storage.md), [platform routing](../decision-router/references/platform-routing.md), and [coverage gate](../coverage-check/SKILL.md). Use the frozen cases, authorized device matrix, App artifact, service target, budget, and `workspacePath`. Use local physical devices only; this release does not support simulators or cloud devices.

## 1. Identify the app and test scope

- Identify the authorized app and read the PRD and available business rules.
- Source code and a project path are optional; use device and app evidence when no repository is available.
- Put scripts, dependencies, and outputs in the private run directory.
- Do not change an available app repository.

## 2. Populate and validate private configuration

- Follow the mobile discovery and missing-field guidance in the private storage rules to populate `projectDir/mobile-private.json` before validation.
- Validate it with `python3 <this-skill>/scripts/validate_config.py <projectDir>/mobile-private.json`.
- The file must use the [MobilePrivateConfig schema](../argus/references/schemas.json).
- The execution process reads role/tenant accounts from `accounts` in this file into memory.
- If a case requires login, require the matching account before execution; report only the file path and missing field names, and never print credentials.
- Do not infer shared accounts or targets from the project.
- Read the existing config first and reuse valid device, app, service, and account settings.
- A configured `appId` identifies the app; verify it on the selected device without asking for its name again.
- Ask only about missing, invalid, ambiguous, or conflicting information, with its purpose and local entry location; never ask the user to re-enter verified information.

## 3. Check platform prerequisites

- For Android, use Appium UiAutomator2. Check ADB authorization, the named device serial, OS version, application ID, APK, and installed app version.
- For iOS, use Appium XCUITest and WebDriverAgent. Check Xcode, driver, UDID, OS version, developer mode, trust, signing, provisioning, bundle ID, and installed app version.
- Missing prerequisites block that platform.
- Never pick the first device.

## 4. Isolate device sessions

Use the [shared scheduler](../argus/references/scheduling.md) for both generation and execution. Generate independent files using the existing Appium language/framework without leasing a device. Freeze page-map probes before consumers. Keep generationConcurrency separate from executionConcurrency. Before execution, claim the authorized physical device and driver-specific host ports in the host-wide registry; verify actual capabilities match the lease. Different isolated devices may run concurrently. On timeout/disconnect, retain the lease until the worker and Session are confirmed stopped; never reassign by age alone.

- Run each device's mutable UI session serially.
- Use stable accessibility or resource IDs.
- Switch explicitly between native and WebView contexts.
- A WebView that cannot be inspected blocks its cases; an external browser is not a substitute.
- Before generating scripts, build and reuse the run's page map under the [page exploration and failure diagnostics rules](../argus/references/page-diagnostics.md).
- Run exploration.py plan over all approved cases and required device matrices before collecting UI. Deduplicate by screen, state, role/tenant, device/OS/orientation/language and native/WebView context. Android and iOS have separate tasks and locators. Run check before batch script generation; keep the existing Appium language and runner.
- Probe necessary controls first: accessibility ID, Android resource-id or stable native strategy; on iOS prefer accessibility ID, predicate or class chain. XPath is a fallback. Validate uniqueness and the action's actual usability. Cache locator descriptions, never Appium element IDs; reacquire after transitions or stale references.
- Fetch page source only when targeted probes and existing scoped evidence cannot resolve required controls. Share that source among needs in the same stable screen; parse locally instead of fetching once per element. Full hierarchy may be unavoidable on a driver: record the real getPageSource cost, then retain only redacted relevant evidence. Do not claim subtree retrieval when the driver returned the whole tree.
- Optional exploration screenshots require a planned visual check or ambiguity. Keep mandatory attempt/failure evidence below. Measure getPageSource, findElement/findElements, screenshot, navigation and session commands separately, identifying UiAutomator2 or XCUITest/WDA. Account for command transport time separately from host reasoning.
- Before each command use exploration.py allow; bound client/transport and explicit waits by its timeoutMs, including implicit-wait cost. A hung in-flight command blocks that session until safely stopped; do not send overlapping recovery commands or start a new command merely because a host timer expired. The coordinator alone updates budgets; independent devices may proceed separately.

## 5. Run cases and verify business state

- Run normal, error/permission, and cross-page cases.
- Create isolated data through the business API and record resource IDs.
- Assert real UI behavior, then verify final business state through the API. Use the database only for cases whose acceptance criteria require it.
- Before each case, decide whether its acceptance criteria require MySQL; if so, call an available MySQL tool and follow the [database verification rules](../argus/references/mysql.md). Otherwise, do not connect to the database.
- Wait for asynchronous final states.

## 6. Save attempt evidence

- Save each attempt, first failure, redacted assertions, screenshots, device and Appium logs, and page source.
- Save video when available.
- Do not create Playwright traces.
- Missing evidence is `evidence_missing`; a screenshot alone does not pass a case.
- Keep retries flaky when an earlier attempt failed.
- For specific screenshot checks, prepare clear evidence and batch judgments using the [image protocol](../decision-router/references/protocol.md#图片请求与并行复核); the frozen vision role reviews every result, and business-state assertions remain required.

## 7. Reproduce failures and clean resources

- Reproduce failures and classify product, environment, data, test, flaky, or unknown.
- Apply the [page exploration and failure diagnostics rules](../argus/references/page-diagnostics.md) to evidence selection, repair limits, and run metrics.
- Change product code only with separate authorization.
- Clean only registered resources, read back cleanup, close Appium/WDA sessions, and release the device.
- On resume, inspect leftover resources before acting.

## 8. Check coverage and delivery

- Use shared coverage-check and bug-reporter.
- Read back the selected Bug table and run summary.
- Report each platform and device.
- A blocked side of `both` blocks the combined result.

## Completion criteria

- Pass requires physical-device normal and error/permission flows, business-state checks, failure evidence, exact cleanup, and the execution gate.
- App launch, generated scripts, mocks, or simulator runs do not count as device acceptance.
- Keep credentials and evidence in protected local storage.

## Android session troubleshooting

When session creation fails, preserve the first Appium error and distinguish ADB authorization, hidden API policy, Appium Settings startup, and UiAutomator2 startup. Check the selected device serial, connection, Appium and driver versions, driver doctor, relevant Appium logs, and redacted device logcat around the failure. For Appium Settings startup, inspect its installed/enabled state and the specific launch or crash error. Treat subsequent errors as separate failures; do not claim a cause without evidence.

If the installed UiAutomator2 driver supports `appium:ignoreHiddenApiPolicyError` and the failure is specifically a rejected hidden API policy change, retry once with that capability and record the limitation. It only ignores that policy-change failure; it does not establish that Settings started or that every test feature works. Consult the [UiAutomator2 driver documentation](https://github.com/appium/appium-uiautomator2-driver) for the installed version.

Request a device setting or permission change only when the observed error or verified driver documentation links it to the failing step and it is needed for the selected test. Explain the evidence and exact setting; ordinary ADB connectivity alone does not justify requesting extra USB debugging security settings. If evidence is insufficient, continue non-destructive diagnosis and report the unknown cause. Do not reset the device, clear data, uninstall apps, or grant unrelated permissions as a generic troubleshooting step. If a necessary action cannot be performed or verified, mark affected cases BLOCKED and preserve evidence.

## Private config example

```json
{
  "schemaVersion": 1,
  "targets": [{
    "platform": "android",
    "deviceId": "",
    "appId": "",
    "appPath": ""
  }],
  "service": { "baseUrl": "" },
  "accounts": {
    "default": { "username": "", "password": "" }
  }
}
```

Add one target per authorized device. Use the Android application ID or iOS bundle ID for `appId`. The user fills `accounts` locally; add entries for each required role and optional `tenant`. Use `{}` when no login is needed. Preserve existing `credentialsRef` entries for compatibility; new account configuration uses `accounts`.

两阶段脚本生成时，执行与执行覆盖使用 `cases/execution-cases.json` 中已发布入口；人工评审与原始计划仍绑定不可变的 `cases/cases.json`。先按 [产物生命周期](../argus/references/lifecycle.md) 完成 bind 和 scripts 门禁，不能手工改写审批文件来匹配 runner。
