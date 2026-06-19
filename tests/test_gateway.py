"""Gateway enforcement tests — v0.1.1.

Validates:
- Token minted at spawn, returned once in manifest
- validate_gateway_token() accepts live token
- validate_gateway_token() rejects missing/malformed token
- validate_gateway_token() rejects evaporated session (enforcement closed)
- validate_gateway_token() rejects TTL-expired session
- GhostGateway proxies requests with valid token
- GhostGateway returns 401 for missing/invalid token
- GhostGateway returns 401 after evaporate (the gap closed by v0.1.1)
"""

from __future__ import annotations

import http.client
import json
import platform
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ghost import session as gsession
from ghost.gateway import GatewayTokenError, GhostGateway, validate_gateway_token, validate_upstream_url
from ghost.session import ExpiredSessionError
from ghost.store import ResidueStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    s = ResidueStore(tmp_path / "residue.db")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolate_sessions(tmp_path, monkeypatch):
    sdir = tmp_path / "sessions"
    monkeypatch.setattr(gsession, "SESSIONS_DIR", sdir)
    return sdir


def _fake_upstream(port: int) -> HTTPServer:
    """Minimal upstream server that echoes method + path as JSON."""
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _respond(self):
            body = json.dumps({
                "method": self.command,
                "path": self.path,
                "upstream": True,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = _respond

    srv = HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


# ---------------------------------------------------------------------------
# Token minting tests
# ---------------------------------------------------------------------------

def test_spawn_returns_token(store):
    res = gsession.spawn(store, intent="test", ttl=60)
    assert "token" in res
    assert res["token"].startswith("ghtok_")
    assert len(res["token"]) > 20


def test_spawn_token_hash_stored(store):
    res = gsession.spawn(store, intent="test", ttl=60)
    # validate_token should find it
    row = store.validate_token(res["token"])
    assert row is not None
    assert row["session_id"] == res["session_id"]


# ---------------------------------------------------------------------------
# validate_gateway_token tests
# ---------------------------------------------------------------------------

def test_validate_accepts_live_token(store):
    res = gsession.spawn(store, intent="test", ttl=60)
    info = validate_gateway_token(store, res["token"])
    assert info["session_id"] == res["session_id"]


def test_validate_rejects_missing_token(store):
    with pytest.raises(GatewayTokenError):
        validate_gateway_token(store, "")


def test_validate_rejects_malformed_token(store):
    with pytest.raises(GatewayTokenError):
        validate_gateway_token(store, "not-a-ghost-token")


def test_validate_rejects_unknown_token(store):
    gsession.spawn(store, intent="test", ttl=60)
    with pytest.raises(GatewayTokenError):
        validate_gateway_token(store, "ghtok_" + "00" * 32)


def test_validate_rejects_evaporated_token(store):
    """THE GAP CLOSED BY v0.1.1: evaporate() invalidates the token server-side."""
    res = gsession.spawn(store, intent="test", ttl=60)
    gsession.evaporate(store, res["session_id"])
    with pytest.raises(GatewayTokenError):
        validate_gateway_token(store, res["token"])


def test_validate_rejects_ttl_expired_token(store):
    res = gsession.spawn(store, intent="test", ttl=1)
    time.sleep(1.1)
    with pytest.raises(ExpiredSessionError):
        validate_gateway_token(store, res["token"])


# ---------------------------------------------------------------------------
# GhostGateway integration tests
# ---------------------------------------------------------------------------

def _get(port: int, path: str = "/", token: str = "", extra_headers: dict = None):
    """Make a GET through the gateway, return (status, body_dict)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {}
    if token:
        headers["X-Ghost-Token"] = token
    if extra_headers:
        headers.update(extra_headers)
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()
    return resp.status, body


def test_gateway_proxies_valid_token(store):
    upstream = _fake_upstream(17391)
    res = gsession.spawn(store, intent="test", ttl=60)
    gw = GhostGateway(store, upstream_url="http://127.0.0.1:17391", port=17392, validate_ssrf=False)
    gw.start()
    try:
        status, body = _get(17392, "/hello", token=res["token"])
        assert status == 200
        assert body["upstream"] is True
        assert body["path"] == "/hello"
    finally:
        gw.stop()
        upstream.shutdown()


def test_gateway_rejects_no_token(store):
    upstream = _fake_upstream(17393)
    gsession.spawn(store, intent="test", ttl=60)
    gw = GhostGateway(store, upstream_url="http://127.0.0.1:17393", port=17394, validate_ssrf=False)
    gw.start()
    try:
        status, body = _get(17394)
        assert status == 401
        assert "unauthorized" in body["error"]
    finally:
        gw.stop()
        upstream.shutdown()


def test_gateway_rejects_after_evaporate(store):
    """Core v0.1.1 claim: gateway returns 401 after evaporate, even with cached token."""
    upstream = _fake_upstream(17395)
    res = gsession.spawn(store, intent="test", ttl=60)
    gw = GhostGateway(store, upstream_url="http://127.0.0.1:17395", port=17396, validate_ssrf=False)
    gw.start()
    try:
        # Token works before evaporate
        status, _ = _get(17396, token=res["token"])
        assert status == 200

        # Evaporate the session
        gsession.evaporate(store, res["session_id"])

        # Same token now rejected — enforcement gap closed
        status, body = _get(17396, token=res["token"])
        assert status == 401
        assert body["error"] in ("unauthorized", "session_expired")
    finally:
        gw.stop()
        upstream.shutdown()


def test_gateway_broker_mode_injects_upstream_key(store):
    """Broker mode: gateway injects upstream key, Authorization never seen by agent."""
    captured = {}

    class _CapturingHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            captured["auth"] = self.headers.get("Authorization", "")
            # None means the header was NOT forwarded (gateway stripped it correctly)
            captured["ghost_token"] = self.headers.get("X-Ghost-Token", None)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = HTTPServer(("127.0.0.1", 17397), _CapturingHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    res = gsession.spawn(store, intent="test", ttl=60)
    gw = GhostGateway(
        store,
        upstream_url="http://127.0.0.1:17397",
        upstream_key="sk_live_real_secret",
        port=17398,
        validate_ssrf=False,
    )
    gw.start()
    try:
        # Agent sends ghost token but NO Authorization
        status, _ = _get(17398, token=res["token"])
        assert status == 200
        # Gateway injected the real key upstream
        assert captured["auth"] == "Bearer sk_live_real_secret"
        # Gateway stripped X-Ghost-Token before forwarding (None = not present)
        assert captured.get("ghost_token") is None
    finally:
        gw.stop()
        upstream.shutdown()


# ---------------------------------------------------------------------------
# SSRF protection tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/internal",
    "http://localhost/internal",
    "http://169.254.169.254/latest/meta-data/",  # AWS metadata endpoint
    "http://10.0.0.1/secret",
    "http://192.168.1.1/admin",
    "http://172.16.0.1/admin",
])
def test_validate_upstream_url_blocks_private(url: str) -> None:
    """SSRF: private/loopback/metadata addresses must be rejected."""
    with pytest.raises(ValueError, match="SSRF|private|internal|loopback|localhost"):
        validate_upstream_url(url)


def test_validate_upstream_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        validate_upstream_url("ftp://example.com/file")


def test_validate_upstream_url_rejects_empty() -> None:
    with pytest.raises(ValueError):
        validate_upstream_url("")


def test_ghost_gateway_rejects_ssrf_upstream(store) -> None:
    """GhostGateway constructor raises ValueError for a private upstream URL."""
    with pytest.raises(ValueError):
        GhostGateway(store, upstream_url="http://169.254.169.254/latest/", validate_ssrf=True)
