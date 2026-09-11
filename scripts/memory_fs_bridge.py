#!/usr/bin/env python3
"""
DeepCode Memory ↔ Filesystem Bridge
═══════════════════════════════════
记忆系统与文件系统的双向桥接工具。

功能 #1 — Vault Obsidian 式管理:
    python scripts/memory_fs_bridge.py vault:index     # 生成 vault MOC 索引
    python scripts/memory_fs_bridge.py vault:tree      # 树形列出知识库
    python scripts/memory_fs_bridge.py vault:moc       # 生成每日/笔记/标签总览

功能 #2 — 记忆导出 Markdown:
    python scripts/memory_fs_bridge.py export          # 全量导出所有后端记忆到 .deepcode/memory/
    python scripts/memory_fs_bridge.py export --key X  # 导出单条

功能 #3 — 文件变更 → 记忆写入 (hook 入口):
    python scripts/memory_fs_bridge.py watch:record <path> [--change add|modify|delete]
    # 供 afterWrite / afterEdit / PreToolUse 等 hooks 调用
"""
import sys
import os
import json
import re
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from memory_manager import MemoryManager

VAULT_DIR = PROJECT_ROOT / ".deepcode" / "skills" / "deepcode-knowledge" / "data" / "vault"
MEMORY_DIR = PROJECT_ROOT / ".deepcode" / "memory"


def _safe(key: str) -> str:
    return re.sub(r'[^\w\u4e00-\u9fff\-.]', '_', key)[:80]


# ══════════════════════════════════════════════
# 功能 #1 — Vault Obsidian 式管理
# ══════════════════════════════════════════════

def vault_index():
    """生成 Vault MOC (Map of Content) 索引 — 让 filesystem 可直接浏览"""
    notes_dir = VAULT_DIR / "notes"
    daily_dir = VAULT_DIR / "daily"
    notes_dir.mkdir(parents=True, exist_ok=True)
    daily_dir.mkdir(parents=True, exist_ok=True)

    notes = sorted(notes_dir.glob("*.md"))
    dailies = sorted(daily_dir.glob("*.md"))

    lines = [
        "# 📚 DEEPCODE 知识库总览 (MOC)",
        "",
        f"> 自动生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 笔记: {len(notes)} · 日志: {len(dailies)}",
        "",
        "## 📝 笔记",
        "",
    ]
    for n in notes:
        lines.append(f"- [[{n.stem}]]")
    lines += ["", "## 📅 每日复盘", ""]
    for d in dailies:
        lines.append(f"- [[{d.stem}]]")
    lines.append("")

    moc_path = VAULT_DIR / "MOC.md"
    moc_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[vault:index] 已生成 {moc_path}  ({len(notes)} 笔记, {len(dailies)} 日志)")
    return str(moc_path)


def vault_tree(max_depth: int = 3):
    """树形列出知识库目录"""
    def walk(directory: Path, prefix: str = "", depth: int = 0):
        if depth > max_depth:
            return
        items = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        for i, item in enumerate(items):
            last = i == len(items) - 1
            branch = "└── " if last else "├── "
            size = ""
            if item.is_file():
                size = f"  ({item.stat().st_size // 1024}KB)"
            print(f"{prefix}{branch}{item.name}{size}")
            if item.is_dir():
                walk(item, prefix + ("    " if last else "│   "), depth + 1)

    print(f"📂 {VAULT_DIR}")
    walk(VAULT_DIR)


def vault_moc():
    """生成按标签/类型分组的 MOC"""
    notes_dir = VAULT_DIR / "notes"
    by_type = {}
    by_tag = {}
    for n in sorted(notes_dir.glob("*.md")):
        content = n.read_text(encoding="utf-8", errors="replace")
        fm = {}
        m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if m:
            for line in m.group(1).split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip().strip('"').strip("'")
        ntype = fm.get("type", "note")
        tags = [t.strip() for t in fm.get("tags", "").strip("[]").split(",") if t.strip()]
        by_type.setdefault(ntype, []).append(n.stem)
        for t in tags:
            by_tag.setdefault(t, []).append(n.stem)

    lines = [f"# 🏷️ 知识库标签索引 ({datetime.now().strftime('%Y-%m-%d')})", ""]
    lines += ["## 按类型", ""]
    for ntype, files in sorted(by_type.items()):
        lines.append(f"### {ntype} ({len(files)})")
        for f in files:
            lines.append(f"- [[{f}]]")
        lines.append("")
    lines += ["## 按标签", ""]
    for tag, files in sorted(by_tag.items()):
        lines.append(f"### #{tag} ({len(files)})")
        for f in files[:20]:
            lines.append(f"- [[{f}]]")
        lines.append("")

    out = VAULT_DIR / "TAGS.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[vault:moc] 已生成 {out}")
    return str(out)


# ══════════════════════════════════════════════
# 功能 #2 — 记忆导出 Markdown
# ══════════════════════════════════════════════

def export_memory(key: str = None):
    """把各后端记忆导出为 .deepcode/memory/*.md"""
    mm = MemoryManager()
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    if key:
        keys = [key]
    else:
        keys = mm.list()

    exported = 0
    for k in keys:
        value = mm.load(k)
        if value is None:
            continue
        tags = []
        # 尝试取标签
        try:
            conn = _db_conn()
            if conn:
                cur = conn.execute("SELECT tags FROM unified_memory WHERE key=?", (k,))
                row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    tags = json.loads(row[0])
        except Exception:
            pass

        content = (
            "---\n"
            f"type: memory\n"
            f"key: \"{k}\"\n"
            f"tags: {json.dumps(tags, ensure_ascii=False)}\n"
            f"created: {datetime.now().isoformat()}\n"
            "---\n\n"
            f"{value}\n"
        )
        # 键名若本身带 .md，避免双后缀 (progress.md → progress.md.md)
        fname = _safe(k)
        if fname.endswith(".md"):
            fname = fname[:-3]
        out = MEMORY_DIR / f"{fname}.md"
        out.write_text(content, encoding="utf-8")
        exported += 1

    # 重建 MEMORY.md 索引（直接调用后端索引，不产生垃圾条目）
    from memory_manager import MarkdownBackend
    MarkdownBackend()._update_index()
    print(f"[export] 已导出 {exported} 条记忆到 {MEMORY_DIR}")
    return exported


def _db_conn():
    import sqlite3
    try:
        return sqlite3.connect(str(PROJECT_ROOT / ".claude" / "memory.db"))
    except Exception:
        return None


# ══════════════════════════════════════════════
# 功能 #3 — 文件变更 → 记忆写入 (hook 入口)
# ══════════════════════════════════════════════

# 只记录这些类型的文件 — 避免临时文件/二进制文件刷屏
_WATCH_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".bat", ".ps1", ".html", ".css", ".txt"}
# 跳过这些目录
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build", ".next", ".cache", "%TEMP%"}
# 每文件最短记录间隔(秒) — 防止高频写入刷屏
_MIN_RECORD_INTERVAL = 60
_LAST_RECORD_FILE = PROJECT_ROOT / ".deepcode" / "memory" / ".watch_last.json"


def _load_last_records() -> dict:
    try:
        if _LAST_RECORD_FILE.exists():
            return json.loads(_LAST_RECORD_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_last_records(data: dict):
    try:
        _LAST_RECORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_RECORD_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def watch_record(path: str, change: str = "modify"):
    """记录文件变更到记忆 — 供 afterWrite/afterEdit hooks 调用

    Args:
        path: 被修改的文件路径
        change: add | modify | delete
    """
    p = Path(path)
    mm = MemoryManager()

    try:
        # ── 过滤: 跳过临时/二进制/无关文件 ──
        if any(part in _SKIP_DIRS for part in p.parts):
            return {"ok": True, "skipped": True, "reason": "skip-dir"}
        if p.suffix.lower() not in _WATCH_EXTENSIONS:
            return {"ok": True, "skipped": True, "reason": "skip-ext"}

        # ── 频率限制: 同一文件 60s 内只记一次 ──
        last_records = _load_last_records()
        now = time.time()
        last_ts = last_records.get(str(p), 0)
        if now - last_ts < _MIN_RECORD_INTERVAL:
            return {"ok": True, "skipped": True, "reason": "rate-limit"}

        if p.exists():
            stat = p.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        else:
            size = 0
            mtime = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 读取文件摘要
        preview = ""
        if p.exists() and p.is_file() and size > 0:
            try:
                preview = p.read_text(encoding="utf-8", errors="ignore")[:200]
            except Exception:
                preview = ""

        value = (
            f"- 变更: {change}\n"
            f"- 路径: {p}\n"
            f"- 大小: {size} 字节\n"
            f"- 时间: {mtime}\n"
            f"- 摘要: {preview}"
        )
        key = f"file:{_safe(p.stem or p.name)}:{change}"
        mm.save(key, value, backend="md", tags=["file", "change", change])

        # 记录本次时间戳
        last_records[str(p)] = now
        _save_last_records(last_records)
        return {"ok": True, "key": key, "path": str(p)}
        return {"ok": True, "key": key, "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "vault:index":
        vault_index()
    elif cmd == "vault:tree":
        vault_tree()
    elif cmd == "vault:moc":
        vault_moc()
    elif cmd == "export":
        key = None
        if "--key" in sys.argv:
            key = sys.argv[sys.argv.index("--key") + 1]
        export_memory(key)
    elif cmd == "watch:record":
        if len(sys.argv) < 3:
            print("Usage: memory_fs_bridge.py watch:record <path> [--change add|modify|delete]")
            sys.exit(1)
        path = sys.argv[2]
        change = "modify"
        if "--change" in sys.argv:
            change = sys.argv[sys.argv.index("--change") + 1]
        result = watch_record(path, change)
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
