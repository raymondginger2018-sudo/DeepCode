#!/usr/bin/env python3
"""
DeepCode Unified Memory Manager
═══════════════════════════════════
整合 5 套记忆系统为统一 API。
主力记忆 = md + ruflo + claude 三写同步 (primary 路由)，flow 已停用。

后  端             位  置                    格  式     状  态
──────────────────────────────────────────────────────────────
ruflo     data/memory/memory.db             SQLite 37表   ⚠️ 空
claude    .claude/memory.db                 SQLite 11表   ✅ 主力
flow      .claude-flow/data/*.json          JSON文件      ⛔ 已停用
tokensave .deepcode/skills/token-saver/data/ SQLite       ✅ 有数据
plan      task_plan.md + progress.md         Markdown     ✅ 有数据
md        .deepcode/memory/*.md              Markdown     ✅ 主力(自动注入)

API:
  save(key, value, backend="auto", tags=None) → str id
  load(key, backend="auto") → value or None
  search(query, backend="auto", limit=10) → list[dict]
  list(backend="auto") → list[str]
  forget(key, backend="auto") → bool
  stats() → dict

主力路由 (primary): auto 默认同时写入 md + ruflo + claude
  - md       → 每次会话自动注入系统提示 (AI 记得住)
  - ruflo    → RuFLO 引擎启用时数据已就绪
  - claude   → 兼容 Claude Code 客户端

CLI:
  python scripts/memory_manager.py save <key> <value> [--backend]
  python scripts/memory_manager.py load <key>
  python scripts/memory_manager.py search <query>
  python scripts/memory_manager.py list [--backend]
  python scripts/memory_manager.py forget <key>
  python scripts/memory_manager.py stats
  python scripts/memory_manager.py seed    # 写入示例数据启动记忆
"""

import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent

# ── 后端路径 ──
BACKEND_PATHS = {
    "ruflo": PROJECT_ROOT / "data" / "memory" / "memory.db",
    "claude": PROJECT_ROOT / ".claude" / "memory.db",
    "tokensave": PROJECT_ROOT / ".deepcode" / "skills" / "token-saver" / "data" / "token_saver_memory.db",
    "plan_dir": PROJECT_ROOT,  # task_plan.md / progress.md / findings.md
    # 功能#2: Markdown 落盘 — 让 filesystem MCP 可搜索 + 自动注入
    "md_dir": PROJECT_ROOT / ".deepcode" / "memory",  # <key>.md + MEMORY.md 索引
}


# ═══════════════════════════════════════════════════
# 核心 API
# ═══════════════════════════════════════════════════

class MemoryManager:
    """统一记忆管理器"""

    # 主力后端 — auto 默认写入这几个 (flow 已停用)
    PRIMARY_BACKENDS = ("md", "ruflo", "claude")

    def __init__(self):
        self._backends = {
            "ruflo": RuFloBackend(),
            "claude": ClaudeNativeBackend(),
            "tokensave": TokenSaverBackend(),
            "plan": PlanFilesBackend(),
            "md": MarkdownBackend(),
        }

    def _resolve_backend(self, backend: str):
        """解析 backend 参数为具体后端实例列表"""
        if backend == "auto":
            return list(self._backends.values())
        if backend == "primary":
            return [self._backends[name] for name in self.PRIMARY_BACKENDS]
        if backend in self._backends:
            return [self._backends[backend]]
        raise ValueError(f"Unknown backend: {backend}. Options: auto, primary, {', '.join(self._backends.keys())}")

    def save(self, key: str, value: str, backend: str = "auto",
             tags: Optional[list] = None) -> str:
        """保存记忆到最优后端"""
        record = {
            "key": key,
            "value": value,
            "tags": tags or [],
            "timestamp": datetime.now().isoformat(),
        }

        # auto: 按内容类型路由；默认走主力 (md+ruflo+claude 三写)
        if backend == "auto":
            if tags and any(t in ["task", "progress", "plan"] for t in tags):
                target = "plan"
            elif tags and any(t in ["compress", "token", "saving"] for t in tags):
                target = "tokensave"
            else:
                target = "primary"  # 主力三写: md + ruflo + claude
        else:
            target = backend

        backends = self._resolve_backend(target)
        for b in backends:
            try:
                b.save(record)
            except Exception:
                pass  # 单个后端失败不阻塞其他后端
        return key

    def load(self, key: str, backend: str = "auto"):
        """加载记忆"""
        for b in self._resolve_backend(backend):
            result = b.load(key)
            if result is not None:
                return result
        return None

    def search(self, query: str, backend: str = "auto", limit: int = 10) -> list:
        """搜索记忆"""
        results = []
        for b in self._resolve_backend(backend):
            results.extend(b.search(query, limit))
            if len(results) >= limit:
                break
        return results[:limit]

    def list(self, backend: str = "auto") -> list:
        """列出所有记忆键名"""
        all_keys = []
        for b in self._resolve_backend(backend):
            all_keys.extend(b.list_keys())
        return sorted(set(all_keys))

    def forget(self, key: str, backend: str = "auto") -> bool:
        """删除记忆"""
        found = False
        for b in self._resolve_backend(backend):
            if b.forget(key):
                found = True
        return found

    def stats(self) -> dict:
        """所有后端统计"""
        return {name: bk.stats() for name, bk in self._backends.items()}

    def seed(self):
        """写入示例记忆，启动记忆系统"""
        seeds = [
            ("user_preference", json.dumps({
                "model": "deepseek-v4-pro",
                "reasoning_effort": "max",
                "thinking": True,
            }), ["preference", "user"], "primary"),
            ("workflow_pattern", "股票分析流程: scan -> analyze -> chanlun -> report",
             ["pattern", "workflow"], "primary"),
            ("last_task", "整合记忆系统", ["task", "progress"], "plan"),
            ("mcp_server_list", ",".join([
                "tushareMcp", "playwright", "filesystem", "sqlite",
                "duckdb", "github", "ghidra-mcp", "router-mcp",
            ]), ["config", "mcp"], "primary"),
        ]
        count = 0
        for key, value, tags, backend in seeds:
            try:
                self.save(key, value, backend=backend, tags=tags)
                count += 1
            except Exception as e:
                print(f"  [seed] Failed: {key}: {e}")
        print(f"[memory_manager] Seeded {count} memories")
        return count


# ═══════════════════════════════════════════════════
# 后端适配器
# ═══════════════════════════════════════════════════

class BaseBackend:
    """后端基类"""
    name = "base"

    def save(self, record: dict):
        raise NotImplementedError

    def load(self, key: str):
        raise NotImplementedError

    def search(self, query: str, limit: int):
        raise NotImplementedError

    def list_keys(self) -> list:
        raise NotImplementedError

    def forget(self, key: str) -> bool:
        raise NotImplementedError

    def stats(self) -> dict:
        return {"status": "unknown"}


class RuFloBackend(BaseBackend):
    """RuFlo V3 记忆引擎 — data/memory/memory.db"""
    name = "ruflo"

    def __init__(self):
        self.db_path = BACKEND_PATHS["ruflo"]

    def _connect(self):
        return sqlite3.connect(str(self.db_path))

    def save(self, record: dict):
        try:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO memory_entries (id, key, content, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (record["key"], record["key"], record["value"],
                 json.dumps(record.get("tags", [])),
                 int(time.time()), int(time.time())),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as e:
            pass

    def load(self, key: str):
        try:
            conn = self._connect()
            cur = conn.execute("SELECT content FROM memory_entries WHERE key=?", (key,))
            row = cur.fetchone()
            conn.close()
            return row[0] if row else None
        except sqlite3.OperationalError:
            pass
        return None

    def search(self, query: str, limit: int = 10):
        try:
            conn = self._connect()
            cur = conn.execute(
                "SELECT key, content FROM memory_entries WHERE key LIKE ? OR content LIKE ? LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            )
            rows = cur.fetchall()
            conn.close()
            return [{"key": r[0], "value": r[1], "backend": "ruflo"} for r in rows]
        except sqlite3.OperationalError:
            return []

    def list_keys(self):
        try:
            conn = self._connect()
            cur = conn.execute("SELECT key FROM memory_entries")
            keys = [r[0] for r in cur.fetchall()]
            conn.close()
            return keys
        except sqlite3.OperationalError:
            return []

    def forget(self, key: str) -> bool:
        try:
            conn = self._connect()
            conn.execute("DELETE FROM memory_entries WHERE key=?", (key,))
            conn.commit()
            affected = conn.total_changes
            conn.close()
            return affected > 0
        except sqlite3.OperationalError:
            return False

    def stats(self):
        try:
            conn = self._connect()
            cur = conn.execute("SELECT COUNT(*) FROM memory_entries")
            count = cur.fetchone()[0]
            conn.close()
            return {"status": "active", "entries": count}
        except sqlite3.OperationalError:
            return {"status": "empty (table not ready)", "entries": 0}


class ClaudeNativeBackend(BaseBackend):
    """Claude Code 原生记忆 — .claude/memory.db"""
    name = "claude"

    def __init__(self):
        self.db_path = BACKEND_PATHS["claude"]

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unified_memory (
                key TEXT PRIMARY KEY,
                value TEXT,
                tags TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()
        return conn

    def save(self, record: dict):
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO unified_memory (key, value, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (record["key"], record["value"], json.dumps(record.get("tags", [])),
             record["timestamp"], record["timestamp"]),
        )
        conn.commit()
        conn.close()

    def load(self, key: str):
        conn = self._connect()
        cur = conn.execute("SELECT value FROM unified_memory WHERE key=?", (key,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    def search(self, query: str, limit: int = 10):
        conn = self._connect()
        cur = conn.execute(
            "SELECT key, value FROM unified_memory WHERE key LIKE ? OR value LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        )
        rows = cur.fetchall()
        conn.close()
        return [{"key": r[0], "value": r[1], "backend": "claude"} for r in rows]

    def list_keys(self):
        conn = self._connect()
        cur = conn.execute("SELECT key FROM unified_memory ORDER BY updated_at DESC")
        keys = [r[0] for r in cur.fetchall()]
        conn.close()
        return keys

    def forget(self, key: str) -> bool:
        conn = self._connect()
        conn.execute("DELETE FROM unified_memory WHERE key=?", (key,))
        conn.commit()
        affected = conn.total_changes
        conn.close()
        return affected > 0

    def stats(self):
        conn = self._connect()
        cur = conn.execute("SELECT COUNT(*) FROM unified_memory")
        count = cur.fetchone()[0]
        conn.close()
        return {"status": "active", "entries": count}


class ClaudeFlowBackend(BaseBackend):
    """Claude Flow 记忆 — ⛔ 已停用 (2026-07-31)

    原实现写入 .claude-flow/data/memory.json，但 claude-flow daemon 已停止，
    无消费方。数据已并入主力 (md/ruflo/claude)，此类仅保留占位防止外部
    import 报错，不再注册进 _backends。
    """
    name = "flow"

    def __init__(self):
        self._disabled = True

    def save(self, record: dict):
        return None

    def load(self, key: str):
        return None

    def search(self, query: str, limit: int = 10):
        return []

    def list_keys(self):
        return []

    def forget(self, key: str) -> bool:
        return False

    def stats(self):
        return {"status": "disabled (2026-07-31)", "entries": 0, "note": "claude-flow daemon 已停止，后端停用"}


class TokenSaverBackend(BaseBackend):
    """Token Saver 记忆 — 压缩历史"""
    name = "tokensave"

    def __init__(self):
        self.db_path = BACKEND_PATHS["tokensave"]

    def _connect(self):
        try:
            return sqlite3.connect(str(self.db_path))
        except Exception:
            return None

    def save(self, record: dict):
        conn = self._connect()
        if not conn:
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO memory (key, value) VALUES (?, ?)",
                (record["key"], record["value"]),
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        conn.close()

    def load(self, key: str):
        conn = self._connect()
        if not conn:
            return None
        try:
            cur = conn.execute("SELECT value FROM memory WHERE key=?", (key,))
            row = cur.fetchone()
            conn.close()
            return row[0] if row else None
        except sqlite3.OperationalError:
            conn.close()
            return None

    def search(self, query: str, limit: int = 10):
        return []  # token_saver DB 结构不确定，跳过搜索

    def list_keys(self):
        conn = self._connect()
        if not conn:
            return []
        try:
            cur = conn.execute("SELECT key FROM memory")
            keys = [r[0] for r in cur.fetchall()]
            conn.close()
            return keys
        except sqlite3.OperationalError:
            conn.close()
            return []

    def forget(self, key: str) -> bool:
        conn = self._connect()
        if not conn:
            return False
        try:
            conn.execute("DELETE FROM memory WHERE key=?", (key,))
            conn.commit()
            affected = conn.total_changes
            conn.close()
            return affected > 0
        except sqlite3.OperationalError:
            conn.close()
            return False

    def stats(self):
        return {"status": "readonly (token saver internal)", "note": "use for compression history only"}


class PlanFilesBackend(BaseBackend):
    """Planning-with-files — task_plan.md / progress.md / findings.md"""
    name = "plan"

    def __init__(self):
        self.root = BACKEND_PATHS["plan_dir"]

    def save(self, record: dict):
        key = record["key"]
        value = record["value"]
        tags = record.get("tags", [])

        if "progress" in tags or "task" in tags:
            # 追加到 progress.md
            path = self.root / "progress.md"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"\n### {key} — {timestamp}\n{value}\n"
            path.write_text(entry, encoding="utf-8") if not path.exists() else open(path, "a", encoding="utf-8").write(entry)

        elif "plan" in tags:
            # 更新 task_plan.md
            path = self.root / "task_plan.md"
            if not path.exists():
                path.write_text(f"# {key}\n\n{value}\n", encoding="utf-8")

        else:
            # 存 findings.md
            path = self.root / "findings.md"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"\n## {key} ({timestamp})\n{value}\n"
            path.write_text(entry, encoding="utf-8") if not path.exists() else open(path, "a", encoding="utf-8").write(entry)

    def load(self, key: str):
        for fname in ["task_plan.md", "progress.md", "findings.md"]:
            path = self.root / fname
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if key in content:
                    return content
        return None

    def search(self, query: str, limit: int = 10):
        results = []
        for fname in ["task_plan.md", "progress.md", "findings.md"]:
            path = self.root / fname
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            if query.lower() in content.lower():
                # 提取匹配段落
                for line in content.split("\n"):
                    if query.lower() in line.lower():
                        results.append({
                            "key": fname,
                            "value": line.strip()[:200],
                            "backend": "plan",
                        })
                        if len(results) >= limit:
                            break
        return results

    def list_keys(self):
        keys = []
        for fname in ["task_plan.md", "progress.md", "findings.md"]:
            path = self.root / fname
            if path.exists():
                keys.append(fname)
        return keys

    def forget(self, key: str) -> bool:
        return False  # 不能直接删除 plan 文件中的内容

    def stats(self):
        files = {}
        for fname in ["task_plan.md", "progress.md", "findings.md"]:
            path = self.root / fname
            if path.exists():
                files[fname] = f"{path.stat().st_size / 1024:.1f}KB"
        return {"status": "active", "files": files}


class MarkdownBackend(BaseBackend):
    """Markdown 落盘后端 — 记忆 ↔ 文件系统桥接 (功能#2)

    每次 save 自动写入 `.deepcode/memory/<key>.md`，并更新 `MEMORY.md` 索引，
    这样 filesystem MCP 可以直接全文搜索/读取记忆。
    """
    name = "md"

    def __init__(self):
        self.mem_dir = BACKEND_PATHS["md_dir"]
        self.index_path = self.mem_dir / "MEMORY.md"
        self.mem_dir.mkdir(parents=True, exist_ok=True)

    # ── 内部工具 ──
    def _safe_filename(self, key: str) -> str:
        safe = re.sub(r'[^\w\u4e00-\u9fff\-.]', '_', key)
        return safe[:80] or "unnamed"

    def _entry_path(self, key: str) -> Path:
        return self.mem_dir / f"{self._safe_filename(key)}.md"

    def _build_md(self, record: dict) -> str:
        fm = [
            "---",
            f"type: memory",
            f"key: \"{record['key']}\"",
            f"tags: {json.dumps(record.get('tags', []), ensure_ascii=False)}",
            f"created: {record.get('timestamp', datetime.now().isoformat())}",
            "---",
            "",
        ]
        body = record.get("value", "")
        return "\n".join(fm) + str(body) + "\n"

    def _update_index(self):
        """重建 MEMORY.md 索引 — 含每条记忆内容摘要

        此文件会被 core/harness/memory.py 在每次会话自动注入系统提示，
        所以带上内容摘要 (每条≤200字符)，AI 启动即可"记得"关键事实。
        """
        entries = []
        for f in sorted(self.mem_dir.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'^key: "?(.+?)"?\s*$', content, re.MULTILINE)
                key = m.group(1) if m else f.stem
                m2 = re.search(r'^tags: (\[.*?\])', content, re.MULTILINE)
                tags = m2.group(1) if m2 else "[]"
                m3 = re.search(r'^created: (.+)$', content, re.MULTILINE)
                created = m3.group(1) if m3 else "?"
                # 提取正文摘要 (去掉 frontmatter，压缩换行为空格)
                body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL).strip()
                summary = re.sub(r'\s+', ' ', body)[:200]
                entries.append((key, tags, created, f.name, summary))
            except Exception:
                entries.append((f.stem, "[]", "?", f.name, ""))

        lines = [
            "# DEEPCODE 记忆索引 (MEMORY.md)",
            "",
            "> 每次会话自动注入系统提示 · 由 MarkdownBackend 自动生成",
            f"> 共 {len(entries)} 条记忆 · 更新时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "| 键 | 标签 | 内容摘要 |",
            "|---|---|---|",
        ]
        for key, tags, created, fname, summary in entries:
            safe_summary = summary.replace("|", "\\|")
            lines.append(f"| {key} | {tags} | {safe_summary} |")
        lines.append("")
        self.index_path.write_text("\n".join(lines), encoding="utf-8")

    # ── 后端 API ──
    def save(self, record: dict):
        self._entry_path(record["key"]).write_text(self._build_md(record), encoding="utf-8")
        self._update_index()

    def load(self, key: str):
        p = self._entry_path(key)
        if not p.exists():
            return None
        content = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)
        return m.group(2).strip() if m else content.strip()

    def search(self, query: str, limit: int = 10):
        results = []
        for f in sorted(self.mem_dir.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if query.lower() in f.stem.lower() or query.lower() in content.lower():
                results.append({"key": f.stem, "value": content[:300], "backend": "md", "file": str(f)})
                if len(results) >= limit:
                    break
        return results

    def list_keys(self):
        return [f.stem for f in sorted(self.mem_dir.glob("*.md")) if f.name != "MEMORY.md"]

    def forget(self, key: str) -> bool:
        p = self._entry_path(key)
        if p.exists():
            p.unlink()
            self._update_index()
            return True
        return False

    def stats(self):
        files = list(self.mem_dir.glob("*.md"))
        return {
            "status": "active",
            "entries": len(files) - (1 if self.index_path.exists() else 0),
            "dir": str(self.mem_dir),
            "index": str(self.index_path),
        }


# ═══════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="DeepCode Unified Memory Manager")
    parser.add_argument("command", choices=["save", "load", "search", "list", "forget", "stats", "seed"],
                        help="操作")
    parser.add_argument("key", nargs="?", help="记忆键名")
    parser.add_argument("value", nargs="?", help="记忆值 (仅 save)")
    parser.add_argument("--backend", default="auto", help="后端 (auto/primary/ruflo/claude/md/tokensave/plan; flow 已停用)")
    parser.add_argument("--tags", nargs="*", default=[], help="标签 (仅 save)")
    parser.add_argument("--limit", type=int, default=10, help="搜索限制 (仅 search)")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    mm = MemoryManager()

    if args.command == "save":
        if not args.key or args.value is None:
            print("Usage: memory_manager.py save <key> <value> [--tags ...] [--backend ...]")
            sys.exit(1)
        result = mm.save(args.key, args.value, backend=args.backend, tags=args.tags)
        print(f"[memory_manager] Saved: {result}" if not args.json else json.dumps({"saved": result}))

    elif args.command == "load":
        if not args.key:
            print("Usage: memory_manager.py load <key>")
            sys.exit(1)
        result = mm.load(args.key, backend=args.backend)
        if args.json:
            print(json.dumps({"key": args.key, "value": result}))
        else:
            print(f"Value: {result}" if result else f"Not found: {args.key}")

    elif args.command == "search":
        if not args.key:
            print("Usage: memory_manager.py search <query>")
            sys.exit(1)
        results = mm.search(args.key, backend=args.backend, limit=args.limit)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(f"Found {len(results)} results:")
            for r in results:
                print(f"  [{r['backend']}] {r['key']}: {str(r['value'])[:80]}")

    elif args.command == "list":
        keys = mm.list(backend=args.backend)
        if args.json:
            print(json.dumps(keys))
        else:
            print(f"Memory keys ({len(keys)}):")
            for k in keys:
                print(f"  - {k}")

    elif args.command == "forget":
        if not args.key:
            print("Usage: memory_manager.py forget <key>")
            sys.exit(1)
        result = mm.forget(args.key, backend=args.backend)
        print(f"[memory_manager] Deleted: {result}")

    elif args.command == "stats":
        stats = mm.stats()
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print("Memory Backend Stats:")
            print(f"{'Backend':<12} {'Status':<20} {'Entries':<10}")
            print("-" * 45)
            for name, s in stats.items():
                entries = s.get("entries", s.get("files", "N/A"))
                status = s.get("status", "?")
                print(f"{name:<12} {str(status):<20} {str(entries):<10}")

    elif args.command == "seed":
        count = mm.seed()
        print(f"[memory_manager] Seeded {count} memories")
        if args.json:
            print(json.dumps({"seeded": count}))


if __name__ == "__main__":
    import sys
    main()
