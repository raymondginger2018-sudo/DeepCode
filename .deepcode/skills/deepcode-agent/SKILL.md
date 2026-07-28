---
name: deepcode-agent
description: >
  DeepCode Agent — 完整 Agent 体系。
  移植自 codex.exe v0.145.0:
  - Agent 消息类型 (AgentMessagePlan / CallToolResult / CommandExecution)
  - Agent 线程派生系统 (SQLite 持久化线程树 + 动态工具注册)
  对标 codex.exe: AgentMessageItem, SubagentStart/Stop, threads/spawn_edges/dynamic_tools.
version: 2.0.0
author: DeepCode + RE (codex.exe v0.145.0)
date: 2026-07-28
tags: [agent, session, tracking, messaging, threading, spawn]
---

# DeepCode Agent

移植自 **codex.exe** 的 Agent Message 类型系统。

## 对标项

| codex.exe | DeepCode Agent |
|:----------|:---------------|
| `AgentMessagePlan` | `AgentPlan` |
| `AgentMessageItem` | `AgentMessage` |
| `AgentMessageContentDeltaEvent` | `AgentMessage(delta=True)` |
| `DynamicToolCall` + `CallToolResult` | `ToolCall` |
| `CommandExecutionItem` + `CommandBeginEvent` | `CommandExecution` |
| `SubagentStart` / `SubagentStop` | `create_sub_agent()` |
| `AgentSession` | `AgentSession` |

## 消息类型 (21 种)

| 类型 | 说明 |
|:----|:-----|
| `reasoning` | 推理过程 |
| `plan` | 执行计划 |
| `tool_call` | 工具调用 |
| `tool_result` | 工具结果 |
| `command_execution` | 命令执行 |
| `command_begin` | 命令开始 |
| `command_end` | 命令结束 |
| `file_change` | 文件变更 |
| `web_search` | 网络搜索 |
| `image_generation` | 图片生成 |
| `mcp_tool_call` | MCP 工具调用 |
| `sub_agent_activity` | 子 Agent 活动 |
| `collab_agent_tool_call` | 协作 Agent |
| `context_compact` | 上下文压缩 |
| `error` | 错误 |
| `text` | 文本 |

## 用法

### CLI

```bash
# 创建计划
python agent.py plan "分析项目结构"

# 记录推理
python agent.py reason "先看package.json, 再看src目录"

# 记录工具调用
python agent.py tool --name Read --input '{"path":"package.json"}' \
  --result '{"content": "..."}'

# 记录命令
python agent.py command "npm test" --stdout "All tests passed"

# 查看状态
python agent.py status

# 导出完整 JSON
python agent.py export
```

### MCP Server

```json
"deepcode-agent": {
  "command": "python",
  "args": [
    "F:/DEEPCODE/.deepcode/skills/deepcode-agent/agent.py",
    "--mcp"
  ]
}
```

### Python 嵌入

```python
from agent import AgentSession, AgentMessageType

sess = AgentSession("分析项目")
sess.start_turn()

# 记录推理
sess.add_reasoning("检查依赖关系...")

# 记录工具调用
tc = sess.add_tool_call("Read", {"path": "Cargo.toml"})
tc.complete({"content": "[package]\nname = \"codex\""})

# 记录命令
ce = sess.add_command("cargo build")
ce.complete(stdout="Compiling...", exit_code=0)

# 导出
print(sess.export_json())
```

## Agent 线程派生系统 (v2.0.0 新增)

移植自 codex.exe 的 SQLite 持久化 Agent 线程架构，对标 `threads` / `thread_spawn_edges` / `thread_dynamic_tools` / `logs` 四表。

### 核心概念

| 概念 | codex.exe 表 | 说明 |
|:------|:------------|:-----|
| Agent 线程 | `threads` | 完整生命周期 (9种状态) |
| 派生树 | `thread_spawn_edges` | 父→子层级关系 |
| 动态工具 | `thread_dynamic_tools` | 每线程独立注册/卸载 |
| 线程日志 | `logs` | 线程级日志 |

### 9 种 Agent 类型

`coder` `reviewer` `tester` `planner` `researcher` `coordinator` `shell_executor` `file_editor` `general`

### CLI

```bash
python agent_thread_manager.py spawn "analyze binary" --type researcher
python agent_thread_manager.py spawn "implement X" --type coder --parent <id>
python agent_thread_manager.py forest
python agent_thread_manager.py stats
```

### MCP Server

```json
"deepcode-agent-threads": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-agent/agent_thread_manager.py", "--mcp"]
}
```

### Python

```python
from agent_thread_manager import AgentThreadManager, AgentType
mgr = AgentThreadManager()
root = mgr.spawn("analyze", agent_type=AgentType.RESEARCHER)
child = mgr.spawn("implement", agent_type=AgentType.CODER, parent_thread_id=root.id)
tree = mgr.get_full_tree(root.id)
mgr.stop_thread_cascade(root.id)
```
