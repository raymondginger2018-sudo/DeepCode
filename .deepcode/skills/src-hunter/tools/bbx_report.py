#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bbx_report — H1 报告草稿生成器 (省 token 专用).

漏洞赏金发现漏洞后, 本地生成 HackerOne 格式的英文报告草稿,
不需要把上下文交给云端模型现写。三段式: Summary / Steps to
Reproduce / Impact + Remediation, 附带 CVSS 速查表和提交前自检。

用法:
  python bbx_report.py new --type sqli --endpoint "POST /api/search" \
      [--title "..."] [--severity High] [--precondition "..."] \
      [--impact "..."] [--steps steps.md] [--out report.md]
  python bbx_report.py cvss [sqli]      # 查 CVSS 速查表
  python bbx_report.py checklist        # 提交前 10 项自检

纯标准库 (argparse + json), 无第三方依赖。
"""
from __future__ import annotations

import argparse
import os
import sys

# --- 漏洞类型 → CVSS 速查表 (来自 references/templates/report-submission.md §4) ---
# key: (显示名, 条件, CVSS vector, score)
_CVSS_MAP: dict[str, tuple[str, str, str, float]] = {
    "rce": ("Remote Code Execution", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    "rce-auth": ("Remote Code Execution", "Authenticated", "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 8.8),
    "sqli": ("SQL Injection", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N", 9.1),
    "sqli-auth": ("SQL Injection", "Authenticated", "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:N", 8.1),
    "idor": ("Insecure Direct Object Reference", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N", 7.5),
    "idor-auth": ("Horizontal Privilege Escalation (IDOR)", "Authenticated", "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N", 6.5),
    "priv-esc": ("Vertical Privilege Escalation", "Authenticated", "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 8.8),
    "file-read": ("Arbitrary File Read", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N", 7.5),
    "file-write": ("Arbitrary File Write", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    "ssrf": ("Server-Side Request Forgery", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    "jwt": ("JWT Algorithm Confusion / Forgery", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    "password-reset": ("Password Reset Account Takeover", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N", 9.1),
    "price-tamper": ("Business Logic - Price Tampering", "Authenticated", "AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:N", 6.5),
    "xss-stored": ("Stored Cross-Site Scripting", "Authenticated", "AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1),
    "xss-reflected": ("Reflected Cross-Site Scripting", "Unauthenticated", "AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1),
    "open-redirect": ("Open Redirect", "Unauthenticated", "AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:N/A:N", 4.7),
    "git-leak": (".git Source Disclosure", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N", 7.5),
    "env-leak": (".env Production Credentials Disclosure", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    "default-creds": ("Default Credentials on Admin Panel", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    "smuggling": ("HTTP Request Smuggling", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:N", 9.0),
    "race": ("Race Condition - Financial", "Authenticated", "AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:N", 6.5),
    "info-leak": ("Information Disclosure", "Unauthenticated", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N", 7.5),
}

# 中文别名 → key (方便中文输入)
_TYPE_ALIASES: dict[str, str] = {
    "sqli": "sqli", "sql": "sqli", "注入": "sqli",
    "rce": "rce", "远程代码执行": "rce", "命令执行": "rce",
    "rce-auth": "rce-auth", "认证rce": "rce-auth",
    "idor": "idor", "越权": "idor", "任意x": "idor",
    "idor-auth": "idor-auth", "水平越权": "idor-auth",
    "priv-esc": "priv-esc", "垂直越权": "priv-esc", "提权": "priv-esc",
    "file-read": "file-read", "任意文件读": "file-read", "lfi": "file-read",
    "file-write": "file-write", "任意文件写": "file-write",
    "ssrf": "ssrf", "服务端请求伪造": "ssrf",
    "jwt": "jwt", "token伪造": "jwt",
    "password-reset": "password-reset", "密码重置": "password-reset", "找回密码": "password-reset",
    "price-tamper": "price-tamper", "价格篡改": "price-tamper",
    "xss-stored": "xss-stored", "存储xss": "xss-stored",
    "xss-reflected": "xss-reflected", "反射xss": "xss-reflected", "xss": "xss-reflected",
    "open-redirect": "open-redirect", "开放重定向": "open-redirect", "跳转": "open-redirect",
    "git-leak": "git-leak", ".git泄露": "git-leak", "git泄露": "git-leak",
    "env-leak": "env-leak", ".env泄露": "env-leak", "env泄露": "env-leak",
    "default-creds": "default-creds", "默认凭据": "default-creds", "默认密码": "default-creds",
    "smuggling": "smuggling", "请求走私": "smuggling", "http走私": "smuggling",
    "race": "race", "竞态": "race", "并发": "race",
    "info-leak": "info-leak", "信息泄露": "info-leak", "泄露": "info-leak",
}

# 漏洞类型 → 通用修复建议 (英文, short/mid 两档, 提交草稿直接可用)
_FIXES: dict[str, list[str]] = {
    "rce": [
        "Sanitize and validate all user-controlled input that reaches dangerous sinks (eval/system/deserialization).",
        "Apply the latest vendor security patches and disable unsafe features (e.g., lookups in log4j).",
    ],
    "rce-auth": [
        "Validate and sanitize input reaching dangerous sinks; apply principle of least privilege for the endpoint.",
        "Apply vendor patches and restrict access to sensitive endpoints by role.",
    ],
    "sqli": [
        "Use parameterized queries (PreparedStatement / ? placeholders) for the affected parameter.",
        "Enforce least-privilege DB accounts (single-table SELECT only) and monitor query logs for anomalies.",
    ],
    "sqli-auth": [
        "Use parameterized queries for the affected parameter.",
        "Audit the codebase for string-concatenated SQL and migrate to ORM / prepared statements.",
    ],
    "idor": [
        "Validate object ownership server-side before returning resources (never trust client-supplied IDs).",
        "Adopt UUIDs or non-enumerable identifiers and enforce authorization on every object access.",
    ],
    "idor-auth": [
        "Enforce object-level authorization (BOLA fix): check the requesting user owns the object.",
        "Add server-side ownership checks to every ID-based endpoint and log anomalous access patterns.",
    ],
    "priv-esc": [
        "Enforce role checks server-side on every privileged action.",
        "Review all role-switch / admin endpoints for missing authorization middleware.",
    ],
    "file-read": [
        "Validate and normalize file paths; block traversal sequences (../) and absolute-path escapes.",
        "Serve files through a whitelist-based handler instead of direct filesystem access.",
    ],
    "file-write": [
        "Validate filenames and extensions; do not allow user input to control write paths.",
        "Restrict write targets to a sandboxed directory and apply strict content-type checks.",
    ],
    "ssrf": [
        "Block requests to private/internal IP ranges and DNS rebinding; validate redirects.",
        "Route outbound requests through a hardened proxy and enforce allowlists for URLs.",
    ],
    "jwt": [
        "Pin the JWT algorithm (HS256/RS256) server-side; reject alg=none and algorithm confusion.",
        "Use a strong, non-guessable signing secret and implement proper key rotation.",
    ],
    "password-reset": [
        "Use cryptographically random, single-use tokens with short expiry.",
        "Invalidate tokens after use and bind them to the requesting session/IP when feasible.",
    ],
    "price-tamper": [
        "Never trust client-supplied prices/amounts; recompute totals server-side.",
        "Add server-side validation for price, quantity, and coupon fields.",
    ],
    "xss-stored": [
        "Encode output contextually (HTML/JS/URL) and adopt a CSP.",
        "Sanitize user-generated content on input and apply strict Content-Security-Policy headers.",
    ],
    "xss-reflected": [
        "Contextually encode reflected user input in responses.",
        "Adopt a Content-Security-Policy and escape output based on context.",
    ],
    "open-redirect": [
        "Validate redirect targets against an allowlist of internal domains.",
        "Use relative redirects or a server-side URL validator for the redirect parameter.",
    ],
    "git-leak": [
        "Remove .git directory from production web roots and deny access via web server config.",
        "Scan the codebase for accidental secret commits and rotate any exposed credentials.",
    ],
    "env-leak": [
        "Block .env and other sensitive files from web server static file serving.",
        "Move secrets to a secrets manager and rotate any exposed credentials immediately.",
    ],
    "default-creds": [
        "Enforce unique credentials at first login and disable default accounts.",
        "Audit all admin panels for default/backdoor credentials and rotate them.",
    ],
    "smuggling": [
        "Normalize Content-Length/Transfer-Encoding handling in the reverse proxy and origin.",
        "Use HTTP/2 or a single parsing engine across the proxy chain; apply vendor patches.",
    ],
    "race": [
        "Serialize sensitive state-changing operations (per-user locks / DB constraints).",
        "Add idempotency keys and transactional checks for financial operations.",
    ],
    "info-leak": [
        "Remove sensitive metadata from public responses; restrict access to debug endpoints.",
        "Apply least-privilege access to configuration and debug endpoints (actuator, swagger, etc.).",
    ],
}

_FILL = "<FILL>"


def _resolve_type(raw: str) -> str:
    """按别名/大小写解析类型 key, 找不到抛 ValueError."""
    key = _TYPE_ALIASES.get(raw.strip().lower(), raw.strip().lower())
    if key not in _CVSS_MAP:
        raise ValueError(f"未知漏洞类型: {raw!r}. 可用: {', '.join(sorted(_CVSS_MAP))}")
    return key


def _fmt_cvss(key: str) -> str:
    name, cond, vec, score = _CVSS_MAP[key]
    return f"{name} ({cond})  {score}  {vec}"


def cmd_cvss(args: argparse.Namespace) -> int:
    if args.vtype:
        try:
            print(_fmt_cvss(_resolve_type(args.vtype)))
        except ValueError as e:
            print(f"错误: {e}")
            return 2
    else:
        print("== CVSS 速查表 ==")
        for key in sorted(_CVSS_MAP):
            print(f"  {key:<16} {_fmt_cvss(key)}")
    return 0


def cmd_checklist(_args: argparse.Namespace) -> int:
    items = [
        "Title matches [Severity][Condition][Type] endpoint - one-liner format",
        "Asset is within program scope (check policy page)",
        "Reproduction steps are numbered and include full HTTP request/response",
        "At least 1 response screenshot + 1 URL-visible screenshot",
        "Side-effect evidence (OOB / data / file / command output)",
        "Reproduced at least 3 times (5 for critical bugs)",
        "CVSS vector + impact section included",
        "Concrete, actionable remediation included",
        "No irreversible impact on production data",
        "Personal PII / third-party data redacted",
    ]
    print("== Pre-submission checklist (10 items) ==")
    for i, item in enumerate(items, 1):
        print(f"  [ ] {i}. {item}")
    return 0


def _default_severity(key: str) -> str:
    """按 CVSS score 推导默认严重度 (标题/正文共用, 保持一致)."""
    _n, _c, _v, score = _CVSS_MAP[key]
    if score >= 9.0:
        return "Critical"
    if score >= 7.0:
        return "High"
    if score >= 4.0:
        return "Medium"
    return "Low"


def _build_title(args: argparse.Namespace, key: str) -> str:
    if args.title:
        return args.title
    name, cond, _v, _s = _CVSS_MAP[key]
    sev = args.severity or _default_severity(key)
    loc = args.endpoint or "<endpoint>"
    return f"[{sev}][{cond}][{name}] {loc} - {_FILL}"


def _build_report(args: argparse.Namespace, key: str) -> str:
    name, cond, vec, score = _CVSS_MAP[key]
    sev = args.severity or _default_severity(key)
    steps_text = ""
    if args.steps:
        if os.path.isfile(args.steps):
            with open(args.steps, encoding="utf-8") as f:
                steps_text = f.read().strip()
        else:
            steps_text = args.steps.strip()
    impact = args.impact or _FILL
    precond = args.precondition or _FILL
    fixes = _FIXES.get(key, [_FILL])
    fix_short = fixes[0]
    fix_mid = fixes[1] if len(fixes) > 1 else fix_short

    lines: list[str] = []
    lines.append(f"# {_build_title(args, key)}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Type**: {name} ({cond})")
    lines.append(f"- **Location**: `{args.endpoint or _FILL}`")
    lines.append(f"- **Severity**: {sev}")
    lines.append(f"- **CVSS**: {score} (`{vec}`)")
    lines.append(f"- **Precondition**: {precond}")
    lines.append(f"- **Impact**: {impact}")
    lines.append("")
    lines.append(f"One-liner: {_FILL}")
    lines.append("")
    lines.append("## Environment")
    lines.append("")
    lines.append(f"- Test time: {args.time or _FILL}")
    lines.append(f"- Test account: {_FILL}")
    lines.append(f"- Target: {_FILL}")
    lines.append("")
    lines.append("## Steps to Reproduce")
    lines.append("")
    if steps_text:
        lines.append(steps_text)
    else:
        lines.append(f"{_FILL} — numbered steps with full HTTP request/response")
    lines.append("")
    lines.append("## Impact")
    lines.append("")
    lines.append(f"- {impact}")
    lines.append("")
    lines.append("## What I did not do")
    lines.append("")
    lines.append(f"- {_FILL} — e.g., did not exfiltrate data / write files / affect other users")
    lines.append("")
    lines.append("## Remediation")
    lines.append("")
    lines.append("### Short term")
    lines.append(f"- {fix_short}")
    lines.append("")
    lines.append("### Medium term")
    lines.append(f"- {fix_mid}")
    lines.append("")
    lines.append("## Attachments")
    lines.append("")
    lines.append("- 01-poc-screenshot.png (request + response)")
    lines.append("- 02-reproduction-recording.mp4 (if applicable)")
    lines.append("")
    return "\n".join(lines)


def cmd_new(args: argparse.Namespace) -> int:
    try:
        key = _resolve_type(args.vtype)
    except ValueError as e:
        print(f"错误: {e}")
        return 2
    report = _build_report(args, key)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"报告草稿已写入: {args.out}")
    else:
        print(report)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="H1 报告草稿生成器 (本地, 零 token)")
    sub = parser.add_subparsers(dest="cmd")

    p_cvss = sub.add_parser("cvss", help="查 CVSS 速查表 (可带类型)")
    p_cvss.add_argument("vtype", nargs="?", default="")

    sub.add_parser("checklist", help="提交前 10 项自检")

    p_new = sub.add_parser("new", help="生成报告草稿")
    p_new.add_argument("--type", dest="vtype", required=True, help="漏洞类型 (如 sqli/idor/ssrf)")
    p_new.add_argument("--title", default="", help="报告标题 (不填则按模板自动生成)")
    p_new.add_argument("--endpoint", default="", help="漏洞位置, 如 POST /api/search")
    p_new.add_argument("--severity", default="", help="覆盖严重度 (Critical/High/Medium/Low)")
    p_new.add_argument("--precondition", default="", help="先决条件, 如 Valid registered account")
    p_new.add_argument("--impact", default="", help="影响, 如 Can dump users table")
    p_new.add_argument("--steps", default="", help="复现步骤文件路径 (或直接传文本)")
    p_new.add_argument("--time", default="", help="测试时间, 如 2026-08-08 12:00 UTC")
    p_new.add_argument("--out", default="", help="输出文件路径 (默认 stdout)")

    args = parser.parse_args()
    if args.cmd == "cvss":
        return cmd_cvss(args)
    if args.cmd == "checklist":
        return cmd_checklist(args)
    if args.cmd == "new":
        return cmd_new(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
