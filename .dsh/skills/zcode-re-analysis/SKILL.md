---
name: zcode-re-analysis
description: >
  对桌面端 AI 编程客户端（ZCode / Claude Code / Cursor / OpenCode / Codex 等 Electron 或
  原生应用）做聚焦逆向分析，提取可借鉴实现（缓存命中率优化、上下文管理、供应商接入、安全缺陷）。
  适用于：想抄作业、做竞品分析、验证某产品宣称能力（如 98.6% 缓存命中率）是否属实。
whenToUse: >
  用户要求"逆向/分析某 AI 编程客户端"、"看看 XX 值得借鉴什么"、"验证 XX 的缓存/上下文方案"、
  "拆解 XX 的实现"时使用。
---

# ZCode 风格桌面 Agent 逆向分析

> 方法学来源：ZCode v3.7.7 逆向实战（2026-08），完整报告见
> `F:\DS-HARNESS\bench\zcode_re_report.md`。本 Skill 把该流程固化为可复用步骤。

## 步骤 0：唤醒逆向工具（强制）

调用 cerebellum MCP 池唤醒（若已唤醒会幂等返回）：

- `cerebellum_mcp_wake("ghidra-mcp")` —— 288 工具，PE 扫描/字符串/导入表/JS bundle 分析/Ghidra 桥
- `cerebellum_mcp_wake("deepcode-decompiler")` —— 5 工具，PE 反编译/字节码反编译/CFG

若 Ghidra 桥未连接：`ghidra-mcp connect_instance`（本机服务在 127.0.0.1:8089）。

## 步骤 1：定位与盘点目标

1. 安装目录：`%LOCALAPPDATA%\Programs\<App>\`（winget 装的就在这）；`Get-ChildItem <dir>` 找主 exe 和 `resources\`。
2. 关键文件：
   - 主程序 `*.exe`（Electron 壳或原生二进制）
   - `resources\app.asar`（Electron 应用 JS 全量包 —— **逻辑所在，最高优先级**）
   - `resources\app.asar.unpacked\`（node 原生模块：config/glm/model-providers/tools 等，直接浏览）
3. 记录：exe 大小、asar 大小、版本号（package.json）。

## 步骤 2：解包 app.asar

- 首选：`npx --yes @electron/asar extract <app.asar> <outdir>`（node ≥ 18 即可）
- 备选：`python -m pip install asar`；或手写最小解包器（pickle 头 + JSON 目录，含每文件 {size, offset}，文件数据顺序拼接在后）
- 解包后浏览结构：`package.json`、主入口（main 字段）、src/dist 布局、node_modules 大件
- **单行压缩 JS 先 beautify 再读**：`npx prettier --parser babel <file> > <file>.pretty.js`；grep 长行会静默失败，用 python 按偏移定位

## 步骤 3：缓存 / 上下文管理分析（核心借鉴点）

在解包 JS 全量搜索（大小写不敏感）：

- 协议与断点：`cache_control`、`cacheControl`、`ephemeral`、`anthropic-version`、`cache_read`、`prompt_cache_hit`、`cached_tokens`
- 上下文构造：`systemPrompt`、`assembleSystem`、`ContextBuilder`、`messages`、`prefix`、`mergeAdjacentUser`、`system-reminder`
- 压缩策略：`compact`、`microcompact`、`summary`、`threshold`、`bufferTokens`、`usage`
- 命中率统计：`cacheHitRate`、`aggregate`、`totalCacheRead`

重点回答：请求如何构造（system 是否拆段、易变内容放哪）、每轮唯一写点在哪个消息、压缩是保结构还是全量摘要、命中率怎么统计。引用具体文件+代码行。

## 步骤 4：模型供应商 / 配置分析

看 `model-providers` 目录或内嵌 models 目录：

- 供应商列表与端点（baseURL、`/anthropic/v1/messages` 或 openai-compatible 路径）
- 模型上下文窗口 / 输出上限 / 默认参数（temperature、thinking、reasoning effort）
- 定价模型（input / output / cache_read / cache_write per M）
- 登录/OAuth 流程、API key 管理

## 步骤 5：安全扫描

- 硬编码密钥：`sk-`、`AKLT`、`ghp_`、PEM 私钥、token（正则全量扫）
- 日志脱敏：`sanitize`、`redact`、`authorization`/`x-api-key` 是否被替换
- 遥测面：RUM、OpenTelemetry（`*_attempt.*` 属性）、会话级聚合上报
- 危险项：请求 body 是否入日志、update 通道指向、webview/沙箱配置

## 步骤 6：交付物

写报告（markdown），结构固定：

1. 技术栈与包结构（壳版本、主进程/Agent 引擎/渲染层、真引擎是 fork 谁的）
2. 缓存命中率优化实现（协议层 → system 构造 → 断点/写点策略 → 压缩策略 → 统计口径，附代码）
3. 模型供应商与 API 配置（表格：provider/baseURL/anthropic 路径/代表模型）
4. 值得借鉴的设计点（≥5 条，每条 2-4 句 + 证据路径）
5. 安全与风险发现（没有就明说没有）
6. 逆向过程的坑（一句话）

## 约束

- 不要运行目标应用本身；不改动安装目录
- 解包产物放 `F:\DS-HARNESS\bench\<app>-asar\`（或工作区 bench 下）
- 时间盒：解包+四线分析控制在 25 分钟内，Ghidra 全量 import 大 exe（>100MB）除非必要否则跳过
