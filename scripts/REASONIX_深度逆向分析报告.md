# REASONIX 深度逆向分析报告

> 目标：`reasonix.exe` v1.21.5（npm 包 `reasonix` 的 Windows 预编译二进制）
> 分析日期：2026-08-10
> 工具链：Ghidra MCP (ghidra-mcp) + Go pclntab 符号恢复 + deepcode-decompiler
> 分析目标：全面还原该二进制的前端架构、启动流程、崩溃处理、CLI 命令集与 DeepSeek 协议逻辑

---

## 1. 二进制概况

| 项目 | 值 |
|---|---|
| 文件 | `C:\Users\raymo\AppData\Roaming\npm\node_modules\reasonix\node_modules\@reasonix\cli-win32-x64\bin\reasonix.exe` |
| 大小 | 53,696,512 字节 (≈51.2 MB) |
| 格式 | PE32+ x86-64，控制台子系统 (CUI) |
| 编译器 | **Go 1.26.5**（`go1.26.5` buildVersion 确认），Go 重写版 "main-v2" |
| 打包方式 | **Bun 打包**（单文件，内嵌 `.bun` 标记、Zig/Rust 运行时字符串），Go 二进制被 Bun 封装 |
| 段布局 | `.text`(0x140001000) / `.rdata`(0x1414c2000) / `.data`(0x143092000) / `.pdata` / `.xdata` / `.idata` / `.reloc` / `.symtab` |
| Ghidra 函数数 | 52,198（符号剥离，`FUN_*` 命名） |
| pclntab 符号数 | **59,145 个函数符号**（从 Go pclntab 完整恢复） |
| 模块信息 | `reasonix` v0.0.0-20260809163207-710f3fb107d0，主包 `reasonix/cmd/reasonix` |
| 依赖亮点 | `charm.land/bubbletea/v2 v2.0.8` + `charm.land/bubbles/v2 v2.1.1`（TUI 框架） |

**关键难点攻克**：Go 1.20+ 的 pcHeader magic 是 `0xFFFFFFF1`（不是早期版本误用的 `0xFFFFFFF0`）；且本二进制被 Bun 打包器修改过，`pcHeader.textStart=0`，真正 text 基址来自 `moduledata.text=0x140001000`。通过"buildVersion 字符串反查指针 → moduledata → ftab → functab → _func → funcnametab"全链路解析，恢复了全部 59,145 个函数符号。

---

## 2. 启动流程与主逻辑入口

### 2.1 入口链

```
main.main (0x141454320)
  └─ main.runWithCrashCapture (0x1414543a0)   ← 崩溃捕获包装器
       ├─ defer main.runWithCrashCapture.func1 (0x141454480)  ← 崩溃处理器
       └─ (**(code **)PTR_PTR_1431be980)()    ← 间接调用主逻辑
            └─ PTR_PTR_1431be980 → 0x141fcf2d0 (.rdata 函数指针表)
                 └─ table[0] = main.init.func1 (0x141454220)  ← 编译器生成的 init 闭包
                      └─ reasonix/internal/cli.RunWithBuildInfo (0x141358d60)  ★ 真正主逻辑入口
```

`main.main` 反编译要点：
- Go runtime 栈增长检查循环（`while(&stack0x00000000 <= *(undefined1 **)(unaff_R14+0x10)) FUN_14008a2e0();`）
- 传入 Go 版本字符串 `"v1.21.5"`（打包进 Go 二进制的版本标识）与 argc/argv 派生参数
- 分支：正常路径 → `runWithCrashCapture`；异常路径 → 不返回的 `FUN_14008c220()`

### 2.2 主逻辑：`cli.RunWithBuildInfo` —— CLI 命令分发中枢

函数签名：`undefined8 RunWithBuildInfo(argv []string, argc, buildInfo)`。核心逻辑：
1. 构造命令名（参数改写：`-p`/`--print` 打印模式标志特殊处理，含追加默认参数）
2. 无参/未知命令 → 打印帮助并返回退出码 2
3. 按**命令名字符串长度 + little-endian 魔数常量**分派到各子命令处理器

---

## 3. CLI 命令全景（22 个子命令）

| 命令 | 处理函数（符号恢复后） | 地址 |
|---|---|---|
| cap | `cli.acpCommand` | 0x141310440 |
| bot | `cli.botCommand` | 0x141313d40 |
| cmp | `cli.mcpCommand` | 0x141390880 |
| run | `cli.runAgent` | 0x14135bda0 |
| task | `cli.taskCommand` | 0x14141d3e0 |
| hook / hooks | `cli.runHookCommand` | 0x141387be0 |
| init | `cli.initHint` | 0x141365860 |
| serve | `cli.runServe` | 0x14135ef40 |
| setup | `cli.setupConfig` | 0x141365060 |
| config | `cli.configCommand` | 0x14136a160 |
| doctor | `cli.doctorCommand` | 0x14137dcc0 |
| plugin | `cli.pluginCommand` | 0x1413b6360 |
| report* | `cli.remoteCommand` | 0x1413c0280 |
| update* | `cli.reportCommand` | 0x1413cc720 |
| review | `cli.reviewCommand` | 0x1413d2aa0 |
| session | `cli.runSessionCommand` | 0x1413e1120 |
| upgrade | `cli.upgradeCommand` | 0x141434620 |
| version | `cli.versionCommand` | 0x14131dba0 |
| subagent | `cli.subagentCommand` | 0x141417c00 |
| compile* | `cli.completionCommand` | 0x1413ff040 |
| doc-config* | `cli.docsManifestCommand` | 0x14137d120 |
| -h / --help / -v / --version | 帮助/版本打印 | — |

> *注：反编译中的字符串槽位与符号存在错位（report→remoteCommand、update→reportCommand、compile→completionCommand、doc-config→docsManifestCommand），符号表为准——这些槽位的真实命令字符串可能为 "remote"/"report"（均为 6 字符）、"completion"（10 字符）、"docs-manifest"（13 字符）。

### 3.1 默认执行器表 (0x141fcf428)

双前端架构：**chatREPL（交互式 REPL）+ chatTUI（BubbleTea 终端 UI）**

| 序号 | 符号 | 地址 |
|---|---|---|
| [0] | `cli.chatREPL` | 0x141361160 |
| [1] | `chatTUI.refreshGitStatus...func3` | 0x14143da40 |
| [2] | `chatTUI.beginClipboardImagePaste...func22` | 0x14143df80 |
| [3] | `chatTUI.autoScrollTick.func3` | 0x14143de40 |
| [4] | `chatTUI.autoScrollTick.func4` | 0x14143de60 |
| [5] | `chatTUI.refreshGitStatus...func27` | 0x14143dfe0 |
| [6] | `chatTUI.elapsedTick.func16` | 0x14143df60 |
| [7] | `chatTUI.elapsedTick.func6` | 0x14143df20 |

闭包命名揭示 TUI 内部机制：git 状态刷新、剪贴板图片粘贴、自动滚动 tick、elapsed tick（全部为 BubbleTea goroutine/tick 处理器）。

---

## 4. 崩溃报告机制（crashreport 子系统）

### 4.1 完整链路

```
main.runWithCrashCapture.func1 (deferred 崩溃处理器)
  ├─ runtime.gorecover()             ← 尝试恢复 panic
  ├─ config.reasonixHomeDir()        ← 定位 home 目录
  ├─ debug.Stack()                   ← 收集堆栈
  ├─ crashreport.CapturePanic()      ← 序列化崩溃上下文
  ├─ runtime.gopanic()               ← 重新抛出（无法恢复时）
  └─ os.Exit(1)                      ← 不返回
```

### 4.2 落盘位置与格式（已 100% 确认）

| 项目 | 值 |
|---|---|
| 目录 | `filepath.Join(REASONIX_HOME, "cli-crash-reports")` |
| 目录权限 | `MkdirAll(..., 0700)` |
| 文件名模板 | `"%020d-%d-%s.json"` |
| 时间戳格式 | `time.RFC3339Nano`（`"2006-01-02T15:04:05.999999999Z07:00"`） |
| %s 部分 | 16 字节随机数 → hex 编码（`"0123456789abcdef"` 查表） |
| 写盘 | 互斥锁保护（defer 解锁）+ `FUN_1404e9240`（flags 0x180） |
| 平台路径 | Windows: `%APPDATA%\reasonix\cli-crash-reports` 或 `%USERPROFILE%\.reasonix\...`；非 Windows: `$XDG_CONFIG_HOME/reasonix/...` 或 `$HOME/.config/reasonix/...` |

### 4.3 `config.reasonixHomeDir` 解析逻辑

1. 优先读环境变量 **`REASONIX_HOME`**（13 字符，已确认）
2. 未设置时按平台分派：
   - Windows：`os.UserConfigDir` → `os.UserHomeDir` 兜底
   - 非 Windows：`os.UserHomeDir` → `os.UserConfigDir` 兜底
3. 经 `filepath.Join` 组合

---

## 5. DeepSeek Provider 协议逻辑（重点还原）

### 5.1 函数清单（20+ 个，符号全部确认）

| 函数 | 地址 | 用途 |
|---|---|---|
| `provider/openai.expectsDeepSeekToolCallReasoning` | 0x1405a3fa0 | 判断是否期望 DeepSeek tool-call reasoning |
| `provider/openai.deepSeekPrefixChatURL` | 0x1405a1700 | baseURL 规范化 → 官方 endpoint |
| `config.MigrateLegacyDeepSeekProtocolUserConfig` | 0x1405f2f20 | 旧协议迁移入口 |
| `config.CanUpgradeDeepSeekProviderProtocol` | 0x1405f2f80 | 升级判定门控 |
| `config.editLegacyDeepSeekProtocolFile` | 0x1405f32a0 | 配置文件编辑 |
| `config.rewriteLegacyDeepSeekProtocol` | 0x1405f3620 | 核心重写（字段编辑） |
| `config.isUnmodifiedLegacyDeepSeekProvider` | 0x1405f4080 | 判定是否未修改旧 provider |
| `config.deepSeekOpenAIEndpointPath` | 0x1405f4660 | 端点路径规范化 |
| `config.rewriteDeepSeekProviderBlock` | 0x1405f6ae0 | 重写 provider 块 |
| `config.backfillDeepSeekPro` | 0x140615120 | 回填 Pro 定价 |
| `config.backfillDeepSeekOfficialPrices` | 0x140615660 | 回填官方定价 |
| `config.backfillDeepSeekOfficialEndpointDefaults` | 0x140615aa0 | 回填官方端点默认值 |
| `config.normalizeOfficialDeepSeekModels` | 0x14061ee40 | 模型名规范化 |
| `config.backfillDeepSeekAnthropicCapabilities` | 0x14061f0e0 | 回填 Anthropic 兼容能力 |
| `config.ensureDeepSeekOfficialProvider` | 0x140622920 | 确保官方 provider 存在 |
| `config.DeepSeekV4PricesForCurrency` | 0x1406425c0 | 按币种取 V4 定价 |
| `config.(*Config).ApplyDeepSeekOfficialDefaultPricing` | 0x140642fc0 | 应用官方默认定价 |
| `provider/anthropic.deepSeekAnthropicUsesProEffortMapping` | 0x141443a00 | Pro effort 映射 |
| `provider/anthropic.normalizeDeepSeekAnthropicEffort` | 0x141443aa0 | effort 规范化 |

### 5.2 `expectsDeepSeekToolCallReasoning` —— 模型匹配规则

```
param_3 以 "enabled" 开头                       → 返回 1
否则检查 model 名是否命中以下前缀（任一命中返回 1）：
  "deepseek-v4-flash"   (17B)
  "deepseek-v4-pro"     (15B)
  "deepseek-v3.2"       (13B)
  "deepseek-reasoner/"  (17B)
  "deepseek-r1"         (11B)
均未命中 → 返回 0
```

### 5.3 `deepSeekPrefixChatURL` —— 官方 endpoint 规范化

```
baseURL 含 "deepseek.com" (12B)？
  → 是：从 config 取对象，写入
      +0x38 = "/beta/chat/completions" (0x141ba4968, 22 字节)
      +0x40 = 22 (Go string 头 len)
      → 返回规范化结果（官方 API 路径）
  → 否：返回空
```

### 5.4 旧协议 → 新协议自动迁移

**判定条件（CanUpgradeDeepSeekProviderProtocol）**：provider 满足以下全部条件才允许升级：
- type == `"openai"`（旧 OpenAI 兼容协议）
- endpoint 为空 或 `"/v1"`（旧默认路径）
- provider ID 为 `"deepseek"`（8B）/ `"deepseek-pro"`（12B）/ `"deepseek-flash"`（14B）
- 全部模型为 `"deepseek-v4-pro"` / `"deepseek-v4-flash"`（V4 系）

**迁移链**：
```
MigrateLegacyDeepSeekProtocolUserConfig (0x1405f2f20)
  └─ loadUserConfig → editLegacyDeepSeekProtocolFile(config, 0, 0, 1)
       └─ read/parse → rewriteLegacyDeepSeekProtocol (0x1405f3620) ← 实际字段编辑
            └─ serialize → 写回磁盘（flags & 0x1ff，与崩溃报告同一写文件函数）
```

**深度语义**：reasonix 正在把用户旧配置中"OpenAI 兼容协议（/v1、openai type）"的 DeepSeek provider 自动改写为"官方原生协议（/beta/chat/completions）"，并回填 V4 官方定价/端点/Anthropic 兼容能力——完全对齐 DeepSeek 官方 API 的新形态（含 prefix-cache 优化设计）。

---

## 6. 架构总结

### 6.1 技术栈特征（Go 重写版）

- **多 Provider AI 网关**：DeepSeek（主）、MiniMax、LongCat、Kimi、GLM、OpenCode、KiloCode、Weixin Bot（字符串扫描发现），OpenAI Responses API 流式调用（`responses.streamedCall`）
- **双前端**：chatREPL（传统交互式）+ chatTUI（BubbleTea v2 现代终端 UI，含 git 状态/剪贴板图片/自动滚动/计时 tick）
- **MCP 集成**：467 个 mcp 相关符号（305 个 MCP + 164 个 mcp），`mcp__` 工具命名前缀
- **插件系统**：661 个 plugin 相关符号（`config.PluginEntry`），TOML 配置 `[[plugins]]`
- **会话系统**：`session`/`upgrade`/`subagent` 子命令，Go 版本标识 `v1.21.5`
- **崩溃自愈**：全链路崩溃捕获 + 标准化 crash report 落盘

### 6.2 数据流

```
reasonix.exe (Bun 封装的 Go 1.26.5 二进制)
  │
  ├─ cli.RunWithBuildInfo → 命令分发器 (22 子命令)
  │     ├─ chat/code → chatREPL / chatTUI (BubbleTea)
  │     ├─ run/task/subagent → Agent 执行
  │     ├─ doctor/config/setup/plugin/hook → 配置与扩展
  │     └─ report/session/upgrade/version → 运维
  │
  ├─ provider 层 → DeepSeek 官方协议 (/beta/chat/completions)
  │     └─ 旧配置自动迁移 (openai/v1 → 官方原生协议)
  │
  └─ crashreport 层 → REASONIX_HOME/cli-crash-reports/*.json
```

---

## 7. 工具与产物清单

| 产物 | 路径 | 说明 |
|---|---|---|
| 符号表 (文本) | `F:\DEEPCODE\scripts\reasonix_symbols.txt` | 59,145 函数，`0x<VA> <符号名>` |
| 符号表 (CSV) | `F:\DEEPCODE\scripts\reasonix_symbols.csv` | name,va,file_offset |
| pclntab 解析脚本 | `F:\DEEPCODE\scripts\extract_reasonix_symbols.py` | 从 moduledata/ftab/functab 恢复符号 |
| 结构分析脚本 | `F:\DEEPCODE\scripts\analyze_reasonix_structure.py` | 包统计 + 关键函数定位 |
| CLI 解析脚本 | `F:\DEEPCODE\scripts\resolve_cli_handlers.py` | 子命令处理器符号解析 |
| 崩溃链反查脚本 | `F:\DEEPCODE\scripts\lookup_crash_functions.py` | 地址→符号反查 |
| Ghidra 重命名 | reasonix.exe 程序内 | 22 个 cli.* 函数 + 核心链函数已重命名 |

## 8. 后续可深入方向

1. **反编译各子命令处理器**（`runAgent`/`chatREPL`/`mcpCommand` 等）细化 Agent 循环逻辑
2. **深挖 MCP 客户端**（467 个符号）——stdio/HTTP 传输、工具注册协议
3. **插件系统**（661 个符号）——`config.PluginEntry` 的 TOML 加载与执行链
4. **prefix-cache 实现**——DeepSeek 前缀缓存优化的具体 batching 逻辑
5. **动态调试**——用 ghidra-mcp debugger 附加运行中的 reasonix，验证崩溃报告落盘
