#!/usr/bin/env python3
"""
MCP Connection Manager v1.0 — MCP 连接运行时管理
==================================================================
参考: MCPConnectionManager 设计

核心设计:
  - 运行时连接状态监控 (health check 每30秒)
  - 自动重连 (exponential backoff)
  - 工具审批工作流 (首次使用需确认)
  - 连接池 (复用连接，避免重复建立)
  - 开关控制 (运行时启用/禁用 MCP Server)

用法:
  from mcp_manager import MCPConnectionManager
  mgr = MCPConnectionManager()
  mgr.load_config(".mcp.json")
  mgr.start_health_checks()
"""

import os
import json
import time
import asyncio
import re
import subprocess
import requests
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone


# ── MCP 层加固 (源自 Cursor MCP SDK patch 逆向) ─────────────
# Cursor 对 MCP SDK 1.25.1 打了生产级补丁:
#   - 并发 OAuth 刷新租约锁 (single-flight)
#   - 心跳续租 / 错误分类 / 脱敏日志
# 以下对应落地:

# 敏感字段脱敏 (对标 Cursor 的 redact 日志)
_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)((?:authorization|bearer|api[_-]?key|token|secret|password)=)([^\s&\"']+)"),
    re.compile(r"(?i)(sk-[A-Za-z0-9]{6})[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9._-]{16,})"),
]


def redact_text(text: str) -> str:
    """脱敏日志 — 打码 token/key/secret (对标 Cursor MCP SDK redact)"""
    if not text:
        return text
    for pat in _SENSITIVE_PATTERNS:
        # group(1) 是字段名+分隔符 (如 "api_key=" / "Bearer ") — 值被完全打码
        text = pat.sub(lambda m: m.group(1) + "***" if m.lastindex == 2 else "***", text)
    return text


class MCPError(Exception):
    """MCP 调用错误 (分类) — 对标 Cursor 的错误分类"""

    KIND_CONNECTION = "connection"      # 连接失败
    KIND_PROTOCOL = "protocol"          # 协议/解析错误
    KIND_TIMEOUT = "timeout"            # 超时
    KIND_AUTH = "auth"                  # 认证/授权失败
    KIND_BUSY = "busy"                  # 并发冲突
    KIND_UNKNOWN = "unknown"            # 未知

    def __init__(self, message: str, kind: str = KIND_UNKNOWN, server: str = ""):
        super().__init__(message)
        self.kind = kind
        self.server = server

    @staticmethod
    def classify(message: str) -> str:
        """错误分类 — 从错误消息推断类别"""
        m = message.lower()
        if any(k in m for k in ("connection refused", "connect call failed")):
            return MCPError.KIND_CONNECTION
        if any(k in m for k in ("timed out", "timeout", "deadline")):
            return MCPError.KIND_TIMEOUT
        if any(k in m for k in ("401", "403", "unauthorized", "forbidden", "auth")):
            return MCPError.KIND_AUTH
        if any(k in m for k in ("parse", "invalid json", "protocol", "schema")):
            return MCPError.KIND_PROTOCOL
        if any(k in m for k in ("busy", "locked", "lock ", "in progress")):
            return MCPError.KIND_BUSY
        return MCPError.KIND_UNKNOWN


@dataclass
class MCPOAuthConfig:
    """MCP server 的 OAuth 2.0 配置 (对标 Cursor MCP OAuth)"""
    server: str
    client_id: str = ""
    client_secret: str = ""          # 公开客户端可留空 (PKCE)
    authorization_url: str = ""      # 授权端点
    token_url: str = ""              # 令牌端点
    scopes: str = "mcp"              # 空格分隔 scope
    redirect_uri: str = "http://127.0.0.1:0/mcp/callback"  # 本地回调
    # 运行时 (不持久化)
    state: str = ""                  # 防 CSRF
    code_verifier: str = ""          # PKCE


class MCPOAuthManager:
    """MCP OAuth 2.0 授权码流程 — 对标 Cursor (www.cursor.com/agents/mcp/oauth/callback)

    流程: authorization_url() → 用户在浏览器授权 → exchange_code()
          → get_auth_headers() 注入 Bearer, 过期自动刷新 (带并发守卫)
    """

    def __init__(self, token_store: str = ""):
        self._configs: Dict[str, MCPOAuthConfig] = {}
        self._tokens: Dict[str, Dict[str, Any]] = {}  # server → token
        self._refresh_in_flight: Dict[str, bool] = {}
        self._store = token_store or os.path.join(
            os.path.expanduser("~"), ".deepcode", "mcp_oauth_tokens.json")
        self._load()

    # ── 配置 ──
    def register(self, server: str, client_id: str, client_secret: str = "",
                 authorization_url: str = "", token_url: str = "",
                 scopes: str = "mcp") -> MCPOAuthConfig:
        cfg = MCPOAuthConfig(
            server=server, client_id=client_id, client_secret=client_secret,
            authorization_url=authorization_url, token_url=token_url,
            scopes=scopes)
        self._configs[server] = cfg
        return cfg

    def is_registered(self, server: str) -> bool:
        return server in self._configs

    # ── 授权码流程 ──
    def authorization_url(self, server: str) -> str:
        """生成授权 URL (带 state + PKCE S256 challenge) — 用户浏览器打开授权"""
        cfg = self._configs.get(server)
        if not cfg:
            raise MCPError(f"server '{server}' 未注册 OAuth", MCPError.KIND_AUTH)
        import base64, hashlib, urllib.parse
        cfg.state = uuid4_hex()
        verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        cfg.code_verifier = verifier
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {
            "response_type": "code",
            "client_id": cfg.client_id,
            "redirect_uri": cfg.redirect_uri,
            "scope": cfg.scopes,
            "state": cfg.state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        sep = "&" if "?" in cfg.authorization_url else "?"
        return f"{cfg.authorization_url}{sep}{urllib.parse.urlencode(params)}"

    def exchange_code(self, server: str, code: str, state: str = "") -> dict:
        """授权回调: code → access_token (authorization_code grant)"""
        cfg = self._configs.get(server)
        if not cfg:
            raise MCPError(f"server '{server}' 未注册 OAuth", MCPError.KIND_AUTH)
        if state and state != cfg.state:
            raise MCPError("OAuth state 不匹配 (CSRF 防护)", MCPError.KIND_AUTH)
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cfg.redirect_uri,
            "client_id": cfg.client_id,
        }
        if cfg.code_verifier:
            payload["code_verifier"] = cfg.code_verifier
        if cfg.client_secret:
            payload["client_secret"] = cfg.client_secret
        data = self._post_token(cfg, payload)
        self._tokens[server] = self._normalize_token(data)
        self._save()
        return self._public_token(server)

    def refresh(self, server: str) -> dict:
        """刷新 token (refresh_token grant) — 并发只刷一次"""
        if self._refresh_in_flight.get(server):
            return {"refreshed": False, "reason": "refresh already in flight",
                    "server": server}
        self._refresh_in_flight[server] = True
        try:
            cfg = self._configs.get(server)
            token = self._tokens.get(server)
            if not cfg or not token or not token.get("refresh_token"):
                raise MCPError("无 refresh_token 可刷新", MCPError.KIND_AUTH)
            payload = {
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
                "client_id": cfg.client_id,
            }
            if cfg.client_secret:
                payload["client_secret"] = cfg.client_secret
            data = self._post_token(cfg, payload)
            # 保留旧 refresh_token (有些 server 刷新时不回发;
            # 注意 _normalize_token 会把缺失字段设为 "", 需用 falsy 判断)
            merged = self._normalize_token(data)
            if not merged.get("refresh_token"):
                merged["refresh_token"] = token.get("refresh_token", "")
            self._tokens[server] = merged
            self._save()
            return {"refreshed": True, "server": server}
        finally:
            self._refresh_in_flight[server] = False

    def get_auth_headers(self, server: str) -> Dict[str, str]:
        """获取认证头 — 过期自动刷新后返回 Bearer"""
        token = self._tokens.get(server)
        if not token:
            raise MCPError(f"server '{server}' 无 token, 先 authorization_url + exchange_code",
                           MCPError.KIND_AUTH)
        now = time.time()
        if token.get("expires_at", 0) and now > token["expires_at"]:
            self.refresh(server)
            token = self._tokens.get(server, {})
        return {"Authorization": f"Bearer {token.get('access_token', '')}"}

    def status(self, server: str = "") -> Dict[str, Any]:
        """OAuth 状态总览"""
        if server:
            t = self._tokens.get(server)
            return {"server": server, "registered": server in self._configs,
                    "has_token": bool(t),
                    "expired": bool(t and t.get("expires_at") and time.time() > t["expires_at"])}
        return {"servers": {s: {"registered": s in self._configs,
                                "has_token": bool(self._tokens.get(s))}
                            for s in set(self._configs) | set(self._tokens)}}

    # ── 内部 ──
    def _post_token(self, cfg: MCPOAuthConfig, payload: dict) -> dict:
        try:
            r = requests.post(cfg.token_url, data=payload, timeout=15)
        except requests.RequestException as e:
            raise MCPError(f"OAuth token 端点请求失败: {e}",
                           MCPError.KIND_CONNECTION) from e
        if r.status_code >= 400:
            raise MCPError(f"OAuth token 端点错误 {r.status_code}: {redact_text(r.text[:200])}",
                           MCPError.KIND_AUTH)
        return r.json()

    @staticmethod
    def _normalize_token(data: dict) -> dict:
        exp_in = int(data.get("expires_in", 3600))
        # 30s 提前刷新缓冲; 对短过期 token 用一半 (避免缓冲 > 有效期)
        buf = min(30, max(1, exp_in // 2))
        exp = time.time() + exp_in - buf
        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": exp,
            "token_type": data.get("token_type", "Bearer"),
        }

    def _public_token(self, server: str) -> dict:
        t = self._tokens.get(server, {})
        return {"server": server, "has_token": bool(t.get("access_token")),
                "expires_at": t.get("expires_at")}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._store), exist_ok=True)
            with open(self._store, "w", encoding="utf-8") as f:
                json.dump(self._tokens, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 存储失败不阻断流程

    def _load(self) -> None:
        try:
            if os.path.exists(self._store):
                with open(self._store, "r", encoding="utf-8") as f:
                    self._tokens = json.load(f)
        except Exception:
            self._tokens = {}


def uuid4_hex() -> str:
    """OAuth state 用随机串"""
    import uuid
    return uuid.uuid4().hex


# ── mcp-core 连接治理 (对标 Cursor mcpProcessMain.js) ─────────
# 连接生命周期状态机: connecting → connected → stabilized / disconnected
# 快慢双轨重试: fastRetry (秒级) → periodic_retry (分钟级)
# 心跳保活: keepalive 探测 + jitter, heartbeat_renewed / heartbeat_lost


class ConnectionState(str, Enum):
    """MCP 连接状态 — 对标 McpProcessLifecycle 状态机"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STABILIZED = "stabilized"


# 重试/保活参数 (对标 mcp-core fastRetry* / keepalive*)
FAST_RETRY_MAX_ATTEMPTS = 3            # 快速重试上限 (之后转周期重试)
FAST_RETRY_BASE_DELAY_MS = 500         # 快速重试起始延迟
FAST_RETRY_MAX_DELAY_MS = 5000         # 快速重试最大延迟
PERIODIC_RETRY_INTERVAL_S = 60         # 周期重试间隔
KEEPALIVE_PROBE_DELAY_MS = 15000       # 心跳探测周期
KEEPALIVE_JITTER_MS = 5000             # 心跳抖动 (防 thundering herd)
HEARTBEAT_LOST_THRESHOLD = 2           # 连续丢失 N 次 → 判定断开
STABILIZE_EVENTS = 2                   # 连续 N 次成功 → stabilized


@dataclass
class MCPServerState:
    """MCP Server 运行时状态"""
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None

    # 运行时状态
    connected: bool = False
    healthy: bool = False
    process: Any = None
    last_health_check: Optional[datetime] = None
    last_error: Optional[str] = None
    reconnect_attempts: int = 0
    max_reconnects: int = 5
    base_delay: float = 1.0
    enabled: bool = True

    # ── mcp-core 连接状态机 (对标 McpProcessLifecycle) ──
    state: ConnectionState = ConnectionState.DISCONNECTED
    backoff_attempts: int = 0           # 退避计数 (fast→periodic 切换依据)
    stability_events: int = 0           # 连续稳定事件 → stabilized
    last_heartbeat: Optional[datetime] = None
    heartbeat_lost_count: int = 0

    # 审批状态
    approved_tools: Dict[str, bool] = field(default_factory=dict)

    # 统计
    total_calls: int = 0
    total_errors: int = 0
    connected_since: Optional[datetime] = None

    def backoff_delay(self) -> float:
        """指数退避延迟"""
        return min(self.base_delay * (2 ** self.reconnect_attempts), 60.0)

    def retry_delay(self) -> Tuple[float, str]:
        """快慢双轨重试延迟 (对标 fastRetry* → periodic_retry)

        fast: 前 FAST_RETRY_MAX_ATTEMPTS 次指数退避 (500ms→5s)
        periodic: 之后固定周期 (60s) 探测
        """
        if self.backoff_attempts <= FAST_RETRY_MAX_ATTEMPTS:
            delay_ms = min(FAST_RETRY_BASE_DELAY_MS *
                           (2 ** max(0, self.backoff_attempts - 1)),
                           FAST_RETRY_MAX_DELAY_MS)
            return delay_ms / 1000.0, "fast"
        return float(PERIODIC_RETRY_INTERVAL_S), "periodic"


class MCPConnectionManager:
    """
    MCP 连接运行时管理器
    实现 MCPConnectionManager 的运行时管理逻辑
    """

    def __init__(self, config_path: str = None):
        self.servers: Dict[str, MCPServerState] = {}
        self.config_path = config_path or ""
        self._health_task = None
        self._running = False
        # 并发单飞锁 (对标 Cursor OAuth 刷新租约锁: 同 server 的调用不并发)
        self._call_locks: Dict[str, asyncio.Lock] = {}
        self._refresh_in_flight: Dict[str, bool] = {}
        # MCP OAuth 2.0 (对标 Cursor MCP OAuth)
        self.oauth = MCPOAuthManager()

    # ── MCP OAuth 2.0 便捷入口 ──
    def register_oauth(self, server: str, client_id: str, client_secret: str = "",
                       authorization_url: str = "", token_url: str = "",
                       scopes: str = "mcp"):
        """为外部 MCP server 注册 OAuth 配置 (对标 Cursor MCP OAuth 流程)"""
        return self.oauth.register(server, client_id, client_secret,
                                   authorization_url, token_url, scopes)

    def oauth_auth_url(self, server: str) -> str:
        """获取授权 URL — 用户在浏览器完成授权"""
        return self.oauth.authorization_url(server)

    def oauth_exchange(self, server: str, code: str, state: str = "") -> dict:
        """授权回调交换 code → token"""
        return self.oauth.exchange_code(server, code, state)

    def oauth_headers(self, server: str) -> Dict[str, str]:
        """取认证头 (过期自动刷新) — 供外部 MCP 调用注入"""
        return self.oauth.get_auth_headers(server)

    def oauth_status(self, server: str = "") -> Dict[str, Any]:
        return self.oauth.status(server)

    # ── mcp-core 连接状态机 (对标 McpProcessLifecycle) ──

    def _state(self, name: str) -> Optional[MCPServerState]:
        s = self.servers.get(name)
        if s is None:
            s = MCPServerState(name=name, command="")
            self.servers[name] = s
        return s

    def record_connection_event(self, name: str, ok: bool = True) -> Dict[str, Any]:
        """驱动连接状态机 (对标 connecting→connected→stabilized / disconnected)

        ok=True:  稳定性累计, 连续 STABILIZE_EVENTS 次 → stabilized (重置退避)
        ok=False: 断线 → disconnected, 退避计数 +1
        """
        s = self._state(name)
        if ok:
            s.connected = True
            s.healthy = True
            if s.state in (ConnectionState.DISCONNECTED, ConnectionState.CONNECTING):
                s.state = ConnectionState.CONNECTED
                s.connected_since = s.connected_since or datetime.now(timezone.utc)
            s.stability_events += 1
            if s.stability_events >= STABILIZE_EVENTS:
                s.state = ConnectionState.STABILIZED
                s.backoff_attempts = 0
                s.reconnect_attempts = 0
        else:
            s.connected = False
            s.healthy = False
            s.stability_events = 0
            if s.state in (ConnectionState.CONNECTED, ConnectionState.STABILIZED):
                s.state = ConnectionState.DISCONNECTED
            s.backoff_attempts += 1
            s.reconnect_attempts += 1
        return {"server": name, "state": s.state.value,
                "backoff_attempts": s.backoff_attempts,
                "stability_events": s.stability_events}

    def get_retry_plan(self, name: str) -> Dict[str, Any]:
        """快慢双轨重试计划 (对标 fastRetry* → periodic_retry)"""
        s = self._state(name)
        delay, track = s.retry_delay()
        return {"server": name, "delay_s": round(delay, 3), "track": track,
                "backoff_attempts": s.backoff_attempts,
                "fast_max": FAST_RETRY_MAX_ATTEMPTS}

    # ── 心跳保活 (对标 keepaliveProbeDelayMs + heartbeat_renewed/lost) ──

    def heartbeat_beat(self, name: str) -> Dict[str, Any]:
        """心跳续租成功 (对标 heartbeat_renewed)"""
        s = self._state(name)
        s.last_heartbeat = datetime.now(timezone.utc)
        s.heartbeat_lost_count = 0
        return {"server": name, "heartbeat": "renewed",
                "last_heartbeat": s.last_heartbeat.isoformat()}

    def heartbeat_check(self, name: str) -> Dict[str, Any]:
        """心跳探测 (对标 heartbeat_lost): 超时未续租 → lost, 连续超阈值判定断开"""
        s = self._state(name)
        now = datetime.now(timezone.utc)
        if s.last_heartbeat is None:
            return {"server": name, "heartbeat": "unknown"}
        probe_s = KEEPALIVE_PROBE_DELAY_MS / 1000.0
        # 加 jitter 的判定窗口 (对标 keepaliveJitterMs)
        window = (KEEPALIVE_PROBE_DELAY_MS + KEEPALIVE_JITTER_MS) / 1000.0
        elapsed = (now - s.last_heartbeat).total_seconds()
        if elapsed > window:
            s.heartbeat_lost_count += 1
            lost = s.heartbeat_lost_count >= HEARTBEAT_LOST_THRESHOLD
            if lost:
                s.state = ConnectionState.DISCONNECTED
                s.connected = False
            return {"server": name, "heartbeat": "lost",
                    "elapsed_s": round(elapsed, 1), "lost_count": s.heartbeat_lost_count,
                    "disconnected": lost}
        return {"server": name, "heartbeat": "ok",
                "elapsed_s": round(elapsed, 1), "probe_s": probe_s}

    # ── OAuth Loopback (对标 McpOAuthLoopback: 本地回环自动收 code) ──

    def oauth_authorize_loopback(self, server: str, host: str = "127.0.0.1",
                                 timeout_s: int = 120) -> Dict[str, Any]:
        """一站式本地回环授权 — 起临时回调服务器, 浏览器授权后自动收 code 并换 token

        流程: 起 http://127.0.0.1:<port>/callback → 生成授权 URL(redirect_uri 指向它)
              → 浏览器授权 → 自动 exchange → 返回 token 状态
        """
        import http.server
        import threading
        import urllib.parse

        if not self.oauth.is_registered(server):
            raise MCPError(f"server '{server}' 未注册 OAuth", MCPError.KIND_AUTH)
        received: Dict[str, str] = {}

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                q = urllib.parse.urlparse(self.path)
                if q.path == "/callback":
                    params = urllib.parse.parse_qs(q.query)
                    received["code"] = params.get("code", [""])[0]
                    received["state"] = params.get("state", [""])[0]
                    body = ("<html><body><h2 style='font-family:sans-serif'>"
                            "MCP OAuth 授权成功，可关闭此窗口</h2></body></html>"
                            ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

        srv = http.server.HTTPServer((host, 0), CallbackHandler)
        port = srv.server_address[1]
        redirect_uri = f"http://{host}:{port}/callback"
        # 把 redirect_uri 指向本地回环并生成授权 URL
        self.oauth._configs[server].redirect_uri = redirect_uri
        auth_url = self.oauth.authorization_url(server)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            deadline = time.time() + timeout_s
            while time.time() < deadline and "code" not in received:
                time.sleep(0.2)
        finally:
            srv.shutdown()
        if "code" not in received:
            return {"ok": False, "reason": "授权超时", "auth_url": auth_url,
                    "redirect_uri": redirect_uri}
        # 校验 state + 换 token
        try:
            result = self.oauth.exchange_code(server, received["code"], received["state"])
        except MCPError as e:
            return {"ok": False, "reason": f"授权回调失败: {e}",
                    "auth_url": auth_url, "redirect_uri": redirect_uri}
        return {"ok": True, "auth_url": auth_url, "redirect_uri": redirect_uri,
                **result}

    # ── MCP 进程治理 (对标 mcp-core spawn/SIGKILL + Windows AppContainer) ──

    def spawn_managed_process(self, name: str, cmd: str, args: Optional[List[str]] = None,
                              cwd: str = "", env: Optional[Dict[str, str]] = None,
                              sandbox: str = "") -> Dict[str, Any]:
        """启动受管 MCP server 子进程

        sandbox='appcontainer' → 尝试 ⑦ AppContainer 容器启动 (失败降级普通启动)
        启动即进入 connecting 状态
        """
        import subprocess
        s = self._state(name)
        s.command, s.args = cmd, list(args or [])
        s.state = ConnectionState.CONNECTING
        s.last_error = None

        if sandbox == "appcontainer":
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "sandbox_runtime",
                    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", ".deepcode", "skills",
                                 "deepcode-sandbox", "sandbox_runtime.py"))
                if spec and spec.loader:
                    sr = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(sr)
                    ac = sr.WindowsAppContainer(f"MCP_{name[:8]}")
                    pid = ac.spawn(" ".join([cmd] + (args or [])))
                    s.process = pid
                    return {"ok": True, "pid": pid, "sandbox": "appcontainer",
                            "state": s.state.value}
            except Exception as e:
                s.last_error = f"AppContainer 启动失败, 降级普通启动: {e}"
        try:
            full_env = dict(os.environ)
            if env:
                full_env.update(env)
            proc = subprocess.Popen(
                [cmd] + list(args or []), cwd=cwd or None,
                env=full_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            s.process = proc
            return {"ok": True, "pid": proc.pid, "sandbox": "none",
                    "state": s.state.value}
        except Exception as e:
            s.last_error = f"SpawnError: {e}"
            s.state = ConnectionState.DISCONNECTED
            return {"ok": False, "error": s.last_error}

    def kill_managed_process(self, name: str, timeout_ms: int = 5000) -> Dict[str, Any]:
        """超时/失败强杀 (对标 mcp-core SIGTERM → SIGKILL)"""
        import subprocess
        s = self._state(name)
        proc = s.process
        if proc is None:
            return {"ok": True, "killed": "none", "reason": "no process"}
        if isinstance(proc, int):  # AppContainer PID
            try:
                import subprocess as sp
                sp.run(["taskkill", "/F", "/T", "/PID", str(proc)],
                       capture_output=True, timeout=10)
            except Exception:
                pass
            s.process = None
            s.state = ConnectionState.DISCONNECTED
            return {"ok": True, "killed": "taskkill", "pid": proc}
        if proc.poll() is not None:
            s.process = None
            s.state = ConnectionState.DISCONNECTED
            return {"ok": True, "killed": "none", "already_exited": True}
        proc.terminate()  # SIGTERM
        try:
            proc.wait(timeout=timeout_ms / 1000.0)
            killed = "SIGTERM"
        except subprocess.TimeoutExpired:
            proc.kill()  # SIGKILL
            proc.wait()
            killed = "SIGKILL"
        s.process = None
        s.state = ConnectionState.DISCONNECTED
        return {"ok": True, "killed": killed, "pid": proc.pid}

    async def call_with_lock(self, server_name: str, coro_fn, *args, **kwargs):
        """以单飞锁执行 MCP 调用 (对标 Cursor OAuth 并发刷新租约锁)

        同一 server 的并发调用会被串行化, 防止刷新令牌/握手竞态。
        coro_fn: async callable; 返回 (ok, result_or_error)
        """
        if server_name not in self._call_locks:
            self._call_locks[server_name] = asyncio.Lock()
        lock = self._call_locks[server_name]
        async with lock:
            try:
                return await coro_fn(*args, **kwargs)
            except Exception as e:
                kind = MCPError.classify(str(e))
                self.record_call(server_name, success=False)
                raise MCPError(str(e), kind=kind, server=server_name) from e

    def with_refresh_guard(self, server_name: str):
        """刷新守卫装饰器 — 并发刷新时只执行一次 (对标租约续期)"""
        def deco(fn):
            async def wrapper(*args, **kwargs):
                if self._refresh_in_flight.get(server_name):
                    return {"refreshed": False, "reason": "refresh already in flight"}
                self._refresh_in_flight[server_name] = True
                try:
                    result = await fn(*args, **kwargs)
                    result["refreshed"] = True
                    return result
                finally:
                    self._refresh_in_flight[server_name] = False
            return wrapper
        return deco

    def load_config(self, config_path: str):
        """从 .mcp.json 加载 MCP 配置"""
        self.config_path = config_path
        if not os.path.exists(config_path):
            return

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        for name, cfg in config.get("mcpServers", {}).items():
            if "url" in cfg:
                self.servers[name] = MCPServerState(
                    name=name,
                    command="",
                    url=cfg["url"],
                )
            elif "command" in cfg:
                self.servers[name] = MCPServerState(
                    name=name,
                    command=cfg["command"],
                    args=cfg.get("args", []),
                    env=cfg.get("env", {}),
                )

    def get_server(self, name: str) -> Optional[MCPServerState]:
        return self.servers.get(name)

    def list_servers(self) -> List[Dict]:
        """列出所有 MCP Server 状态"""
        return [
            {
                "name": s.name,
                "connected": s.connected,
                "healthy": s.healthy,
                "enabled": s.enabled,
                "total_calls": s.total_calls,
                "total_errors": s.total_errors,
                "reconnect_attempts": s.reconnect_attempts,
                "last_error": s.last_error,
                "last_health_check": s.last_health_check.isoformat() if s.last_health_check else None,
            }
            for s in self.servers.values()
        ]

    async def reconnect_server(self, name: str) -> Dict:
        """重连指定 MCP Server"""
        server = self.servers.get(name)
        if not server:
            return {"error": f"Server '{name}' not found"}

        server.reconnect_attempts += 1
        delay = server.backoff_delay()

        if server.reconnect_attempts > server.max_reconnects:
            return {"error": f"Max reconnect attempts ({server.max_reconnects}) exceeded"}

        await asyncio.sleep(delay)

        try:
            # 尝试健康检查
            healthy = await self._check_health(server)
            server.connected = healthy
            server.healthy = healthy
            server.last_error = None if healthy else "Health check failed"
            server.reconnect_attempts = 0 if healthy else server.reconnect_attempts

            return {
                "server": name,
                "connected": healthy,
                "attempt": server.reconnect_attempts,
                "delay_used": delay,
            }
        except Exception as e:
            server.last_error = str(e)
            return {
                "server": name,
                "connected": False,
                "error": str(e),
                "next_retry_delay": server.backoff_delay(),
            }

    def toggle_server(self, name: str) -> Dict:
        """开关 MCP Server"""
        server = self.servers.get(name)
        if not server:
            return {"error": f"Server '{name}' not found"}

        server.enabled = not server.enabled
        return {
            "server": name,
            "enabled": server.enabled,
            "action": "enabled" if server.enabled else "disabled",
        }

    def approve_tool(self, server_name: str, tool_name: str, approved: bool = True):
        """审批工具调用"""
        server = self.servers.get(server_name)
        if server:
            server.approved_tools[tool_name] = approved

    def is_tool_approved(self, server_name: str, tool_name: str) -> bool:
        """检查工具是否已审批"""
        server = self.servers.get(server_name)
        if not server:
            return False
        return server.approved_tools.get(tool_name, False)

    def needs_approval(self, server_name: str, tool_name: str) -> bool:
        """检查工具是否需要审批"""
        server = self.servers.get(server_name)
        if not server:
            return True
        return tool_name not in server.approved_tools

    async def start_health_checks(self, interval: float = 30.0):
        """启动定期健康检查"""
        self._running = True
        while self._running:
            for name, server in self.servers.items():
                if server.enabled:
                    healthy = await self._check_health(server)
                    server.healthy = healthy
                    server.last_health_check = datetime.now()
                    if not healthy and server.reconnect_attempts < server.max_reconnects:
                        await self.reconnect_server(name)
            await asyncio.sleep(interval)

    def stop_health_checks(self):
        """停止健康检查"""
        self._running = False

    async def _check_health(self, server: MCPServerState) -> bool:
        """检查单个 Server 健康状态"""
        if server.url:
            try:
                health_url = server.url.rstrip("/") + "/health"
                resp = requests.get(health_url, timeout=5)
                return resp.status_code == 200
            except Exception:
                pass

            try:
                resp = requests.get(server.url, timeout=5)
                return resp.status_code < 500
            except Exception:
                return False

        # 对于命令行类型的 MCP Server，通过检查进程状态
        if server.process:
            return server.process.poll() is None

        return False

    def record_call(self, name: str, success: bool = True):
        """记录工具调用"""
        server = self.servers.get(name)
        if server:
            server.total_calls += 1
            if not success:
                server.total_errors += 1

    def get_health_report(self) -> Dict:
        """获取健康报告"""
        total = len(self.servers)
        healthy = sum(1 for s in self.servers.values() if s.healthy)
        connected = sum(1 for s in self.servers.values() if s.connected)
        enabled = sum(1 for s in self.servers.values() if s.enabled)

        return {
            "total_servers": total,
            "enabled": enabled,
            "connected": connected,
            "healthy": healthy,
            "health_ratio": f"{healthy}/{total}",
            "servers": self.list_servers(),
        }


# ══════════════════════════════════════════════
# 便捷函数
# ══════════════════════════════════════════════

_mcp_manager: Optional[MCPConnectionManager] = None


def get_mcp_manager(config_path: str = None) -> MCPConnectionManager:
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPConnectionManager()
        if config_path:
            _mcp_manager.load_config(config_path)
    return _mcp_manager


# 测试
if __name__ == "__main__":
    mgr = MCPConnectionManager()
    # 模拟加载配置
    mgr.servers["test_mcp"] = MCPServerState(
        name="test_mcp",
        command="python3",
        args=["-c", "print('ok')"],
    )
    mgr.servers["test_mcp"].connected = True
    mgr.servers["test_mcp"].healthy = True

    print(json.dumps(mgr.get_health_report(), indent=2, ensure_ascii=False))
