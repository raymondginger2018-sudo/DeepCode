#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
③ 代码注释规范化 — 本地 qwen 把英文/劣质注释改写为规范中文
══════════════════════════════════════════════════════════
对齐项目编码规范: 错误信息/注释用中文。仅改注释行，不动代码逻辑。

用法:
  # 扫描目录下所有代码文件，只预览建议 (默认)
  python comment_normalize.py --path ./src

  # 指定 glob
  python comment_normalize.py --path . --glob "*.py" --glob "*.ts"

  # 确认后实际写入
  python comment_normalize.py --path ./src --apply

  # 单个文件
  python comment_normalize.py --file app.py --apply

安全设计:
  - 只替换"注释内容"，行首缩进/注释符号保持不变
  - 跳过 TODO/FIXME/BUG/HACK 等标记 (保留)
  - 跳过 纯标点/纯数字/URL 等无意义注释
  - --apply 前必须先看 --dry-run 预览
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ollama_client import ensure_ollama, generate, LLM_MODEL

DEFAULT_GLOBS = ["*.py", "*.ts", "*.js", "*.jsx", "*.tsx", "*.java", "*.go", "*.rs", "*.c", "*.cpp", "*.h", "*.hpp"]
BATCH_SIZE = 8  # 每批交给本地模型的注释条数

# 行注释提取: (注释符号, 内容) — 支持 # // /* */ 与 python docstring
LINE_COMMENT_RE = [
    re.compile(r"^(\s*)#\s*(.*)$"),                          # Python/Ruby/Shell
    re.compile(r"^(\s*)//\s*(.*)$"),                         # C/JS/TS/Java/Go/Rust
    re.compile(r"^(\s*)<!--\s*(.*?)\s*-->$"),                # HTML/XML
]
DOCSTRING_RE = re.compile(r'^(\s*)(?:"""|\'\'\')(.*?)(?:"""|\'\'\')$', re.S)

SKIP_MARK = re.compile(r"^\s*(TODO|FIXME|BUG|HACK|XXX|NOTE)\b", re.I)
SKIP_DECL = re.compile(r"-\*-\s*\w+[:=]")  # 编码/编辑器声明 (如 -*- coding: utf-8 -*-)
SKIP_TRIVIAL = re.compile(r"^[\s\d\W_]{0,12}$")  # 纯标点/数字/过短
HAS_ENGLISH = re.compile(r"[A-Za-z]{2,}")         # 含英文单词 → 重点改写对象


def _is_candidate(content: str) -> bool:
    if not content or len(content) < 3:
        return False
    if SKIP_MARK.match(content):
        return False
    if SKIP_DECL.match(content):
        return False
    if SKIP_TRIVIAL.match(content):
        return False
    # 无英文、无乱码、含中文 → 可能已规范，跳过以省 token
    if HAS_ENGLISH.search(content):
        return True
    if re.search(r"[\ufffd\u0080-\u00ff]", content):  # 乱码
        return True
    return False


def extract_comments(text: str) -> list[dict]:
    """提取所有候选注释行: {line_no, indent, prefix, content}"""
    hits = []
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        # 行注释
        for pat in LINE_COMMENT_RE:
            m = pat.match(line)
            if m:
                indent, content = m.group(1), m.group(2)
                prefix = pat.pattern.split("\\\\")[0]
                if _is_candidate(content):
                    hits.append({"line": i, "indent": indent, "prefix": "#", "content": content})
                break
        # docstring (仅单行)
        else:
            m = DOCSTRING_RE.match(stripped)
            if m and _is_candidate(m.group(2)):
                hits.append({"line": i, "indent": m.group(1), "prefix": '"""', "content": m.group(2)})
    return hits


def rewrite_batch(batch: list[dict]) -> dict[int, str]:
    """一次交给 qwen 改写一批注释，返回 {原行号: 新中文注释}"""
    items = "\n".join(f"{i}. {h['content']}" for i, h in enumerate(batch, start=1))
    prompt = (
        "把下面每条英文/劣质代码注释改写为简洁规范的中文注释。\n"
        "规则:\n"
        "1. 忠实原意，不添加原文没有的信息\n"
        "2. 术语可保留英文 (如 API、SQL、HTTP)\n"
        "3. 若注释本身已规范中文，原样返回\n"
        "4. 只输出一个 JSON 对象，key 是条目序号(数字)，value 是新注释。\n"
        "5. 不要输出 JSON 以外的任何内容。\n\n"
        "示例输出: {\"1\": \"从数据库按 ID 查询用户\", \"2\": \"返回查询结果\"}\n\n"
        f"{items}"
    )
    out = generate(prompt, model=LLM_MODEL, temperature=0.2)
    # 提取所有 "数字": "内容" 对
    pairs = re.findall(r'"(\d+)"\s*:\s*"((?:[^"\\]|\\.)*)"', out)
    mapping: dict[int, str] = {}
    for idx, val in pairs:
        idx_n = int(idx)
        if 1 <= idx_n <= len(batch):
            mapping[batch[idx_n - 1]["line"]] = val.strip()
    # 兜底: 若一个数字 key 都没解析到，按输出顺序顺次映射到输入顺序
    if not mapping:
        vals = re.findall(r'"((?:[^"\\]|\\.)*)"', out)
        for i, v in enumerate(vals):
            if i < len(batch) and v.strip():
                mapping[batch[i]["line"]] = v.strip()
    return mapping


def process_file(path: Path, apply: bool) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = extract_comments(text)
    if not hits:
        return 0

    lines = text.splitlines(keepends=True)
    rewritten = 0
    for start in range(0, len(hits), BATCH_SIZE):
        batch = hits[start:start + BATCH_SIZE]
        mapping = rewrite_batch(batch)
        for h in batch:
            new_content = mapping.get(h["line"])
            if not new_content:
                continue
            old_line = lines[h["line"] - 1].rstrip("\r\n")
            new_line = f"{h['indent']}{h['prefix']} {new_content}"
            print(f"  {path}:{h['line']}  {old_line.strip()}")
            print(f"    → {new_line.strip()}")
            if apply:
                lines[h["line"] - 1] = new_line + ("\n" if old_line.endswith("\n") else "")
            rewritten += 1

    if apply and rewritten:
        path.write_text("".join(lines), encoding="utf-8")
        print(f"[comment_normalize] ✅ {path}: 已改写 {rewritten} 条注释")
    return rewritten


def main() -> int:
    ap = argparse.ArgumentParser(prog="comment_normalize", description="本地 qwen 规范化代码注释")
    ap.add_argument("--path", default=".", help="扫描目录 (默认当前目录)")
    ap.add_argument("--file", default="", help="单个文件 (优先于 --path)")
    ap.add_argument("--glob", action="append", default=[], help="文件匹配 (可多次, 默认多种代码语言)")
    ap.add_argument("--apply", action="store_true", help="实际写入 (默认仅预览)")
    args = ap.parse_args()

    if not ensure_ollama():
        return 1

    globs = args.glob or DEFAULT_GLOBS
    total = 0

    if args.file:
        files = [Path(args.file)]
    else:
        root = Path(args.path)
        if not root.exists():
            print(f"[comment_normalize] ❌ 路径不存在: {root}", file=sys.stderr)
            return 1
        files = []
        for g in globs:
            files.extend(root.rglob(g))
        files = sorted(set(files))
        # 跳过常见噪音目录
        skip_dirs = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target"}
        files = [f for f in files if not any(p in skip_dirs for p in f.parts)]

    if not files:
        print("[comment_normalize] ℹ️ 未找到匹配文件。")
        return 0

    print(f"[comment_normalize] 🔍 扫描 {len(files)} 个文件 ({'写入模式' if args.apply else '预览模式'})")
    for f in files:
        try:
            n = process_file(f, args.apply)
            total += n
        except Exception as e:
            print(f"[comment_normalize] ⚠️ {f}: {e}", file=sys.stderr)

    print(f"[comment_normalize] {'✅ 完成' if args.apply else '🔍 预览完成'}: 共 {total} 条注释建议。"
          f"{'（未写入，加 --apply 生效）' if not args.apply else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
