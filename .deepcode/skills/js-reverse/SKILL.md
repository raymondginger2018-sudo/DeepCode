---
name: js-reverse
description: >
  前端 JavaScript 逆向方法论（适配 DeepCode 工具链）。当需要定位接口签名、加密参数、风控字段，
  观察页面请求链路、在运行时抓取函数入参与返回值、追踪 XHR/Fetch/WebSocket 触发点，或把页面证据
  带回 Node 做本地复现与补环境时使用。执行面以 DeepCode 的 playwright MCP 为主。
  源自 zhaoxuya520/reverse-skill 的 js-reverse skill，适配 DeepCode。
version: 1.0.0
author: DeepCode (adapted from zhaoxuya520/reverse-skill)
source: https://github.com/zhaoxuya520/reverse-skill
date: 2026-08-01
tags: [reverse, js, frontend, encryption, hook, cdp, deobfuscation]
---

# 前端 JS 逆向作业规范

## 适用范围

- 定位接口签名、加密参数、风控字段
- 观察页面请求链路与脚本来源
- 在运行时抓取函数入参与返回值
- 追踪某个 XHR / Fetch / WebSocket 的触发点
- 把页面证据带回 Node 做本地复现与补环境

> 目标是二进制/APK/PE/DLL → 走 ida-reverse；目标是 JS 虚拟机 → 走 dsl-vm-reverse。

## 核心原则

```
Observe-first   （先页面观察，不猜环境）
Hook-preferred  （优先运行时采样，其次断点）
Breakpoint-last （断点是最后手段）
Rebuild-oriented（以本地复现为目标）
Evidence-first  （每步留下证据）
```

## 工具链适配（DeepCode）

| 能力 | 原 js-reverse-mcp 工具 | DeepCode 可用替代（playwright MCP） |
|------|----------------------|-----------------------------------|
| 打开页面 | `js-reverse_navigate_page` | `mcp__playwright__browser_navigate` |
| 列网络请求 | `js-reverse_list_network_requests` | `mcp__playwright__browser_network_requests` |
| 请求详情/发起者 | `js-reverse_get_request_initiator` | `mcp__playwright__browser_network_request`（headers/body） |
| 运行时执行 | `js-reverse_evaluate_script` | `mcp__playwright__browser_evaluate` |
| 脚本列表/源码 | `js-reverse_list_scripts` | `browser_snapshot` / `browser_network_requests` 定位脚本 URL |
| 断点 | `js-reverse_set_breakpoint_on_text` | `browser_evaluate` 注入 hook（无原生断点时） |
| WebSocket | `js-reverse_get_websocket_messages` | `browser_network_requests`（ws:// 请求） |
| 截图 | `js-reverse_take_screenshot` | `mcp__playwright__browser_take_screenshot` |
| 页面/帧切换 | `js-reverse_select_page/frame` | `mcp__playwright__browser_tabs` / `browser_snapshot` |

## 五阶段工作流

### 1. Observe（观察）
目标：先确认目标请求、相关脚本、候选函数，**不猜环境**。
- `browser_navigate` 打开目标页面
- `browser_network_requests` 找到目标请求
- `browser_network_request` 看请求头/体，回溯来源
- `browser_snapshot` 观察页面结构，定位可疑脚本

必须产出：目标请求 URL 或特征、发起线索、可疑脚本、初始任务记录。

### 2. Capture（采样）
目标：最小侵入采样，拿到参数样例、调用顺序、运行时证据。
- 优先 `browser_evaluate` 注入轻量 hook（如覆写 `fetch`/`XMLHttpRequest` 记录入参）
- 触发目标操作（`browser_click` / `browser_type` / `browser_press_key`）
- 从 `browser_console_messages` 收集 hook 输出
- 抓函数入参与返回值：在页面里 `window.__captured = []`，hook 目标函数 push 参数

### 3. Rebuild（本地复现）
目标：把页面证据整理成本地可迭代的 Node 复现材料。
- 导出目标函数源码（从 network 请求定位脚本，`browser_evaluate` 读取或直接拉取）
- 本地补环境**必须以页面观测证据为依据**，不允许空想式补 `window/document/navigator/crypto/storage`
- 每次只记录一个最小因果补丁决策

### 4. Patch（补环境）
目标：按报错和 first divergence 驱动补环境，直到本地脚本稳定跑出目标参数。
- 先看缺什么，再补什么；一次只做一个最小补丁决策
- 每次补丁后立即复测
- 每次补丁都写入任务记录
- 常见补丁面：`navigator`（webdriver/plugins/languages）、`window` 属性、`document`、`crypto.getRandomValues`、`localStorage/sessionStorage`、定时器

### 5. DeepDive（深度还原）
目标：本地跑通后做去混淆、控制流还原、业务逻辑提纯。
- 如果当前任务只是出签名，此阶段可降级
- 如果要长期复用算法链路，此阶段必须做
- 去混淆要点：AST 遍历（esprima/acorn parse → 常量折叠 → 重命名 → unparse）、字符串解密函数定位

## 执行要求

- 所有重要步骤写入本地 task artifact
- 无法解释为什么调用某个工具时，不要调用
- 优先用 MCP 现成能力直接取证，不要先写脚本重造能力
- 失败时回退：换采样点 → 换触发方式 → 换环境补丁，记录每次 divergence

## 任务完成自检（声称完成前 MUST 通过）

- [ ] 我是否执行了五阶段工作流（而不是只阅读）？
- [ ] 是否基于页面观测证据补环境（无空想补丁）？
- [ ] 是否产出可复现证据（脚本/请求样例/截图/报告）？
- [ ] 本地复现是否稳定跑出目标参数？
