#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
5 项功能整合进小脑 — 统一桥接脚本
====================================
1. rev-deposit / rev-lookup : 逆向分析摘要入小脑 + sha256 查重 (重复分析不重复反编译)
2. browser-save / browser-recall : 浏览器操作序列记忆 (重复任务一键重放)
3. design-index : CAD / name_fortune / game_project 设计参数与经验沉淀
4. tools-index : 25 个 MCP 工具能力清单入小脑 (语义路由 "哪个工具适合什么任务")
5. vault-chunk : knowledge-vault 全文分块向量化 (深化 L4 知识层, 非仅前 500 字符)

用法:
  python scripts/integrate_cerebellum.py rev-deposit --file sample.exe --summary "摘要..."
  python scripts/integrate_cerebellum.py rev-lookup --file sample.exe
  python scripts/integrate_cerebellum.py browser-save --site https://x --task "登录" --steps '[{"action":"click","target":"@e1"}]'
  python scripts/integrate_cerebellum.py browser-recall --query "登录 财务网站"
  python scripts/integrate_cerebellum.py design-index
  python scripts/integrate_cerebellum.py tools-index
  python scripts/integrate_cerebellum.py vault-chunk
  python scripts/integrate_cerebellum.py status

设计原则:
  - 只读现有模块, 不改动任何既有代码 (纯增量)
  - 小脑不可用时优雅降级 (exit 2), 不阻断主流程
  - 幂等: 同 key 覆盖旧条目 (INSERT OR REPLACE 语义)
"""
import argparse
import ast
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CEREBELLUM_DIR = _PROJECT_ROOT / ".deepcode" / "skills" / "deepcode-cerebellum"
_SETTINGS = _PROJECT_ROOT / ".deepcode" / "settings.json"

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_CEREBELLUM_DIR) not in sys.path:
    sys.path.insert(0, str(_CEREBELLUM_DIR))


def _import_cerebellum():
    """导入小脑核心库, 失败返回 None (优雅降级)"""
    try:
        from cerebellum_core import CerebellumMemory, DEFAULT_DB, ollama_embed
        return CerebellumMemory, DEFAULT_DB, ollama_embed
    except Exception as e:
        print(f"[cerebellum] 小脑核心库导入失败: {e}", file=sys.stderr)
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _semantic_search(ollama_embed, db_path, query: str, prefix: str, limit: int = 5):
    """在小脑语义库中按前缀 + 余弦相似度检索"""
    import sqlite3
    qv = ollama_embed([query])[0]
    if not qv:
        return []
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT source_key, content, embedding, created_at FROM semantic_entries "
        "WHERE source_key LIKE ?", (prefix + "%",)).fetchall()
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
    return res[:limit]


# ── 1. 逆向分析 ──────────────────────────────────────────
def cmd_rev_deposit(args) -> int:
    dep = _import_cerebellum()
    if dep is None:
        print("[rev-deposit] 跳过: 小脑不可用")
        return 2
    CerebellumMemory, db_path, ollama_embed = dep
    f = Path(args.file)
    if not f.exists():
        print(f"[rev-deposit] 文件不存在: {f}", file=sys.stderr)
        return 1
    digest = _sha256(f)
    key = f"rev:{digest}"
    # 查重: 已分析过 → 不重复反编译
    mem = CerebellumMemory()
    existing = mem.load(key).get("value")
    if existing:
        print(f"[rev-deposit] 已分析过 (sha256={digest[:12]}...), 跳过反编译:")
        print(f"  {str(existing)[:200]}")
        return 0
    payload = {
        "file": str(f),
        "name": f.name,
        "size": f.stat().st_size,
        "sha256": digest,
        "summary": args.summary or "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    value = json.dumps(payload, ensure_ascii=False)
    saved = mem.save(key=key, value=value, tags=["逆向", "反编译", f"sha256:{digest[:12]}"])
    print(f"[rev-deposit] ✅ 逆向摘要入小脑: key={key}")
    print(f"  backends={saved.get('backends')} indexed={saved.get('indexed')}")
    return 0


def cmd_rev_lookup(args) -> int:
    dep = _import_cerebellum()
    if dep is None:
        print("[rev-lookup] 跳过: 小脑不可用")
        return 2
    CerebellumMemory, _, _ = dep
    f = Path(args.file)
    if not f.exists():
        print(f"[rev-lookup] 文件不存在: {f}", file=sys.stderr)
        return 1
    digest = _sha256(f)
    key = f"rev:{digest}"
    mem = CerebellumMemory()
    loaded = mem.load(key).get("value")
    if not loaded:
        print(f"[rev-lookup] 未命中: {f.name} (sha256={digest[:12]}...) — 需要反编译")
        return 1
    print(f"[rev-lookup] ✅ 命中历史分析 (sha256={digest[:12]}...):")
    try:
        obj = json.loads(loaded)
        print(f"  文件: {obj.get('name')} ({obj.get('size', 0)} bytes)")
        print(f"  摘要: {obj.get('summary', '')[:300]}")
    except Exception:
        print(f"  {str(loaded)[:300]}")
    return 0


# ── 2. 浏览器操作序列 ────────────────────────────────────
def cmd_browser_save(args) -> int:
    dep = _import_cerebellum()
    if dep is None:
        print("[browser-save] 跳过: 小脑不可用")
        return 2
    CerebellumMemory, _, _ = dep
    try:
        steps = json.loads(args.steps)
    except Exception:
        steps = [args.steps]
    site = args.site.rstrip("/")
    key = f"browser:{site}:{args.task}"
    payload = {
        "site": site,
        "task": args.task,
        "steps": steps,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    value = json.dumps(payload, ensure_ascii=False)
    saved = CerebellumMemory().save(
        key=key, value=value,
        tags=["浏览器", "操作序列", f"site:{site}"],
    )
    print(f"[browser-save] ✅ 操作序列入小脑: key={key}")
    print(f"  {len(steps)} 步 | backends={saved.get('backends')} indexed={saved.get('indexed')}")
    return 0


def cmd_browser_recall(args) -> int:
    dep = _import_cerebellum()
    if dep is None:
        print("[browser-recall] 跳过: 小脑不可用")
        return 2
    CerebellumMemory, db_path, ollama_embed = dep
    hits = _semantic_search(ollama_embed, db_path, args.query, "browser:", args.limit)
    print(f"[browser-recall] 命中 {len(hits)} 条操作序列 (query={args.query!r})")
    if not hits:
        print("  无记录 — 需要先执行一次并 browser-save")
        return 1
    for sim, key, content, created in hits:
        # semantic_entries.content 存的是 "{key}: {json}" (CerebellumMemory.save 拼接),
        # 先剥离 key 前缀再解析 JSON
        raw = content
        if raw.startswith(key):
            raw = raw[len(key):].lstrip(": ")
        try:
            obj = json.loads(raw)
            site = obj.get("site", "")
            task = obj.get("task", "")
            n = len(obj.get("steps", []))
            print(f"  {sim:.2f}  {site} [{task}] ({n} 步, {created[:10]})")
            if args.show:
                for i, s in enumerate(obj.get("steps", []), 1):
                    print(f"    {i}. {json.dumps(s, ensure_ascii=False)[:120]}")
        except Exception:
            print(f"  {sim:.2f}  {key}: {str(raw)[:120]}")
    return 0


# ── 3. 设计参数沉淀 ──────────────────────────────────────
def _extract_docstring(path: Path) -> str:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
        return ast.get_docstring(tree) or ""
    except Exception:
        return ""


def cmd_design_index(args) -> int:
    dep = _import_cerebellum()
    if dep is None:
        print("[design-index] 跳过: 小脑不可用")
        return 2
    CerebellumMemory, _, _ = dep
    mem = CerebellumMemory()
    indexed = 0

    # CAD 脚本
    cad_dir = _PROJECT_ROOT / "CAD"
    if cad_dir.exists():
        for py in sorted(cad_dir.glob("*.py"))[:50]:
            doc = _extract_docstring(py).strip()
            key = f"design:cad:{py.stem}"
            value = json.dumps({
                "domain": "CAD", "name": py.stem, "path": str(py.relative_to(_PROJECT_ROOT)),
                "doc": doc[:500],
            }, ensure_ascii=False)
            mem.save(key=key, value=value, tags=["设计", "CAD", "工程图纸"])
            indexed += 1
        print(f"[design-index] CAD: {indexed} 个脚本已索引")

    # name_fortune
    nf_dir = _PROJECT_ROOT / "name_fortune"
    nf_n = 0
    if nf_dir.exists():
        for py in sorted(list((nf_dir / "engine").glob("*.py")) + list(nf_dir.glob("*.py")))[:30]:
            doc = _extract_docstring(py).strip()
            key = f"design:fortune:{py.stem}"
            value = json.dumps({
                "domain": "name_fortune", "name": py.stem,
                "path": str(py.relative_to(_PROJECT_ROOT)), "doc": doc[:400],
            }, ensure_ascii=False)
            mem.save(key=key, value=value, tags=["设计", "起名", "命理"])
            nf_n += 1
        print(f"[design-index] name_fortune: {nf_n} 个模块已索引")

    # game_project (.mcp.json 配置)
    gp_dir = _PROJECT_ROOT / "game_project"
    gp_n = 0
    if gp_dir.exists():
        for mcp in gp_dir.glob(".mcp.json"):
            try:
                cfg = json.loads(mcp.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
            for sname, scfg in (cfg.get("mcpServers") or {}).items():
                key = f"design:game:{sname}"
                value = json.dumps({
                    "domain": "game_project", "name": sname, "mcp": scfg,
                }, ensure_ascii=False)
                mem.save(key=key, value=value, tags=["设计", "游戏", "MCP"])
                gp_n += 1
        print(f"[design-index] game_project: {gp_n} 个 MCP 配置已索引")

    print(f"[design-index] ✅ 设计参数沉淀完成: CAD {indexed} + fortune {nf_n} + game {gp_n}")
    return 0


# ── 4. 工具路由经验 ──────────────────────────────────────
def cmd_tools_index(args) -> int:
    dep = _import_cerebellum()
    if dep is None:
        print("[tools-index] 跳过: 小脑不可用")
        return 2
    CerebellumMemory, _, _ = dep
    if not _SETTINGS.exists():
        print("[tools-index] settings.json 不存在", file=sys.stderr)
        return 1
    data = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or {}
    mem = CerebellumMemory()
    n = 0
    for name, cfg in sorted(servers.items()):
        cmd = cfg.get("command", "")
        args_ = " ".join(str(a) for a in (cfg.get("args") or []))
        key = f"tool:{name}"
        value = json.dumps({
            "server": name, "command": cmd, "args": args_[:200],
            "note": "MCP 工具服务器 — 按任务语义检索选择",
        }, ensure_ascii=False)
        mem.save(key=key, value=value, tags=["工具路由", "MCP", f"server:{name}"])
        n += 1
    print(f"[tools-index] ✅ {n} 个 MCP 工具服务器能力入小脑")
    return 0


# ── 5. knowledge-vault 全文分块 ──────────────────────────
def _chunk_text(text: str, size: int = 500, overlap: int = 100):
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def cmd_vault_chunk(args) -> int:
    dep = _import_cerebellum()
    if dep is None:
        print("[vault-chunk] 跳过: 小脑不可用")
        return 2
    CerebellumMemory, _, _ = dep
    vault = _PROJECT_ROOT / "knowledge-vault" / "notes"
    if not vault.exists():
        print(f"[vault-chunk] 目录不存在: {vault}", file=sys.stderr)
        return 1
    mem = CerebellumMemory()
    total_chunks = 0
    for f in sorted(vault.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        chunks = _chunk_text(text, args.chunk_size, args.overlap)
        for i, chunk in enumerate(chunks):
            key = f"vault:{f.stem}:{i:03d}"
            value = json.dumps({
                "note": f.stem, "chunk": i, "chunk_count": len(chunks),
                "content": chunk,
            }, ensure_ascii=False)
            mem.save(key=key, value=value, tags=["知识库", "L4", f"note:{f.stem}"])
            total_chunks += 1
    print(f"[vault-chunk] ✅ knowledge-vault 全文分块入小脑: "
          f"{len(list(vault.glob('*.md')))} 篇 → {total_chunks} 块")
    return 0


# ── 状态 ─────────────────────────────────────────────────
def cmd_status(args) -> int:
    dep = _import_cerebellum()
    if dep is None:
        print("[status] 小脑不可用")
        return 2
    import sqlite3
    _, db_path, _ = dep
    conn = sqlite3.connect(str(db_path))
    prefixes = ["rev:", "browser:", "design:", "tool:", "vault:"]
    print("[status] 小脑整合条目统计:")
    for p in prefixes:
        n = conn.execute("SELECT COUNT(*) FROM semantic_entries WHERE source_key LIKE ?",
                         (p + "%",)).fetchone()[0]
        print(f"  {p:<10} {n}")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="5 项功能整合进小脑 (逆向/浏览器/设计/工具路由/知识库)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # 1. 逆向
    p = sub.add_parser("rev-deposit", help="逆向分析摘要入小脑 + sha256 查重")
    p.add_argument("--file", required=True, help="二进制文件路径")
    p.add_argument("--summary", default="", help="反编译结果摘要")
    p = sub.add_parser("rev-lookup", help="查重: 该文件是否已分析过")
    p.add_argument("--file", required=True, help="二进制文件路径")

    # 2. 浏览器
    p = sub.add_parser("browser-save", help="保存浏览器操作序列")
    p.add_argument("--site", required=True, help="站点 URL")
    p.add_argument("--task", required=True, help="任务描述 (如 登录)")
    p.add_argument("--steps", required=True, help="操作步骤 JSON 数组或文本")
    p = sub.add_parser("browser-recall", help="检索历史操作序列")
    p.add_argument("--query", required=True, help="任务语义描述")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--show", action="store_true", help="展开每一步骤")

    # 3. 设计
    p = sub.add_parser("design-index", help="CAD/name_fortune/game_project 设计参数沉淀")

    # 4. 工具路由
    p = sub.add_parser("tools-index", help="MCP 工具能力清单入小脑")

    # 5. 知识库
    p = sub.add_parser("vault-chunk", help="knowledge-vault 全文分块向量化")
    p.add_argument("--chunk-size", type=int, default=500)
    p.add_argument("--overlap", type=int, default=100)

    sub.add_parser("status", help="查看整合条目统计")

    args = ap.parse_args()
    handlers = {
        "rev-deposit": cmd_rev_deposit,
        "rev-lookup": cmd_rev_lookup,
        "browser-save": cmd_browser_save,
        "browser-recall": cmd_browser_recall,
        "design-index": cmd_design_index,
        "tools-index": cmd_tools_index,
        "vault-chunk": cmd_vault_chunk,
        "status": cmd_status,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
