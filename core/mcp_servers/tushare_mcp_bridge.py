#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tushare MCP Bridge — 将 HTTP MCP 代理为 stdio MCP
================================================
从 stdin 读取 JSON-RPC 请求，代理到 tushare.pro 的 HTTP MCP 端点，
结果写回 stdout。这样 Deep Code CLI 就能通过 stdio 使用 tushare 了。
"""
import os
import sys
import json
import urllib.request
import urllib.error


# MCP stdio 协议要求 UTF-8。Windows 下 Python stdout/stderr 默认 GBK,
# 会破坏 JSON-RPC 帧(工具描述含中文时)导致 CLI 解析失败。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


def _resolve_token() -> str:
    """
    解析 Tushare token, 优先级:
      1) 环境变量 TUSHARE_TOKEN (settings.json env 注入)
      2) .env 文件 (quant_trading/.env 或 项目根 .env)

    修复: settings.json 配的是 ${TUSHARE_TOKEN} 变量引用,
    若启动 CLI 的 shell 无此环境变量则拿到空 token → 40101。
    .env 文件里有正确 token, 作为兜底。
    """
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token

    # 从 .env 兜底读取
    env_candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "quant_trading", ".env"),
    ]
    for env_path in env_candidates:
        env_path = os.path.abspath(env_path)
        if os.path.exists(env_path):
            try:
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("TUSHARE_TOKEN="):
                            t = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if t:
                                return t
            except OSError:
                continue
    return ""


TOKEN = _resolve_token()
if not TOKEN:
    sys.stderr.write("[tushare-bridge] ERROR: TUSHARE_TOKEN 环境变量未设置, 且 .env 中未找到\n")
    sys.stderr.flush()
    sys.exit(1)

TUSHARE_MCP_URL = f"https://api.tushare.pro/mcp/?token={TOKEN}"


_REQ_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "User-Agent": "DeepCode-MCP-Bridge/1.0",
}

# 强制直连: tushare.pro 为国内服务, 无需走 HTTP(S)_PROXY。
# 若系统代理失效(如 Clash 端口与 env 不一致)会导致 WinError 10061 连接被拒。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _parse_sse(body: str) -> dict:
    """解析 SSE (text/event-stream) 响应中的 JSON 数据"""
    data_lines: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            data_lines.append(line[6:])  # 去掉 "data: " 前缀
        elif line.startswith("data:"):
            data_lines.append(line[5:])
    raw = "".join(data_lines)
    if raw:
        return json.loads(raw)
    # 没有 SSE data 行，尝试直接解析全文
    return json.loads(body)


def proxy_request(request: dict) -> dict:
    """将 JSON-RPC 请求转发到 tushare HTTP MCP（支持 SSE 响应）"""
    data = json.dumps(request, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        TUSHARE_MCP_URL, data=data, headers=_REQ_HEADERS, method="POST"
    )
    try:
        with _OPENER.open(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            content_type = resp.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                return _parse_sse(body)
            return json.loads(body)
    except urllib.error.HTTPError as e:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32000, "message": f"HTTP {e.code}: {e.reason}"},
        }
    except urllib.error.URLError as e:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32001, "message": f"连接失败: {e.reason}"},
        }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32002, "message": str(e)},
        }


def main() -> None:
    """主循环: 逐行读取 stdin JSON-RPC，转发并回写 stdout"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        # 通知类消息(无 id)直接忽略
        if "id" not in req:
            continue

        # 先尝试本地处理 initialize — 返回兼容的协议版本和能力声明
        if req.get("method") == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {
                        "name": "tushare-mcp-bridge",
                        "version": "1.0.0",
                    },
                },
            }
        else:
            # 其余请求全部代理到 tushare HTTP MCP
            resp = proxy_request(req)

        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
