#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CodeRetrieval v1.0 — Cursor 风格代码检索 (RAG)
=============================================
参考: Cursor cursor-retrieval extension

基于 keywords + ripgrep + 简单向量索引的代码检索系统。

用法:
  cr = CodeRetrieval("/path/to/project")
  cr.index()                              # 索引代码库
  results = cr.search("function_name")    # 搜索
"""

import hashlib, json, os, re, sqlite3, time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class CodeChunk:
    """代码块 — 函数/类定义"""
    path: str
    name: str
    type: str          # function / class / method
    line_start: int
    line_end: int
    content: str
    signature: str = ""
    language: str = ""
    keywords: List[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """搜索结果"""
    path: str
    name: str
    type: str
    line: int
    content: str
    score: float = 0.0
    context_before: List[str] = field(default_factory=list)
    context_after: List[str] = field(default_factory=list)


# 常见编程语言的注释/函数模式
LANG_PATTERNS = {
    ".py":  {"comment": "#", "func": r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', "class": r'^\s*class\s+(\w+)'},
    ".js":  {"comment": "//", "func": r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', "class": r'^\s*class\s+(\w+)'},
    ".ts":  {"comment": "//", "func": r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', "class": r'^\s*class\s+(\w+)'},
    ".rs":  {"comment": "//", "func": r'^\s*(?:pub\s+)?(?:unsafe\s+)?fn\s+(\w+)\s*\(', "class": r'^\s*struct\s+(\w+)|^\s*enum\s+(\w+)'},
    ".go":  {"comment": "//", "func": r'^\s*(?:func\s+)(?:\w+\.)?(\w+)\s*\(', "class": r''},
    ".java": {"comment": "//", "func": r'^\s*(?:public|private|protected|static|\s)*\s+\w+\s+(\w+)\s*\(', "class": r'^\s*(?:public|abstract|final\s+)?class\s+(\w+)'},
    ".cpp": {"comment": "//", "func": r'(?:\w+\s*::\s*)?(\w+)\s*\(', "class": r'^\s*class\s+(\w+)'},
    ".c":   {"comment": "//", "func": r'(?:\w+\s+)+(\w+)\s*\(', "class": r'^\s*struct\s+(\w+)'},
}


class CodeRetrieval:
    """
    代码检索系统 — Cursor retrieval 风格

    - ripgrep 搜索
    - 函数/类定义索引
    - 关键字匹配 + 哈希去重
    - SQLite 持久化
    """

    def __init__(self, workspace: str, db_path: str = ""):
        self.workspace = os.path.abspath(workspace)
        self.db_path = db_path or os.path.join(self.workspace, ".deepcode", "code_retrieval.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        self._chunks: Dict[str, CodeChunk] = {}
        self._stats = {"indexed_files": 0, "indexed_chunks": 0, "searches": 0}

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS chunks ("
                     "id TEXT PRIMARY KEY, path TEXT, name TEXT, type TEXT,"
                     "line_start INT, line_end INT, content TEXT, "
                     "signature TEXT, language TEXT, keywords TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_path ON chunks(path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON chunks(name)")
        conn.commit()
        conn.close()

    def _detect_language(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        return {"py": "python", "js": "javascript", "ts": "typescript",
                "rs": "rust", "go": "go", "java": "java",
                "cpp": "cpp", "c": "c", "h": "c", "hpp": "cpp"}.get(ext.lstrip("."), "")

    def _parse_functions(self, path: str, content: str) -> List[CodeChunk]:
        """解析文件中的函数和类定义"""
        ext = Path(path).suffix.lower()
        patterns = LANG_PATTERNS.get(ext, LANG_PATTERNS.get(".py"))
        if not patterns:
            return []

        lang = self._detect_language(path)
        lines = content.splitlines()
        chunks = []

        for i, line in enumerate(lines, 1):
            # Match function
            m = re.search(patterns["func"], line) if patterns["func"] else None
            if m:
                name = m.group(1) or m.group(0)
                chunk = CodeChunk(path=path, name=name, type="function",
                                  line_start=i, line_end=i + 20,
                                  content="\n".join(lines[i-1:i+20]),
                                  signature=line.strip(),
                                  language=lang,
                                  keywords=[name] + re.findall(r'\w{3,}', line.lower()))
                chunks.append(chunk)

            # Match class
            if patterns.get("class"):
                m = re.search(patterns["class"], line)
                if m:
                    name = m.group(1) or m.group(2) or ""
                    if name:
                        chunk = CodeChunk(path=path, name=name, type="class",
                                          line_start=i, line_end=i + 30,
                                          content="\n".join(lines[i-1:i+30]),
                                          signature=line.strip(),
                                          language=lang,
                                          keywords=[name] + re.findall(r'\w{3,}', line.lower()))
                        chunks.append(chunk)

        return chunks

    # ── 索引 ──

    def index(self, paths: List[str] = None) -> int:
        """
        索引代码库

        Args:
            paths: 要索引的文件列表 (None = 全库)

        Returns:
            索引的代码块数量
        """
        if paths is None:
            paths = self._find_code_files()

        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM chunks WHERE path IN ({})".format(
            ",".join("?" for _ in paths)), paths)

        count = 0
        for path in paths:
            abs_path = path if os.path.isabs(path) else os.path.join(self.workspace, path)
            if not os.path.exists(abs_path):
                continue
            try:
                with open(abs_path, encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            chunks = self._parse_functions(abs_path, content)
            for chunk in chunks:
                cid = hashlib.md5(f"{chunk.path}:{chunk.name}:{chunk.line_start}".encode()).hexdigest()[:12]
                conn.execute(
                    "INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (cid, chunk.path, chunk.name, chunk.type,
                     chunk.line_start, chunk.line_end, chunk.content[:2000],
                     chunk.signature[:200], chunk.language,
                     json.dumps(chunk.keywords)))
                count += 1

            self._stats["indexed_files"] += 1

        conn.commit()
        conn.close()
        self._stats["indexed_chunks"] += count
        return count

    def _find_code_files(self) -> List[str]:
        """查找可索引的代码文件"""
        exts = {".py", ".js", ".ts", ".rs", ".go", ".java", ".cpp", ".c", ".h", ".hpp"}
        ignore_dirs = {".git", "node_modules", "__pycache__", ".venv",
                       "venv", "target", "build", "dist", ".deepcode"}
        files = []
        for root, dirs, names in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for name in names:
                if Path(name).suffix in exts:
                    files.append(os.path.join(root, name))
        return files

    # ── 搜索 ──

    def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        """
        搜索代码

        Args:
            query: 搜索关键词
            limit: 最大结果数

        Returns:
            SearchResult 列表
        """
        self._stats["searches"] += 1
        results = []

        # 1. SQLite 关键词匹配
        conn = sqlite3.connect(self.db_path)
        terms = re.findall(r'\w{2,}', query.lower())
        for term in terms:
            rows = conn.execute(
                "SELECT DISTINCT path, name, type, line_start, content, signature, keywords "
                "FROM chunks WHERE name LIKE ? OR keywords LIKE ? LIMIT ?",
                (f"%{term}%", f"%{term}%", limit)).fetchall()

            for row in rows:
                kw_list = json.loads(row[6]) if row[6] else []
                score = sum(1 for t in terms if t in kw_list or t in row[1].lower())
                results.append(SearchResult(
                    path=row[0], name=row[1], type=row[2],
                    line=int(row[3]), content=row[4][:500],
                    score=score,
                ))
        conn.close()

        # 2. ripgrep 全文搜索
        try:
            import subprocess
            rg = subprocess.run(
                ["rg", "-n", "--color=never", "-m", "5", query, self.workspace],
                capture_output=True, text=True, timeout=10
            )
            for line in rg.stdout.splitlines()[:limit]:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    rel = os.path.relpath(parts[0], self.workspace)
                    results.append(SearchResult(
                        path=rel, name="", type="match",
                        line=int(parts[1]), content=parts[2][:200],
                        score=0.5,
                    ))
        except Exception:
            pass

        # 去重 + 排序
        seen = set()
        unique = []
        for r in sorted(results, key=lambda x: -x.score):
            key = f"{r.path}:{r.line}"
            if key not in seen:
                seen.add(key)
                unique.append(r)

        return unique[:limit]

    def get_stats(self) -> dict:
        return dict(self._stats)
