#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用存档器: 改进 / 调研报告 / 笔记 → knowledge-vault + 小脑语义库
================================================================
以后所有改进、调研报告、技术笔记统一走此通道, 实现"存一次, 处处可检索":

  1. 写入 knowledge-vault/notes/{YYYYMMDD}-{标题}.md   (人可读, Obsidian 风格)
  2. 调用小脑 CerebellumMemory.save → KV 记忆 + bge-m3 语义索引
     (语义库可检索: 记不清标题原词也能用自然语言命中)

用法:
  python scripts/archive_to_cerebellum.py add --title "标题" --type improvement --content "..." [--tags 小脑,提速]
      --type: improvement|research|report|note   --content 或 --file report.md

  python scripts/archive_to_cerebellum.py search --query "小脑 提速"   # 验证检索

设计原则:
  - 幂等: 同 title 覆盖旧条目 (小脑 save 为 INSERT OR REPLACE 语义)
  - 小脑不可用 (Ollama 离线/依赖缺失) 时优雅降级: 仍写 vault, 返回退出码 2
  - 只新增不修改: 不改动任何既有模块, 纯增量工具
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CEREBELLUM_DIR = _PROJECT_ROOT / ".deepcode" / "skills" / "deepcode-cerebellum"
_VAULT_DIR = _PROJECT_ROOT / "knowledge-vault" / "notes"

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_CEREBELLUM_DIR) not in sys.path:
    sys.path.insert(0, str(_CEREBELLUM_DIR))

VALID_TYPES = ("improvement", "research", "report", "note")
TYPE_CN = {"improvement": "改进", "research": "调研", "report": "报告", "note": "笔记"}


def _import_cerebellum():
    """导入小脑核心库, 失败返回 None (优雅降级)"""
    try:
        from cerebellum_core import CerebellumMemory
        return CerebellumMemory
    except Exception as e:
        print(f"[archive] 小脑核心库导入失败: {e}", file=sys.stderr)
        return None


def _load_content(args) -> str:
    """内容来源: --content 或 --file"""
    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"[archive] 文件不存在: {p}", file=sys.stderr)
            sys.exit(1)
        return p.read_text(encoding="utf-8", errors="replace")
    if args.content:
        return args.content
    print("[archive] 需要 --content 或 --file", file=sys.stderr)
    sys.exit(1)


def _safe_title(title: str) -> str:
    """标题 → 文件名安全片段"""
    for ch in '\\/:*?"<>|':
        title = title.replace(ch, "_")
    return title.strip()


def cmd_add(args) -> int:
    date = datetime.now().strftime("%Y%m%d")
    title = _safe_title(args.title)
    typ = args.type if args.type in VALID_TYPES else "note"
    content = _load_content(args)
    tags = [typ, TYPE_CN.get(typ, typ)]
    if args.tags:
        tags += [t.strip() for t in args.tags.split(",") if t.strip()]

    # 1) 写入 knowledge-vault (人可读存档)
    _VAULT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{date}-{title}.md"
    note_path = _VAULT_DIR / fname
    note = (
        f"---\n"
        f"type: {typ}\n"
        f"date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"tags: [{', '.join(tags)}]\n"
        f"---\n\n"
        f"# {args.title}\n\n{content}\n"
    )
    note_path.write_text(note, encoding="utf-8")
    print(f"[archive] ✅ vault: {note_path.relative_to(_PROJECT_ROOT)}")

    # 2) 写入小脑语义库 (KV + bge-m3 向量)
    CerebellumMemory = _import_cerebellum()
    if CerebellumMemory is None:
        print("[archive] ⚠️ 小脑不可用, 已保留 vault 存档 (退出码 2)")
        return 2
    key = f"arc:{typ}:{args.title}"
    mem = CerebellumMemory()
    saved = mem.save(key=key, value=note, tags=tags)
    print(f"[archive] ✅ 小脑: key={key} backends={saved.get('backends')} "
          f"indexed={saved.get('indexed')}")
    return 0


def cmd_search(args) -> int:
    """检索验证: 语义搜索小脑语义库中 arc: 条目"""
    CerebellumMemory = _import_cerebellum()
    if CerebellumMemory is None:
        print("[search] 小脑不可用")
        return 2
    from cerebellum_core import DEFAULT_DB, ollama_embed
    import sqlite3

    qv = ollama_embed([args.query])[0]
    if not qv:
        print("[search] 向量化失败 (Ollama 不可用?)")
        return 2
    conn = sqlite3.connect(str(DEFAULT_DB))
    rows = conn.execute(
        "SELECT source_key, content, embedding, created_at FROM semantic_entries "
        "WHERE source_key LIKE 'arc:%'").fetchall()
    conn.close()
    res = []
    for key, content, emb_json, created in rows:
        if not emb_json:
            continue
        try:
            ev = json.loads(emb_json)
        except Exception:
            continue
        if not ev:
            continue
        n = sum(a * b for a, b in zip(qv, ev))
        d = (sum(a * a for a in qv) ** 0.5) * (sum(b * b for b in ev) ** 0.5)
        sim = n / d if d else 0.0
        res.append((sim, key, content, created))
    res.sort(key=lambda x: -x[0])
    print(f"[search] 命中 {len(res)} 条 (query={args.query!r})")
    for sim, key, content, created in res[: args.limit]:
        head = content.splitlines()[0:2]
        head = " | ".join(h.strip("# ").strip() for h in head if h.strip())
        print(f"  {sim:.2f}  {key}: {head[:100]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="通用存档器: 改进/调研/报告 → knowledge-vault + 小脑")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="存档一条改进/调研/报告/笔记")
    p_add.add_argument("--title", required=True, help="标题 (幂等键, 重复覆盖)")
    p_add.add_argument("--type", default="note", choices=VALID_TYPES,
                       help="improvement|research|report|note")
    p_add.add_argument("--content", default=None, help="内容文本")
    p_add.add_argument("--file", default=None, help="从 markdown/文本文件读取内容")
    p_add.add_argument("--tags", default=None, help="附加标签, 逗号分隔")

    p_sea = sub.add_parser("search", help="检索已存档条目 (语义)")
    p_sea.add_argument("--query", required=True)
    p_sea.add_argument("--limit", type=int, default=5)

    args = ap.parse_args()
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "search":
        return cmd_search(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
