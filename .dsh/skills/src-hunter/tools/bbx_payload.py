#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bbx_payload — 本地 payload 检索匹配器 (省 token 专用).

从本地 payloader 资产 (raw/web.json + raw/tools.json + waf-bypass.md) 匹配
漏洞赏金需要的 payload / 工具命令 / WAF 绕过变体, 全程本地, 零云端 token。

用法:
  python bbx_payload.py search sqli mysql          # 关键词搜索 (打分排序)
  python bbx_payload.py match < compressed.txt     # 喂 bbx_compress 输出 → 自动推荐攻击面
  python bbx_payload.py waf "SQL/NoSQL注入"        # 查某类别的 WAF 绕过变体
  python bbx_payload.py tools nmap                 # 查工具命令
  python bbx_payload.py --top 5 search idor        # 限制输出条数

纯标准库 (json + re), 无第三方依赖。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PAYLOADER = os.path.join(_BASE, "references", "payloader")

# --- 攻击面 → 关键词规则 (轻量专家规则, 用于 match 自动推荐) ---
# 三元组: (攻击面名, 压缩文本匹配关键词, 资产检索词)
#   压缩文本匹配关键词: 用于从 bbx_compress 输出识别攻击面
#   资产检索词: 用于对 payloader 资产打分排序 (payload 的 category 名与
#               攻击面名不完全一致, 如 IDOR 在 API安全/业务逻辑漏洞 下,
#               故用检索词打分而非精确类别匹配)
_ATTACK_PROFILES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("SQL/NoSQL注入",
     ("sql", "union", "order by", "select", "sleep", "' or ", "id=", "query"),
     ("sql", "nosql", "注入", "injection", "mysql", "union", "sqli")),
    ("XSS跨站脚本",
     ("xss", "<script", "alert(", "innerhtml", "dom", "html"),
     ("xss", "跨站", "html", "dom", "alert", "反射", "payload")),
    ("SSRF服务端请求伪造",
     ("url", "fetch", "webhook", "callback", "redirect", "proxy", "image_url"),
     ("ssrf", "服务端请求伪造", "file://", "redirect", "proxy", "内网", "url")),
    ("RCE远程代码执行",
     ("exec", "system", "cmd", "command", "shell", "ping", "eval", "child_process"),
     ("rce", "命令执行", "远程代码", "deserialization", "反序列化", "shell", "exec", "ssti")),
    ("任意X越权",
     ("userid", "user_id", "profile", "account", "uid", "role", "admin"),
     ("idor", "authz", "越权", "bola", "access", "user", "account", "privilege", "object")),
    ("文件上传",
     ("upload", "file", "multipart", "filename", "attachment"),
     ("upload", "上传", "file", "文件漏洞", "multipart", "filename", "bypass")),
    ("认证漏洞",
     ("login", "password", "reset", "otp", "verify", "token"),
     ("auth", "认证", "login", "password", "reset", "otp", "verify", "token", "bypass", "session")),
    ("信息泄露",
     ("git", ".env", "backup", "actuator", "swagger", "debug", "config"),
     ("leak", "泄露", "disclosure", ".env", "actuator", "swagger", "debug", "config", "information", "备份")),
    ("业务逻辑漏洞",
     ("price", "amount", "order", "coupon", "balance", "recharge"),
     ("logic", "逻辑", "biz", "price", "amount", "order", "coupon", "balance", "越权", "支付")),
    ("JWT安全",
     ("jwt", "authorization", "bearer", "signature", "sign"),
     ("jwt", "token", "signature", "sign", "authorization", "bearer", "密钥")),
    ("路径遍历",
     ("download", "filepath", "path", "filename", "include"),
     ("traversal", "lfi", "rfi", "路径", "遍历", "include", "download", "目录")),
    ("API安全",
     ("api", "graphql", "rest", "json", "batch"),
     ("api", "graphql", "rest", "bola", "idor", "batch", "mass assignment", "object")),
    ("SSTI模板注入",
     ("template", "render", "{{", "freemarker", "velocity"),
     ("ssti", "template", "模板", "render", "freemarker", "velocity", "jinja", "twig")),
    ("XXE实体注入",
     ("xml", "doctype", "entity", "soap"),
     ("xxe", "xml", "doctype", "entity", "实体", "soap")),
]

_CATEGORY_ALIASES: dict[str, str] = {
    "sqli": "SQL/NoSQL注入", "sql": "SQL/NoSQL注入", "injection": "SQL/NoSQL注入",
    "xss": "XSS跨站脚本", "ssrf": "SSRF服务端请求伪造",
    "rce": "RCE远程代码执行", "command": "RCE远程代码执行",
    "idor": "任意X越权", "authz": "任意X越权", "越权": "任意X越权",
    "upload": "文件上传", "auth": "认证漏洞", "login": "认证漏洞",
    "info": "信息泄露", "leak": "信息泄露", "jwt": "JWT安全",
    "traversal": "路径遍历", "lfi": "路径遍历", "api": "API安全",
    "ssti": "SSTI模板注入", "xxe": "XXE实体注入", "logic": "业务逻辑漏洞",
}


def _load_web_payloads() -> list[dict]:
    """加载 raw/web.json (177 条 web payload)."""
    path = os.path.join(_PAYLOADER, "raw", "web.json")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_tools() -> list[dict]:
    """加载 raw/tools.json (114 条工具命令)."""
    path = os.path.join(_PAYLOADER, "raw", "tools.json")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_waf_bypass() -> dict[str, list[dict]]:
    """解析 waf-bypass.md → {类别: [{id, name, description, variants: [str]}]}.

    结构: ## 类别 → ### 名称 `id` → **WAF 绕过：** → **绕过X** 小节 + 代码块 payload
    """
    path = os.path.join(_PAYLOADER, "waf-bypass.md")
    if not os.path.isfile(path):
        return {}
    text = open(path, encoding="utf-8").read()
    result: dict[str, list[dict]] = {}
    cur_cat = ""
    cur_entry: dict | None = None
    cur_variant_title = ""
    cur_variant_lines: list[str] = []
    in_code = False

    def _flush_variant() -> None:
        nonlocal cur_variant_title, cur_variant_lines
        if cur_entry is not None and cur_variant_title:
            payload = "\n".join(cur_variant_lines).strip()
            if payload:
                cur_entry.setdefault("variants", []).append(
                    {"title": cur_variant_title, "payload": payload}
                )
        cur_variant_title = ""
        cur_variant_lines = []

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## "):
            _flush_variant()
            cur_cat = s[3:].strip()
            result.setdefault(cur_cat, [])
            cur_entry = None
        elif s.startswith("### "):
            _flush_variant()
            m = re.match(r"### (.+?)\s*`([^`]+)`\s*$", s)
            if m:
                cur_entry = {"id": m.group(2), "name": m.group(1), "variants": []}
                result[cur_cat].append(cur_entry)
            else:
                cur_entry = None
        elif s.startswith("```"):
            in_code = not in_code
            if not in_code:
                _flush_variant()
        elif cur_entry is not None:
            if s.startswith("**") and s.endswith("**"):
                _flush_variant()
                cur_variant_title = s.strip("*").strip()
            elif in_code or (cur_variant_title and s and not s.startswith(("**", ">", "-", "*", "—"))):
                cur_variant_lines.append(s)
    _flush_variant()
    return result


def _score_payload(p: dict, terms: list[str]) -> int:
    """打分: tags 3 / category+sub 2 / name 2 / desc 1."""
    score = 0
    text_parts: dict[str, int] = {}
    for t in p.get("tags", []):
        text_parts[str(t).lower()] = 3
    text_parts[str(p.get("category", "")).lower()] = 2
    text_parts[str(p.get("subCategory", "")).lower()] = 2
    text_parts[str(p.get("name", "")).lower()] = 2
    text_parts[str(p.get("description", "")).lower()] = 1
    for term in terms:
        tl = term.lower()
        if not tl:
            continue
        for text, w in text_parts.items():
            if tl in text:
                score += w
    return score


def _fmt_payload(p: dict) -> str:
    """输出单条 payload 的紧凑摘要 (低 token)."""
    execs = p.get("execution", [])
    lines = [f"[{p.get('category','')}] {p.get('name','')}  ({p.get('id','')})"]
    for step in execs[:2]:
        cmd = (step.get("command") or "").splitlines()[0][:100]
        lines.append(f"  > {cmd}")
    return "\n".join(lines)


def cmd_search(args: argparse.Namespace) -> int:
    terms = args.terms
    if not terms:
        print("用法: bbx_payload.py search <关键词...> [--top N]")
        return 2
    payloads = _load_web_payloads()
    scored = [(p, _score_payload(p, terms)) for p in payloads]
    scored = [(p, s) for p, s in scored if s > 0]
    scored.sort(key=lambda x: -x[1])
    top = scored[: args.top]
    if not top:
        print(f"未命中 (关键词: {' '.join(terms)})。试试更宽泛的词, 或 --top 调大。")
        return 0
    print(f"== payload 匹配 ({len(top)}/{len(payloads)}, 关键词: {' '.join(terms)}) ==")
    for p, s in top:
        print(f"score={s} " + _fmt_payload(p).replace("\n", "\n        "))
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    """读取压缩文本 (bbx_compress 输出), 自动推荐攻击面 payload."""
    text = args.text or sys.stdin.read()
    text_l = text.lower()
    profiles = []
    for cat, kws, _asset in _ATTACK_PROFILES:
        hits = sum(1 for k in kws if k in text_l)
        if hits:
            profiles.append((cat, hits))
    profiles.sort(key=lambda x: -x[1])
    if not profiles:
        print("未识别出明显攻击面。可尝试: bbx_payload.py search <关键词>")
        return 0
    print(f"== 识别攻击面 ({len(profiles)}) ==")
    payloads = _load_web_payloads()
    seen: set[str] = set()
    shown = 0
    for cat, _hits in profiles:
        print(f"-- {cat} --")
        # 用该攻击面的资产检索词对全部 payload 打分 (不依赖精确类别名,
        # 因为 IDOR/文件上传等实际落在 API安全/业务逻辑漏洞/文件漏洞 下)
        asset_terms = [a for c, _k, a in _ATTACK_PROFILES if c == cat][0]
        scored = [(p, _score_payload(p, asset_terms)) for p in payloads]
        scored = [(p, s) for p, s in scored if s > 0 and p.get("id") not in seen]
        scored.sort(key=lambda x: -x[1])
        for p, s in scored[: args.top]:
            if shown >= args.top:
                break
            seen.add(p.get("id", ""))
            print(f"  score={s} " + _fmt_payload(p).replace("\n", "\n  "))
            shown += 1
        if shown >= args.top:
            break
    return 0


def cmd_waf(args: argparse.Namespace) -> int:
    by_cat = _load_waf_bypass()
    cat = args.category
    if not cat:
        print("可用类别:")
        for c in by_cat:
            if by_cat[c]:
                print(f"  {c} ({len(by_cat[c])} 条)")
        return 0
    # 模糊匹配类别名
    entries = by_cat.get(cat)
    if entries is None:
        low = cat.lower()
        candidates = [c for c in by_cat if low in c.lower() and by_cat[c]]
        if len(candidates) == 1:
            entries = by_cat[candidates[0]]
        else:
            print(f"未找到类别 '{cat}'。可用: " + ", ".join(list(by_cat)[:10]))
            return 1
    print(f"== WAF 绕过: {cat} ({len(entries)} 条) ==")
    for e in entries[: args.top]:
        print(f"### {e['name']} ({e['id']})")
        for v in e.get("variants", [])[:2]:
            print(f"  [{v['title']}]")
            for ln in v["payload"].splitlines()[:4]:
                print(f"    {ln[:120]}")
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    terms = args.terms
    tools = _load_tools()
    scored = []
    for t in tools:
        s = 0
        hay = " ".join([
            str(t.get("name", "")), str(t.get("category", "")),
            str(t.get("description", "")), " ".join(str(x) for x in t.get("tags", [])),
        ]).lower()
        for term in terms:
            if term.lower() in hay:
                s += 1
        if s:
            scored.append((t, s))
    scored.sort(key=lambda x: -x[1])
    if not scored:
        print("未命中工具。")
        return 0
    print(f"== 工具命令匹配 ({len(scored[:args.top])}/{len(tools)}) ==")
    for t, s in scored[: args.top]:
        print(f"[{t.get('category','')}] {t.get('name','')} ({t.get('id','')})")
        for c in t.get("commands", [])[:2]:
            print(f"  > {(c.get('command') or '')[:100]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 payload 检索匹配器")
    parser.add_argument("--top", type=int, default=8, help="最多输出条数 (默认 8)")
    sub = parser.add_subparsers(dest="cmd")

    p_search = sub.add_parser("search", help="关键词搜索 payload")
    p_search.add_argument("terms", nargs="+", help="关键词 (AND 打分)")

    p_match = sub.add_parser("match", help="从压缩文本自动推荐攻击面")
    p_match.add_argument("--text", default="", help="压缩文本 (缺省读 stdin)")

    p_waf = sub.add_parser("waf", help="查某类别 WAF 绕过变体")
    p_waf.add_argument("category", nargs="?", default="", help="类别名 (留空列出全部)")

    p_tools = sub.add_parser("tools", help="查工具命令")
    p_tools.add_argument("terms", nargs="+", help="工具关键词")

    args = parser.parse_args()
    if args.cmd == "search":
        return cmd_search(args)
    if args.cmd == "match":
        return cmd_match(args)
    if args.cmd == "waf":
        return cmd_waf(args)
    if args.cmd == "tools":
        return cmd_tools(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
