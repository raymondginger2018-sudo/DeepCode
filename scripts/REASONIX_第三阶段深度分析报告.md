# REASONIX 第三阶段深度分析报告

> 目标：`reasonix.exe` (v1.21.5, Go 1.26.5, Bun 打包, PE32+ x86-64)
> 阶段：第三阶段 — 穷尽挖掘方向 A-E（Agent 核心循环 / chatTUI 闭环 / 启动身份 / 插件 spawn 原语 / 动态验证）
> 方法：Ghidra MCP 反编译 + Go pclntab 符号表(59,145 函数) 地址反查 + 字符串池解码 + 真实运行验证

---

## 0. 本阶段重大认知修正

第三阶段通过 **符号表地址反查**（`reasonix_symbols.txt`）纠正了第二阶段对若干关键函数的错误定性：

| 地址 | 第二阶段误判 | 第三阶段确认真相（符号表） |
|---|---|---|
| `FUN_1407ada40` | Agent 核心轮询循环本体 | `agent.(*Agent).interceptAgentStart` — `agent.before_start` 钩子拦截器 |
| `FUN_1407dc620` | 后处理/结果收集 | `agent.(*Agent).beginRunTurn` — 每轮开始状态机 |
| `FUN_1406a5380` | `tools/call` 分发器 | `plugin.(*Client).timeoutError` — 超时错误构造器 |
| `FUN_1406a58e0` | 插件 spawn | `plugin.(*Client).initializeSession` — MCP initialize 握手 |
| `FUN_1406a4360` | 启动分发器 | `plugin.(*Client).call` — MCP JSON-RPC 调用方法 |
| `FUN_1406a4f20` | （未知） | `plugin.(*Client).callTransport` — 传输层调用 |
| `FUN_1406a49a0` | （未知） | `plugin.(*Client).withProgress` — 进度包装 |
| `FUN_1406a51a0` | （未知） | `plugin.(*Client).contextWithCallTimeout` — 超时上下文 |
| `FUN_1406a5700` | （未知） | `plugin.formatTimeout` — 超时格式化 |

**教训**：Ghidra 反编译只能给出结构与调用关系，**函数定性必须以符号表为准**——反编译启发式判断（"像循环"、"像分发器"）在高优化 Go 二进制中容易误判。

---

## 1. 方向 A：Agent 核心循环最终定论

### 1.1 构造器 `agent.New` @ 0x14076ac00

~0x210+ 字节对象，字段默认值：

- `+0x71~0x74`：temperature 四档 0.5 / 0.6 / 0.8 / 0.9（传入 ≤0 时回退）
- `+0x76`：maxSteps = 2（传入 <1 时回退）
- `+0x20` 区域：0x20000 (131072) 上下文/预算上限
- `+0x80`：`"default"` profile（ptr+len=7）
- 模型名默认 `&DAT_141b7ad34`(8B)

调用链：`FUN_1407c7b20`(系统提示注入) → `FUN_140769080` → `FUN_140769000` → `FUN_1407b5fa0`(工具注册) → log `&DAT_141bcb989`(36B)。返回接口 `PTR_DAT_141fe5978`。

### 1.2 主入口 `agent.(*Agent).Run` @ 0x14076bda0

```
Run(agent)
├─ 栈检查 FUN_1407c6620
├─ LOCK() 递增 agent+0x1a0 并发计数器 (自旋锁 agent+0x230 保护单次运行标志)
├─ time.Now() 记录启动 (FUN_1400c6380)
├─ 分配运行上下文 DAT_141829ce0 (cleanup=FUN_14076c540)
├─ 注册 5 个 deferred/finalizer:
│    FUN_14076c440 (恒) / FUN_14076c400 (条件 bVar9) / FUN_14076c3e0 (恒)
│    FUN_14076c500 (恒) / FUN_14076c5c0 (条件 bVar3)
├─ 每轮调用 beginRunTurn → 核心执行 → 后处理
└─ 返回 (error, result) 16 字节
```

### 1.3 `agent.(*Agent).interceptAgentStart` @ 0x1407ada40 ★ 修正

**不是**轮询循环，而是 `agent.before_start` 生命周期钩子拦截器：

- 检查 `agent+0x168` 字段（非零才执行）
- 通过 `FUN_14072bce0` / `FUN_14072d500` 调用钩子，钩子名 = `"agent.before_start"`（`&DAT_141b990d6` + 0x12=18 字节）
- 错误路径 `FUN_1407ad6e0` 返回错误码

### 1.4 `agent.(*Agent).beginRunTurn` @ 0x1407dc620 ★ 修正

**不是**后处理，而是**每轮开始状态机**：

- 清零 `agent+0x2d0` 区域
- 遍历工具/消息列表（`FUN_140466d20`/`FUN_140467320`/`FUN_140467080`，逐元素 `FUN_1404398e0`/`FUN_1404369e0`）
- 设置状态标志 `+0x2d8..+0x2db`
- LOCK 下写毫秒时间戳到 `agent+0x510`（`FUN_1400c6380()` → 毫秒换算）
- 通过接口 `+0x30` 通知上游
- 构建每轮结果对象（`&DAT_141b0b760`，字段 +0x60=context / +0x68=bool / +0x70/+0x78=string / +0x48=flags / +0x50=byte）
- 返回 24 字节 `{string, string, heap_object}`

### 1.5 CLI 双入口

- `cli.runAgent` @ 0x14135bda0：~40 个 flag（`--model`/`--profile`/`--max-steps`/`--show-thinking`/`--output-format`/`--allowed-tools`/`--metrics`/`--add-dir`/`--continue`/`--permission-mode`/`--trajectory`/`--events-jsonl`/`--ablate` 等）；`--events-jsonl` 魔数 `0x6a2d73746e657665`="events-j"+`0x6c6e6f73`="sonl"+len 0xc → `FUN_1413e3280()`；生命周期 `FUN_140964f40`(创建)→`FUN_14096ef80`(历史)→`FUN_14097a700`(运行)→`FUN_140961040`(后处理)；返回码 0=成功/1=运行错误/2=参数错误
- `cli.chatREPL` @ 0x141361160：检测 `"reasonix-machine-session-v1"`(26B, iStack_9518==0x1a) → `FUN_141366020()` 机器会话模式；处理器表 `PTR_DAT_14202a440`；channel 缓冲 0x400

---

## 2. 方向 B：chatTUI（BubbleTea）完整闭环

### 2.1 Update 入口 `cli.chatTUI.update` @ 0x141321a00 → 内部 `FUN_141322540`

`FUN_141322540` 是 BubbleTea **消息类型哈希分发器**：

```c
switch(*(dword *)(param_1 + 0x10) >> 0x13 & 0x7f)  // 消息类型哈希 → case
```

已识别的消息类型 case（20+）：

| Case | 类型对象 | 语义 |
|---|---|---|
| 0x00 | `DAT_1419f2300` | 按键事件变体（\x01/\x02/\x03 = key down/up） |
| 0x08 | `DAT_141726240` | 1s 定时器 tick |
| 0x09 | `UNK_1417a4940` | 窗口大小变化 |
| 0x0b | `UNK_1418f5880` | 消息/行输入 |
| 0x0c | `UNK_14186b1a0` | 消息提交 |
| 0x11 | `DAT_141855cc0` | 输入模式切换（`FUN_1413527c0`） |
| 0x1d | `UNK_1419d4620` | 1.5s 延迟命令 |
| 0x21 | `DAT_141872900` | 输入缓冲 resize / 按键输入 |
| 0x2a | `UNK_1417a47c0` | 退出事件 |
| 0x2f | `DAT_1417262a0` | 16ms 定时器 |
| 0x30 | `UNK_141adaa60` | ★ 消息发送：存输入文本 → 追加历史 → `FUN_1413e07e0` |
| 0x33 | `UNK_1417a48c0` | 选择状态存储 |
| 0x34 | `UNK_141b462e0` | ★ 广域按键分发循环（最多 0x200 键，逐键 `FUN_141340ce0`） |
| 0x39 | `DAT_141726300` | 关闭/退出（接口 +0x3d8 调用 + `FUN_1413e07e0`） |
| 0x3f | `DAT_141a9c940` | ★ 命令模式按键（大端小端字符串比较） |
| 0x45 | `UNK_1417a4740` | 状态存储 |
| 0x46 | `DAT_141727b00` | no-op |
| 0x48 | `UNK_1419d47e0` | 多行/建议事件（fmt.Sprintf 风格格式化） |
| 0x4a | `UNK_1418f5100` | 设置/通知 |
| 0x56 | `UNK_1417a4840` | 历史标记 |
| 0x5d | `UNK_1417261e0` | 滚动/选择调整（80ms 定时器） |
| 0x61 | `UNK_14199a360` | 转发 `FUN_14139de80` |
| 0x69 | `DAT_1419f24c0` | PageUp/Down（3 行）`FUN_141376f60`/`FUN_141376d20` |
| 0x79 | `DAT_1419f25a0` | 选择/滚动更新 |
| 0x7a | `DAT_14186b100` | 建议/插入 |
| 0x7e | `UNK_1418fd800` | 从模型更新状态 |

### 2.2 快捷键绑定（小端魔数匹配）

case 0x34 / 0x3f 中的按键字符串匹配：

- `"ctrl+c"`：len==6, `0x6c727463`+"c+"→ `0x632b`
- `"super+c"`：len==7, `0x65707573`+`0x2b72`+'c'
- `"meta+c"`：len==6, `0x6174656d`+`0x632b`
- `"ctrl+insert"`：len==0xb, `0x736e692b6c727463` + `0x7265` + 't'
- `"alt+v"`：len==5, `0x2b746c61`+'v'
- `"up"`：`0x7075` / `"down"`：`0x6e776f64` / `"left"`：`0x7466656c` / `"right"`：`0x74686769`
- `"enter"`：len==5, `0x65746e65`+'r' / `"esc"`：`0x7365`+'c' / `"tab"`：`0x6174`+'b'

### 2.3 View 渲染 `cli.chatTUI.View` @ 0x1413397a0

- 首字符 `'!'` 进入命令模式
- `FUN_1410a2560(buffer, 0x2000, &DAT_14169c5c0, ...)` 渲染到 0x2000 缓冲
- 收集 ~14 个组件字符串（`FUN_141355bc0`/`FUN_1413d5c20`/`FUN_141396760`/`FUN_1413d2100`/`FUN_14133e3e0`/`FUN_14133cf00`/`FUN_141379e80`/`FUN_141374c80`/`FUN_1413bd960`/`FUN_14132f560`/`FUN_14132fbc0`/`FUN_141320be0`/`FUN_14142de40`/`FUN_14132ff40`）
- `FUN_140144280(components, len, cap, &DAT_141fd20c8, 1)` = `strings.Join`
- 滚动位置 `FUN_141377100()`

### 2.4 结论

chatTUI 是标准 BubbleTea v2.0.8 应用：Update（消息类型哈希分发）+ View（多组件拼接渲染），覆盖按键/定时器/窗口/消息发送/滚动/剪贴板粘贴/自动滚动/git 状态刷新等完整交互闭环。

---

## 3. 方向 C：ProjectLaunchIdentityDigest（启动身份）完整算法

### 3.1 流水线（符号表 + 反编译交叉确认）

```
mcplaunch.ProjectLaunchIdentityDigest (FUN_1405acb60)
├─ FUN_1405acee0(1)              — 收集身份字段（5~7 个 string/slice）
│    ├─ normalizeTransport (FUN_1405b0140)   — transport 规范化
│    ├─ canonicalPath (FUN_1405b0280)        — 路径规范化
│    └─ cleanStrings (FUN_1405b0340)         — 字符串清理
├─ 字符串构建器 DAT_141ad9b00 (FUN_14001eca0)
├─ crypto/sha256.Sum256 (FUN_140206720)      — 32 字节摘要
│    └─ 内部: init (FUN_141455380) → write (FUN_141455480) → sum (FUN_141455720)
└─ mcplaunch.digestBytes (FUN_1405b06e0)     — hex 编码 → 64 字符指纹
     └─ 查表 "0123456789abcdef" (&DAT_141b9170e) 高低 nibble
```

### 3.2 SHA-256 确认

`FUN_140206720` 反编译清晰呈现标准 SHA-256 三阶段：init → write → sum，输出 32 字节到 `local_a0[32]`；`FUN_1405b06e0` 将 32 字节摘要逐 nibble 编码为 64 字符小写 hex（分配 `&DAT_14169c400` 池 0x40=64 字节）。

### 3.3 结论

**ProjectLaunchIdentityDigest = SHA-256 hex( normalizeTransport + canonicalPath + cleanStrings + workspace 指纹 )**，与 `WorkspaceFingerprint`、`LauncherLockFingerprint` 共同构成 mcplaunch 三层启动授权（文件锁 + 身份摘要比对 + 进程存活校验）。

---

## 4. 方向 D：插件 spawn 原语 — 100% 闭环

### 4.1 完整 spawn 链（本阶段最终确认）

```
plugin.newStdioTransport (FUN_1406b78e0)  ★ stdio 传输构造
├─ plugin.resolveStdioExecutable (FUN_1406b9a80)   — 解析可执行文件路径
├─ plugin.prepareMCPPrivateStateForOS (FUN_1406b8da0) — 准备 MCP 私有状态(env)
├─ "confined" 模式检测（小端 0x64656e69666e6f63 = "confined"，&DAT_141b7b22c）
│    → 设置 cmd 内部标志 |0x8000000、|0x4000（隐藏窗口等）
├─ os/exec.CommandContext (FUN_1401881e0)  ★ 创建 Cmd（带 context 取消）
├─ os/exec.(*Cmd).StdinPipe (FUN_14018afe0)  — stdin 管道（错误串 &DAT_141b77dba）
├─ os/exec.(*Cmd).StdoutPipe (FUN_14018b260) — stdout 管道
└─ proc.startTracked (FUN_140481480)  ★ 跟踪式启动
     ├─ os/exec.(*Cmd).Start (FUN_1401892e0)  ★★ 真正启动点
     │    └─ os.StartProcess (FUN_1400e9320)
     │         └─ os.startProcess → Windows CreateProcess
     ├─ proc.assignJob (FUN_1404827e0)          — 分配 Job Object（进程树隔离）
     └─ 分支:
          ├─ param_2≠0 && assignJob==0 → terminateAndReapStartedProcess (FUN_140481cc0)
          └─ else → resumeProcess (FUN_140482a60) + 错误格式 FUN_14012e580(&DAT_141bbf1bc, 0x1f)
```

### 4.2 传输工厂 `plugin.newTransport` (FUN_1406a3fc0)

按 scheme 分发（Go interface 返回）：

| scheme | 构造 | 类型 |
|---|---|---|
| 空 / `"stdio"` (len 5) | `FUN_1406b78e0` | `PTR_DAT_141fec1a0` |
| `"sse"` (len 3) | `FUN_1406b37c0` | `PTR_DAT_141fec200` |
| `"http"`/`"https"` (len 4) | `FUN_1406afca0` | `PTR_DAT_141fec1d0` |
| `"streamable-http"`/`"streamable_http"` (len 15) | `FUN_1406afca0` | `PTR_DAT_141fec1d0` |
| 未知 | 错误 `FUN_14012e580(&DAT_141be5a27, 0x2f)` | error |

### 4.3 `proc` 包进程管理全家（符号表）

```
0x140481280 proc.HideWindow                     — 隐藏窗口 (SysProcAttr 标志)
0x140481320 proc.KillTree                       — 进程树终止（清理 defer）
0x140481480 proc.startTracked                   — ★ 启动并跟踪
0x140481cc0 proc.terminateAndReapStartedProcess — 终止收割
0x1404827e0 proc.assignJob                      — Job Object 分配
0x140482a60 proc.resumeProcess                  — 恢复进程
0x140482fa0 proc.KillTracked                    — 终止跟踪
0x140483020 proc.FinishTracked                  — 完成跟踪
0x140483080 proc.RunCommand                     — 通用命令执行
0x1404837a0 proc.waitForTrackedCommand          — 等待跟踪命令
0x140483f20 proc.(*TrackedCommand).Kill         — 跟踪命令终止
0x140484080 proc.(*TrackedCommand).StopTracking — 停止跟踪
0x140484160 proc.(*TrackedCommand).Diagnostics  — 诊断
```

### 4.4 sidecar 对比

`extension/sidecar.startProcess` (FUN_14085ba60) 同样使用 `os/exec.Command` (FUN_140187d20) + StdinPipe/StdoutPipe + `proc.startTracked`——**插件与 sidecar 共用同一 spawn 原语** `startTracked`。

### 4.5 关键字符串

- `0x141b7b22c` = `"confined"` — 受限/隐藏模式
- `0x141bcbb81` = `"stdio plugin %q: command is required"` — stdio 插件错误
- `0x141bbf1bc` (0x1f=31B) — startTracked 错误格式

### 4.6 结论

**plugin stdio transport 的完整 spawn 链**：

```
newStdioTransport → resolveStdioExecutable + prepareMCPPrivateStateForOS
  → os/exec.CommandContext → StdinPipe/StdoutPipe
  → proc.startTracked → os/exec.(*Cmd).Start → os.StartProcess → CreateProcess
  → proc.assignJob (Job Object) + KillTree 清理
```

MCP 超时机制（方向 3 已知）：`contextWithCallTimeout` + `timeoutError`（`MCP tool %q timed out after %s; increase tool_timeout_seconds or call_timeout_seconds`）。

---

## 5. 方向 E：动态验证结果

- **运行确认**：`reasonix --version` → `v1.21.5`
- **MCP 服务器加载**：`reasonix mcp list` 正确列出 2 个 stdio 服务器（deepcode-decompiler + ghidra-mcp）
- **激活持久化**：`%APPDATA%\reasonix\mcp-activation.json` 记录 2 条启用覆盖
- **运行时目录**：`%APPDATA%\reasonix\` = config.toml + mcp-activation.json + mcp-state/ + state/；`~/.reasonix/locks/` 存在
- **崩溃报告**：`cli-crash-reports` 目录不存在（reasonix 尚未崩溃；机制为静态确认 `REASONIX_HOME/cli-crash-reports/%020d-%d-%s.json`）
- **插件 spawn 动态佐证**：ghidra-mcp 桥本身就是一个通过 stdio 传输的 MCP 插件进程——其 `bridge-mcp-ghidra.exe --transport stdio` 启动方式与静态分析出的 stdio transport spawn 链完全一致

---

## 6. 最终架构全景（三阶段合并）

```
reasonix.exe (Go 1.26.5, 59,145 符号恢复)
├─ 入口链: main.main → main.runWithCrashCapture → cli.RunWithBuildInfo → 22 子命令分发
├─ Agent 核心:
│    agent.New (temp 四档/maxSteps=2/0x20000 预算)
│    ├─ (*Agent).Run — 主入口（并发守卫 + 5×defer + beginRunTurn 每轮状态机）
│    ├─ (*Agent).interceptAgentStart — agent.before_start 钩子
│    └─ 双前端: chatREPL (机器会话模式) + chatTUI (BubbleTea v2.0.8, 26 消息类型)
├─ MCP 客户端 (安全优先):
│    mcplaunch (3 层授权: WorkspaceFingerprint + ProjectLaunchIdentityDigest=SHA-256 hex + 文件锁)
│    mcpregistry (注册表缓存: fetch→cacheKey→storeCache→精确/模糊双匹配)
│    MCPCapabilityRuntime (runtime identity 绑定, 防配置漂移/MITM)
├─ 插件系统 = MCP 服务器:
│    config.toml [[plugins]] → PluginEntry (176B) → plugin-packages.json 状态 (120B/条)
│    plugin.Start → newTransport (stdio/sse/http/streamable-http)
│    └─ stdio: newStdioTransport → CommandContext → proc.startTracked → (*Cmd).Start → CreateProcess
│         └─ proc.assignJob (Job Object 隔离) + proc.KillTree 清理
├─ 崩溃自愈: runWithCrashCapture.func1 → gorecover → reasonixHomeDir → debug.Stack
│    → CapturePanic → REASONIX_HOME/cli-crash-reports/%020d-%d-%s.json → gopanic → os.Exit(1)
└─ DeepSeek 协议: 模型前缀匹配 + /beta/chat/completions + 旧协议自动迁移 + prefix-cache(服务端)
```

---

## 7. 工具与产物清单

| 文件 | 说明 |
|---|---|
| `F:\DEEPCODE\scripts\reasonix_symbols.txt` | 59,145 函数符号表（地址→符号，本阶段定性依据） |
| `F:\DEEPCODE\scripts\REASONIX_深度逆向分析报告.md` | 第一阶段报告 |
| `F:\DEEPCODE\scripts\REASONIX_第二阶段深度分析报告.md` | 第二阶段报告 |
| `F:\DEEPCODE\scripts\REASONIX_第三阶段深度分析报告.md` | 本报告 |
| `/tmp/stdio_strs.py` 等 | 字符串解码工具（VA→文件偏移映射） |

---

*生成于 2026-08-10 · F:\DEEPCODE\scripts*
