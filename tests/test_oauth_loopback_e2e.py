"""End-to-end test for ``MCPOAuthManager.oauth_authorize_loopback``.

Real HTTP end-to-end (no mocking of the HTTP layer):
  - a fake OAuth authorization server (token endpoint) runs on a random port
  - ``oauth_authorize_loopback`` starts the local callback server, we then
    simulate the browser hitting the auth callback URL with code+state
  - assert the whole chain: auth_url → callback → exchange → token stored

Also covers the negative path: callback with wrong state → CSRF rejection.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mcp_manager import MCPConnectionManager, MCPError  # noqa: E402


# ── fake OAuth authorization server (real HTTP, random port) ──


class FakeTokenHandler(BaseHTTPRequestHandler):
    """Serves POST /token returning a fixed token JSON."""

    access_token = "e2e-access-token-1234567890"
    refresh_token = "e2e-refresh-token-abcdef"
    requests_seen: list[dict] = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        self.requests_seen.append(urllib.parse.parse_qs(body))
        payload = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # health probe
        data = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_auth_server():
    """Start the fake token server; yield base URL."""
    FakeTokenHandler.requests_seen = []
    srv = HTTPServer(("127.0.0.1", 0), FakeTokenHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _http_get(url: str, timeout: float = 5.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


# ── E2E: happy path ──


class TestLoopbackE2E:
    def test_full_authorization_chain(self, fake_auth_server):
        mgr = MCPConnectionManager()
        mgr.register_oauth(
            "e2e-srv",
            client_id="e2e-client",
            authorization_url=f"{fake_auth_server}/authorize",
            token_url=f"{fake_auth_server}/token",
        )

        # Run loopback in a background thread (it blocks waiting for callback).
        result_holder: dict = {}
        thread = threading.Thread(
            target=lambda: result_holder.update(
                mgr.oauth_authorize_loopback("e2e-srv", timeout_s=15)),
            daemon=True)
        thread.start()

        # Wait for the callback server to be up (redirect_uri gets a real port).
        cfg = mgr.oauth._configs["e2e-srv"]
        deadline = time.time() + 10
        while time.time() < deadline and ":0/" in cfg.redirect_uri:
            time.sleep(0.05)
        assert ":0/" not in cfg.redirect_uri, "loopback server never started"

        # Simulate the browser: user authorized → redirect to local callback.
        state = cfg.state
        callback = f"{cfg.redirect_uri}?code=e2e-auth-code&state={state}"
        page = _http_get(callback)
        assert "授权成功" in page

        thread.join(timeout=15)
        assert not thread.is_alive(), "loopback did not finish in time"

        # Whole chain succeeded: token exchanged + stored.
        assert result_holder["ok"] is True
        assert result_holder["has_token"] is True
        headers = mgr.oauth.get_auth_headers("e2e-srv")
        assert headers["Authorization"] == "Bearer e2e-access-token-1234567890"

        # The token endpoint was actually hit with the authorization_code grant.
        seen = FakeTokenHandler.requests_seen
        assert seen, "token endpoint was never called"
        assert seen[-1].get("grant_type") == ["authorization_code"]
        assert seen[-1].get("code") == ["e2e-auth-code"]
        assert seen[-1].get("code_verifier"), "PKCE verifier must be sent"

    def test_callback_wrong_state_rejected(self, fake_auth_server):
        """CSRF 防护: 回调 state 不匹配 → 拒绝并保留无 token 状态。"""
        mgr = MCPConnectionManager()
        mgr.register_oauth(
            "e2e-srv2",
            client_id="e2e-client",
            authorization_url=f"{fake_auth_server}/authorize",
            token_url=f"{fake_auth_server}/token",
        )
        mgr.oauth.authorization_url("e2e-srv2")  # generate real state

        result_holder: dict = {}
        thread = threading.Thread(
            target=lambda: result_holder.update(
                mgr.oauth_authorize_loopback("e2e-srv2", timeout_s=15)),
            daemon=True)
        thread.start()

        cfg = mgr.oauth._configs["e2e-srv2"]
        deadline = time.time() + 10
        while time.time() < deadline and ":0/" in cfg.redirect_uri:
            time.sleep(0.05)

        # Attacker / stale browser sends a wrong state.
        callback = f"{cfg.redirect_uri}?code=evil&state=wrong-state-value"
        _http_get(callback)
        thread.join(timeout=15)

        # Loopback must report failure and NOT store a token.
        assert result_holder.get("ok") is False
        assert mgr.oauth.status("e2e-srv2")["has_token"] is False

    def test_timeout_without_callback(self, fake_auth_server):
        """无浏览器回调 → 超时返回 ok=False + auth_url 可访问。"""
        mgr = MCPConnectionManager()
        mgr.register_oauth(
            "e2e-srv3",
            client_id="e2e-client",
            authorization_url=f"{fake_auth_server}/authorize",
            token_url=f"{fake_auth_server}/token",
        )
        result = mgr.oauth_authorize_loopback("e2e-srv3", timeout_s=1)
        assert result["ok"] is False
        assert result["reason"] == "授权超时"
        assert "auth_url" in result and result["auth_url"]

    def test_unregistered_server_raises(self, fake_auth_server):
        mgr = MCPConnectionManager()
        with pytest.raises(MCPError) as ei:
            mgr.oauth_authorize_loopback("never-registered")
        assert ei.value.kind == MCPError.KIND_AUTH
