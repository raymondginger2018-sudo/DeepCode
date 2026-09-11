---
name: deepcode-repair
description: >
  DeepCode 基础设施修复总纲 —— bash/SDK/MCP server/CLI 核心模块/引擎等
  DEEPCODE 自身基础设施的修复方法论 + 强制沉淀更新机制。
  Use when 修复 DeepCode 自身（bash 工具、deepcode-agent-sdk、
  deepcode-agent、MCP server、CLI 模块、路由器、引擎）报错/崩溃/卡死/死锁，
  or when user says "修一下"/"又挂了"/"报错"/"卡死"/"崩溃"/"死锁"/"假死"/"修复",
  or when a repair needs to be logged and summarized into this skill.
version: 1.1.0
author: raymondginger
date: 2026-09-10
tags: [deepcode, repair, fix, infrastructure, mcp, bash, sdk, cli, engine]
---

# DeepCode 基础设施修复总纲

DeepCode 自身基础设施（bash 工具、deepcode-agent-sdk、deepcode-agent、
MCP server、CLI 核心模块、router/engine）的修复总纲层技能。

**职责边界**：
- 本 skill = 总纲：修复方法论 + 强制沉淀机制 + 案例台账入口。
- 专项 skill 各自负责（修复时交叉引用，不重复写方法论）：
  - `deepcode-tool-discipline` — 工具调用规范/失败诊断（字段白名单、强类型、command 纯字符串）
  - `dsh-harness-repair` — DSH（DeepSeek Harness）专项修复 Playbook
  - `deepcode-repair-strategy` — Docker/WSL 类环境修复策略
  - `deepcode-agent` / `deepcode-agent-sdk` / `deepcode-cerebellum` — 对应模块的源码级知识

---

## 铁律（违反过就会付出代价）

1. **修复前先探活、先复现**：复现失败现场（错误日志/栈/退出码），
   再动手改。没有复现就没有修复。
2. **一修一验**：每修一层验证一层，不要叠加修改后一起测——否则不知道哪层有问题。
3. **最小改动**：修 bug 只改必要部分，不捆绑无关重构（工程纪律 E1）。
4. **改了源码必须验证可加载**：Python 模块 `python -m py_compile`，
   TS/JS 构建 `npm run build` / esbuild 后重新部署到实际生效路径。
5. **修复生效要确认加载链**：改 MCP server 源码后，旧进程仍在跑旧代码——
   必须重启 MCP server（`/mcp` 重连）或重启 CLI 会话，否则白修。
6. **写注释解释"为什么"**：给非常规修复（polyfill/兼容层/防御性检查）加注释，
   说明触发场景和消费者，避免未来被"清理"掉。
7. **每次修复必须沉淀**（详见下方"流程"）：追加案例 + 提炼教训 + bump 版本，
   三者缺一即视为未完成。

---

## 流程（强制，不可跳过）

### 第 0 步：先读本 skill（每次修复开始时必须）

任何 DeepCode 基础设施修复任务开始前：
1. 先看本文件「已知修复模式」和 `reference.md` 案例表——
   可能已有同类问题的修复先例，避免重复踩坑。
2. 确认涉及模块 → 交叉引用对应专项 skill。
3. **记录开始时间与症状**（供收尾沉淀对比）。

### 修复步骤（每层验证后再进下一层）

1. **诊断**：读错误日志/堆栈/退出码，确认根因，不要凭症状猜。
2. **定位**：找到出错的文件与代码行（`rg -n` 定位关键调用）。
3. **修复**：最小改动解决问题（加防御、改参数、适配新 API、兼容层等）。
4. **验证**：
   - 语法：`python -m py_compile <file>` / 构建命令
   - 行为：真实触发场景测试（不崩溃、输出正确、状态流转正确）
   - 加载链：确认生效路径（MCP server 重启 / CLI 重启 / 构建产物替换）
5. **收尾沉淀（强制）**：见下方。

### 收尾沉淀（每次修复后同一天内必须完成）

> ⚠️ 2026-09-01 教训：规则藏在文档里不会自动触发（hkuds-pr skill
> 断更一周，用户质问"为什么没实行"）。故本条设为不可跳过的收尾步骤，
> 并且 skill 流程第 0 步强制每次修复前先读本文件。

每次修复（无论大小）结束后，**同一天内**必须完成以下三项：

- **a. 追加案例**：在 `reference.md` 历史案例表新增一行：
  日期 | 症状 | 根因 | 修复 | 教训
- **b. 提炼教训**：若出现新的修复模式/坑 → 更新本文件「已知修复模式」
  或「常见坑」，或修正现有条目。
- **c. bump 版本**：SKILL.md frontmatter `version` +0.1，
  更新 `date` 为当日。

**完成条件（双检查）**：
1. `reference.md` 案例表存在当日记录；
2. SKILL.md 版本号 > 上次修复时记录的版本。
两者缺一即视为未完成，不得宣告修复任务结束。

---

## 已知修复模式（持续积累）

### 模式 1：子进程输出二进制 → UnicodeDecodeError 死锁

**症状**：MCP server 调用 `subprocess.run(..., capture_output=True, text=True)`
执行命令，命令输出含非 UTF-8 字节（如 gitleaks 扫描 .png/.woff 二进制文件）时，
Python 读取线程抛 `UnicodeDecodeError`，一个线程崩溃 → 整个 server 所有工具超时死锁。

**修复**：`text=True` 时加 `errors='replace'`：
```python
subprocess.run(cmd, shell=True, capture_output=True, text=True, errors='replace', timeout=t, cwd=...)
```
**案例**：2026-09-01，deepcode-agent-sdk `agent_sdk_server.py` L524/L660 两处修复。

### 模式 2：会话工作目录持久化无效路径 → bash 工具 ENOENT 坏死

**症状**：bash 工具报 `spawn ...\bash.exe ENOENT`，
startCwd 指向不存在的 Windows 路径（如 `\\tmp\\ci_logs`），
整个会话所有 bash 调用失败，重启会话才能恢复。

**根因**：sessionWorkingDirs 持久化了无效 cwd（POSIX 路径转 Windows 失败），
下次 spawn 时 cwd 无效 → ENOENT。

**修复**：getSessionCwd / updateSessionCwd 增加 `isUsableCwd()` 校验
（`fs.statSync(cwd).isDirectory()`），无效则回退 fallback 并清除脏记录。
**案例**：2026-09-01，bash-handler.ts。

### 模式 3：只创建记录不执行 → 线程/任务永远卡在初始状态

**症状**：spawn 后返回 status=created，但任务永远不推进（calls_made=0）。

**根因**：只写了 DB/状态机记录，没有执行器接管实际执行。

**修复**：spawn 后启动后台执行器（threading/subprocess），
把 goal 交给真实执行链，并更新状态 created → running → completed/failed。
**案例**：2026-09-01，deepcode-agent `agent_thread_manager.py` spawn 分支
新增 `_launch_auto_executor`。

### 模式 4：历史密钥泄漏 → gitleaks CI 红

**症状**：Security CI secret-scan job 失败，`gitleaks git` 报 N 处泄漏。

**修复路径**：
1. 本地跑 `gitleaks git --source <repo> --report-format=json --report-path out.json` 定位泄漏（commit/file/rule/line）。
2. 当前文件仍有真实密钥 → 替换为环境变量引用/占位符。
3. 历史泄漏（无法删历史）→ 追加 `.gitleaksignore` 指纹
   （格式 `commit:file:rule:line`，必须是完整 commit SHA）。
4. **自忽略陷阱**：`.gitleaksignore` 的注释里不要写真实密钥模式
   （如 `sk-1234567890abcdefghijklmn`），否则 gitleaks 会在
   .gitleaksignore 自身发现新泄漏。注释用纯文字描述。
**案例**：2026-08-27 ~ 2026-09-01，Security CI #200/#199 系列。

### 模式 5：依赖漏洞 → pip-audit / npm audit CI 红

**症状**：Dependency audit job 失败，`Found N known vulnerabilities`。

**修复**：升级锁定版本（如 `pip==26.1.2 → 26.2`），
修改 lock 文件后验证 `pip-audit --file <lock> --requirement` 归零。
**案例**：2026-08-27，PYSEC-2026-3721 in pip==26.1.2。

---

## 常见坑

- **改了 Python 源码不 py_compile 就宣布修好** → 缩进/语法错误到运行才暴露。
- **改完 MCP server 源码不重启** → 旧进程跑旧代码，用户复测仍失败，误判"没修好"。
- **把"输出有二进制"当成 server 卡死** → 实际是解码崩溃，看 server stderr 而不是只盯超时。
- **用 `/tmp` 等 POSIX 路径在 Windows 工具链里** → 路径转换后可能变成 `\\tmp` 无效路径。
- **修复时叠加多个改动一起测** → 失败不知道哪层的问题（一修一验）。
- **.gitleaksignore 注释写真实密钥示例** → 自泄漏。
- **项目 settings.json 的 mcpServers 路径不存在/缺 `--mcp`/命令名错误** → 项目级覆盖用户级配置后 server 启动失败；注册任何 MCP server 必须确认：文件路径 `os.path.exists()`、`--mcp` 标志、`python` vs `python3`（Windows 上 `python3` 可能无 shim）。

---

## 相关技能

| 技能 | 职责 |
|------|------|
| `deepcode-tool-discipline` | 工具调用三大铁律 + 失败诊断四原则 |
| `dsh-harness-repair` | DSH（DeepSeek Harness）专项修复 Playbook |
| `deepcode-repair-strategy` | Docker/WSL 类环境修复策略 |
| `deepcode-agent` | Agent 线程派生系统源码知识 |
| `deepcode-agent-sdk` | Agent SDK（HTTP/MCP/Python 嵌入）源码知识 |
| `deepcode-cerebellum` | 小脑记忆引擎（沉淀机制依赖其记忆库） |

历史案例明细 → 见 [reference.md](reference.md)。
