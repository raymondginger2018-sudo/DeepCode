#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode 小脑 — MCP Server
═══════════════════════════
让大脑 (DeepSeek) 通过 MCP 工具访问小脑记忆引擎:
  - 设置快照/查询/搜索
  - 统一记忆存取
  - 经验记录/搜索
  - 会话摘要/最近会话
  - 知识库索引
  - 小脑健康状态

注册到 settings.json:
  "deepcode-cerebellum": {
    "command": "python",
    "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/cerebellum_mcp_server.py", "--mcp"]
  }
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cerebellum_core import (
    CerebellumMemory,
    experience_graph,
    experience_graph_query,
    experience_record,
    experience_search,
    index_vault,
    ollama_status,
    overview,
    session_recent,
    session_summarize,
    settings_analyses_history,
    settings_analyze,
    settings_latest,
    settings_search,
    settings_snapshot,
)

# ═══════════════════════════════════════════
# MCP 工具定义
# ═══════════════════════════════════════════

TOOLS = {
    "cerebellum_overview": {
        "description": "小脑全景状态 — Ollama 健康/模型/记忆统计/数据库位置",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cerebellum_ollama_status": {
        "description": "小脑硬件层 (Ollama) 健康检查 — 模型列表/可用性",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cerebellum_settings_snapshot": {
        "description": "快照 settings.json 到记忆库 (含变更检测), scope: all/project/user",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": {"type": "string", "description": "all|project|user, 默认 all"}},
        },
    },
    "cerebellum_settings_latest": {
        "description": "读取最新设置快照 — 含完整版与脱敏版 (密钥指纹展示)",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": {"type": "string", "description": "project|user, 默认 project"}},
        },
    },
    "cerebellum_settings_search": {
        "description": "在设置快照中搜索配置项 — 关键词 + 语义双通道 (记不清原词也能找到)",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索词, 如 '模型路由' 'hooks' 'mcp 服务器'"}},
        },
        "required": ["query"],
    },
    "cerebellum_memory_save": {
        "description": "保存记忆 — 统一收编到全部记忆后端 + 语义索引",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
        "required": ["key", "value"],
    },
    "cerebellum_memory_load": {
        "description": "读取记忆 (统一后端 + 语义兜底)",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
        },
        "required": ["key"],
    },
    "cerebellum_memory_search": {
        "description": "搜索记忆 — 语义优先, 关键词兜底, 返回双通道结果",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        },
        "required": ["query"],
    },
    "cerebellum_memory_list": {
        "description": "列出所有记忆键名",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cerebellum_experience_record": {
        "description": "记录一条经验教训 (PostTask 提炼, 本地模型)",
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "任务描述"}},
        },
        "required": ["task"],
    },
    "cerebellum_experience_search": {
        "description": "搜索历史经验教训 — 语义相似度匹配",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        "required": ["query"],
    },
    "cerebellum_session_summarize": {
        "description": "生成会话摘要并持久化 (PreCompact, 本地模型)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "对话内容 (截断到 4000 字符)"},
                "session_id": {"type": "string"},
            },
        },
    },
    "cerebellum_session_recent": {
        "description": "查看最近会话摘要",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "默认 5"}},
        },
    },
    "cerebellum_index_vault": {
        "description": "索引知识库 vault 笔记到语义检索 (支持跨笔记语义搜索)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cerebellum_settings_analyze": {
        "description": "用 deepseek-r1:1.5b 语义分析 settings.json — 配置语义/风险/建议 (幂等缓存)",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": {"type": "string", "description": "project|user, 默认 project"}},
        },
    },
    "cerebellum_settings_analyses_history": {
        "description": "查看设置语义分析历史",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 5"},
            },
        },
    },
    "cerebellum_experience_graph": {
        "description": "构建/查看跨会话经验关联图谱 — embedding 相似度建边 (rebuild=True 重建)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_similarity": {"type": "number", "description": "建边阈值, 默认 0.5"},
                "rebuild": {"type": "boolean", "description": "True 重建全部边"},
            },
        },
    },
    "cerebellum_experience_graph_query": {
        "description": "按任务语义查询经验图谱 — 返回最相关经验节点及其关联邻居",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        "required": ["query"],
    },
}


def _call(name: str, args: dict) -> dict:
    """分发工具调用"""
    if name == "cerebellum_overview":
        return overview()
    if name == "cerebellum_ollama_status":
        return ollama_status()
    if name == "cerebellum_settings_snapshot":
        return settings_snapshot(args.get("scope", "all"))
    if name == "cerebellum_settings_latest":
        return settings_latest(args.get("scope", "project"))
    if name == "cerebellum_settings_search":
        return settings_search(args["query"])
    if name == "cerebellum_memory_save":
        return CerebellumMemory().save(args["key"], args["value"], args.get("tags"))
    if name == "cerebellum_memory_load":
        return CerebellumMemory().load(args["key"])
    if name == "cerebellum_memory_search":
        return CerebellumMemory().search(args["query"], limit=args.get("limit", 10))
    if name == "cerebellum_memory_list":
        return {"ok": True, "keys": CerebellumMemory().list()}
    if name == "cerebellum_experience_record":
        return experience_record(args["task"])
    if name == "cerebellum_experience_search":
        return experience_search(args["query"])
    if name == "cerebellum_session_summarize":
        return session_summarize(args.get("context", ""), args.get("session_id", "adhoc"))
    if name == "cerebellum_session_recent":
        return session_recent(limit=args.get("limit", 5))
    if name == "cerebellum_index_vault":
        return index_vault()
    if name == "cerebellum_settings_analyze":
        return settings_analyze(args.get("scope", "project"))
    if name == "cerebellum_settings_analyses_history":
        return settings_analyses_history(args.get("scope", "project"),
                                         limit=args.get("limit", 5))
    if name == "cerebellum_experience_graph":
        return experience_graph(
            min_similarity=float(args.get("min_similarity", 0.5)),
            rebuild=bool(args.get("rebuild", False)))
    if name == "cerebellum_experience_graph_query":
        return experience_graph_query(args["query"])
    return {"ok": False, "error": f"未知工具: {name}"}


# ═══════════════════════════════════════════
# stdio JSON-RPC 2.0 循环
# ═══════════════════════════════════════════

def run_mcp() -> None:
    # Windows 下强制 UTF-8, 避免中文参数 surrogate 崩溃 (同 deepcode-agent 修复)
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        rid = req.get("id", None)

        if method == "initialize":
            print(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "deepcode-cerebellum", "version": "1.0.0"},
                },
            }), flush=True)

        elif method == "tools/list":
            tools = [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]}
                     for k, v in TOOLS.items()]
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}),
                  flush=True)

        elif method == "tools/call":
            name = req.get("params", {}).get("name", "")
            args = req.get("params", {}).get("arguments", {}) or {}
            try:
                result = _call(name, args)
                content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                out = {"jsonrpc": "2.0", "id": rid, "result": {"content": content}}
            except Exception as e:
                out = {"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32000, "message": f"{type(e).__name__}: {e}",
                                 "data": traceback.format_exc()[-2000:]}}
            print(json.dumps(out, ensure_ascii=False), flush=True)

        elif method == "notifications/initialized":
            pass  # 无操作
        elif method == "ping":
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}), flush=True)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="cerebellum_mcp_server")
    ap.add_argument("--mcp", action="store_true", help="MCP stdio 模式")
    ap.add_argument("cmd", nargs="?", default=None,
                    help="CLI 命令: overview/settings_snapshot/settings_latest/settings_search/memory_save/memory_search/experience_record/session_summarize/index_vault")
    args = ap.parse_args()

    if args.mcp:
        run_mcp()
        return 0
    if args.cmd:
        # 代理到 core CLI
        from cerebellum_core import main as core_main
        sys.argv = [sys.argv[0], args.cmd] + sys.argv[3:]
        core_main()
        return 0
    print(json.dumps(overview(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
