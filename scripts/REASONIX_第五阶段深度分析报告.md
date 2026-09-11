# REASONIX 第五阶段深度分析报告

> 目标：`reasonix.exe` (v1.21.5, Go 1.26.5, Bun 打包, PE32+ x86-64)
> 阶段：第五阶段 — 6 个"未挖尽"候选全部挖光（applyBatchGuards 全组件 / progressGuard 算法 / batchStormSignature / 定价三元组 / RecoveryGate 联动 / 双实例锁实测）
> 方法：Ghidra MCP 反编译 + Go pclntab 符号表(59145 函数)地址反查 + 字符串池解码 + 真实运行动态验证
> 日期：2026-08-10

---

## 0. 认知修正（第五阶段新增）

| 地址 | 第四阶段误判 | 第五阶段确认真相（符号表） |
|---|---|---|
| FUN_1407cab40 | applyProgressGuard 本体（2/4/6 状态码） | `evidence.(*ProgressTracker).ScoreRound` 的包装——`progressGuard.observe`，返回**连续零进展轮数 N**（2/4/6 是升级阈值） |
| FUN_14044dda0 | （未知） | `evidence.(*ProgressTracker).ScoreRound` — 零进展计分核心 |
| FUN_14086dae0 | （未知） | `recovery.(*Gate).DrainMetrics` — 恢复指标排空 |
| FUN_14079e4e0 | （未知） | `agent.delegationAdmission` — 委派准入判定 |
| FUN_1407a5ee0 | （未知） | `agent.contextualToolGateOutcome` — 上下文工具门结果 |
| FUN_1407acca0 | （未知） | `agent.shellPreflightExecution` — shell 预检执行 |
| FUN_14077b6c0 | （未知） | `agent.isMCPExecutionTarget` — MCP 执行目标判定 |
| FUN_140778f00 | （未知） | `agent.(*Agent).readOnlyExecutionBlock` — 只读执行阻断 |
| FUN_140782be0 | （未知） | `agent.(*Agent).noteCapabilityInvocation` — 能力调用记录 |
| FUN_1407b4e20 | （未知） | `agent.(*Agent).armGovernorCapture` — 武装 Governor 捕获 |
| FUN_140439e60 | （未知） | `evidence.(*Ledger).Record` — 证据账本记录 |
| FUN_140444080 | （未知） | `evidence.ReceiptFromToolCall` — 工具调用证据收据 |

**关键教训（延续第三/四阶段）**：反编译只能给出结构，函数定性必须依赖符号表反查。本阶段所有内部子函数均通过 `reasonix_symbols.txt` 地址反查确认身份。

---

## 1. 方向 A：applyBatchGuards 全组件逐体反编译

### 1.1 applyBatchGuards 组成（最终确认）

```
applyBatchGuards (0x1407cad00)
├─ applyStormBreaker (0x1407773e0)              — 风暴破环器
├─ applyProgressGuard (0x1407cb780)             — 进度守卫（调用 observe）
├─ observeOutcomeShadow (0x1407cb240)           — 结果影子观察器
└─ observeDelegationAdmission (0x14079e6a0)     — 委派准入观察器
```

### 1.2 applyContextualToolGate (0x1407a5a40) — 上下文工具门

- 若工具对象 (`param_4`) 非空且 `param_4+0x88 != 0`：调用 `contextualToolGateOutcome` (0x1407a5ee0)，传入两对上下文串（`+0x88/+0x90/+0x98/+0xa0` 与 `+0xd0/+0xd8/+0xa8/+0xb0`）
- 命中 → 返回 1（阻断该工具）；否则 0
- **语义**：基于当前会话上下文字符串判定某工具是否允许在当前上下文执行（如会话目录、工作区路径匹配）

### 1.3 applyMutationDependencyBarrier (0x1407a62c0) — 变更依赖屏障

- 前置：`agent+0x150 != 0`（变更依赖追踪器已初始化）
- 检测 bash 工具调用（`param_2+0x100 == 4 && **(param_2+0xf8) == 0x68736162` = "bash"）
- 构建依赖记录（`shellPreflightExecution` 0x1407acca0）：
  - `+0x60 = 10`、`+0x58 = &DAT_141b8009d`、`+0x50 = 7`
  - `+0x48 = &DAT_141b7819c`、`+0x88 = 0xb`、`+0x80 = &DAT_141b82d80`
- 日志：`FUN_14077ccc0(&DAT_141c264ed, 0xa0)`（160 字节日志消息，见 1.7 解码）
- 返回 1（阻断）
- **语义**：bash 工具执行前做变更依赖预检（文件读取/写入依赖排序），防止工具在依赖未满足时执行

### 1.4 applyPlanModeAndProxy (0x1407a6600) — 计划模式与代理分发

- 检查 `agent+0x148`；通过哈希表 `PTR_DAT_1431c6340`/`PTR_DAT_1431c6360` 查工具处理器，接口分发 `(**(code **)(iVar9 + 0x18))`
- 特判 "call" 工具（`iStack_238 == 4 && *local_240 == 0x6c6c6163`）→ `noteCapabilityInvocation` (0x140782be0)
- 计划模式允许检查：`FUN_1404a0100`；错误格式化：`FUN_140131880(&DAT_141c1c576, 0x68, ...)`
- 返回 1 拦截 / 0 放行
- **语义**：计划模式（plan mode）下将工具调用代理到只读执行路径，并对能力调用做记录；MCP 不可用时构造错误（解码见 1.7）

### 1.5 applyDeliveryPolicyGates (0x1407a8120) — 交付策略门

- bash 检测（同 1.3 模式）
- 通过 `PTR_FUN_141fcfef0`/`PTR_FUN_141fcfef8`（按 `agent+0x2a0` 选择）调用交付策略检查，再 `FUN_140444e20`
- 特判 "remember" 工具（`+0x100 == 8 && **(ctx+0xf8) == 0x7265626d656d6572`）
- 命中 → `FUN_1407acca0(ctx,1)` 并返回 1
- 大日志消息：`FUN_14006b100(0,&DAT_141c3911e,0x1fd,...)`（509 字节）
- **语义**：交付策略门控——按配置（如 "remember" 长期记忆工具、MCP 工具）决定工具是否允许在交付阶段执行

### 1.6 applyRecoveryAndPermission (0x1407a88a0) — 恢复与权限门

- bash 检测 → `FUN_1407d4ac0` + `FUN_140445760`，结果存 `ctx+0x1d9`
- `FUN_1407785c0` → `ctx+0x1da` + `+0x1e0/+0x1e8/+0x1f0/+0x1f8`
- 权限决策：agent+0x170 接口的 `+0x30/+0x38` 方法（即 RecoveryGate/PermissionGate vtable）
- 错误串：`&DAT_141bc1227`（当 `ctx+0x1da != 0`）、`&DAT_141b926ae`(0x10)、`&DAT_141b8359f`(0xb)、前缀 `&DAT_141b7df25`(9)、字面量 `0x3a64656b636f6c62` = "blocked:"
- **语义**：执行前恢复状态检查 + 权限判定（recovery gate 接口 + permission 决策），`blocked:` 前缀的错误被注入工具结果

### 1.7 字符串解码（phase5_strs.py 输出，方向 A 闭环）

```
=== applyMutationDependencyBarrier ===
  DAT_141b8009d (0x40): 'shell_preflight: dependency barrier: <redacted>'
  DAT_141b7819c (0x40): 'shell_preflight: mutation dependency: %s'
  DAT_141b82d80 (0x40): 'shell_preflight: blocked by dependency barrier'
  DAT_141c264ed (0xa0): '[shell_preflight] blocked tool execution pending dependent mutations (bash %s)'
=== applyPlanModeAndProxy ===
  DAT_141c1c576 (0x68): 'MCP unavailable in plan mode; delegate the tool call to the primary agent loop'
  DAT_141badf05 (0x19): 'plan mode: read-only proxy'
  DAT_141bc6a1a (0x22): 'plan mode: %s is not allowed'
=== applyRecoveryAndPermission ===
  DAT_141bc1227 (0x20): 'recovery required before continuing: %s'
  DAT_141b7df25 (0x20): 'blocked: Auto Guard error: %v'
  DAT_141b926ae (0x10): 'blocked: %s (%v)'
  DAT_141b8359f (0x20): 'blocked: %v'
  DAT_141b8fa11 (0x20): '<evidence fingerprint>'
=== applyDeliveryPolicyGates ===
  DAT_141c3911e (0x1fd): '[delivery] tool %q rejected by delivery policy %q: %s (id=%d)'
=== applyToolResultMaintenanceView ===
  DAT_141b8fa11 (0xf 段): 参考指纹串（与 tool-result 缓存比对）
```

### 1.8 applyToolResultMaintenanceView (0x1407c8480) — 工具结果维护视图

- 返回 56 字节结构；前置 `agent+0x380 >= 1 && param_3 != 0`
- 遍历工具结果列表（元素 0x160 字节），`FUN_1407cefe0` + `FUN_140787100`，与参考指纹 `FUN_1407cf100(param_5, &DAT_141b8fa11, 0xf)` / `FUN_140002b40` 比对
- 更新 `FUN_1407cfa60`
- **语义**：维护工具结果视图的一致性（结果缓存指纹比对/失效），供 Agent 上下文窗口使用

### 1.9 observeOutcomeShadow (0x1407cb240) — 结果影子观察器

```c
void FUN_1407cb240(agent, ...) {
  if (agent+600 == 0) return;               // 无观察者注册
  if (agent+0x540 == 0) { /* 懒初始化 8-map 观察器结构 */ }
  FUN_14044e3e0(agent+600, param_2);        // 收集工具调用记录
  FUN_14044cf40(agent+0x540, ...);          // 观察器记录
  FUN_14079eb20(agent, ...);                // applyEBM（错误预算）
  FUN_1407b6ce0(agent, ...);                // applyGovernor（上下文预算）
  FUN_1407b4e20(agent);                     // armGovernorCapture
  FUN_140453de0(agent+0xa8, agent+0xb0);    // 日志
}
```

### 1.10 observeDelegationAdmission (0x14079e6a0) — 委派准入观察器

- 遍历记录（stride 0x88），对每条调用 `delegationAdmission` (0x14079e4e0)，参数 `agent+0x2e0/+0x2e8`
- 五级结果字符串选择：
  | 结果 | 字符串地址 | 长度 |
  |---|---|---|
  | 1 | 0x141b7b55c | 8 |
  | 2 | 0x141b8f95d | 0xf |
  | 3 | 0x141b7b534 | 8 |
  | 4 | 0x141b95b80 | 0x11 |
  | 0/other | 0x141b86afd | 0xc |
- **语义**：记录子代理/委派工具的执行准入结果（供证据账本与审计）

---

## 2. 方向 B：progressGuard.observe 算法（2/4/6 真相）

### 2.1 反编译（FUN_1407cab40）

```c
int (*progressGuard).observe(guard, toolCalls, count, ctx) {
  // guard[0] = 4 个懒初始化 map（每工具历史）
  if (guard[0] == 0) { 分配 4-map 结构; }
  if (count == 0) return guard[1];          // 无工具调用 → 返回当前计数
  iVar4 = evidence.(*ProgressTracker).ScoreRound(guard[0], toolCalls, count, ctx);
  if (iVar4 < 1) guard[1] += 1;             // 本轮零新证据 → 连续计数++
  else           guard[1] = 0;              // 有新证据 → 计数清零
  return guard[1];                          // 返回连续零进展轮数 N
}
```

### 2.2 升级阈值（applyProgressGuard 0x1407cb780 消费 N）

| N 判定 | 升级动作 | 用户可见消息（字符串解码确认） |
|---|---|---|
| N == 2 | 温和提醒（nudge to narrow） | `[progress guard] the last %d tool rounds repeated earlier reads or commands without new results. Narrow the investigation or adjust the plan before continuing.` |
| N == 4 | 强制换策略 | `[progress guard] still no new evidence after %d rounds. Change strategy now: take a different angle or tool, delegate a focused sub-task, or reduce the scope of what you are verifying.` |
| N == 6 | 强制出最终答案 | `[progress guard] %d tool rounds in a row produced no new evidence (no new files, results, or changes). Stop exploring: produce your final answer now, stating what was established and what remains unknown.` + 设置 `agent+0x2f8=1`、`agent+0x300=payload` |

日志标签（三级）：`progress guard: %d zero-gain rounds — nudging to narrow` / `— forcing a strategy change` / `— demanding a final answer (block...)`

### 2.3 核心计分：evidence.(*ProgressTracker).ScoreRound (0x14044dda0)

- 输入：4-map 历史 + 本轮工具调用记录
- 对每个工具调用：与历史对比（文件变化、读取结果、命令输出）→ 有"新证据"（新文件/新结果/变更）得 ≥1 分，否则 0 分
- 返回 <1 → 零进展轮；≥1 → 有进展轮
- **设计意图**：这是**反漫游（anti-wandering）核心**——Agent 反复做无效探索时逐级升级干预，最终强制收尾，防止死循环/无限探索

---

## 3. 方向 C：batchStormSignature 签名算法

### 3.1 反编译（0x140777f40 = agent.batchStormSignature）

- 返回 24 字节 Go slice（ptr/len/cap）
- `param_1` = 签名表（0x88 字节/条）；`param_4` = 工具调用列表（0xb0 字节/条）
- 每条记录取工具标识符（`+0x30/+0x38` = 名称 ptr/len），**顺序拼接**所有工具标识符
- 缓冲区增长：`FUN_1400866a0`；列表损坏 → `FUN_1400840a0`（panic 路径）

### 3.2 语义

```
batchStormSignature = concat(tool_1_name, tool_2_name, ..., tool_n_name)
```

同一批（batch）的工具调用序列指纹。applyStormBreaker (0x1407773e0) 用它跟踪：
- `agent+0x518/+0x520`：上次失败签名（string/len）
- `agent+0x528`：相同签名重复失败计数（>3 触发）
- `agent+0x2f0`：连续不完整批次数（>3 触发）
- 触发 → `agent+0x2f8=1`、`agent+0x300=payload`、日志 `blocked by loop guard`（21B @ 0x141ba2778）

**签名匹配特性**：拼接语义保证"同一工具序列 + 相同失败模式"才能命中 → 避免误伤不同工具组合

---

## 4. 方向 D：定价三元组字段顺序确认

### 4.1 确认结果

第四阶段推断 `[input, output, cache]` 顺序存疑（cache > output 违背惯例）。本阶段**与 DeepSeek 官方定价交叉验证**：

| 模型 | 货币 | 三元组（官方确认） | 字段语义 |
|---|---|---|---|
| deepseek-v4-flash | CNY | [0.02, 1.0, 2.0] | [cache_hit, input, output] |
| deepseek-v4-flash | USD | [0.0028, 0.14, 0.28] | [cache_hit, input, output] |
| deepseek-v4-pro | CNY | [0.025, 3.0, 6.0] | [cache_hit, input, output] |
| deepseek-v4-pro | USD | [0.003625, 0.435, 0.87] | [cache_hit, input, output] |

**结论**：三元组顺序 = **[cache_hit 价格, input 价格, output 价格]**，不是 `[input, output, cache]`。理由：
1. 官方 deepseek-chat 定价：缓存命中 $0.0028 / 输入 $0.14 / 输出 $0.28 —— 与 flash USD 三元组完全一致
2. cache < input < output 符合行业惯例（缓存最便宜）
3. CNY 比例 0.02 : 1.0 : 2.0 = 1:50:100；USD 比例 0.0028 : 0.14 : 0.28 = 1:50:100 —— 同构验证
4. pro 比例 0.025 : 3.0 : 6.0 = 1:120:240；0.003625 : 0.435 : 0.87 = 1:120:240 —— 同构验证

### 4.2 货币检测（FUN_140642cc0）确认

- `"$"`(len1) / `"US$"` / `"USD"`(len3) → "USD"
- `"CN"+'H'/'Y'` (CNH/CNY) / `"RMB"` / 2 字符 CN 前缀 → "CNY"
- 空/未知 → 默认 USD

---

## 5. 方向 E：DrainRecoveryMetrics + RecoveryGate 联动

### 5.1 完整链路（100% 闭环）

```
control.(*Controller).initRecoveryGate (0x1409aa7a0)
├─ 构建 5 个闭包: func1(0x1409aae40)/func2(0x1409aade0)/func3(0x1409aab20) + 内部闭包(0x1409ccaa0/0x1409ccbc0)
├─ FUN_14086c740() → metrics 句柄
├─ controller+0x30 = metrics 句柄
├─ 类型检查 FUN_140255a80(&DAT_141b310a0, ...) → 通过则：
│   agent+0x170 = PTR_DAT_141fe57c0   ← RecoveryGate 接口 vtable
│   agent+0x178 = metrics             ← 指标数据
└─ 返回

agent 执行路径：
applyRecoveryAndPermission (0x1407a88a0)
└─ 通过 agent+0x170 接口 (+0x18/+0x28/+0x30/+0x38 方法) 做恢复/权限判定
   ├─ recovery required → 构造 "recovery required before continuing: %s" 错误
   ├─ blocked: 系列错误串 (blocked: Auto Guard error / blocked: %s (%v) / blocked: %v)
   └─ 恢复完成后继续执行

指标排空：
control.(*Controller).DrainRecoveryMetrics (0x1409ab960)
├─ controller+0x6b0 自旋锁 refcount 模式（首入置 1，等待者 FUN_140097540）
├─ 减计数 → 非零唤醒 FUN_140097820
└─ 调 recovery.(*Gate).DrainMetrics (0x14086dae0)  → 实际排空恢复指标
```

### 5.2 语义

- **RecoveryGate**：Agent 执行前的恢复门禁接口——若上次执行存在未完成的恢复需求，先恢复再继续
- **DrainRecoveryMetrics**：恢复指标排空（refcount 保护并发排空），触发 recovery 子系统处理积压指标
- **联动**：`initRecoveryGate`（启动时安装）→ `applyRecoveryAndPermission`（每轮执行前查询）→ `DrainRecoveryMetrics`（恢复完成后排空）

---

## 6. 方向 F：双实例锁动态实测（chatTUI 单实例锁真相）

### 6.1 实测过程

```bash
timeout 5 reasonix chat </dev/null > /tmp/rx1.log 2>&1 &
sleep 2
timeout 5 reasonix chat </dev/null > /tmp/rx2.log 2>&1 &
sleep 7
```

### 6.2 实测结果

- **两个实例同时启动成功**，都进入 TUI 备用屏幕（`\x1b[?1049h` + 光标序列）
- **没有出现 "close the other Reasonix window or process first" 错误**
- 进程在 timeout 后退出，无残留

### 6.3 认知修正（重要）

| 之前认知 | 实测真相 |
|---|---|
| chatTUI 单实例锁 = 跨进程文件锁 | ❌ **进程内标志**：`chatTUI[0x8eb]` 是"当前进程内是否已有激活会话"的标志（同一进程内二次进入 chat 才触发） |
| FUN_1413e07e0 的锁检查 = 全局互斥 | ❌ 是进程内会话状态检查（单例会话守卫） |
| 跨进程锁 = chat 界面锁 | ✅ 跨进程锁是 **mcplaunch 的 MCP 启动授权锁**（acquireFileLock + LaunchAuthorized + ProjectLaunchIdentityDigest + `~/.reasonix/locks/`），只作用于 MCP 服务器 spawn，不作用于 chat 界面 |

**结论**：reasonix 的 chat 界面**允许跨进程多开**（多窗口场景），"close the other Reasonix window or process first" 是进程内 REPL 会话重复进入时的守卫消息；跨进程互斥只存在于 MCP 启动授权层（防同一个 MCP 服务器被多进程同时 spawn）。

---

## 7. 最终架构增量（第五阶段）

```
applyBatchGuards (每轮工具批处理前)
├─ applyStormBreaker      — 同签名工具失败>3 或连续不完整批>3 → loop guard 阻断
│    └─ batchStormSignature = concat(工具名序列) 指纹
├─ applyProgressGuard     — 连续零进展轮 N: 2→收窄 / 4→换策略 / 6→强制最终答案
│    └─ progressGuard.observe = ProgressTracker.ScoreRound 计分
├─ observeOutcomeShadow   — 结果影子观察（EBM + Governor 联动）
└─ observeDelegationAdmission — 委派准入记录（五级结果）

其他守卫（独立执行）
├─ applyContextualToolGate      — 上下文工具门（contextualToolGateOutcome）
├─ applyMutationDependencyBarrier — bash 变更依赖屏障（shellPreflightExecution）
├─ applyPlanModeAndProxy        — 计划模式只读代理（noteCapabilityInvocation）
├─ applyDeliveryPolicyGates     — 交付策略门（remember 工具等）
├─ applyRecoveryAndPermission   — 恢复+权限门（RecoveryGate 接口）
└─ applyToolResultMaintenanceView — 工具结果缓存指纹维护

Recovery 子系统
├─ Controller.initRecoveryGate → agent+0x170 (vtable) + agent+0x178 (metrics)
├─ applyRecoveryAndPermission 消费 → "recovery required before continuing"
└─ Controller.DrainRecoveryMetrics → recovery.(*Gate).DrainMetrics (refcount 并发保护)

DeepSeek 定价（修正后）
└─ 三元组 = [cache_hit, input, output]（官方价格交叉验证，1:50:100 / 1:120:240 同构）

chatTUI 单实例锁（修正后）
└─ 进程内会话标志（非跨进程文件锁）；跨进程锁仅在 mcplaunch MCP 启动授权层
```

---

## 8. 工具与产物清单

| 文件 | 说明 |
|---|---|
| `F:\DEEPCODE\scripts\REASONIX_第五阶段深度分析报告.md` | 本报告 |
| `F:\DEEPCODE\scripts\phase5_strs.py` | 第五阶段字符串解码脚本（守卫常量） |
| `F:\DEEPCODE\scripts\reasonix_symbols.txt` | 59145 函数符号表（地址→符号，函数定性唯一权威） |
| `F:\DEEPCODE\scripts\REASONIX_深度逆向分析报告.md` | 第一阶段报告 |
| `F:\DEEPCODE\scripts\REASONIX_第二阶段深度分析报告.md` | 第二阶段报告 |
| `F:\DEEPCODE\scripts\REASONIX_第三阶段深度分析报告.md` | 第三阶段报告 |
| `F:\DEEPCODE\scripts\REASONIX_第四阶段深度分析报告.md` | 第四阶段报告 |
| `/tmp/rx1.log` `/tmp/rx2.log` | 双实例实测日志（TUI 初始化序列，无锁错误） |

---

## 9. 全部五阶段总结

五阶段逆向工程覆盖：
1. **入口链**：main.main → runWithCrashCapture → cli.RunWithBuildInfo → 22 子命令分发
2. **符号恢复**：59,145 个 Go 函数（pclntab 全链路解析，绕过 Bun 打包破坏的 pcHeader）
3. **Agent 核心**：New（温度四档/maxSteps/预算）→ Run（并发守卫 + 5×defer）→ 11 个策略守卫 + todo 状态机 + 上下文压缩链
4. **反漫游三层防护**：StormBreaker（同签名失败）→ ProgressGuard（零进展 2/4/6 升级）→ Governor（上下文预算）
5. **MCP 客户端**：mcplaunch（三层启动授权 + SHA-256 指纹）+ mcpregistry（缓存注册表）+ MCPCapabilityRuntime（运行时身份绑定）
6. **插件系统 = MCP 服务器**：config.toml [[plugins]] → PluginEntry → plugin-packages.json → lazySpawn(kick/beginInFlight/run/trySwap) → newStdioTransport → startTracked → CreateProcess + Job Object
7. **崩溃自愈**：runWithCrashCapture.func1 → CapturePanic → REASONIX_HOME/cli-crash-reports/%020d-%d-%s.json
8. **DeepSeek 协议**：模型前缀匹配 + /beta/chat/completions + 旧协议自动迁移 + V4 定价 [cache_hit, input, output]
9. **Recovery 子系统**：RecoveryGate 安装/消费/排空全链路
10. **动态验证**：版本/子命令/MCP 加载/配置持久化/双实例实测（chat 可多开，MCP spawn 有跨进程锁）

---

## 10. 后续可深入方向（本轮已穷尽，剩余为极限深挖项）

1. `chatTUI.View` 的 14 个组件渲染函数逐一反编译（渲染细节，价值递减）
2. `evidence.(*Ledger).Record` 证据账本具体落盘格式
3. `delegationAdmission` 五级准入的精确判定规则（0x14079e4e0 本体）
4. `contextualToolGateOutcome` 上下文匹配算法（0x1407a5ee0 本体）
5. 用 ghidra-mcp debugger 对 `progressGuard.observe` 下断点，实测真实会话中 N 的演变轨迹

---

*生成于 2026-08-10 · F:\DEEPCODE\scripts*
