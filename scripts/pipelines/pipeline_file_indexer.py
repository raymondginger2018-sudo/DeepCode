#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
管道 2: 文件变更 → 知识库索引 (Hook 端)
=======================================
由 PostToolUse hook 触发，将变更文件元数据写入待索引队列。
之后由 AI 或 pipeline_flush_index.py 消费队列 → knowledge MCP。

Hook 配置 (settings.json):
  "PostToolUse": [{
    "matcher": "Write|Edit",
    "type": "command",
    "command": "python3 F:/DEEPCODE/scripts/pipelines/pipeline_file_indexer.py {file_path}",
    "timeout": 10
  }]

队列文件: F:/DEEPCODE/.deepcode/pending_index.jsonl
"""
import sys, json, os
from pathlib import Path
from datetime import datetime

QUEUE_FILE = Path(__file__).parent.parent.parent / ".deepcode" / "pending_index.jsonl"


def extract_metadata(filepath: str) -> dict:
    """提取文件元数据，准备入库"""
    p = Path(filepath)
    if not p.exists():
        return {"path": filepath, "error": "file not found", "indexed_at": datetime.now().isoformat()}

    st = p.stat()
    meta = {
        "path": str(p.relative_to(Path.cwd())) if str(p).startswith(str(Path.cwd())) else str(p),
        "name": p.name,
        "suffix": p.suffix,
        "size": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
        "indexed_at": datetime.now().isoformat(),
    }

    # 提取前 5 行作为预览
    if p.is_file() and p.suffix in (".py", ".md", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".txt"):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[:5]
            meta["preview"] = "\n".join(lines)[:300]
        except Exception:
            meta["preview"] = "(binary or unreadable)"

    # Python 文件: 提取 import 和函数名
    if p.suffix == ".py":
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            imports = [l.strip() for l in content.splitlines() if l.strip().startswith(("import ", "from "))][:10]
            defs = [l.strip() for l in content.splitlines() if l.strip().startswith(("def ", "class "))][:10]
            if imports:
                meta["imports"] = imports
            if defs:
                meta["definitions"] = defs
        except Exception:
            pass

    return meta


def main():
    if len(sys.argv) < 2:
        print("Usage: pipeline_file_indexer.py <file_path>")
        return

    filepath = sys.argv[1]
    meta = extract_metadata(filepath)

    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")

    # 静默输出: 不污染 context
    queue_size = 0
    if QUEUE_FILE.exists():
        queue_size = QUEUE_FILE.stat().st_size
    print(f"[indexer] queued: {Path(filepath).name}  (queue: {queue_size} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
