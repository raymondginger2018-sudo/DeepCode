---
name: deepcode-cerebellum
description: >
  DeepCode 小脑统一记忆引擎 — 本地 Ollama 三模型 (qwen2.5:3b / deepseek-r1:1.5b /
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
  ├── 经验提取       (PostTask hook → qwen2.5:3b 提炼)
  └── 会话摘要       (SessionEnd hook → 本地模型压缩)
        │  Ollama HTTP
        ▼
🖥️ 本地 Ollama (小脑硬件层)
  ├── qwen2.5:3b       通用文本 (摘要/分类/提取, 零成本)
  ├── deepseek-r1:1.5b 本地推理 (设置分析/经验关联)
  └── bge-m3            向量嵌入 (语义检索)
```

## 记忆分层模型

| 层级 | 内容 | 载体 | 触发时机 |
|:-----|:-----|:-----|:---------|
| L0 设置层 | DEEPCODE settings.json 快照 | SQLite `settings_snapshots` | SessionStart |
| L1 事实层 | KV 记忆 (统一 5 后端) | 各后端原生存储 | save API |
| L2 经验层 | 任务完成提炼的教训/模式 | SQLite `experience_entries` | PostTask |
| L3 会话层 | 会话摘要 (会话结束强制持久化) | SQLite `session_summaries` | SessionEnd |
| L4 知识层 | 知识库笔记/vault | knowledge.db + vault notes | 分析结果落盘 |
| L4.5 实体层 ⭐ | 语义关系图（实体-关系三元组，LightRAG 图索引借鉴，2026-08-29 新增） | SQLite `entity_nodes` + `entity_edges` | 经验/知识入库时抽三元组 |

> **L4.5 实体层说明**（来源 `F:\DS-HARNESS\docs\hkuds-study\03-lightrag-retrieval-patterns.md`，对标 LightRAG `llm_roles.py` / `operate.py::_perform_kg_search`）：
> - **抽取**：用 `ollama_generate_json`（qwen2.5:3b）从 `experience_record` / 笔记提取实体-关系三元组，写入 `entity_nodes` / `entity_edges` 表（轻量 SQLite 图，不上 Neo4J；实体数 >1 万再评估 Milvus+Neo4J 双后端）。
> - **检索**：`_semantic_query` 从"`LIMIT 200` 暴力余弦"升级为**双层级检索**：① local——实体名命中取邻接实体；② global——沿 `entity_edges` 扩散关系链；③ 三段注入（实体 + 关系 + 原文块）供 LLM 作答。
> - **一致性**：`memory_forget` 加引用检查（防删载体留孤儿实体），`semantic_entries` 加 doc_status 列，fail-closed 删除。
> - **价值**：把"相似度聚簇经验图"升级为"语义关系图"，解决"检索到但没用上"的教训。

## 技能进化证据门控（E3，OpenSpace SkillLineage 借鉴，2026-08-29）

- **版本 DAG**：`skill_evolution_propose` 记录当前 SKILL.md 内容哈希与血缘（imported/derived）；`skill_evolution_apply` 落 `skill_lineage_versions` 版本快照（generation+1、content、content_hash、parent_hash、proposal_id）。
- **证据信任**：apply 为人工确认路径 → trust=trusted；reject → trust=rejected；默认 provisional。
- **查询**：`cerebellum_skill_lineage_history(skill_name)` 看血缘；`cerebellum_skill_version_diff(skill_name, from_g, to_g)` 看版本 content diff。
- **模型层**：`core/skills/models.py` 的 `SkillRecord` 新增 lineage/trust/parent_id/generation/content_hash 字段 + `derive()` / `with_trust()` / `content_digest()`（向后兼容默认值）。

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
    "CEREBELLUM_LLM_MODEL": "qwen2.5:3b",
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

# 保存/加载记忆 (统一走小脑, 已收编原 knowledge__memory_* 系列)
mcp__deepcode-cerebellum__cerebellum_memory_save key=xxx value=yyy
mcp__deepcode-cerebellum__cerebellum_memory_load key=xxx
mcp__deepcode-cerebellum__cerebellum_memory_forget key=xxx
mcp__deepcode-cerebellum__cerebellum_memory_stats

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
  "SessionEnd": "python3 F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/cerebellum_cli.py session_end",
  "OnError": "python3 F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/cerebellum_cli.py on_error"
}
```

> 迁移说明: 会话摘要原挂在 `PreCompact` 事件 (依赖 core CLI 压缩触发, 可能不触发)。
> 已迁移至 `SessionEnd` — 任何会话结束必然沉淀摘要, 不再依赖 compact。
> `PreCompact` 事件保留给 token-saver-prewarm (压缩加速仍有用)。

## 记忆进化引擎 (对标 MindMemOS)

由 `cerebellum_evolution.py` 承载，4 大模块，全部 SQLite 存储、本地模型驱动：

| 模块 | 功能 | 触发方式 |
|:----|:----|:--------|
| **Dreaming 离线巩固** | 跨会话 session_summaries/experience_entries 聚类 → LLM 合并 → 归档 (consolidated 标记) | `cerebellum_cli.py dreaming [--kind session\|experience] [--no-llm]` / MCP `cerebellum_dreaming_run` |
| **反馈闭环** | 显式 (rating 1/-1/-2) + 隐式 (OnError 自动 -1) 反馈回灌检索排序 (`similarity += FEEDBACK_WEIGHT × net`) | `cerebellum_cli.py feedback_add --target-type ... --target-key ... --rating ...` / MCP `cerebellum_feedback_add` |
| **Skill 进化信号** | 失败信号采集，≥3 次自动 LLM 分析失败模式 → 生成提案 (pending)，人工确认后追加到 SKILL.md 进化记录章节 | `skill_signal / skill_propose / skill_proposals / skill_apply / skill_reject` / MCP 对应工具 |
| **自评测** | Recall@1 / Recall@k / MRR 基线分，输出 Markdown 报告到 `data/benchmark_report.md` | `cerebellum_cli.py benchmark [--top-k N]` / MCP `cerebellum_benchmark_run` |

### OnError 隐式闭环

错误发生时自动执行三步：① 失败信号写入 `skill_signals` ② 隐式负反馈写入 `feedback_entries` ③ 失败数 ≥3 自动生成 Skill 进化提案（计数不足时零 LLM 开销）。

## 设计原则

- **小脑优先**：日常记忆/摘要/检索走本地模型 (零成本)，大脑只做最终消费
- **统一入口**：所有记忆操作经小脑 API，后端可替换
- **密钥完整存储**：按用户要求 settings 快照含密钥，本地 SQLite 存储 (不推送到 git)
- **进化不破坏**：Dreaming 只归档不删除原始数据；Skill 提案人工确认后只追加不改写 SKILL.md
- **语义优于关键词**：bge-m3 向量化让"记不清原词"也能找到
