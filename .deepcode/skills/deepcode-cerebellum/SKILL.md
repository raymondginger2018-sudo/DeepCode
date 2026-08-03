---
name: deepcode-cerebellum
description: >
  DeepCode 小脑统一记忆引擎 — 本地 Ollama 三模型 (qwen3:4b / deepseek-r1:1.5b /
  bge-m3) 驱动的系统记忆中枢。统一收编现有全部记忆后端
  (MemoryManager ruflo/claude/flow/tokensave/plan + knowledge vault + agent threads)，
  提供设置快照、语义检索、经验提取、会话摘要四大能力。
  Use when remembering DEEPCODE settings, searching past memories semantically,
  extracting lessons from completed tasks, or persisting session summaries.
version: 1.0.0
author: DeepCode
date: 2026-08-02
tags: [memory, cerebellum, ollama, settings, embedding, semantic-search, hooks]
---

# DeepCode 小脑统一记忆引擎

## 架构定位

```
🧠 大脑 (云端 DeepSeek V4)          ← 复杂推理、任务执行
        │  MCP 工具调用
        ▼
🧠 小脑 (deepcode-cerebellum)        ← 统一记忆中枢 (本引擎)
  ├── 统一记忆 API   (收编全部现有后端)
  ├── 设置快照       (settings.json 全量快照 + 变更检测)
  ├── 语义检索       (bge-m3 向量化)
  ├── 经验提取       (PostTask hook → qwen3:4b 提炼)
  └── 会话摘要       (PreCompact hook → 本地模型压缩)
        │  Ollama HTTP
        ▼
🖥️ 本地 Ollama (小脑硬件层)
  ├── qwen3:4b         通用文本 (摘要/分类/提取, 零成本)
  ├── deepseek-r1:1.5b 本地推理 (设置分析/经验关联)
  └── bge-m3            向量嵌入 (语义检索)
```

## 记忆分层模型

| 层级 | 内容 | 载体 | 触发时机 |
|:-----|:-----|:-----|:---------|
| L0 设置层 | DEEPCODE settings.json 快照 | SQLite `settings_snapshots` | SessionStart |
| L1 事实层 | KV 记忆 (统一 5 后端) | 各后端原生存储 | save API |
| L2 经验层 | 任务完成提炼的教训/模式 | SQLite `experience_entries` | PostTask |
| L3 会话层 | 会话摘要 (压缩前持久化) | SQLite `session_summaries` | PreCompact |
| L4 知识层 | 知识库笔记/vault | knowledge.db + vault notes | 分析结果落盘 |

## 统一收编的现有系统

| 现有系统 | 路径 | 收编方式 |
|:---------|:-----|:---------|
| MemoryManager ruflo | data/memory/memory.db | 代理调用 |
| MemoryManager claude | .claude/memory.db | 代理调用 |
| MemoryManager flow | .claude-flow/data/memory.json | 代理调用 |
| MemoryManager tokensave | token-saver/data/*.db | 代理调用 (只读) |
| MemoryManager plan | task_plan.md 等 | 代理调用 |
| knowledge vault | deepcode-knowledge/data/vault | 索引 + 向量化 |
| agent threads | database.db agent_threads | 状态索引 |

## MCP 注册

```json
"deepcode-cerebellum": {
  "command": "python",
  "args": [
    "F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/cerebellum_mcp_server.py",
    "--mcp"
  ],
  "env": {
    "CEREBELLUM_OLLAMA_HOST": "http://127.0.0.1:11434",
    "CEREBELLUM_LLM_MODEL": "qwen3:4b",
    "CEREBELLUM_EMBED_MODEL": "bge-m3",
    "CEREBELLUM_DB": "F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/data/cerebellum.db"
  }
}
```

## 使用示例

```
# 记忆设置 (大脑可随时查询)
mcp__deepcode-cerebellum__cerebellum_settings_snapshot
mcp__deepcode-cerebellum__cerebellum_settings_latest scope=project
mcp__deepcode-cerebellum__cerebellum_settings_search query="模型路由"

# 语义搜索历史记忆 (不再只靠关键词)
mcp__deepcode-cerebellum__cerebellum_memory_search query="之前怎么配置的模型路由"

# 保存/加载记忆 (统一走小脑)
mcp__deepcode-cerebellum__cerebellum_memory_save key=xxx value=yyy
mcp__deepcode-cerebellum__cerebellum_memory_load key=xxx

# 经验与摘要
mcp__deepcode-cerebellum__cerebellum_experience_record
mcp__deepcode-cerebellum__cerebellum_session_summarize

# r1 设置语义分析 (deepseek-r1:1.5b 本地推理)
mcp__deepcode-cerebellum__cerebellum_settings_analyze scope=project
mcp__deepcode-cerebellum__cerebellum_settings_analyses_history

# 跨会话经验关联图谱 (embedding 相似度建边)
mcp__deepcode-cerebellum__cerebellum_experience_graph min_similarity=0.5 rebuild=false
mcp__deepcode-cerebellum__cerebellum_experience_graph_query query="怎么配置 MCP"
```

## Hooks 三层接入

```json
"hooks": {
  "SessionStart": "python3 F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/cerebellum_cli.py session_start",
  "PostTask": "python3 F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/cerebellum_cli.py post_task --task {task}",
  "PreCompact": "python3 F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/cerebellum_cli.py pre_compact"
}
```

## 设计原则

- **小脑优先**：日常记忆/摘要/检索走本地模型 (零成本)，大脑只做最终消费
- **统一入口**：所有记忆操作经小脑 API，后端可替换
- **密钥完整存储**：按用户要求 settings 快照含密钥，本地 SQLite 存储 (不推送到 git)
- **语义优于关键词**：bge-m3 向量化让"记不清原词"也能找到
