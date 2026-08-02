"""Tests for MCP layer hardening in ``core/mcp_manager.py``.

Covers the Cursor-MCP-SDK-inspired additions:
  - ``redact_text`` sensitive-field redaction
  - ``MCPError`` error classification
  - ``MCPOAuthManager`` authorization-code flow (PKCE, refresh single-flight)
  - ``MCPServerState`` backoff / fast→periodic retry
  - ``MCPConnectionManager`` connection state machine + heartbeat
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import mcp_manager  # noqa: E402
from core.mcp_manager import (  # noqa: E402
    ConnectionState,
    FAST_RETRY_MAX_ATTEMPTS,
    MCPConnectionManager,
    MCPError,
    MCPOAuthConfig,
    MCPOAuthManager,
    MCPServerState,
    STABILIZE_EVENTS,
    redact_text,
)


# ══════════ 1. redact_text ══════════


class TestRedactText:
    def test_redacts_api_key_param(self):
        text = "GET /data?api_key=sk-1234567890abcdefghijklmnop"
        out = redact_text(text)
        assert "sk-1234567890abcdefghijklmnop" not in out
        assert "api_key=" in out
        assert "***" in out

    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdef"
        out = redact_text(text)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in out
        assert "Bearer " in out

    def test_redacts_sk_prefix(self):
        text = "token=sk-proj-0123456789abcdef0123456789abcdef0123456789abcdef"
        out = redact_text(text)
        assert "sk-proj" not in out

    def test_empty_unchanged(self):
        assert redact_text("") == ""
        assert redact_text(None) is None

    def test_plain_text_unchanged(self):
        assert redact_text("hello world 123") == "hello world 123"


# ══════════ 2. MCPError classification ══════════


class TestMCPError:
    def test_has_kind_and_server(self):
        err = MCPError("boom", MCPError.KIND_CONNECTION, server="foo")
        assert err.kind == MCPError.KIND_CONNECTION
        assert err.server == "foo"
        assert str(err) == "boom"

    @pytest.mark.parametrize(
        "msg, expected",
        [
            ("connection refused by remote host", MCPError.KIND_CONNECTION),
            ("connect call failed", MCPError.KIND_CONNECTION),
            ("request timed out after 30s", MCPError.KIND_TIMEOUT),
            ("deadline exceeded", MCPError.KIND_TIMEOUT),
            ("HTTP 401 unauthorized", MCPError.KIND_AUTH),
            ("403 forbidden", MCPError.KIND_AUTH),
            ("authentication required", MCPError.KIND_AUTH),
            ("invalid json payload", MCPError.KIND_PROTOCOL),
            ("parse error at line 3", MCPError.KIND_PROTOCOL),
            ("server busy, retry later", MCPError.KIND_BUSY),
            ("lock held by another client", MCPError.KIND_BUSY),
            ("something weird happened", MCPError.KIND_UNKNOWN),
        ],
    )
    def test_classify(self, msg, expected):
        assert MCPError.classify(msg) == expected


# ══════════ 3. MCPServerState backoff / retry ══════════


class TestMCPServerState:
    def test_backoff_delay_exponential(self):
        s = MCPServerState(name="srv", command="x")
        assert s.backoff_delay() == 1.0
        s.reconnect_attempts = 2
        assert s.backoff_delay() == 4.0

    def test_backoff_delay_capped(self):
        s = MCPServerState(name="srv", command="x")
        s.reconnect_attempts = 20
        assert s.backoff_delay() == 60.0

    def test_retry_delay_fast_track(self):
        s = MCPServerState(name="srv", command="x")
        s.backoff_attempts = 1
        delay, track = s.retry_delay()
        assert track == "fast"
        assert delay == 0.5  # 500ms base

    def test_retry_delay_periodic_after_max(self):
        s = MCPServerState(name="srv", command="x")
        s.backoff_attempts = FAST_RETRY_MAX_ATTEMPTS + 1
        delay, track = s.retry_delay()
        assert track == "periodic"
        assert delay == 60.0


# ══════════ 4. MCPConnectionManager state machine ══════════


class TestConnectionStateMachine:
    def test_success_path_to_stabilized(self):
        mgr = MCPConnectionManager()
        r1 = mgr.record_connection_event("srv", ok=True)
        assert r1["state"] == ConnectionState.CONNECTED.value
        for _ in range(STABILIZE_EVENTS):
            mgr.record_connection_event("srv", ok=True)
        s = mgr.servers["srv"]
        assert s.state == ConnectionState.STABILIZED
        assert s.backoff_attempts == 0

    def test_failure_resets_and_accumulates_backoff(self):
        mgr = MCPConnectionManager()
        mgr.record_connection_event("srv", ok=True)
        r = mgr.record_connection_event("srv", ok=False)
        s = mgr.servers["srv"]
        assert s.state == ConnectionState.DISCONNECTED
        assert r["backoff_attempts"] == 1
        assert s.stability_events == 0

    def test_retry_plan_tracks(self):
        mgr = MCPConnectionManager()
        plan = mgr.get_retry_plan("srv")
        assert plan["track"] == "fast"
        assert plan["fast_max"] == FAST_RETRY_MAX_ATTEMPTS

    def test_heartbeat_renewal_and_loss(self):
        mgr = MCPConnectionManager()
        mgr.heartbeat_beat("srv")
        s = mgr.servers["srv"]
        assert s.heartbeat_lost_count == 0
        assert s.last_heartbeat is not None


# ══════════ 5. MCPOAuthManager ══════════


class TestOAuthManager:
    def make_manager(self, tmp_path) -> MCPOAuthManager:
        store = str(tmp_path / "oauth_tokens.json")
        mgr = MCPOAuthManager(token_store=store)
        mgr.register(
            "github-mcp",
            client_id="cid123",
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
        )
        return mgr

    def test_register_and_status(self, tmp_path):
        mgr = self.make_manager(tmp_path)
        assert mgr.is_registered("github-mcp")
        assert mgr.status("github-mcp")["registered"] is True
        assert mgr.status("github-mcp")["has_token"] is False

    def test_authorization_url_includes_pkce_and_state(self, tmp_path):
        mgr = self.make_manager(tmp_path)
        url = mgr.authorization_url("github-mcp")
        assert "response_type=code" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert "state=" in url
        # PKCE verifier captured for later exchange
        cfg = mgr._configs["github-mcp"]
        assert cfg.code_verifier
        assert cfg.state

    def test_authorization_url_unregistered_raises(self, tmp_path):
        mgr = self.make_manager(tmp_path)
        with pytest.raises(MCPError) as ei:
            mgr.authorization_url("nope")
        assert ei.value.kind == MCPError.KIND_AUTH

    def test_exchange_code_stores_token(self, tmp_path, monkeypatch):
        mgr = self.make_manager(tmp_path)
        mgr.authorization_url("github-mcp")
        state = mgr._configs["github-mcp"].state

        def fake_post(cfg, payload):
            assert payload["grant_type"] == "authorization_code"
            assert payload["code_verifier"]  # PKCE sent
            return {"access_token": "acc-123", "refresh_token": "ref-456",
                    "expires_in": 3600}

        monkeypatch.setattr(mgr, "_post_token", fake_post)
        out = mgr.exchange_code("github-mcp", "the-code", state=state)
        assert out["has_token"] is True
        headers = mgr.get_auth_headers("github-mcp")
        assert headers["Authorization"] == "Bearer acc-123"

    def test_exchange_code_state_mismatch_raises(self, tmp_path):
        mgr = self.make_manager(tmp_path)
        mgr.authorization_url("github-mcp")
        with pytest.raises(MCPError) as ei:
            mgr.exchange_code("github-mcp", "code", state="wrong-state")
        assert ei.value.kind == MCPError.KIND_AUTH

    def test_refresh_single_flight(self, tmp_path, monkeypatch):
        mgr = self.make_manager(tmp_path)
        mgr.authorization_url("github-mcp")
        calls = {"n": 0}

        def fake_post(cfg, payload):
            calls["n"] += 1
            return {"access_token": "acc-new", "expires_in": 3600}

        monkeypatch.setattr(mgr, "_post_token", fake_post)
        mgr.exchange_code("github-mcp", "code", state=mgr._configs["github-mcp"].state)
        # 第二次 refresh 应被 single-flight 拦截
        mgr._refresh_in_flight["github-mcp"] = True
        r = mgr.refresh("github-mcp")
        assert r["refreshed"] is False
        assert "in flight" in r["reason"]

    def test_refresh_keeps_old_refresh_token(self, tmp_path, monkeypatch):
        mgr = self.make_manager(tmp_path)
        mgr.authorization_url("github-mcp")
        monkeypatch.setattr(
            mgr, "_post_token",
            lambda cfg, payload: {"access_token": "acc-1",
                                  "refresh_token": "ref-old", "expires_in": 3600})
        mgr.exchange_code("github-mcp", "code", state=mgr._configs["github-mcp"].state)

        # 刷新响应不含 refresh_token → 保留旧值
        monkeypatch.setattr(
            mgr, "_post_token",
            lambda cfg, payload: {"access_token": "acc-2", "expires_in": 3600})
        r = mgr.refresh("github-mcp")
        assert r["refreshed"] is True
        assert mgr._tokens["github-mcp"]["refresh_token"] == "ref-old"
        assert mgr._tokens["github-mcp"]["access_token"] == "acc-2"

    def test_get_auth_headers_auto_refresh_on_expiry(self, tmp_path, monkeypatch):
        mgr = self.make_manager(tmp_path)
        mgr.authorization_url("github-mcp")
        monkeypatch.setattr(
            mgr, "_post_token",
            lambda cfg, payload: {"access_token": "acc-1",
                                  "refresh_token": "ref-old", "expires_in": 3600})
        mgr.exchange_code("github-mcp", "code", state=mgr._configs["github-mcp"].state)

        # 把 token 标记为已过期 → get_auth_headers 应触发刷新
        mgr._tokens["github-mcp"]["expires_at"] = time.time() - 10
        monkeypatch.setattr(
            mgr, "_post_token",
            lambda cfg, payload: {"access_token": "acc-fresh", "expires_in": 3600})
        headers = mgr.get_auth_headers("github-mcp")
        assert headers["Authorization"] == "Bearer acc-fresh"

    def test_get_auth_headers_without_token_raises(self, tmp_path):
        mgr = self.make_manager(tmp_path)
        with pytest.raises(MCPError) as ei:
            mgr.get_auth_headers("github-mcp")
        assert ei.value.kind == MCPError.KIND_AUTH

    def test_connection_manager_oauth_shortcuts(self, tmp_path, monkeypatch):
        mgr = MCPConnectionManager()
        monkeypatch.setattr(mgr, "oauth", self.make_manager(tmp_path))
        mgr.register_oauth("srv", client_id="c", token_url="https://t")
        assert mgr.oauth_auth_url("srv")  # no exception
        assert mgr.oauth_status("srv")["registered"] is True


# ══════════ 6. MCPOAuthConfig dataclass defaults ══════════


class TestOAuthConfig:
    def test_defaults(self):
        cfg = MCPOAuthConfig(server="s")
        assert cfg.scopes == "mcp"
        assert cfg.redirect_uri.startswith("http://127.0.0.1")
        assert cfg.client_secret == ""
