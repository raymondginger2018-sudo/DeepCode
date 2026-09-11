#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bbx_capture — PostToolUse 抓包自动压缩 Hook (省 token 专用).

漏洞赏金流程中, playwright 的 network_request / fetch 等工具会把原始
HTTP 请求/响应/JS 全文回传, 直接进上下文非常烧 token。本 hook 在
PostToolUse 时把抓包结果用本地规则压缩成结构化摘要 (复用 src-hunter
工具箱的 bbx_compress, 零云端 token), 通过 additionalContext 回注,
让大脑只看到压缩后的攻击面要点。

输入 (Hook stdin JSON, Claude Code 风格):
  { "session_id": "...", "tool_name": "mcp__playwright__browser_network_request",
    "tool_input": {...}, "tool_response": {...} }

输出 (stdout JSON):
  { "hookSpecificOutput": { "additionalContext": "[bbx-capture] ..." } }

设计原则:
  - 白名单过滤: 只处理抓包类工具, 其余直接退出 (MCP 注册的 hook matcher=*,
    必须内部过滤, 避免每个工具调用都白跑)。
  - 零成本: 只用规则压缩 (bbx_compress.compress), 不调 LLM。
  - 收益判断: 规则压缩是信息保留型 (提取方法/路径/白名单头/JSON 键名),
    只要严格短于原文即采用 — 避免用固定比率门槛把真实抓包 (headers 占大头,
    压缩率天然 0.6-0.8) 误判为"无收益"而退回零节省的截断。
  - 静默失败: 任何异常 exit 0 且无输出, 绝不影响主流程。
"""
import json
import sys
from pathlib import Path

# ── 抓包类工具白名单 (tool_name 后缀匹配) ──────────────────────
_CAPTURE_SUFFIXES = (
    # playwright: 网络请求详情 / 请求列表
    "browser_network_request",
    "browser_network_requests",
    # fetch MCP: 网页抓取
    "fetch",
)

_MIN_TEXT = 400          # 原文低于此长度不压缩 (不值得)
_MAX_OUT = 4000          # additionalContext 输出上限
_MAX_PLAIN = 1500        # 压缩率不足时的纯截断上限


def _load_compress():
    """导入 src-hunter 的 bbx_compress (失败返回 None, 优雅降级)."""
    try:
        tools_dir = (
            Path(__file__).resolve().parent.parent
            / ".deepcode" / "skills" / "src-hunter" / "tools"
        )
        sys.path.insert(0, str(tools_dir))
        import bbx_compress  # noqa: PLC0415
        return bbx_compress
    except Exception:
        return None


def _textify(response) -> str:
    """把 tool_response 转成可压缩文本.

    dict (playwright network_request 风格: url/method/headers/body/response)
    → 伪 HTTP 报文 (请求块 + 响应块), 让 bbx_compress 规则压缩能识别出
    METHOD/路径/白名单头/JSON 键名; 其余 (str/list) 原样返回。
    """
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return _dict_to_http(response)
    if isinstance(response, list):
        try:
            return json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return str(response)
    return str(response)


def _dict_to_http(d: dict) -> str:
    """playwright network_request dict → 伪 HTTP 报文.

    ``bbx_compress`` 按 ``METHOD url`` / ``HTTP/x.y status`` 开头识别 HTTP
    块, 只保留安全相关头白名单并提取 JSON body 键名 — 比直接压 JSON 更
    省 token 且保留攻击面信息。
    """
    parts: list[str] = []

    def _fmt_headers(headers: dict) -> list[str]:
        lines = []
        for k, v in headers.items():
            if v is None:
                continue
            lines.append(f"{k}: {v}")
        return lines

    # ── 请求块 ──
    url = str(d.get("url") or "")
    method = str(d.get("method") or d.get("requestMethod") or "GET").upper()
    req_lines = [f"{method} {url} HTTP/1.1"]
    req_headers = d.get("requestHeaders") or d.get("headers") or {}
    if isinstance(req_headers, dict):
        req_lines.extend(_fmt_headers(req_headers))
    req_lines.append("")
    req_body = d.get("requestBody") if d.get("requestBody") is not None else d.get("body")
    if isinstance(req_body, (dict, list)):
        req_body = json.dumps(req_body, ensure_ascii=False, separators=(",", ":"))
    if req_body:
        req_lines.append(str(req_body))
    parts.append("\n".join(req_lines))

    # ── 响应块 ──
    resp = d.get("response")
    if isinstance(resp, dict):
        status = resp.get("status") or resp.get("statusCode") or 200
        status_text = str(resp.get("statusText") or "")
        res_lines = [f"HTTP/1.1 {status} {status_text}".strip()]
        res_headers = resp.get("headers") or {}
        if isinstance(res_headers, dict):
            res_lines.extend(_fmt_headers(res_headers))
        res_lines.append("")
        res_body = resp.get("body") if resp.get("body") is not None else resp.get("content")
        if isinstance(res_body, (dict, list)):
            res_body = json.dumps(res_body, ensure_ascii=False, separators=(",", ":"))
        if res_body:
            res_lines.append(str(res_body))
        parts.append("\n".join(res_lines))

    return "\n\n".join(parts)


def _is_capture_tool(tool_name: str) -> bool:
    if not tool_name:
        return False
    # 排除掉 fetch 工具的子类误匹配: 只匹配 mcp__fetch__fetch / playwright 网络类
    low = tool_name.lower()
    for suffix in _CAPTURE_SUFFIXES:
        if low.endswith(suffix):
            return True
    return False


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    try:
        if sys.stdin.isatty():
            return 0
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        data = json.loads(raw)
    except Exception:
        return 0

    tool_name = str(data.get("tool_name") or "")
    if not _is_capture_tool(tool_name):
        return 0

    bbx = _load_compress()
    text = _textify(data.get("tool_response"))
    text = text.strip()
    if not text or len(text) < _MIN_TEXT:
        return 0

    if bbx is not None:
        try:
            compressed = bbx.compress(text)
        except Exception:
            compressed = ""
    else:
        compressed = ""

    if compressed and len(compressed) < len(text):
        body = compressed
        kind = "规则压缩"
    else:
        body = text[:_MAX_PLAIN]
        kind = "截断"

    body = body[:_MAX_OUT]
    if not body.strip():
        return 0

    out = {
        "hookSpecificOutput": {
            "additionalContext": f"[bbx-capture:{kind}] {tool_name}\n{body}",
        }
    }
    try:
        print(json.dumps(out, ensure_ascii=False), flush=True)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
