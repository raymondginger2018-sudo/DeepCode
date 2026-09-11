# REASONIX 第四阶段深度分析报告

> 目标：`reasonix.exe` (v1.21.5, Go 1.26.5, Bun 打包, PE32+ x86-64)
> 阶段：第四阶段 — Agent 策略守卫层 / DeepSeek 定价回填 / lazySpawn 生命周期 / chatTUI 消息处理
> 方法：Ghidra MCP 反编译 + Go pclntab 符号表(59145 函数) + 字符串池解码交叉验证
> 生成：2026-08-10

---

## 0. 重要认知修正（延续第三阶段方法论）

本阶段继续坚持"函数定性必须以符号表为准"原则，又修正了一批关键误判：

| 地址 | 之前误判 | 第四阶段符号表确认真相 |
|---|---|---|
| FUN_1409ab960 | (*Agent).Run 执行循环候选 | `control.(*Controller).DrainRecoveryMetrics`（与执行循环无关） |
| FUN_140777f40 | applyStormBreaker 内部子函数 | `agent.batchStormSignature`（批次风暴签名函数，独立符号） |
| FUN_1407b6ce0 | 未定性 | `agent.(*Agent).applyGovernor`（上下文预算守卫） |
| FUN_14079eb20 | 未定性 | `agent.(*Agent).applyEBM`（错误预算监控） |
| FUN_1407773e0 | 未定性 | `agent.(*Agent).applyStormBreaker`（工具风暴断路器） |
| FUN_1407cb780 | 未定性 | `agent.(*Agent).applyProgressGuard`（零进展守卫） |
| FUN_1407cad00 | 未定性 | `agent.(*Agent).applyBatchGuards`（批次守卫编排器） |

**教训延续**：Ghidra 反编译只给结构，函数身份必须回查 `reasonix_symbols.txt`。

---

## 1. 方向 A：Agent 主循环 — 策略守卫层完整还原

### 1.1 守卫层架构全景

`agent.(*Agent).Run` @ 0x14076bda0 是薄编排壳，真正的执行前策略由 **11 个 apply* 守卫** 构成，每个守卫检查上下文块（param_2 指向 10+ qword 的 turn 上下文）：

```
Run (0x14076bda0)
├─ 并发守卫: LOCK() 递增 agent+0x1a0 + 自旋锁 agent+0x230 单次运行标志
├─ 5×defer 清理链: FUN_14076c440/400/3e0/500/5c0
├─ applyGovernor          (0x1407b6ce0) — 上下文预算守卫
├─ applyEBM               (0x14079eb20) — 错误预算监控
├─ applyStormBreaker      (0x1407773e0) — 工具风暴断路器
├─ applyBatchGuards       (0x1407cad00) — 批次守卫编排
│    ├─ applyStormBreaker (batchStormSignature 参与)
│    ├─ applyProgressGuard (0x1407cb780)
│    ├─ FUN_1407cb240
│    └─ FUN_14079e6a0
├─ applyContextualToolGate (0x1407a5a40)
├─ applyMutationDependencyBarrier (0x1407a62c0)
├─ applyPlanModeAndProxy   (0x1407a6600)
├─ applyDeliveryPolicyGates (0x1407a8120)
├─ applyRecoveryAndPermission (0x1407a88a0)
├─ applyToolResultMaintenanceView (0x1407c8480)
├─ applyProgressGuard      (0x1407cb780)
├─ todo 状态机: advanceCanonicalTodo / rebuildTodoState / recordTodoState / emitTodoState
└─ 上下文压缩链: summarize* / foldToSummary / compactToProjection / installVisibleCompression
```

### 1.2 applyGovernor — 上下文预算守卫 (0x1407b6ce0)

```c
if (local_20 == 0 && byte2(param_2[10]) == 0) {
    bVar6 = 0x5db < *(int *)(agent + 0x568);   // 预算计数器 > 1499 → 上下文满
}
*(param_2 + 0x53) = bVar6;                      // 写"context full"标志
if (agent+0x558 == 0 && param_2+0x53 != 0) {
    agent+0x558 = 1;                            // 触发一次
    if (agent+0x559 == 0) { agent+0x559 = 1;    // 只记一次日志
        // itab 日志调用: (*(agent+0xa8)+0x18)(agent+0xb0, DAT_1420220a8 128B消息)
    }
}
```

**语义**：上下文窗口预算守卫。`agent+0x568` 计数器超过阈值 1499（0x5db）时标记"上下文满"，通过双标志位（+0x558 触发 / +0x559 日志）保证只通知一次，防日志风暴。

### 1.3 applyEBM — 错误预算监控 (0x14079eb20)

```c
if (param_2[8] != 0 && 2 < (int)param_2[9]) {   // 批次有错误且 >2 个
    *(param_2 + 10) = 1;                        // 标记预算耗尽
    if (agent+0x548 == 0 && param_4 != 0 && param_7 != 0) {
        agent+0x548 = 1;                        // EBM 触发标志 (uint16)
        *(param_2 + 0x51) = 1;
        // 构造带类型错误: FUN_14006b1a0(0, ..., &DAT_141b70b4f, 2, &DAT_141c31191, 0x12e)
        // 0x12e = 302 字符错误消息（含位置信息行号302）
    }
}
```

**语义**：错误预算监控器。单批次工具调用错误数 >2 → 标记 EBM 预算耗尽，构造带类型的错误（`reasonix.error` 类型标签 DAT_141b70b4f），防止 agent 在错误泥潭中反复空转。

### 1.4 applyStormBreaker — 工具风暴断路器 (0x1407773e0)

**两级触发条件**：
1. **同一工具签名连续失败 >3 次**：`agent+0x518/+0x520` 记录上次失败工具签名（字符串/长度），`+0x528` 记录重复次数；签名变化时重置为 1，相同则累加；`重复次数 > 3` → 触发。
2. **连续 >3 批不完整工具结果**：`agent+0x2f0` 记录连续未完成批次计数（记录中第 5 个 qword 字节为标志），`>3` 且无失败工具时 → 触发。

触发后：`agent+0x2f8 = 1`、`agent+0x300 = param_8`（肇事载荷），构造错误消息（含 `a batch of %d calls`、`The user answered:`、`permission.decision`、`compaction.complete` 等上下文串），通过 `batchStormSignature` (0x140777f40) 计算签名。

**核心字符串**（解码确认）：
- `blocked by loop guard`（21B @ 0x141ba2778）— 风暴签名标记
- `Change approach: do ...`（@ 0x141c30f38）— 建议改方向
- `been blocked or failed`（22B @ 0x141ba5518）
- `Change approach: if an argumen...`（@ 0x141c2bab2）
- 错误格式串 @ 0x141c2890c (184B)、@ 0x141c181eb (94B)、@ 0x141c2c5d0 (225B)

**语义**：Agent 循环的断路器——识别"同一工具反复失败"或"多批次无完整结果"的循环陷阱，强制中断并建议换策略。

### 1.5 applyProgressGuard — 零进展守卫（三级升级）⭐ 本阶段重点

状态机：`FUN_1407cab40(agent+0x530, ...)` 返回 2/4/6 三档状态，对应三级警告：

| 状态 | 用户消息（完整解码） | 日志标签 | 行为 |
|---|---|---|---|
| 2 | `[progress guard] the last %d tool rounds repeated earlier reads or commands without new results. Narrow the investigation or adjust the plan before continuing.` | `progress guard: %d zero-gain rounds — nudging to narrow` | 温和提示收窄调查 |
| 4 | `[progress guard] still no new evidence after %d rounds. Change strategy now: take a different angle or tool, delegate a focused sub-task, or reduce the scope of what you are verifying.` | `progress guard: %d zero-gain rounds — forcing a strategy change` | 强制更换策略 |
| 6 | `[progress guard] %d tool rounds in a row produced no new evidence (no new files, results, or changes). Stop exploring: produce your final answer now, stating what was established and what remains unknown.` | `progress guard: %d zero-gain rounds — demanding a final answer (block...)` | **强制输出最终答案**，`agent+0x2f8=1`、`agent+0x300=param_8` |

**语义**：这是 agent 的**反漫游/反无限循环守卫**，三级渐进：收窄 → 换策略 → 强制收尾。每次工具轮次后检查是否有新证据（新文件/新结果/变更），连续零进展则逐级升级。状态 6 时标记恢复字段并阻断继续探索。

### 1.6 守卫层小结

```
Agent 四道防线:
1. applyGovernor      — 上下文预算（防 token 爆炸）
2. applyEBM           — 错误预算（防错误泥潭）
3. applyStormBreaker  — 工具风暴（防同一工具死循环）
4. applyProgressGuard — 零进展（防漫游探索，三级升级强制收尾）
```

---

## 2. 方向 B：DeepSeek 定价回填链 — 完整闭环 ⭐

### 2.1 定价表精确值（IEEE-754 double 解码确认）

`DeepSeekV4PricesForCurrency` @ 0x1406425c0 内嵌两套货币定价，每模型 3 个 double：

| 模型 | 货币 | [0] | [1] | [2] |
|---|---|---|---|---|
| deepseek-v4-flash (17B @ 0x141b94c18) | CNY | **0.02** | 1.0 | 2.0 |
| deepseek-v4-flash | USD | **0.0028** | 0.14 | 0.28 |
| deepseek-v4-pro (15B @ 0x141b8eb3e) | CNY | **0.025** | 3.0 | 6.0 |
| deepseek-v4-pro | USD | **0.003625** | 0.435 | 0.87 |

汇率一致性验证：flash CNY/USD ≈ 7.14，pro CNY/USD ≈ 6.9，两档模型 CNY/USD 比一致（同一货币对）。

> ⚠️ 三元组字段顺序（[0]/[1]/[2]）语义：`[输入价, 输出价, 缓存价]` 为推测——按比例推断 [1]:[2] = 1:2（输出:缓存），但 flash CNY [1]=1.0 < [2]=2.0，与"缓存价 < 输出价"的常规惯例相反，**顺序待运行时确认**，报告中诚实标注。

### 2.2 货币检测逻辑 (0x140642cc0) 完整还原

```
用户货币配置 → 规范化:
  "$"            (len 1)  → USD
  "US$" / "USD"  (len 3)  → USD
  "CN" + 'H'/'Y' (len 3)  → CNY (CNH/CNY)
  "RMB"          (len 3)  → CNY
  "CN?" 前缀     (len 2)  → CNY
  其他           → 返回空 (默认 USD 分支)
```

### 2.3 回填算法 (backfillDeepSeekOfficialPrices @ 0x140615660) 完整还原

```c
for each provider in config (provider 数组 @+0x338/+0x340, 每条 0x210 字节):
    if provider ID == "deepseek" (8B 小端 0x6b65657370656564):
        backfillDeepSeekOfficialEndpointDefaults(entry)   // 官方端点回填
        定价 = DeepSeekV4PricesForCurrency(货币)          // 货币感知定价
        遍历定价表:
            for each model in entry 模型列表 (+0x50/+0x58 或 +0x40/+0x48):
                if 模型不在 entry 价格表 (+0x148) 中:
                    克隆 40 字节价格记录 → 插入
```

**语义**：任何名为 `deepseek` 的 provider，自动按用户货币（CNY/USD）回填全部 V4 模型官方定价；端点默认值同步补齐（`backfillDeepSeekOfficialEndpointDefaults` @ 0x140615aa0）。

### 2.4 定价链调用链

```
DeepSeekV4PricesForCurrency (0x1406425c0)
  └─ FUN_140642cc0   — 货币检测 (CNY/USD)
deepSeekV4PricesForConfig (0x1406428a0) — 中转
  └─ FUN_1406429e0 + DeepSeekV4PricesForCurrency
applyDeepSeekOfficialDefaultPricing (0x140643000)
  └─ FUN_1405e3360 + applyDeepSeekOfficialDefaultPricingWithOverride (0x140643060)
backfillDeepSeekOfficialPrices (0x140615660) — 逐 provider 回填
backfillDeepSeekPro (0x140615120) — Pro 模型回填
backfillDeepSeekOfficialEndpointDefaults (0x140615aa0) — 官方端点
backfillDeepSeekAnthropicCapabilities (0x14061f0e0) — Anthropic 能力回填
```

---

## 3. 方向 C：lazySpawn 插件机制 — 四件套完整闭环 ⭐

### 3.1 生命周期全景

```
kick (0x140689140)          — 请求启动
  ├─ 自旋锁 +0x260 (已有则 cond wait FUN_140097540)
  ├─ 若已在跑 (+0x268≠0) → 直接 run 内联
  ├─ beginInFlight (0x140688fc0) — 检查 host 存活
  │    ├─ host 关闭 → 状态3 + 错误 "plugin host is closed" (21B @ 0x141ba2985)
  │    └─ 正常 → 状态1 + channel (0x298)
  ├─ go FUN_140689280 (goroutine 包装) → run
  └─ run (0x140689380)      — 真正的执行
       ├─ 30s 超时上下文 (contextWithTimeout)
       ├─ FUN_1406a0440 执行 spawn
       ├─ 成功: 状态2 (+0x4d=2) + 结果切片 (+0x4e) + trySwap + channel 通知
       ├─ 失败: 状态3 (+0x4d=3) + 错误 (+0x4f/+0x50) + 通知 waiter
       └─ defer 解锁 (+0x4c) + 清理 FUN_140689d80
trySwap (0x140689fc0)       — 懒→激活 提升
  ├─ 条件: +0x288==0(未交换) && +0x268==2(成功)
  ├─ 关闭旧 transport (+0x240/+0x2a0)
  ├─ 遍历旧工具 map (+0x270): FUN_14049a980 逐个移除
  └─ +0x288 = 1 (标记已交换)
```

### 3.2 结构字段映射

| 偏移 | 含义 |
|---|---|
| +0x238 | host 存活标志 |
| +0x260 | 启动锁（自旋锁） |
| +0x268 | 状态 int（1=in-flight, 2=成功, 3=错误） |
| +0x270 | 工具注册表 map |
| +0x288 | swapped 标志 |
| +0x298 | in-flight channel |
| +0x4c | 执行锁 |
| +0x4d | 状态 byte（2=成功, 3=错误） |
| +0x4e | 结果切片 |
| +0x4f/+0x50 | 错误存储 |
| +0x53 | 清理句柄 |

**语义**：插件懒加载生命周期 = 请求(kick) → 存活检查(beginInFlight) → 后台执行(run, 30s 超时) → 成功后原子提升(trySwap，关闭旧 transport、清理旧工具表)。失败时状态 3 并通知所有 waiter。

---

## 4. 方向 D：chatTUI 消息处理细节

### 4.1 FUN_1413e07e0 — 单实例锁 + 消息发送检查

```c
if (chatTUI[0x8eb] != 0) {                          // 有挂起的会话上下文
    auVar2 = (**(chatTUI + 0x358))(chatTUI[1]);     // vtable 方法调用
    iVar1 = FUN_1409bc4c0(chatTUI[0x8eb], ...);
    if (iVar1 != 0) {                               // 锁被占用
        // 构造错误: "close the other Reasonix window or process first" (48B @ 0x141be702f)
        FUN_14134dd80(chatTUI, err);
    }
}
```

**语义**：chatTUI 启动/恢复会话时的**单实例检查**——若已有其他 Reasonix 窗口/进程持有会话锁，提示关闭后再试。与 mcplaunch 的文件锁机制（acquireFileLock / launchLockContention）形成前后端闭环：CLI 层单实例锁 + chatTUI 层友好提示。

### 4.2 消息类型哈希分发回顾（第三阶段成果）

`chatTUI.update` (FUN_141322540) 是 BubbleTea 消息类型哈希分发器：`switch(*(param_1+0x10) >> 0x13 & 0x7f)`，26+ 种消息类型，关键 case：
- 0x30: 消息发送（存储输入 → 追加历史 → FUN_1413e07e0）
- 0x34: 宽键分发（≤0x200 键，逐键 FUN_141340ce0）
- 0x3f: 命令模式键（ctrl+c/super+c/meta+c/ctrl+insert 等小端魔数匹配）
- 0x69: PageUp/Down

---

## 5. 动态验证补充

| 验证项 | 结果 |
|---|---|
| `reasonix --version` | v1.21.5 ✅ |
| `reasonix mcp list` | deepcode-decompiler + ghidra-mcp 两个 stdio 服务器正确加载 ✅ |
| `%APPDATA%\reasonix\mcp-activation.json` | 2 条启用覆盖持久化 ✅ |
| `cli-crash-reports` | 当前不存在（未崩溃过，机制已静态确认） |
| 单实例锁 | 静态确认（chatTUI "close the other Reasonix window" + mcplaunch 文件锁） |

---

## 6. 第四阶段架构增量

```
reasonix.exe — 第四阶段新增认知
├─ Agent 四道防线:
│    Governor(上下文预算) → EBM(错误预算) → StormBreaker(工具风暴) → ProgressGuard(零进展三级)
├─ DeepSeek 定价: 货币感知(CNY/USD) × 2模型(flash/pro) × 3价格 = 12 个 double 精确值
│    回填链: 官方端点 + 官方定价 + Anthropic 能力 (逐 provider 0x210B)
├─ lazySpawn: kick → beginInFlight(host存活) → run(30s) → trySwap(原子提升+清理)
└─ chatTUI: 单实例锁 "close the other Reasonix window or process first"
```

---

## 7. 工具与产物清单

| 文件 | 说明 |
|---|---|
| `F:\DEEPCODE\scripts\reasonix_symbols.txt` | 59145 函数符号表（地址→符号，本阶段定性依据） |
| `F:\DEEPCODE\scripts\decode_deepseek_prices.py` | DeepSeek 定价 double 解码 + 字符串读取脚本 |
| `F:\DEEPCODE\scripts\REASONIX_深度逆向分析报告.md` | 第一阶段（入口/符号恢复/CLI 全景/崩溃/DeepSeek 协议） |
| `F:\DEEPCODE\scripts\REASONIX_第二阶段深度分析报告.md` | 第二阶段（Agent/MCP 客户端/插件系统/prefix-cache） |
| `F:\DEEPCODE\scripts\REASONIX_第三阶段深度分析报告.md` | 第三阶段（9 项认知修正 + chatTUI 分发器 + 启动身份 + spawn 闭环） |
| `F:\DEEPCODE\scripts\REASONIX_第四阶段深度分析报告.md` | 本报告 |

---

## 8. 后续可深入方向（未挖尽）

1. `applyContextualToolGate` / `applyMutationDependencyBarrier` / `applyPlanModeAndProxy` 等剩余 7 个守卫反编译（已定位地址，未逐体分析）
2. `FUN_1407cab40`（applyProgressGuard 状态计算器）本体——零进展判定算法细节
3. `agent.batchStormSignature` (0x140777f40) 签名计算算法
4. 定价三元组字段顺序运行时确认（[输入/输出/缓存] vs [输入/缓存/输出]）
5. `DrainRecoveryMetrics` (0x1409ab960) 与 RecoveryGate 的联动机制
6. 用 ghidra-mcp debugger 实测单实例锁（开两个 reasonix chat 窗口验证 "close the other..." 提示）
