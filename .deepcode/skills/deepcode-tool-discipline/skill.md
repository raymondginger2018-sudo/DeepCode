---
name: deepcode-tool-discipline
description: >
  DeepCode 工具调用规范与自修复技能。当 DeepCode 需要调用工具（尤其是 pwsh、MCP、API 等）、修复错误、或诊断失败模式时，自动加载此技能。覆盖工具调用三大铁律（字段白名单、强类型匹配、command 纯字符串）、失败诊断与修复规范（4 条：执行前校验网关、三元认知触发、修复策略金字塔、最小验证闭环）、常见陷阱速查表、HarnessAgent 诊断流程。源自真实会话中 20+ 次重复错误的教训总结。
---

# DeepCode 工具调用规范与自修复技能

## 工具调用三大铁律

### 铁律 1：严格字段白名单
- 只使用工具 schema 中**明确列出的字段**，禁止添加任何未定义字段（如 `justification`、`reason`、`explanation`）
- ❌ 错误：`{"command": "ls", "justification": "list files"}`
- ✅ 正确：`{"command": "ls"}`

### 铁律 2：强类型匹配
- 布尔值必须写裸 `true`/`false`（不带引号）
- 数字必须写裸数字（不带引号）
- ❌ 错误：`"run_in_background": "true"`, `"timeoutMs": "240000"`
- ✅ 正确：`"run_in_background": true`, `"timeoutMs": 240000`

### 铁律 3：command 是纯字符串
- `command` 字段的值必须是**可直接在 shell 中执行的纯命令字符串**
- 严禁嵌套 JSON 对象、严禁用字面引号包裹整个命令
- ❌ 错误：`"command": "{\"cmd\": \"ls\"}"` 或 `"command": "\"python3 script.py\""`
- ✅ 正确：`"command": "python3 script.py 2>&1"`

## 失败诊断与修复规范

### 规范 1：执行前校验网关
启动任何代价较高（时间/金钱）的任务前，必须执行环境完整性检查：
- 检查 API key 与端点是否匹配（DeepSeek key → api.deepseek.com，SCNET key → api.scnet.cn/api/llm/v1）
- 检查 Docker 是否正常运行
- 检查依赖是否已安装
- 检查必要环境变量是否已设置

### 规范 2：三元认知触发
当同一错误类型的修复尝试 ≥ 3 次且均失败时：
1. 立即暂停当前修改策略
2. 切换至根因诊断模式：生成独立探针、测量端到端耗时、分阶段日志插桩
3. 诊断完成前禁止继续修改配置
4. 记录诊断结果到小脑记忆库

### 规范 3：修复策略金字塔
修复已知软件环境问题时，按优先级排序：
1. 🔝 **重装/升级**（winget upgrade、pip install --upgrade）
2. 🔄 **重置配置**（factory reset、删除缓存数据）
3. ⚙️ **配置修改**（改 YAML、改环境变量）
4. 🔁 **重启服务**（restart process、restart Docker）
5. 🔽 **手动命令**（WSL 命令、手动清理）

禁止跳过高层级直接进入低层级手动命令。

### 规范 4：最小验证闭环
任何修改完成后，必须自动生成并执行最小验证：
- 不仅仅是检查异常消失
- 而是检查新行为是否真正生效（调用链验证、钩子触发验证、端点连通性验证）
- 验证未通过则标记为"修复未完成"，禁止关闭任务

## 常见陷阱速查

| 陷阱 | 症状 | 标准解法 |
|:--|:--|:--|
| 字段名拼错 | 报错说字段不存在 | 对照 schema 重新生成，不加额外字段 |
| 类型混用 | must be boolean/number | 布尔值不加引号，数字不加引号 |
| JSON 嵌套 | Powershell 解析 { 语法错误 | command 是纯字符串，不是对象 |
| 引号包裹 | ENOENT（文件不存在） | command 首尾不加字面引号 |
| 端点不匹配 | AuthenticationError | key 与 base URL 必须配对 |
| 修复只做一半 | 改完后没效果 | 验证调用链是否经过新组件 |
| 重复试错 | 连续 3+ 次同类错误 | 停！切根因诊断模式 |

## 诊断工具（HarnessAgent）

当需要诊断复杂失败模式时：
1. 收集完整失败轨迹（工具调用→报错→尝试→失败）
2. 通过 SCNET DeepSeek-V4-Pro（模型名大写 `DeepSeek-V4-Pro`）分析
3. SCNET 端点：`https://api.scnet.cn/api/llm/v1`
4. 参数：`extra_body={'thinking':{'type':'enabled'}, 'reasoning_effort':'high'}`
5. 输出根因分析 + 改进规则 + 可写入 system prompt 的精确文本
6. 将规则存入小脑记忆库和用户经验库

---

## 自进化更新（2026-08-26 18:29）

连续20次给工具传不存在的justification字段，每次报错但agent不改正，换一种错误继续犯

[{'before': 'Agent随意添加未定义的字段，如justification，导致工具调用失败，但仍重复该错误。', 'after': '调用工具前，必须将参数名与工具定义中的字段白名单逐一比对，移除任何未出现在白名单中的字段。'}, {'before': '收到参数错误后，Agent无视错误信息，继续使用相同错误参数。', 'after': "任何工具调用返回的错误中若包含'unknown field'、'unexpected key'等关键词，必须立即回到工具定义，删除对应字段并重新生成调用。"}, {'before': 'Agent尝试各种变体错误而不分析根本原因，例如换一种错误继续犯。', 'after': "连续两次同类型错误后，强制进入'工具定义复诵'模式：逐字段核对名称、类型与必填性，仅允许精确匹配的参数。"}, {'before': '未记录已犯过的错误，导致循环试错。', 'after': '在运行时维护一个禁止字段列表，每次因未知字段报错后，将该字段记入列表，后续调用中自动过滤。'}]
