# -*- coding: utf-8 -*-
"""批量提取 Cursor extensions JS 情报: IPC 通道 / 工具名 / SQLite schema / endpoint / MCP 配置"""
import os, re, json, sys

ROOT = r"C:\Users\raymo\Downloads\cursor_install\resources\app\extensions"
OUT = {}

PATTERNS = {
    "ipc_channel":  re.compile(rb'[\'"]((?:cursor|mcp|agent|workspace|retrieval)[\w.\-:/]*(?:channel|pipe|port|socket|request|response|event|message|topic)[\w.\-:/]*)[\'"]', re.I),
    "create_table": re.compile(rb'CREATE\s+TABLE[^;]{20,600}', re.I),
    "endpoint":     re.compile(rb'https?://[\w.\-]+(?:\.(?:cursor|anysphere|statsig|posthog|sentry|openai|anthropic|huggingface))[^\s"\'`)]{0,120}'),
    "tool_name":    re.compile(rb'"(?:tool|name|command)"\s*:\s*"([a-z_]{3,40})"', re.I),
    "mcp_server":   re.compile(rb'(command|args|transport|stdio|sse|serverName|url)\s*[:=]\s*[^,}]{3,80}', re.I),
}

for ext in sorted(os.listdir(ROOT)):
    if not ext.startswith("cursor"):
        continue
    ext_dir = os.path.join(ROOT, ext)
    if not os.path.isdir(ext_dir):
        continue
    info = {"package": None, "hits": {k: [] for k in PATTERNS}, "js_bytes": 0, "js_files": 0}
    pkg = os.path.join(ext_dir, "package.json")
    if os.path.exists(pkg):
        try:
            info["package"] = json.load(open(pkg, encoding="utf-8"))
        except Exception:
            pass
    for dirpath, _, files in os.walk(ext_dir):
        for f in files:
            if not f.endswith((".js", ".cjs", ".mjs", ".ts")):
                continue
            p = os.path.join(dirpath, f)
            try:
                data = open(p, "rb").read()
            except Exception:
                continue
            if len(data) > 8_000_000:  # 跳过超大的 (map 等)
                continue
            info["js_bytes"] += len(data)
            info["js_files"] += 1
            for key, rx in PATTERNS.items():
                try:
                    for m in rx.finditer(data):
                        s = m.group(0)
                        if len(s) > 500:
                            continue
                        try:
                            s = s.decode("utf-8", "replace")
                        except Exception:
                            continue
                        if s not in info["hits"][key]:
                            info["hits"][key].append(s)
                            if len(info["hits"][key]) > 40:
                                break
                except Exception:
                    pass
    # 截断输出
    for k in info["hits"]:
        info["hits"][k] = info["hits"][k][:25]
    if info["package"]:
        info["package"] = {
            "name": info["package"].get("name"),
            "version": info["package"].get("version"),
            "description": info["package"].get("description"),
            "contributes_commands": [c.get("command") for c in (info["package"].get("contributes", {}).get("commands") or [])][:15],
        }
    OUT[ext] = info

print(json.dumps(OUT, ensure_ascii=False, indent=1)[:60000])
