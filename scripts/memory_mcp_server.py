#!/usr/bin/env python3
"""
Memory Manager MCP Server — 供 AI 直接调用
═══════════════════════════════════════════════
通过标准 MCP JSON-RPC 2.0 协议暴露记忆管理器。

修复记录:
  2026-07-29: 修复 import path + 实现标准 MCP 协议 (initialize/tools/list/tools/call)

MCP 配置:
```json
"deepcode-memory": {
  "command": "python",
  "args": ["F:/DEEPCODE/scripts/memory_mcp_server.py"]
}
```
"""

import json
import os
import sys

# ── 修复导入路径: 确保 scripts/ 目录在 sys.path ──
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from memory_manager import MemoryManager

mm = MemoryManager()

# ── 工具定义（用于 tools/list） ──
TOOLS = [
    {
        "name": "deepcode-memory__save",
        "description": "保存一条记忆。key: 键名, value: 值, tags: 标签列表, backend: 后端(默认auto)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "记忆键名"},
                "value": {"type": "string", "description": "记忆值"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                "backend": {"type": "string", "description": "后端名称(默认auto)"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "deepcode-memory__load",
        "description": "加载指定键的记忆。key: 键名",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "记忆键名"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "deepcode-memory__search",
        "description": "搜索记忆。query: 搜索词, limit: 返回数量上限",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索词"},
                "limit": {"type": "integer", "description": "返回数量上限(默认10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "deepcode-memory__list",
        "description": "列出所有记忆键名",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "deepcode-memory__forget",
        "description": "删除一条记忆。key: 键名",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "记忆键名"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "deepcode-memory__stats",
        "description": "查看记忆系统统计信息",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def handle_tool_call(tool_name: str, arguments: dict) -> dict:
    """处理 tools/call — 与旧版 handle_request 兼容"""
    if tool_name == "deepcode-memory__save":
        key = arguments.get("key", "")
        value = arguments.get("value", "")
        tags = arguments.get("tags", [])
        backend = arguments.get("backend", "auto")
        if not key:
            return {"ok": False, "error": "Missing 'key'"}
        mm.save(key, value, backend=backend, tags=tags)
        return {"ok": True, "result": f"saved: {key}"}

    elif tool_name == "deepcode-memory__load":
        key = arguments.get("key", "")
        if not key:
            return {"ok": False, "error": "Missing 'key'"}
        value = mm.load(key)
        return {"ok": True, "result": value}

    elif tool_name == "deepcode-memory__search":
        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)
        results = mm.search(query, limit=limit)
        return {"ok": True, "result": results}

    elif tool_name == "deepcode-memory__list":
        keys = mm.list()
        return {"ok": True, "result": keys}

    elif tool_name == "deepcode-memory__forget":
        key = arguments.get("key", "")
        if not key:
            return {"ok": False, "error": "Missing 'key'"}
        ok = mm.forget(key)
        return {"ok": True, "result": ok}

    elif tool_name == "deepcode-memory__stats":
        stats = mm.stats()
        return {"ok": True, "result": stats}

    else:
        return {"ok": False, "error": f"Unknown tool: {tool_name}"}


# ═══ MCP JSON-RPC 2.0 stdio 主循环 ═══
if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            err = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"},
                "id": None,
            }
            sys.stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        req_id = request.get("id", 1)
        method = request.get("method", "")
        params = request.get("params", {})

        # ── initialize ──
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "deepcode-memory", "version": "1.0"},
                },
            }
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        # ── notifications/initialized ──
        if method == "notifications/initialized":
            continue

        # ── tools/list ──
        if method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            }
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        # ── tools/call ──
        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                result = handle_tool_call(tool_name, tool_args)
                is_error = not result.get("ok", True)
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, ensure_ascii=False, default=str),
                            }
                        ],
                        "isError": is_error,
                    },
                }
            except Exception as e:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                }
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        # ── 兼容旧版直接方法调用（过渡期） ──
        try:
            result = handle_tool_call(method, params)
            is_error = not result.get("ok", True)
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    ],
                    "isError": is_error,
                },
            }
        except Exception as e:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method} ({e})"},
            }
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
