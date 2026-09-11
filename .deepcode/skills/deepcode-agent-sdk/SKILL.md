---
name: deepcode-agent-sdk
description: >
  DeepCode Agent SDK — 移植自 Claude Code v2.1.216 agentSdk.ts。
  让 DeepCode 作为 Agent 被外部程序调用 (HTTP API / MCP stdio / Python 嵌入)。
  支持工具注册、任务执行、文件操作、命令执行、AI 查询等。
version: 1.0.0
author: DeepCode + Ghidra RE (Claude Code v2.1.216)
date: 2026-07-26
tags: [agent, sdk, mcp, api, integration]
---

# DeepCode Agent SDK

移植自 **Claude Code v2.1.216** 的 `agentSdk.ts` 入口点。

## 功能

| 模式 | 说明 | 端口/协议 |
|:---|:-----|:----------|
| **HTTP API** | RESTful API Server | 8088 (HTTP) |
| **MCP stdio** | 作为 MCP Server 运行 | stdin/stdout |
| **Python 嵌入** | `from agent_sdk_server import DeepCodeAgent` | — |

## 内置工具

| 工具名 | 说明 |
|:------|:-----|
| `read_file` | 读取文件内容 |
| `write_file` | 写入文件 |
| `execute_command` | 执行 Shell 命令 |
| `search_files` | 搜索文件 (通配符) |
| `list_directory` | 列出目录内容 |
| `agent_query` | 向 AI 模型发送查询 |
| `agent_status` | 获取 Agent 状态 |

## 用法

### 启动 HTTP Server

```bash
python agent_sdk_server.py --http --port 8088
```

外部调用示例:

```bash
# 列出工具
curl http://127.0.0.1:8088/tools

# 执行工具
curl -X POST http://127.0.0.1:8088/execute \
  -H "Content-Type: application/json" \
  -d '{"tool": "list_directory", "params": {"path": "."}}'

# 批量执行
curl -X POST http://127.0.0.1:8088/batch \
  -H "Content-Type: application/json" \
  -d '{"tasks": [{"tool": "agent_status", "params": {}}]}'
```

### 作为 MCP Server (注册到 settings.json)

```json
"deepcode-agent-sdk": {
  "command": "python",
  "args": [
    "F:/DEEPCODE/.deepcode/skills/deepcode-agent-sdk/agent_sdk_server.py",
    "--mcp"
  ],
  "env": {
    "DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}"
  }
}
```

### Python 嵌入

```python
from agent_sdk_server import DeepCodeAgent

agent = DeepCodeAgent(workspace="/path/to/project")
# 注册自定义工具
agent.register_tool("my_tool", my_handler, "My tool description")
# 执行工具
result = await agent.execute("read_file", {"path": "test.txt"})
```

## 配置

通过环境变量控制:

| 变量 | 默认值 | 说明 |
|:----|:------|:----|
| `DEEPSEEK_API_KEY` | — | DeepSeek API 密钥 (agent_query 需要) |
| `DEEPCODE_AGENT_PORT` | 8088 | HTTP 服务端口 |
| `DEEPCODE_AGENT_HOST` | 127.0.0.1 | HTTP 绑定地址 |

## 安全性

- 所有文件操作限制在 `allowed_dirs` 范围内 (默认 = workspace)
- 跨目录路径访问会被拒绝
- HTTP 模式默认仅绑定 localhost
- 命令执行受 timeout 保护 (默认 30s)

## 注意事项

- HTTP 模式需要 `fastapi` + `uvicorn`: `pip install fastapi uvicorn`
- 默认无认证，生产环境请加 reverse proxy 认证
- agent_query 工具需要设置 `DEEPSEEK_API_KEY` 环境变量

## v2.0 新增能力 (对标官方 @anthropic-ai/claude-agent-sdk v0.3.220)

### 1. 事件流 (AgentEvent)

| 事件 | 说明 |
|:----|:-----|
| `init` | 会话初始化 (含 session_id/model/permission_mode) |
| `assistant_delta` | 模型增量输出 |
| `tool_call` | 工具调用请求 (含 allowed 权限标记) |
| `tool_result` | 工具执行结果 |
| `result` | 最终结果 (success / error_max_turns / error_max_budget_usd) |

### 2. 多轮 Agent 循环

```python
# 事件流 (对标官方 streamQuery)
async for ev in agent.stream_agent("分析项目结构"):
    print(ev.type, ev.data)

# 一次跑完 (对标官方 query)
result = await agent.run_agent("分析项目结构", {"permission_mode": "acceptEdits"})
```

### 3. 权限与预算 (AgentOptions)

| 选项 | 说明 |
|:----|:-----|
| `permission_mode` | default / acceptEdits / bypassPermissions / plan / dontAsk |
| `allowed_tools` / `disallowed_tools` | 工具白/黑名单 |
| `can_use_tool` | 自定义权限回调 (tool_name, params) -> bool |
| `max_turns` | 最大推理轮数 |
| `max_budget_usd` | 成本上限 (按 token 估算, 可配单价) |
| `model` | 模型名 (默认 deepseek-chat) |

### 4. 会话持久化 (SQLite)

- 会话库: `~/.deepcode/agent_sdk_sessions.db` (可用 `--db` 覆盖)
- API: `list_sessions()` / `get_session_events(id)` / `resume_session(id)` / `store.delete_session(id)`

### 5. 协议新增

- HTTP: `POST /agent/run` (完整事件列表), `POST /agent/stream` (SSE 流式),
  `GET/POST/DELETE /sessions*`
- MCP: `tools/call` 新增 `agent_run` 工具 (多轮循环, 在 tools/list 中可发现)

### 6. 向后兼容

- `execute()` / `execute_batch()` / 原有 HTTP `/execute` `/batch` `/history` 全部保留
- 旧版 `--http` / `--mcp` / 交互模式用法不变

### 7. 子 agent 治理 — spawn_deepcode (自己雇自己)

**原理**：`spawn_deepcode` 工具派生一个完整 DEEPCODE 子进程干活
（`python -m cli.exec_cli` headless 通道，NDJSON 事件流输出），子进程加载
全部 MCP 生态/skills/hooks —— 比 `agent_run` 的 7 个内置工具强一个量级。

```json
{
  "prompt": "子任务描述",
  "workspace": "F:/DEEPCODE/.workers/task-1",
  "max_iterations": 20,
  "timeout": 300,
  "max_output_chars": 8000,
  "allow_spawn": false
}
```

**治理模型：两层封顶 + 显式升格**

```
总管 (DEEPCODE_AGENT_DEPTH=0, 默认可派生)
 ├── 工人 (depth=1, allow_spawn=false) → 禁止再派生
 └── 组长 (depth=2, allow_spawn=true)  → 可再派生一层工人
       └── 工人 (depth=3) → 深度封顶，拒绝派生
```

- 深度/授权通过环境变量 `DEEPCODE_AGENT_DEPTH` / `DEEPCODE_AGENT_ALLOW_SPAWN` 自动传播
- `allow_spawn` 默认 false —— 递归是显式授权的，不是默认能力
- 深度硬封顶 3，物理防递归爆炸
- 并发上限 5 (模块级信号量)，防进程风暴 + API 限流
- 输出按 `max_output_chars` 截断 + `task_complete` 摘要，防上下文膨胀
- 每个工人用独立 `--workspace` 目录隔离，防写冲突

## 回归测试

`tests/` 目录内有 mock 冒烟测试（零 API 成本）：

```bash
# 本机 Windows 环境 (cmd 的 python 是 WindowsApps stub, 用 node 启动器跑)
node tests/run_test.js tests/test_agent_sdk.py   # 核心 18 项
node tests/run_test.js tests/test_mcp.py         # MCP 协议 7 项
node tests/run_test.js tests/test_spawn.py       # spawn_deepcode 21 项
node tests/verify_mcp.js                         # MCP server 真实启动冒烟
```
