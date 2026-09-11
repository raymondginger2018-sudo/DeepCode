# REASONIX 第六阶段深度分析报告

> 目标：`reasonix.exe` (v1.21.5, Go 1.26.5, Bun 打包, PE32+ x86-64)
> 阶段：第六阶段 — chatTUI 渲染组件全量反编译 + evidence 证据账本 / delegationAdmission / contextualToolGate 极限深挖
> 方法：Ghidra MCP 反编译 + 符号表反查(59,145 函数) + PE 字符串池解码交叉验证
> 日期：2026-08-10

---

## 0. 认知修正（第五阶段 → 第六阶段）

| 地址 | 第五阶段认知 | 第六阶段确认真相（符号表） |
|---|---|---|
| 0x140439e60 | （未深入） | `evidence.(*Ledger).Record` — 证据账本记录器 |
| 0x14044dda0 | （未深入） | `evidence.(*ProgressTracker).ScoreRound` — 轮次打分 |
| 0x14044dfa0 | （未深入） | `evidence.(*ProgressTracker).scoreReceipt` — 单条打分 |
| 0x14079e4e0 | （未深入） | `agent.delegationAdmission` — 委派准入判定器 |
| 0x1407a5ee0 | （未深入） | `agent.contextualToolGateOutcome` — 上下文工具门控 |
| 0x141355bc0 | （未深入） | `chatTUI.renderChooser` |
| 0x1413d5c20 | （未深入） | `chatTUI.renderRewind` |
| 0x141396760 | （未深入） | `chatTUI.renderMCPImport` |
| 0x1413d2100 | （未深入） | `chatTUI.renderResumePicker` |
| 0x14133fca0 | （未深入） | `chatTUI.modeTagText` |
| 0x141378fa0 | （未深入） | `chatTUI.renderComposerInput` |
| 0x14132fbc0 | （未深入） | `chatTUI.renderMainManagerFooter` |

**经验**：chatTUI 渲染层与 agent 守卫层是两块完全独立的代码面——渲染函数反编译噪声极高（大量栈拷贝/内联），必须以"字符串常量 + 数据布局"为锚点解读，不能依赖反编译控制流。

---

## 1. 方向 1：evidence 证据账本（`(*Ledger).Record` 落盘格式）

### 1.1 `evidence.(*Ledger).Record` @ 0x140439e60

**反编译核心**：
```c
void FUN_140439e60(int *param_1, ...)  // (*Ledger).Record
{
  // 1. 类型 magic 检测: 命令类型是 "complete_steps" (len==0xd)
  //    *ptr == 0x6574656c706d6f63 ("complete") && ptr[1]==0x6574735f ("_ste") && byte[12]=='p'
  // 2. LOCK(); if (ledger[0]==0) ledger[0]=1; UNLOCK();  // 首次进入置锁
  //    if (!locked) FUN_140097540(ledger);               // 已有锁则等待
  // 3. 按 0xb0 (176) 字节条目追加: *(slice_ptr + len*0xb0) = 新条目
  // 4. defer 解锁 FUN_14043a2e0
}
```

**条目布局（0xb0 = 176 字节 = 22 qword）**：
```
+0x00 类型 ptr        +0x08 类型 len
+0x10 slice ptr       +0x18 slice len   +0x20 slice cap
+0x28/0x30/0x38 指针三件套
+0x40 string1 (ptr)   +0x48 string1 (len)
+0x50 string2 (ptr)   +0x58 string2 (len)
+0x60 数据区
+0x68 *char 标志
+0x70~0xa8 两组 3×qword 指针数据（来自 FUN_14044c7e0 / FUN_14044abc0）
```

**结论**：`Ledger.Record` 是**纯内存追加**记录器（0xb0 字节/条 + 自旋锁），**不负责落盘**——落盘逻辑在调用方（事件持久化链）。这解释了第五阶段观察到的高频追加语义。

### 1.2 `evidence.(*ProgressTracker).ScoreRound` @ 0x14044dda0

轮次打分入口。每轮工具调用结束后调用，对**整批**工具调用打分（批量证据评估）。

### 1.3 `evidence.(*ProgressTracker).scoreReceipt` @ 0x14044dfa0

**单条 receipt 打分语义（0-3 分）**：
```c
// 空字符串分支:
//   a) 双零标志 → 3 分（最差：无任何证据）
//   b) 工具名是 "task"(4B) / "parallel_tasks"(0xeB) / "fleet"(5B) → 2 分（重复工具调用）
//   c) in_68/in_70/in_a0 任一非零 → 1 分（部分证据）
//   d) 遍历 in_stack_00000078 指向的 slice（每元素 16 字节=字符串对）
//      查 map DAT_141790ac0 计数（证据查重）
// 非空字符串分支:
//   查 map[ledger1] / map[ledger2] → 返回 2 / -2 / 1 / 0
```

**评分语义**：
- **0 分** = 本轮有新证据（正常）
- **1 分** = 部分证据
- **2 分** = 重复工具调用（无新产出）
- **3 分** = 完全零证据（双零：无新文件无新结果）

此分数供 `applyProgressGuard` 消费（N=2/4/6 升级阈值，见第五阶段）。

---

## 2. 方向 2：delegationAdmission 委派准入判定器

### 2.1 函数体 @ 0x14079e4e0（100% 闭环）

```c
undefined1 [40] FUN_14079e4e0(...)  // agent.delegationAdmission
{
  uVar2 = FUN_14075a3c0();        // 获取委派状态码
  cVar1 = (char)uVar2;
  if (cVar1 != 3 && cVar1 != 4) {
    // 非委派场景 → 放行
    return {5, "allow", "non_local_intent", 0x10, uVar2, 0};
  }
  // 委派场景（状态 3/4）:
  // 1. 遍历白名单表 PTR_PTR_1431c5ee0（6 条 (ptr,len) 对，表基址 0x1431eaaa0）
  for (i = 0; i < 6; i++) {
    if (FUN_140015ca0(param_1, param_2, table[i].ptr, table[i].len) >= 0) break;
  }
  // 2. 白名单未命中 → 检查 URL
  if (循环未命中) {
    if (FUN_140015ca0(param_3, param_4, "http://", 7) < 0 &&
        FUN_140015ca0(param_3, param_4, "https://", 8) < 0) {
      // 无外部源 → 拒绝
      return {4, "deny", "local_fix_no_external_need", 0x1a, cVar1, 0};
    }
    return {5, "allow", "external_source_cited", 0x15, cVar1, 0};
  }
  return {5, "allow", "user_requested", 0xe, cVar1, 0};
}
```

### 2.2 白名单表（6 条全解码）

| # | 字符串 | 长度 | 语义 |
|---|---|---|---|
| 0 | `research` | 8B | 英文"调研"指令 |
| 1 | `调研` | 6B | 中文调研指令 |
| 2 | `查资料` | 9B | 中文查资料指令 |
| 3 | `查文档` | 9B | 中文查文档指令 |
| 4 | `search the web` | 14B | 英文搜网指令 |
| 5 | `look up online` | 14B | 英文在线查询指令 |

### 2.3 语义总结

**delegationAdmission = "本地修复 vs 外部调研" 判定器**：
- 委派状态非 3/4（非委派场景）→ 一律放行 `allow(non_local_intent)`
- 委派场景（状态 3/4）：
  - 用户指令命中调研白名单（research/调研/查资料/查文档/search the web/look up online）→ 放行 `allow(user_requested)`
  - 指令含 `http://` / `https://`（引用外部源）→ 放行 `allow(external_source_cited)`
  - 否则 → **拒绝** `deny(local_fix_no_external_need)`（本地修复场景无需外部调研）

**返回结构（40 字节 = 5×8）**：`{n, allow/deny 字符串, reason 字符串, reason_len, 状态码, 0}`

**设计意图**：子代理/委派任务中，只允许"明确要求外部调研"的任务访问外部资源；纯本地修复任务被强制隔离在本地。

---

## 3. 方向 3：contextualToolGateOutcome 上下文工具门控

### 3.1 函数体 @ 0x1407a5ee0

```c
int FUN_1407a5ee0(...)  // agent.contextualToolGateOutcome
{
  // 1. 通过类型 hash 表 PTR_DAT_1431c6320 查找 *(int*)(ctx+8)
  // 2. 命中 → 调用匹配器 (**(code **)(match+0x18))(d0,b8,c0,d0,d8,e0)
  // 3. 匹配器返回 0 → 阻塞:
  //      FUN_140131880(&DAT_141bffaeb, 0x3f, &local_18, 1, 1)  // 格式化错误
  //      FUN_14077ccc0()                                        // 记录日志
  //      return 1;   // 阻塞
  // 4. 否则 → return 0;  // 放行
}
```

### 3.2 错误串

```
DAT_141bffaeb (0x3f=63B): 'blocked: tool %q is unavailable in the current workflow context'
```

**语义**：按工具类型 hash 分派到专用匹配器（每个工具类型一个匹配函数），判断当前工作流上下文是否允许该工具执行。不匹配/上下文不允许 → 阻塞并记录日志。

---

## 4. 方向 4：chatTUI 渲染组件全量反编译（33 个全挖完）

### 4.1 本次新增 6 个组件语义

| 组件 | 地址 | 语义 |
|---|---|---|
| `renderDetachedComposerInput` | 0x141379560 | 分离式输入框渲染（textarea 值 + 滚动偏移 + 按行渲染） |
| `renderMainManagerFooter` | 0x14132fbc0 | 主管理器页脚（6 种状态文本 + 5 种 skillPicker 模式页脚选择器） |
| `renderTranscriptWithMainManager` | 0x14132ff40 | 带主管理器的消息记录渲染（分段 + 可滚动窗口） |
| `renderTranscriptSource` | 0x14142baa0 | 消息源渲染分发器（switch 8 种模式，case 6 → renderReplayBundle） |
| `renderReplayBundle` | 0x14142bd20 | 回放包渲染（两段式：头 + 列表 join "\n"） |
| `renderReplayBundleCopy` | 0x14142c020 | 回放包复制（闭包 FUN_14142c140 委托 renderReplayBundle） |

### 4.2 renderMainManagerFooter 状态文本（全部解码）

| 状态 | 字符串 |
|---|---|
| mgr==1 | `↑/↓ navigate · r refresh · Enter to select · Esc to back` |
| mgr 2-3 | `Esc to back` |
| mgr 5-6 | `Enter to select · y confirm · n cancel · Esc to back` |
| mgr[2]==0 | `↑/↓ navigate · Enter to confirm · Esc to cancel` |
| mgr other | `↑/↓ navigate · r refresh · Enter for details · Esc to close` |
| in_4688!=0 | `Enter confirm · y clear · n/Esc cancel` |

### 4.3 skillPicker 模式页脚表（.data 指针表，5 组全解码）

| 模式 | 页脚 |
|---|---|
| `skills` | `↑↓ navigate · Space toggle · Enter save · / search · s sources · r rescan · Esc cancel` |
| `detail` | `↑↓ navigate · Enter select · Space toggle · Esc back` |
| `sources` | `↑↓ navigate · Enter skills · d diagnostics · s skills · r rescan · Esc close` |
| `source-skills` | `↑↓ navigate · Space toggle · Enter details · Esc sources` |
| `confirm-delete` | `Enter confirm · y delete · n/Esc cancel` |

### 4.4 33 个渲染组件全景（全部闭环）

```
chatTUI.View (0x1413397a0)
├─ renderMainManager (0x14132f560) ── 主管理器分发
│    ├─ renderMainManagerFooter (0x14132fbc0) ── 页脚（状态 + skillPicker 模式）
│    └─ renderClearConfirm (0x141358660) / skillPicker 系列（7 个）
├─ renderTranscriptWithMainManager (0x14132ff40)
│    └─ renderTranscript (0x14142de40)
│         └─ renderTranscriptSource (0x14142baa0) ── 8 路消息源分发
│              ├─ case 6 → renderReplayBundle (0x14142bd20)
│              │    └─ renderReplayBundleCopy (0x14142c020, 复制闭包)
│              └─ case 8 → map 类型分发 (UNK_141790bc0)
├─ renderStatusBlock (0x141416140) ── 状态块
│    ├─ primaryStatusLine (0x1414149a0) ── 主状态行
│    └─ runningWorkingLine (0x141339080) ── 工作行
├─ renderTodoPanel (0x14133e3e0) ── Todo 面板
├─ renderApprovalBanner (0x14133cf00) ── 审批横幅
├─ renderComposerInput (0x141378fa0) ── 输入框
│    └─ renderDetachedComposerInput (0x141379560) ── 分离输入框
├─ renderQueueIndicator (0x141320be0) ── 队列指示器
├─ renderChooser (0x141355bc0) ── 选项选择器
├─ renderRewind (0x1413d5c20) ── 回滚选择器（6 选项：Code+conversation/Conversation only/Code only/Fork/Compress after/before）
├─ renderMCPImport (0x141396760) ── MCP 导入（cc-switch）
├─ renderResumePicker (0x1413d2100) ── 会话恢复选择器
├─ renderCompletion (0x141374c80) / renderCopyPicker (0x141379e80)
├─ quickPicker.render (0x1413bd960) ── 快捷选择器（Search:）
├─ modeTagText (0x14133fca0) ── 模式标签（12 组合）
└─ 辅助: renderSkillPicker(0x14140a0e0, 5 路分发) + skillPicker 变体(0x14140a6e0/bfe0/ca60/d9e0/e500)
```

### 4.5 关键交互快捷键全景

| 场景 | 快捷键 |
|---|---|
| Todo/审批 | `↑/↓ navigate · Enter select · y/a/p/n shortcuts` |
| 审批 | `↑/↓ navigate · Enter select · y/a/p/n shortcuts` |
| 复制 | `↑/↓ navigate · Enter copy · Esc cancel` |
| 完成 | `↑/↓ move · Tab/Enter select · Esc close` |
| MCP 导入 | `Space select · Enter import · Esc cancel` |
| 技能管理 | `↑↓ navigate · Space toggle · Enter save · / search · s sources · r rescan · Esc cancel` |
| 技能删除 | `Enter confirm · y delete · n/Esc cancel` |
| 清空会话 | `y confirm · n/Esc cancel` |

### 4.6 渲染架构总结

- **组件化渲染**：每个渲染函数返回 `(ptr,len)` 字符串对，`View` 按序 append 到组件数组，最后 `strings.Join(sep="\n")` + lipgloss 样式化输出
- **模式分发**：`renderMainManager`/`renderSkillPicker`/`renderTranscriptSource` 均为按状态字符串/整数分发的 switch 分发器
- **数据布局**：各 picker 条目 stride 不同（0x38/0x40/0x88/0xa8/0x188/0x1a0），反映不同数据结构（文本对/三字段/完整条目）
- **ANSI 装饰**：`\x1b[1m`（粗体）/`\x1b[0m`（重置）等用于选中项高亮
- **滚动窗口**：统一 clamp 模式 `start = clamp(current-4, 0, total-8)`，上下截断提示 `  ↑ more` / `  ↓ more`

---

## 5. 方向 5：renderMCPImport / renderResumePicker / renderRewind 细节

### 5.1 renderMCPImport（cc-switch MCP 导入）

```
标题: 'Import MCP from cc-switch' (25B)
副标题: 'Space select · Enter import · Esc cancel' (42B)
条目: 0x1a qword stride；选择框: '[ ]'(3B) / '[x]'(3B)
格式: '%s %s %-34s %s' (14B)；反色: '\x1b[7m' + '\x1b[0m'
```

### 5.2 renderResumePicker（会话恢复）

```
委托: in_stack_00004648[5]!=0 → quickPicker.render
条目: 0x1f qword stride；标题/页脚来自 .data 指针表 (0x143200ed8 系列)
选中标记: '(active)' (8B)
```

### 5.3 renderRewind（回滚选择器）

```
6 个选项（索引 0-5，.data 指针表 0x143201fa8 系列）:
  'Code + conversation' / 'Conversation only' / 'Code only'
  / 'Fork' / 'Compress after here (history kept)' / 'Compress before here (history kept)'
滚动: 头部超滚 '  ↑ more'(10B) / 尾部 '  ↓ more'(10B)
```

---

## 6. 方向 6：modeTagText 模式标签（12 组合全解码）

| 组合 | 标签 |
|---|---|
| 桌面+紧急+YOLO | `Plan+YOLO` (9B) |
| 桌面+running+YOLO | `Goal+YOLO` (9B) |
| 桌面+YOLO | `YOLO` (4B) |
| 桌面+紧急 | `Plan` (4B) |
| 桌面+running+auto | `Goal+Auto` (9B) |
| 桌面+auto | `Auto` (4B) |
| 桌面+dontAsk | `Don't Ask` (9B) |
| 桌面+running 兜底 | `Goal` (4B) |
| 桌面默认 | `Ask` (3B) |
| 非桌面+紧急+auto | `Plan+Approve` (12B) |
| 非桌面+running+auto | `Goal+Approve` (12B) |
| 非桌面+auto | `Auto+Approve` (12B) |

**语义**：模式标签 = 权限模式（Plan/YOLO/Goal/Auto/Ask/Approve）+ 平台（desktop/running）组合，显示在 TUI 状态区。

---

## 7. 最终架构增量（第六阶段）

```
reasonix.exe — 第六阶段新增认知
├─ evidence 证据体系:
│    Ledger.Record (0xb0B/条 内存追加, 自旋锁) ← 无落盘
│    ProgressTracker.ScoreRound → scoreReceipt (0-3 分: 新证据0/部分1/重复2/零证据3)
│    → applyProgressGuard 消费 (N=2/4/6 升级)
├─ 委派安全:
│    delegationAdmission: 非委派放行 / 调研白名单(6条中英)放行 / http(s):// 放行 / 否则拒绝
│    contextualToolGateOutcome: 类型 hash 分发 + 上下文匹配器
├─ chatTUI 渲染 (33 组件全闭环):
│    View → 分发器(主管理器/技能/消息源) → 组件渲染 → strings.Join("\n") → lipgloss
│    输入: composer + detached composer (textarea 渲染核心)
│    选择: chooser/rewind/mcpImport/resumePicker/completion/copyPicker/quickPicker
│    状态: statusBlock/todoPanel/approvalBanner/queueIndicator/modeTagText
```

---

## 8. 工具与产物清单

| 文件/工具 | 说明 |
|---|---|
| `F:\DEEPCODE\scripts\REASONIX_第六阶段深度分析报告.md` | 本报告 |
| `F:\DEEPCODE\scripts\reasonix_symbols.txt` | 59,145 函数符号表（地址→符号） |
| `/tmp/phase6_strs.py` 等 | 字符串解码脚本（VA→文件偏移映射） |
| 前五阶段报告 | `REASONIX_深度逆向分析报告.md` ~ `第五阶段` |

---

## 9. 后续可深入方向（已穷尽，剩余为极限深挖项）

1. `renderTranscriptSource` case 8 的 map 类型分发（UNK_141790bc0）——消息源类型注册表内容
2. `renderRewind` 6 个选项对应的实际回滚动作函数（反编译选项选择后的执行路径）
3. `delegationAdmission` 的调用方（哪些委派流程触发状态 3/4）
4. `evidence.Ledger` 落盘链：Record 的内存条目最终由哪个持久化函数写盘（配合事件流）
5. ghidra-mcp debugger 实测：真实会话中触发 delegationAdmission 拒绝场景，验证白名单判定

---
*生成于 2026-08-10 · F:\DEEPCODE\scripts*
