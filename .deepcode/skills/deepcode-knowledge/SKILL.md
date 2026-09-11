---
name: deepcode-knowledge
description: >
  DeepCode 知识库引擎 — Obsidian式知识库。
  结构化Markdown笔记、模板(stock/daily/strategy/note)、[[wikilink]]双向链接、
  知识图谱、每日复盘。
  Use when saving analysis results, searching knowledge base, generating daily
  reviews, or building personal knowledge systems.
version: 1.0.0
author: DeepCode
date: 2026-07-29
tags: [knowledge, vault, obsidian, notes, templates, graph]
---

# DeepCode Knowledge Engine

Obsidian式知识库引擎（记忆功能已于 2026-08-12 收编至 deepcode-cerebellum）。

## 架构

```
AI 调用
  │
  ▼
knowledge_server.py (MCP, 8 tools)
  └── Vault 引擎 (8 tools)
      ├── Markdown 笔记 CRUD
      ├── [[wikilink]] 双向链接
      ├── 知识图谱
      ├── 模板渲染 (stock/daily/strategy/note)
      └── Obsidian 双向同步
```

## 8 工具一览

### Vault (知识库)
| 工具 | 说明 |
|:------|:-----|
| `knowledge__vault_status` | 知识库统计 — 笔记数、类型分布、磁盘占用 |
| `knowledge__save_analysis` | 保存分析结果 — 自动模板 + 标签 + [[wikilink]] |
| `knowledge__search_notes` | 全文搜索笔记 |
| `knowledge__read_note` | 读取笔记完整内容 (frontmatter + body) |
| `knowledge__show_graph` | 知识图谱 — 笔记关联网络 |
| `knowledge__daily_log` | 每日复盘 — generate/show/list |
| `knowledge__list_templates` | 列出 4 种分析模板 |
| `knowledge__sync_to_obsidian` | 同步到 Obsidian 仓库 |

> 记忆工具已于 2026-08-12 收编至 deepcode-cerebellum，统一入口: `cerebellum_memory_save/load/search/list/forget/stats`。

## MCP 注册

```json
"deepcode-knowledge": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-knowledge/knowledge_server.py"]
}
```

## 使用示例

```
# 保存茅台分析
mcp__deepcode-knowledge__knowledge__save_analysis
  type=stock, symbol=600519, title="茅台技术面分析"

# 搜索历史分析
mcp__deepcode-knowledge__knowledge__search_notes query=茅台

# 查看知识图谱
mcp__deepcode-knowledge__knowledge__show_graph symbol=600519
```
