# DEEPCODE 项目概述

## 项目定位
DEEPCODE 是一个增强版 AI 编码助手 CLI，基于 Deep Code CLI 二次开发，集成了大量 MCP 工具、Skills 和自研引擎。

## 技术栈
- **运行时**: Node.js v26+ / Python 3.12+
- **核心语言**: Python (核心引擎、MCP 服务器)
- **辅助语言**: JavaScript/TypeScript (CLI 插件、工具链)
- **AI 模型**: DeepSeek V4 (Flash/Pro/R1) + 可选 Anthropic Claude
- **MCP 协议**: Model Context Protocol (JSON-RPC 2.0 over stdio)

## 核心架构
- **deepcode-engine**: 核心引擎 (Python) — 工具注册、权限管理、Token 预算、事件总线
- **router-mcp**: 智能路由 — 根据任务复杂度自动选择模型
- **ghidra-mcp**: Ghidra 逆向分析桥接
- **skills 系统**: SKILL.md 驱动的可扩展能力包

## 关键目录结构
- `core/` — Deep Code CLI 源码 (clone)
- `.deepcode/` — Deep Code 配置 + Skills
- `.deepcode/skills/` — 项目级 Skills
- `core/mcp_servers/` — 自定义 MCP 服务器
- `tools/` — 分析工具集
- `scripts/` — Python 分析脚本

## MCP 生态
- tushareMcp — 股票数据
- playwright — 浏览器控制
- filesystem — 文件读写
- sqlite / duckdb — 数据库
- github — GitHub API
- ghidra-mcp — 逆向分析
- router-mcp — 智能路由
- deepcode-engine — 核心引擎
- winapp — Windows UI 自动化
- fetch — 网页抓取

## 编码规范
- Python: snake_case, type hints, f-strings
- TypeScript: camelCase, interfaces over types
- MCP 工具命名: `mcp__<服务名>__<工具名>`
- 配置文件: JSON with comments (settings.json)

## mattpocock skills 执行规则 (2026-08-16 定)
- 来源: github.com/mattpocock/skills (MIT)，25 个已安装到 ./.deepcode/skills/ (项目级, 与原有 skills 同目录)。仅供 DEEPCODE 使用 (DSH 已撤, 不用)。
- 这些 skill (tdd / code-review / implement / to-spec / to-tickets / triage / research / prototype / diagnosing-bugs / domain-modeling / grilling / handoff / teach / wizard / setup-pre-commit 等) 面向前沿大模型编写。
- 铁律: 执行这些 skill 时必须挂云端模型 (DeepSeek V4 Flash/Pro 或 Anthropic Claude)，禁止用本地 Ollama (qwen2.5:3b 等小模型)。
- 原因: 本地 3B 模型推理能力不足，照执行会产出低质量结果；宁可跳过 skill 也不降级到本地小模型。

## 改进落地铁律 (2026-08-17 定)
- 给 DEEPCODE 的任何改进，**必须先落地到本地生效**（构建 + 安装到全局 dist/cli.js + 功能验证通过），之后才谈提 PR。
- PR 只是锦上添花（回馈上游社区 lessweb/deepcode-cli、HKUDS/DeepCode），初始目的永远是改进 DEEPCODE 本身。
- 顺序不可颠倒: **落地在前，PR 在后**。提 PR 前自检清单:
  1. 本地是否已 build (esbuild 产物 dist/cli.js)？
  2. 是否已复制到全局安装 C:\Users\raymo\AppData\Roaming\npm\node_modules\@vegamo\deepcode-cli\dist\cli.js？
  3. `deepcode --version` 版本号是否符合预期？
  4. 特征标记是否在 bundle 里 (grep dist/cli.js)？
  5. 是否实机跑过 `-x -p` 功能验证？
- 若改进与 PR 分支冲突（如 dirty 工作树），以本地 merged 分支为落地源，PR 分支只做提交载体。

## 工程纪律（E1，源自 HKUDS/nanobot .agent/design.md，2026-08-29 落地）
> 来源: F:\DS-HARNESS\hkuds-study\nanobot\.agent\design.md（47.5k★ 极简 agent 框架的架构守则），对标文档 05-agent-ecosystem-scan.md。

1. **Core stays small; extend at the edges** — 新能力优先放工具/skill/MCP server/渠道适配器；`core/agent_runtime/runner.py` 等关键路径改动必须最小且有理由，不得内联到 agent 主循环。
2. **Less structure, more intelligence** — 优先简单可读代码，不为新框架层/间接层加结构；最好的修复往往是更小的 prompt、更紧的工具契约、一条聚焦的回归测试。
3. **Prefer duplication over premature abstraction** — 渠道/提供者间允许重复相似逻辑，不引入复杂基类/共享助手仅为消除重复；每个文件保持自包含可读。
4. **Minimal change that solves the real problem** — 修 bug 只改必要部分，不捆绑无关重构；确需重构则独立 PR。
5. **Keep PRs reviewable** — 修复应讲清被保护的不变量、改最小表面、只加最贴近的回归测试；diff 开始跨界或混入清理就拆分。
6. **Type dynamic boundaries at the edge** — 网络载荷/持久化记录/第三方 SDK 对象是不信任的动态边界，在所属边缘做解析/规整，用 TypedDict 定型；不在核心扩散裸 dict；`typing.cast` 必须有运行时检查支撑。
7. **Explicit over magical** — 配置显式声明（Pydantic schema），错误处理抛清晰异常而非静默纠正；自动检测路径必须从工厂可追溯到具体实现类。
