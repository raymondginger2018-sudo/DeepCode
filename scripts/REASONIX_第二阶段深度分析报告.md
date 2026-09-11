# REASONIX 第二阶段深度分析报告

> 目标：`reasonix.exe` (v1.21.5, Go 1.26.5, Bun 打包, PE32+ x86-64)
> 阶段：第二阶段 — 深入 Agent 主循环 / MCP 客户端 / 插件系统 / prefix-cache
> 方法：Ghidra MCP 反编译 + Go pclntab 符号表(59145 函数) + 字符串池解码交叉验证

---

## 1. Agent 核心循环

### 1.1 构造器 `reasonix/internal/agent.New` @ 0x14076ac00

构造一个 ~0x210+ 字节的 Agent 对象，默认值如下：

| 字段偏移 | 默认值 | 语义 |
|---|---|---|
| +0x71~0x74 | 0.5 / 0.6 / 0.8 / 0.9 | 四档 temperature（传入 ≤0 时回退） |
| +0x76 | 2 | maxSteps（<1 时回退 2） |
| +0x20 区域 | 0x20000 (131072) | 上下文/预算上限 |
| +0x80 | `"default"` (ptr+len=7) | 默认 profile |
| 其他 | 模型名默认 `&DAT_141b7ad34`(8B) | 默认模型 |

- 最后调用链：`FUN_1407c7b20`（系统提示注入）→ `FUN_140769080` → `FUN_140769000` → `FUN_1407b5fa0`（工具注册）→ 日志 `&DAT_141bcb989`（36B）。
- 返回接口类型 `PTR_DAT_141fe5978` 包装的对象。
- **隐藏实验特性**：字符串池暴露环境变量 `REASONIX_EXPERIMENT_FORK_CAPTURE_DIR` —— 存在一个 fork 捕获目录的实验开关（可能用于子进程崩溃捕获/调试）。

### 1.2 主循环 `reasonix/internal/agent.(*Agent).Run` @ 0x14076bda0

```
Run(agent)
├─ 栈检查 FUN_1407c6620
├─ LOCK() 递增 agent+0x1a0 并发计数器 (自旋锁 agent+0x230 保护单次运行标志)
├─ time.Now() 记录启动 (FUN_1400c6380)
├─ 分配运行上下文 DAT_141829ce0 (cleanup=FUN_14076c540)
├─ 注册 5 个 deferred/finalizer:
│    FUN_14076c440 (恒) / FUN_14076c400 (条件 bVar9) / FUN_14076c3e0 (恒)
│    FUN_14076c500 (恒) / FUN_14076c5c0 (条件 bVar3)
├─ FUN_1407ada40 ──★ 核心执行体 (agent 循环主驱动)
├─ 成功路径: FUN_1407dc620 后处理 → agent+0x560 接口回调 → FUN_1407dd460 完成回调
└─ 返回 (error, result) 16 字节
```

**要点**：Run 本身是一个带并发守卫 + defer 清理链的调度器；真正的 Agent 轮询循环在 `FUN_1407ada40`（可继续深入）。

### 1.3 CLI 入口 `cli.runAgent` @ 0x14135bda0

- 命令对象：`FUN_140022b80(&DAT_141b210c0)`，名长 3（`run`）。
- 注册 ~14 个 flag（经 `FUN_1411437c0` / `FUN_14112e660`），字符串池解码出的 flag 名：
  `--debug(5B)`、`--eventlog(7B)`、`--api-key(9B)`、`--model(13B)`、`--profile`、`--max-steps`、`--show-thinking`、`--output-format`、`--allowed-tools`、`--metrics`、`--add-dir`、`--continue`、`--cwd`、`--permission-mode`、`--trajectory`、`--token-file`、`--currency`、`--events-jsonl`(12B)、`--compact-ratio`、`--docs-manifest`、`--verify-source`、`--release-notes`、`--allowedTools`、`--behind-proxy`、`--ablate` 等。
- 关键逻辑：
  - `--events-jsonl` 检测（uint64 魔数 `0x6a2d73746e657665`="events-j" + `0x6c6e6f73`="sonl" + len 0xc）→ `FUN_1413e3280()` 事件日志模式。
  - 错误检查：`FUN_14135b8e0`（非零直接退出）、`FUN_14135b9e0`（出错返回 1）。
  - Agent 生命周期：`FUN_140964f40`(创建) → `FUN_14096ef80`(加载历史) → `FUN_14097a700`(运行) → `FUN_140961040`(后处理) → 事件记录 `FUN_1411298a0`/`FUN_1413d7680`/`FUN_1413d7980`/`FUN_1413d9240`/`FUN_1413d9a20`。
  - 返回码：0=成功 / 1=运行错误 / 2=参数错误。

### 1.4 TUI 入口 `cli.chatREPL` @ 0x141361160

- 命令名长 8（`chat`/REPL），字符串池解码出：
  `"dangerously-skip-permissions"`、`"compact_ratio = %s (%s: %s)"`、`"mcp browse: unknown flag %q"`、`"mcp import: nothing selected"`、`"updated connection mode for Confirm"`、`"clear authentication"`、`"reasoning-language set to %s"`、`"host %s: %s@%s workspace=%s"`、`"machine_identity_unavailable"`、`"reasonix-machine-session-v1"`(26B)、`"--print-last-message"`。
- 关键分支：检测 `"reasonix-machine-session-v1"`（iStack_9518==0x1a）→ `FUN_141366020()` 进入**机器会话模式**（无头/自动化）；`FUN_141369f40()` 权限检查。
- 构造 REPL 会话：注册处理器表 `PTR_DAT_14202a440`（经 `FUN_14131f6a0`），channel 缓冲 0x400 (`FUN_140018940(&DAT_1416a1600, 0x400)`)，`FUN_14001eca0` + `FUN_14108e980` 启动异步 REPL。
- 清理：遍历处理器表调用 `(*(code *)ppuVar15[0x17])` 回调；REPL 会话走 `FUN_140980820(iStack_49b0, 1, 0)`。
- 回调：`FUN_1413644a0` / `FUN_141364120` / `FUN_141364020`。

### 1.5 双前端结论

```
chat（无参数）
├─ chatREPL (0x141361160) — 传统 REPL 会话 + 机器会话模式 (reasonix-machine-session-v1)
└─ chatTUI (BubbleTea v2.0.8) — 默认执行器表 [1..7] 闭包:
     refreshGitStatus.fetchGitStatus.func3 (git 状态刷新)
     beginClipboardImagePaste.pasteClipboardImage.func22 (剪贴板图片粘贴)
     autoScrollTick.func3/func4 (自动滚动 tick)
     elapsedTick.func16/func6 (计时 tick)
```

---

## 2. MCP 客户端深挖

### 2.1 生态包结构（修正早期认知）

符号表确认 MCP 相关包为 4 个，而非单个 `mcp` 包：
`reasonix/internal/mcplaunch`（37 函数，启动授权/锁管理）+ `reasonix/internal/mcpregistry`（13 函数，注册表目录）+ `reasonix/internal/mcpdiag` + `agent` 包内的 `MCPCapabilityRuntime`。

### 2.2 mcplaunch — 启动授权与文件锁

| 函数 | 地址 | 语义 |
|---|---|---|
| `(*Manager).Authorize` | 0x1405ad580 | 三参数校验(server/config source/identity) → 自旋锁(mgr+0x20) → updatePersistent 持久化；错误串 0x45B |
| `(*Manager).LaunchAuthorized` | 0x1405addc0 | Load 状态遍历，精确比对 ProjectLaunchIdentityDigest / workspace / transport；defer Revoke |
| `(*Manager).Revoke` | 0x1405ae360 | 撤销授权 |
| `(*Manager).GetLauncherLock` / `PutLauncherLock` | 0x1405ae840 / 0x1405aed40 | 启动器锁 |
| `(*Manager).Load` / `updatePersistent` | 0x1405ad260 / 0x1405af4c0 | 状态加载/持久化 |
| `acquireFileLock` | 0x1405b07e0 | 文件锁（防并发启动） |
| `ProjectLaunchIdentityDigest` | 0x1405acb60 | 项目启动身份摘要 |
| `WorkspaceFingerprint` | 0x1405acac0 | 工作区指纹 |

模式：MCP 服务器的**启动授权**由工作区指纹 + 项目身份摘要 + 文件锁三层控制，防止未授权/并发启动。

### 2.3 mcpregistry — 注册表解析

| 函数 | 地址 | 语义 |
|---|---|---|
| `(*Client).Search` | 0x14126aec0 | 搜索 |
| `(*Client).Resolve` | 0x14126b1a0 | 解析：fetch(超时100) → cacheKey → storeCache → 精确/模糊双匹配；错误 "registry server name is required"(0x20B) / "MCP Registry has no server named %q"(0x23B) |
| `(*Client).fetch` | 0x14126bd80 | HTTP 拉取（带超时） |
| `cacheKey` / `storeCache` / `loadCache` | 0x14126d780 / 0x14126db40 / 0x14126d8a0 | 本地缓存 |

### 2.4 MCPCapabilityRuntime（Agent 内运行时）

- `agent.NewMCPCapabilityRuntime` @ 0x140827e20：构造运行时，字段 0xb/0xc 为 map（工具名 → 代理状态）。
- `(*MCPCapabilityRuntime).NewFrontend` @ 0x14082bfa0：创建前端代理。
- `(*MCPCapabilityRuntime).ConnectedProxyTools` @ 0x14082c1c0：连接后的代理工具集。
- `(*onDemandMCPConnect).Execute` @ 0x140839d40：按需连接执行器 —— 先执行工具，失败时读取 error 接口（`(**(code **)(err_ptr+0x18))` 即 `error.Error()`），若运行时身份变化则抛错：
  > "MCP server %q runtime identity changed after resolution; retry so Reasonix can bind the current configuration" (109B @ 0x141c1da59)
- `control.(*Controller).ConnectMCPServer` @ 0x14097cdc0、`ConnectConfiguredMCPServer` @ 0x14097f420：控制器层连接。

### 2.5 MCP 结论

MCP 集成是**安全优先**的：按需连接（onDemandMCPConnect）+ 启动授权（mcplaunch）+ 运行身份绑定校验（runtime identity changed 报错防中间人/配置漂移）+ 注册表目录（mcpregistry 带缓存）。工具命名 `mcp__<server>__<tool>` 前缀与 Agent 工具面一致。

---

## 3. 插件系统（661 符号）

### 3.1 加载链全貌

```
config.loadPluginEntriesFromTOML (0x140633500)
│  解析 config.toml 的 [[plugins]] → PluginEntry (176B/条 = 22 个 8B 字段)
│    ├─ FUN_1405f2640(local_c8, local_1a0, &DAT_141b64240, local_10)  TOML 解析
│    └─ FUN_140629e40()  逐条规范化
├─ config.loadLegacyConfigPlugins (0x140633a60)  旧格式兼容
├─ config.LoadMCPJSONPlugin (0x14062ae80)        MCP JSON 插件
└─ pluginpkg.LoadInstalled (0x1405d0600)
    ├─ pluginpkg.LoadState (0x1405cf420)
    │    └─ 读取 <stateDir>/plugin-packages.json (20B 确认) → JSON 反序列化
    ├─ 遍历插件列表 (120B/条 = 15 个 8B 字段)
    │    └─ 检查 +0xc 槽位 enabled 标志
    ├─ 验证 FUN_1405d0e00 / FUN_1405d0fc0
    └─ 聚合到 0x210B/条 的结果结构
```

### 3.2 数据结构

- **PluginEntry**：176 字节（22 × 8B），TOML `[[plugins]]` 条目 → 类型元数据 `DAT_141b4c460`。
- **插件状态**：120 字节（15 × 8B），enabled 标志在 +0xc（第 13 个字段）。
- **插件包状态文件**：`plugin-packages.json`（确认 20 字节字符串 @ 0x141ba0337）。
- 注册域：`reasonix.io/plu...`（包仓库域名）。
- 宿主：`plugin.(*Host).registerDeferredCancel` @ 0x14069d720。

### 3.3 插件 = MCP 服务器（协议闭环确认）

反编译 `FUN_1406a5380` / `FUN_1406a4360` / `FUN_1406a58e0` / `FUN_1406a2ac0` 后确认：**插件进程以独立 MCP 服务器形式由 reasonix host 启动，宿主充当 MCP 客户端**，走标准 JSON-RPC 协议。

**启动链（5 层）**：
```
plugin.Start (FUN_1406993c0) — 每服务器 goroutine，结果推回 host channel (+0xb8)
└─ FUN_1406a2ac0   — launch / authorization / initialize 三段错误前缀检查
│    ("launch"(6B @ 0x141b75767) / "authorization%s:%d: %s"(13B @ 0x141b896bc) / "initialize"(10B @ 0x141b803f9))
└─ FUN_1406a58e0   — MCP 握手构造：clientInfo/name/version + protocolVersion + capabilities
│    (tools/prompts/resources 能力声明 + notifications/initialized 通知)
└─ FUN_1406a4360   — 启动分发：读 name 字段、26B 启动日志(&DAT_141bb0c5c)
└─ FUN_1406a5380   — ★ "tools/call" JSON-RPC 方法分发器
     (小端魔数 0x61632f736c6f6f74="tools/ca" + 0x6c6c="ll" 匹配 10B "tools/call")
```

**MCP 握手字符串**（FUN_1406a58e0 引用）：`name`(4B)、`protocolVersion`(15B)、`capabilities`(12B)、`clientInfo`(10B)、`notifications/initialized`(25B)、`tools`(5B)、`prompts`(7B)、`resources`(9B)、`listChanged`、`roots`。

**tools/call 分发器超时机制**（两个格式化错误串）：
```
"MCP tool %q timed out after %s; increase tool_timeout_seconds or call_timeout_seconds to allow longer runs: %w"      (110B @ 0x141c1df7c)
"MCP method %q on server %q timed out after %s; increase mcp_call_timeout_seconds or call_timeout_seconds to allow longer runs: %w" (129B @ 0x141c223de)
```
暴露 Go 风格配置键：`tool_timeout_seconds` / `call_timeout_seconds` / `mcp_call_timeout_seconds`。相邻字符串还发现 `read_skill: skill %q is a subagent`、`tls: failed to find PEM block`。

### 3.4 结论

插件系统 = TOML 声明（config.toml `[[plugins]]`）+ 包管理状态（plugin-packages.json）+ 宿主运行时（plugin.Host），**插件进程 = MCP 服务器**（JSON-RPC：initialize → notifications/initialized → tools/list → tools/call），支持 MCP JSON 插件与旧配置兼容。

---

## 4. DeepSeek prefix-cache —— 结论

字符串解码确认（@ 0x141c30836）：
```
"prefix-cache reuse.blocked: the trailing echo/printf of $? masks..."
```

**结论**：prefix-cache 是 **DeepSeek API 服务端** 的缓存机制（对字节稳定前缀的 KV 缓存复用）。客户端 `reasonix.exe` 中不存在本地缓存实现，该字符串只是服务端错误消息的透传/检测文本。符号表中也无任何 `prefixcache`/`prefix-cache` 客户端函数。这与 DeepSeek 官方文档一致：服务端自动对重复前缀计费折扣。

---

## 5. 架构总结（第二阶段增量）

```
reasonix.exe (Go 1.26.5)
├─ 入口链: main.main → runWithCrashCapture → cli.RunWithBuildInfo → 子命令分发
├─ Agent 核心
│    ├─ agent.New (默认 temperature 四档 / maxSteps=2 / 0x20000 预算)
│    ├─ (*Agent).Run (并发守卫 + 5×defer 清理链 + 核心循环 FUN_1407ada40)
│    └─ 前端: chatREPL (机器会话模式) + chatTUI (BubbleTea)
├─ MCP 客户端 (安全优先)
│    ├─ mcplaunch: 工作区指纹 + 身份摘要 + 文件锁的启动授权
│    ├─ mcpregistry: 注册表 + 缓存
│    ├─ MCPCapabilityRuntime: 按需连接 + runtime identity 绑定校验
│    └─ 工具面: mcp__<server>__<tool>
├─ 插件系统
│    ├─ config.toml [[plugins]] → PluginEntry (176B)
│    ├─ plugin-packages.json 状态 (120B/条, enabled @ +0xc)
│    ├─ plugin.Host 运行时
│    └─ ★ 插件 = MCP 服务器: plugin.Start → 启动链(FUN_1406a2ac0→a58e0→a4360→a5380)
│         JSON-RPC 协议: initialize → notifications/initialized → tools/list → tools/call
│         超时键: tool_timeout_seconds / call_timeout_seconds / mcp_call_timeout_seconds
├─ 崩溃自愈: REASONIX_HOME/cli-crash-reports/%020d-%d-%s.json
└─ DeepSeek 协议: 模型前缀匹配 + /beta/chat/completions + 旧协议自动迁移 + prefix-cache(服务端)
```

**隐藏特性**：`REASONIX_EXPERIMENT_FORK_CAPTURE_DIR`（fork 捕获目录实验开关）、`reasonix-machine-session-v1`（机器会话/无头模式）、`dangerously-skip-permissions`（权限跳过）。

---

## 6. 可继续深入方向

1. 反编译 `FUN_1407ada40`（Agent 核心轮询循环本体）+ `FUN_1407dc620` 后处理。
2. `chatTUI` BubbleTea 模型方法反编译（Update/View 完整闭环）。
3. `mcplaunch` 的 `ProjectLaunchIdentityDigest` 具体哈希算法（已确认是 SHA-256 hex，具体输入拼接顺序待细化）。
4. 插件进程实际 spawn 原语：`FUN_1406a58e0`/`FUN_1406a4360` 下游的进程创建细节（插件= MCP 服务器已确认，非 ACP 协议）。
5. 崩溃报告落盘动态实测：用 ghidra-mcp debugger 附加运行中的 reasonix，对 `runWithCrashCapture` 下断点强制崩溃，验证 `cli-crash-reports/*.json` 实际生成（MCP 加载/配置持久化已动态验证，见第 7 节）。

## 7. 动态验证结果

对 `reasonix v1.21.5` 做了真实运行验证（静态分析的动态闭环）：

- **运行确认**：`reasonix --version` → `v1.21.5`，二进制可正常执行。
- **MCP 服务器加载**：`reasonix mcp list` 正确列出已注册的 2 个 stdio MCP 服务器：
  ```
  deepcode-decompiler (stdio)  python F:/DEEPCODE/tools/mcp_decompiler_server.py
  ghidra-mcp       (stdio)  F:/DEEPCODE/tools/ghidra-mcp/.venv/Scripts/bridge-mcp-ghidra.exe --transport stdio
  ```
- **激活状态持久化**：`%APPDATA%\reasonix\mcp-activation.json` 记录 2 条启用覆盖：
  ```json
  {"version": 1, "overrides": [
    {"scope": "global", "source": "user_config", "server": "deepcode-decompiler", "enabled": true},
    {"scope": "global", "source": "user_config", "server": "ghidra-mcp", "enabled": true}]}
  ```
- **运行时目录结构**：`%APPDATA%\reasonix\` 含 `config.toml` + `mcp-activation.json` + `mcp-state/`（空）+ `state/`（legacy-keyring-checked 标记）；`~/.reasonix/locks/` 存在——与静态分析中的 `plugin-packages.json` / `.mcp-activation.lock` 机制吻合。
- **崩溃报告**：`cli-crash-reports` 目录当前不存在（reasonix 尚未崩溃过），静态分析确认崩溃时会按 `REASONIX_HOME/cli-crash-reports/%020d-%d-%s.json` 生成。

> 动态验证覆盖了：MCP 客户端（mcpregistry 注册 + mcplaunch 授权配置）与插件/MCP 服务器注册的运行时行为；崩溃报告落盘需强制触发崩溃，留待方向 5 的 debugger 实测。

---
*生成于 2026-08-10 · F:\DEEPCODE\scripts*
